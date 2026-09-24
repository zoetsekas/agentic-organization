"""What every router shares: the services, the one authenticator, and the
authorisation helpers (ADR-0047, ADR-0114, ADR-0116).

`create_app` builds one `ApiContext` and hands it to each router's `build`.
The dependencies here — `principal`, `caller` — are the same function objects
in every router, so every route takes its caller from one place: no route
declares an identity header or reads `request.headers` itself (a test holds
the routes to that).

Services are created here, in the order the single `create_app` used to create
them, before any route is registered.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from ..designer import (
    DesignerError,
    DesignerService,
    LockConflict,
    PermissionDenied,
    Principal,
    build_repository,
)
from ..designer.audit import AuditAction as DesignerAuditAction
from ..designer.audit import AuditOutcome as DesignerAuditOutcome
from ..designer.auth import Authenticator, AuthError, verifier_from_settings
from ..designer.models import DesignerSettings
from ..fabric import rbac as fabric_rbac
from ..fabric.rbac import OperatorRegistry
from ..platform import Platform
from ..runtime_access import RUNTIME_READ, Grants, RuntimeAccess


class ApiContext:
    """The app's services and the helpers every route group uses."""

    def __init__(self, app: FastAPI, platform: Platform) -> None:
        self.app = app
        self.platform = platform

        # -- designer: workspaces, systems, canvas, locks (ADR-0031/0032/0033)
        settings = DesignerSettings(
            persistence=os.environ.get("ORGAGENTS_DESIGNER_STORE", "relational"),  # type: ignore[arg-type]
            storage_path=os.environ.get("ORGAGENTS_DESIGNER_PATH", "./designer-data"),
            # `none` (single-user local) unless a mode is asked for (ADR-0114).
            # The default used to be `trusted_proxy`, which with no proxy in
            # front meant any caller's X-User was believed; that mode now has
            # to be asked for *and* told how to recognise its proxy.
            auth_mode=os.environ.get("ORGAGENTS_DESIGNER_AUTH", "none"),  # type: ignore[arg-type]
            oidc_issuer=os.environ.get("ORGAGENTS_OIDC_ISSUER", ""),
            oidc_audiences=[a for a in os.environ.get(
                "ORGAGENTS_OIDC_AUDIENCE", "").split(",") if a],
            oidc_jwks_uri=os.environ.get("ORGAGENTS_OIDC_JWKS_URI", ""),
        )
        self.designer = DesignerService(
            build_repository(settings, platform.store), settings
        )
        app.state.designer = self.designer

        # One authenticator per app, holding the JWKS cache so keys are
        # fetched once rather than per request. Tests and air-gapped installs
        # replace its `verifier` with one over a local key set.
        designer_auth = Authenticator(
            settings, verifier=verifier_from_settings(settings),
            audit=self.designer.audit,
            proxy_secret=os.environ.get("ORGAGENTS_PROXY_SECRET", ""),
            proxy_sources=[s.strip() for s in os.environ.get(
                "ORGAGENTS_PROXY_SOURCES", "").split(",") if s.strip()],
            proxy_user_header=os.environ.get("ORGAGENTS_PROXY_USER_HEADER", ""),
            proxy_name_header=os.environ.get("ORGAGENTS_PROXY_NAME_HEADER", ""),
            proxy_email_header=os.environ.get("ORGAGENTS_PROXY_EMAIL_HEADER", ""),
        )
        # Refuses to start trusted-proxy mode with no way to tell the proxy apart.
        designer_auth.require_proxy_guard()
        app.state.designer_auth = designer_auth

        def principal(
            request: Request,
            authorization: str = Header(default=""),
            x_user: str = Header(default="anonymous"),
            x_user_name: str = Header(default=""),
            x_user_email: str = Header(default=""),
            x_orgagents_proxy_secret: str = Header(default=""),
        ) -> Principal:
            """Identity per the configured `auth_mode` (ADR-0047).

            In `oidc` mode this is a verified bearer token and the X-User header is
            ignored entirely; in `trusted_proxy` mode it is the header, which is
            only as good as the proxy. Either way the service never trusts a
            client-sent role. A failure here is a 401 — `_guard` owns the 403.
            """
            try:
                # The X-User parameters above document the `none`-mode headers;
                # which headers are actually read is the authenticator's call
                # (trusted_proxy reads only the ones its proxy sets, ADR-0114).
                auth = app.state.designer_auth
                return auth.authenticate(  # type: ignore[no-any-return]
                    authorization=authorization,
                    **auth.identity_headers(request.headers),
                    proxy_secret_header=x_orgagents_proxy_secret,
                    client_host=request.client.host if request.client else "",
                )
            except AuthError as e:
                raise HTTPException(401, str(e)) from e

        self.principal: Callable[..., Principal] = principal

        # -- runtime and catalog access (ADR-0116) -------------------------
        #
        # Created here rather than with the rest of the fabric, because the
        # runtime's operator grants are read from the same registry.
        self.fabric_operators = OperatorRegistry(
            platform.store,
            bootstrap=fabric_rbac.parse_bootstrap(
                os.environ.get("ORGAGENTS_FABRIC_OPERATORS", "")
            ),
        )
        self.runtime_access = RuntimeAccess(
            self.designer, self.fabric_operators,
            local_user=os.environ.get("ORGAGENTS_LOCAL_USER", "anonymous"),
        )
        app.state.runtime_access = self.runtime_access

        runtime_access = self.runtime_access

        def caller(user: Principal = Depends(principal)) -> Grants:
            """The authenticated caller with what they may do in the runtime."""
            return runtime_access.grants(user)

        self.caller: Callable[..., Grants] = caller

        # -- platform catalog (ADR-0041) -----------------------------------
        from ..catalogs import CatalogService, seed_catalog

        self.catalog_service = CatalogService(platform.store)
        if not self.catalog_service.list():
            seed_catalog(self.catalog_service)
        app.state.catalog = self.catalog_service

        # -- designer review surfaces (WS-009) -----------------------------
        from ..evaluations import EvaluationService

        self.designer_evaluations = EvaluationService(platform.store)

        # -- fabric: the command centre (ADR-0049, ADR-0051) ---------------
        from ..fabric.audit import FabricAuditLog
        from ..fabric.deployments import DeploymentService
        from ..fabric.health import HealthService, StubBackend
        from ..fabric.quotas import QuotaService
        from ..fabric.services import ServiceRegistry
        from ..fabric.tenants import TenantRegistry

        self.fabric_tenants = TenantRegistry(platform.store)
        self.fabric_deployments = DeploymentService(platform.store)
        self.fabric_quotas = QuotaService(platform.store)
        self.fabric_services = ServiceRegistry(platform.store)
        # No target adapter exists in this environment (WS-030 M5): the backend
        # is injected so an installation — or a test — supplies a real one.
        self.fabric_health_backend = StubBackend()
        self.fabric_health = HealthService(platform.store, self.fabric_deployments,
                                           self.fabric_health_backend)
        self.fabric_audit = FabricAuditLog(platform.store)
        app.state.fabric = {
            "tenants": self.fabric_tenants,
            "deployments": self.fabric_deployments,
            "quotas": self.fabric_quotas,
            "services": self.fabric_services,
            "health": self.fabric_health,
            "health_backend": self.fabric_health_backend,
            "audit": self.fabric_audit,
            "operators": self.fabric_operators,
        }

    # -- the designer's refusals, as HTTP ------------------------------------

    def guard(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        from ..designer.service import FieldErrors
        try:
            return fn(*args, **kwargs)
        except FieldErrors as e:
            # Named per field, so a form can put each message under its own
            # field (ADR-0106).
            raise HTTPException(422, {"message": str(e),
                                      "fields": e.fields}) from e
        except PermissionDenied as e:
            raise HTTPException(403, str(e)) from e
        except LockConflict as e:
            raise HTTPException(
                409, {"error": str(e), "lock": e.lock.model_dump(mode="json")}
            ) from e
        except ValueError as e:
            # A value the model refuses is the caller's mistake, not a missing
            # thing: 422 with the reason, never a 500 from a store.
            raise HTTPException(422, str(e)) from e
        except DesignerError as e:
            raise HTTPException(404, str(e)) from e

    # -- runtime and catalog authorisation (ADR-0116) ------------------------

    def record(self, g: Grants, action: DesignerAuditAction,
               outcome: DesignerAuditOutcome, *, permission: str,
               workspace_id: Optional[str], reason: str = "",
               detail: Optional[dict[str, Any]] = None) -> None:
        self.designer.audit.record(
            action, g.principal, outcome=outcome,
            workspace_id=workspace_id or "", permission=permission,
            reason=reason, detail=detail or {},
        )

    def authorize(self, g: Grants, permission: str, workspace_id: Optional[str],
                  action: DesignerAuditAction, *, anywhere: bool = False,
                  detail: Optional[dict[str, Any]] = None) -> None:
        """Deny by default, and write the refusal down before raising it."""
        ok = g.anywhere(permission) if anywhere else g.allows(permission, workspace_id)
        if not ok:
            reason = (f"{g.principal.label} does not hold '{permission}' anywhere"
                      if anywhere else g.reason(permission, workspace_id))
            self.record(g, action, DesignerAuditOutcome.DENIED, permission=permission,
                        workspace_id=workspace_id, reason=reason, detail=detail)
            raise HTTPException(403, reason)

    def allowed(self, g: Grants, action: DesignerAuditAction, permission: str,
                workspace_id: Optional[str],
                detail: Optional[dict[str, Any]] = None) -> None:
        self.record(g, action, DesignerAuditOutcome.SUCCESS, permission=permission,
                    workspace_id=workspace_id, detail=detail)

    def scope_of_agent_id(self, agent_id: str) -> Optional[str]:
        agent = self.platform.org.agent(agent_id)
        return self.runtime_access.workspace_of_agent(agent) if agent else None

    def visible_agent_ids(self, g: Grants) -> set[str]:
        return {a.id for a in self.platform.org.agents()
                if g.allows(RUNTIME_READ, self.runtime_access.workspace_of_agent(a))}

    def require_read_anywhere(self, g: Grants) -> None:
        self.authorize(g, RUNTIME_READ, None, DesignerAuditAction.RUNTIME_READ,
                       anywhere=True)

    def read_catalog(self, g: Grants) -> None:
        from ..runtime_access import CATALOG_READ

        self.authorize(g, CATALOG_READ, None, DesignerAuditAction.CATALOG_READ,
                       anywhere=True)

    def acting_as(self, g: Grants, claimed: str) -> str:
        """Who a run or a resume is recorded against: the authenticated
        caller, never a name in the body. An MCP server's `mcp:<user>` label
        is kept when `<user>` is the caller (ADR-0115)."""
        me = g.principal.user_id
        return claimed if claimed in (me, f"mcp:{me}") else me

    def session_scope_check(self, g: Grants, session_id: str, permission: str,
                            action: DesignerAuditAction) -> Optional[str]:
        s = self.platform.sessions.get(session_id)
        if s is None:
            raise HTTPException(404, "session not found")
        scope = self.scope_of_agent_id(s.agent_id)
        self.authorize(g, permission, scope, action,
                       detail={"session": session_id, "agent": s.agent_id})
        return scope
