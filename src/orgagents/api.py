"""HTTP API for the platform and the agentic designer UI.

Route groups:

``/api/org``        org units, agents, hierarchy tree
``/api/agents``     CRUD, harness preview, runs
``/api/sessions``   sessions, events, traces, resume — each session has a URL
``/api/catalog``    marketplace search, detail, install, rate
``/api/components`` the designer's palette: runtimes, sandboxes, workflows,
                    channels, data planes, infrastructure options
``/api/ops``        metrics, alerts, health
``/api/fabric``     the command centre: tenants, deployments, health, quotas,
                    common services, operator actions and the operator audit
                    log (ADR-0051, docs/COMMAND_CENTRE_API.md)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .models import (
    Agent,
    ChannelKind,
    OrgUnit,
    Plugin,
    Runtime,
    SandboxTemplate,
    Skill,
    Visibility,
    WorkflowRef,
)
from .platform import Platform
from .store import PLUGINS, SKILLS, WORKFLOWS

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class RunRequest(BaseModel):
    prompt: str
    created_by: str = "ui"
    session_id: Optional[str] = None


class ResumeRequest(BaseModel):
    response: str
    actor: str = "human"


class InstallRequest(BaseModel):
    agent_id: str


class CreateSystemRequest(BaseModel):
    workspace_id: str
    name: str
    description: str = ""
    spec: Optional[dict[str, Any]] = None


class SaveSystemRequest(BaseModel):
    spec: Optional[dict[str, Any]] = None
    layout: Optional[dict[str, Any]] = None
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    base_version: Optional[int] = None
    strategy: Optional[str] = None
    resolutions: dict[str, Any] = {}
    message: str = ""


class LockRequest(BaseModel):
    target: str = "*"
    scope: str = "component"
    note: str = ""


class WorkspaceRequest(BaseModel):
    name: str
    description: str = ""


class MemberRequest(BaseModel):
    user_id: str
    display_name: str = ""
    email: str = ""
    role: str = "viewer"


class PublishRequest(BaseModel):
    kind: str
    ref_id: str
    owner: str = ""
    tags: list[str] = []
    visibility: Visibility = Visibility.PUBLIC
    groups: list[str] = []


class OperatorActionRequest(BaseModel):
    """Why an operator acted. Recorded; not validated beyond being present."""

    reason: str = ""


class RequotaRequest(BaseModel):
    #: QuotaKind value -> {soft_limit, hard_ceiling?, unit?}
    quotas: dict[str, dict[str, Any]] = {}
    #: Omitted leaves the entitlement allow-list alone; [] empties it.
    catalog_entries: Optional[list[str]] = None
    reason: str = ""


class OperatorGrantRequest(BaseModel):
    user_id: str
    roles: list[str] = []
    reason: str = ""


def create_app(
    db_path: Optional[str] = None, base_url: str = "http://localhost:8000"
) -> FastAPI:
    platform = Platform(db_path or os.environ.get("ORGAGENTS_DB", "orgagents.db"),
                        base_url=base_url)
    app = FastAPI(title="Organizational Agentic System", version="0.1.0")
    app.state.platform = platform

    # -- org ---------------------------------------------------------------

    @app.get("/api/org/units")
    def list_units() -> list[dict]:
        return [u.model_dump() for u in platform.org.units()]

    @app.post("/api/org/units")
    def create_unit(unit: OrgUnit) -> dict:
        return platform.org.add_unit(unit).model_dump()

    @app.get("/api/org/tree")
    def org_tree(root: Optional[str] = None) -> list[dict]:
        return platform.org.to_tree(root)

    # -- agents ------------------------------------------------------------

    @app.get("/api/agents")
    def list_agents(org_unit_id: Optional[str] = None) -> list[dict]:
        return [a.model_dump() for a in platform.org.agents(org_unit_id)]

    @app.post("/api/agents")
    def create_agent(agent: Agent) -> dict:
        return platform.org.add_agent(agent).model_dump()

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str) -> dict:
        agent = platform.org.agent(agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        return agent.model_dump()

    @app.put("/api/agents/{agent_id}")
    def update_agent(agent_id: str, agent: Agent) -> dict:
        if platform.org.agent(agent_id) is None:
            raise HTTPException(404, "agent not found")
        agent.id = agent_id
        return platform.org.add_agent(agent).model_dump()

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str) -> dict:
        return {"deleted": platform.store.delete("agents", agent_id)}

    @app.get("/api/agents/{agent_id}/harness")
    def agent_harness(agent_id: str) -> dict:
        agent = platform.org.agent(agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        return {
            "harness": agent.harness.model_dump(),
            "system_prompt": platform.harness.system_prompt(agent),
            "tools": [b.model_dump() for b in platform.harness.bindings(agent)],
            "sandbox": (
                platform.sandboxes.container_spec(agent.sandbox) if agent.sandbox else None
            ),
            "skills": [s.model_dump() for s in platform.harness.skills(agent)],
        }

    @app.post("/api/agents/{agent_id}/run")
    def run_agent(agent_id: str, req: RunRequest) -> dict:
        try:
            result = platform.runtime.run(
                agent_id, req.prompt, session_id=req.session_id,
                created_by=req.created_by,
            )
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        return {
            "session_id": result.session_id,
            "session_url": result.session_url,
            "output": result.output,
            "state": result.state.value,
            "delegations": result.delegations,
            "error": result.error,
        }

    # -- sessions ----------------------------------------------------------

    @app.get("/api/sessions")
    def list_sessions(agent_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        return [
            s.model_dump() | {"url": platform.sessions.url(s.id)}
            for s in platform.sessions.list(agent_id, limit)
        ]

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        s = platform.sessions.get(session_id)
        if s is None:
            raise HTTPException(404, "session not found")
        return s.model_dump() | {"url": platform.sessions.url(session_id)}

    @app.get("/api/sessions/{session_id}/events")
    def session_events(session_id: str, limit: int = 500) -> list[dict]:
        return [e.model_dump() for e in platform.sessions.events(session_id, limit)]

    @app.get("/api/sessions/{session_id}/trace")
    def session_trace(session_id: str) -> dict:
        trace = platform.sessions.trace(session_id)
        if not trace:
            raise HTTPException(404, "session not found")
        return trace

    @app.post("/api/sessions/{session_id}/resume")
    def resume_session(session_id: str, req: ResumeRequest) -> dict:
        try:
            r = platform.runtime.resume(session_id, req.response, req.actor)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        return {"output": r.output, "state": r.state.value, "session_url": r.session_url}

    @app.get("/sessions/{session_id}")
    def session_page(session_id: str) -> Any:
        """Human-addressable session URL; the UI deep-links to it."""
        if platform.sessions.get(session_id) is None:
            raise HTTPException(404, "session not found")
        return RedirectResponse(f"/ui/#/sessions/{session_id}")

    # -- catalog / marketplace --------------------------------------------

    @app.get("/api/catalog")
    def search_catalog(
        q: str = "",
        kind: Optional[str] = None,
        tags: Optional[list[str]] = Query(default=None),
        groups: Optional[list[str]] = Query(default=None),
        sort: str = "recent",
        limit: int = 50,
    ) -> list[dict]:
        entries = platform.catalog.search(
            q, kind=kind or None, tags=tags or None, viewer_groups=groups,  # type: ignore[arg-type]
            sort=sort, limit=limit,
        )
        return [
            e.model_dump() | {"rating": e.rating, "url": platform.catalog.url(e)}
            for e in entries
        ]

    @app.get("/api/catalog/stats")
    def catalog_stats() -> dict:
        return platform.catalog.stats()

    @app.get("/api/catalog/{entry_id}")
    def catalog_detail(entry_id: str) -> dict:
        detail = platform.catalog.detail(entry_id)
        if detail is None:
            raise HTTPException(404, "catalog entry not found")
        return detail

    @app.post("/api/catalog/publish")
    def publish(req: PublishRequest) -> dict:
        try:
            entry = platform.catalog.publish(
                req.kind, req.ref_id, owner=req.owner, tags=req.tags,  # type: ignore[arg-type]
                visibility=req.visibility, groups=req.groups,
            )
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        return entry.model_dump()

    @app.post("/api/catalog/{entry_id}/install")
    def install(entry_id: str, req: InstallRequest) -> dict:
        try:
            return platform.catalog.install(entry_id, req.agent_id)
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/catalog/{entry_id}/rate")
    def rate(entry_id: str, stars: float) -> dict:
        try:
            return platform.catalog.rate(entry_id, stars).model_dump()
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)) from e

    # -- designer component palette ---------------------------------------

    @app.get("/api/components")
    def components() -> dict:
        """Everything the designer canvas can place."""
        return {
            "runtimes": [
                {"id": r.value, "name": r.name.replace("_", " ").title()} for r in Runtime
            ],
            "sandbox_templates": [
                t.model_dump() for t in platform.sandboxes.templates()
            ],
            "workflows": [
                w.model_dump()
                for w in platform.store.list(WORKFLOWS, WorkflowRef, limit=200)
            ],
            "skills": [s.model_dump() for s in platform.store.list(SKILLS, Skill, limit=200)],
            "plugins": [
                p.model_dump() for p in platform.store.list(PLUGINS, Plugin, limit=200)
            ],
            "channels": [{"id": c.value, "name": c.name.title()} for c in ChannelKind],
            "data_planes": [
                {
                    "id": Visibility.PRIVATE.value,
                    "name": "Private",
                    "detail": "Owned by one agent and its human counterpart.",
                },
                {
                    "id": Visibility.PROTECTED.value,
                    "name": "Protected",
                    "detail": "Shared with the agent's groups.",
                },
                {
                    "id": Visibility.PUBLIC.value,
                    "name": "Public",
                    "detail": "Readable org-wide; any agent may contribute.",
                },
            ],
            "infrastructure": INFRASTRUCTURE_CATALOG,
        }

    @app.get("/api/components/sandbox_templates")
    def sandbox_templates() -> list[dict]:
        return [t.model_dump() for t in platform.sandboxes.templates()]

    @app.post("/api/components/sandbox_templates")
    def publish_template(template: SandboxTemplate) -> dict:
        return platform.sandboxes.publish(template).model_dump()

    # -- operations --------------------------------------------------------

    @app.get("/api/ops/metrics")
    def metrics() -> dict:
        return platform.obs.metrics()

    @app.get("/api/ops/alerts")
    def alerts(include_acknowledged: bool = False) -> list[dict]:
        platform.obs.evaluate_alerts()
        return [a.model_dump() for a in platform.obs.alerts(include_acknowledged)]

    @app.post("/api/ops/alerts/{alert_id}/ack")
    def ack_alert(alert_id: str) -> dict:
        a = platform.obs.acknowledge(alert_id)
        if a is None:
            raise HTTPException(404, "alert not found")
        return a.model_dump()

    @app.get("/api/ops/health/{agent_id}")
    def agent_health(agent_id: str) -> dict:
        return platform.obs.agent_health(agent_id)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "agents": platform.store.count("agents")}

    # -- platform catalog (ADR-0041) --------------------------------------

    from .catalogs import (
        ApprovalStatus,
        CatalogEntry as PlatformCatalogEntry,
        CatalogKind,
        CatalogService,
        Entitlement,
        seed_catalog,
    )

    catalog_service = CatalogService(platform.store)
    if not catalog_service.list():
        seed_catalog(catalog_service)
    app.state.catalog = catalog_service

    @app.get("/api/catalogs")
    def catalogs_search(
        q: str = "",
        kind: Optional[str] = None,
        status: Optional[str] = None,
        groups: Optional[list[str]] = Query(default=None),
        environment: str = "development",
        selectable_only: bool = False,
    ) -> list[dict]:
        entries = catalog_service.search(
            q, kind=CatalogKind(kind) if kind else None,
            status=ApprovalStatus(status) if status else None,
            groups=groups, environment=environment,
            selectable_only=selectable_only,
        )
        return [e.model_dump(mode="json") for e in entries]

    @app.get("/api/catalogs/stats")
    def catalogs_stats() -> dict:
        return catalog_service.stats()

    @app.get("/api/catalogs/kinds")
    def catalogs_kinds() -> list[dict]:
        return [
            {"id": k.value, "label": k.value.replace("_", " ").title(),
             "count": len(catalog_service.list(k))}
            for k in CatalogKind
        ]

    @app.get("/api/catalogs/{entry_id}")
    def catalogs_detail(entry_id: str) -> dict:
        try:
            return catalog_service.describe(entry_id)
        except Exception as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/api/catalogs")
    def catalogs_publish(entry: PlatformCatalogEntry) -> dict:
        return catalog_service.publish(entry).model_dump(mode="json")

    @app.post("/api/catalogs/{entry_id}/review")
    def catalogs_review(entry_id: str, status: str, note: str = "",
                        x_user: str = Header(default="anonymous")) -> dict:
        try:
            return catalog_service.review(
                entry_id, ApprovalStatus(status), reviewer=x_user, note=note
            ).model_dump(mode="json")
        except Exception as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/catalogs/{entry_id}/entitle")
    def catalogs_entitle(entry_id: str, entitlement: Entitlement) -> dict:
        try:
            return catalog_service.entitle(entry_id,
                                           entitlement).model_dump(mode="json")
        except Exception as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/catalogs/models/permitted")
    def catalogs_permitted_models(policy: dict[str, Any],
                                  environment: str = "development") -> list[dict]:
        """Which catalogued models an agent's policy permits, cheapest first."""
        from .spec.model import ModelPolicy

        permitted = catalog_service.permitted_models(
            ModelPolicy.model_validate(policy), environment=environment)
        return [
            {"id": e.id, "name": e.name, "summary": e.summary,
             "attributes": e.attributes, "status": e.status.value}
            for e in permitted
        ]

    # -- designer: workspaces, systems, canvas, locks (ADR-0031/0032/0033) --

    from .designer import (
        DesignerError,
        DesignerService,
        Layout,
        LockConflict,
        Member,
        PermissionDenied,
        Principal,
        SystemStatus,
        UserRole,
        build_repository,
    )
    from .designer.auth import AuthError, Authenticator, verifier_from_settings
    from .designer.models import DesignerSettings

    designer_settings = DesignerSettings(
        persistence=os.environ.get("ORGAGENTS_DESIGNER_STORE", "relational"),  # type: ignore[arg-type]
        storage_path=os.environ.get("ORGAGENTS_DESIGNER_PATH", "./designer-data"),
        auth_mode=os.environ.get("ORGAGENTS_DESIGNER_AUTH", "trusted_proxy"),  # type: ignore[arg-type]
        oidc_issuer=os.environ.get("ORGAGENTS_OIDC_ISSUER", ""),
        oidc_audiences=[a for a in os.environ.get(
            "ORGAGENTS_OIDC_AUDIENCE", "").split(",") if a],
        oidc_jwks_uri=os.environ.get("ORGAGENTS_OIDC_JWKS_URI", ""),
    )
    designer = DesignerService(
        build_repository(designer_settings, platform.store), designer_settings
    )
    app.state.designer = designer

    # One authenticator per app, holding the JWKS cache so keys are fetched
    # once rather than per request. Tests and air-gapped installs replace its
    # `verifier` with one over a local key set.
    designer_auth = Authenticator(
        designer_settings, verifier=verifier_from_settings(designer_settings),
        audit=designer.audit,
    )
    app.state.designer_auth = designer_auth

    def principal(
        authorization: str = Header(default=""),
        x_user: str = Header(default="anonymous"),
        x_user_name: str = Header(default=""),
        x_user_email: str = Header(default=""),
    ) -> Principal:
        """Identity per the configured `auth_mode` (ADR-0047).

        In `oidc` mode this is a verified bearer token and the X-User header is
        ignored entirely; in `trusted_proxy` mode it is the header, which is
        only as good as the proxy. Either way the service never trusts a
        client-sent role. A failure here is a 401 — `_guard` owns the 403.
        """
        try:
            return app.state.designer_auth.authenticate(
                authorization=authorization, user_header=x_user,
                name_header=x_user_name, email_header=x_user_email,
            )
        except AuthError as e:
            raise HTTPException(401, str(e)) from e

    def _guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except PermissionDenied as e:
            raise HTTPException(403, str(e)) from e
        except LockConflict as e:
            raise HTTPException(
                409, {"error": str(e), "lock": e.lock.model_dump(mode="json")}
            ) from e
        except DesignerError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/api/designer/whoami")
    def designer_whoami(user: Principal = Depends(principal)) -> dict:
        return designer.whoami(user)

    @app.get("/api/designer/settings")
    def designer_get_settings() -> dict:
        return designer.settings.model_dump(mode="json")

    @app.put("/api/designer/settings")
    def designer_update_settings(changes: dict[str, Any],
                                 user: Principal = Depends(principal)) -> dict:
        return _guard(designer.update_settings, user, changes).model_dump(mode="json")

    @app.get("/api/designer/workspaces")
    def designer_workspaces(user: Principal = Depends(principal)) -> list[dict]:
        return [w.model_dump(mode="json") for w in designer.workspaces(user)]

    @app.post("/api/designer/workspaces")
    def designer_create_workspace(req: WorkspaceRequest,
                                  user: Principal = Depends(principal)) -> dict:
        return designer.create_workspace(user, req.name,
                                         req.description).model_dump(mode="json")

    @app.post("/api/designer/workspaces/{workspace_id}/members")
    def designer_add_member(workspace_id: str, req: MemberRequest,
                            user: Principal = Depends(principal)) -> dict:
        member = Member(user_id=req.user_id, display_name=req.display_name,
                        email=req.email, role=UserRole(req.role))
        return _guard(designer.add_member, user, workspace_id,
                      member).model_dump(mode="json")

    @app.delete("/api/designer/workspaces/{workspace_id}/members/{user_id}")
    def designer_remove_member(workspace_id: str, user_id: str,
                               user: Principal = Depends(principal)) -> dict:
        return _guard(designer.remove_member, user, workspace_id,
                      user_id).model_dump(mode="json")

    @app.get("/api/designer/systems")
    def designer_systems(workspace_id: Optional[str] = None,
                         user: Principal = Depends(principal)) -> list[dict]:
        return designer.list_systems(user, workspace_id)

    @app.post("/api/designer/systems")
    def designer_create_system(req: CreateSystemRequest,
                               user: Principal = Depends(principal)) -> dict:
        return _guard(designer.create_system, user, workspace_id=req.workspace_id,
                      name=req.name, description=req.description,
                      spec=req.spec).model_dump(mode="json")

    @app.get("/api/designer/systems/{system_id}")
    def designer_open_system(system_id: str,
                             user: Principal = Depends(principal)) -> dict:
        return _guard(designer.open_system, user, system_id)

    @app.put("/api/designer/systems/{system_id}")
    def designer_save_system(system_id: str, req: SaveSystemRequest,
                             user: Principal = Depends(principal)) -> dict:
        outcome = _guard(
            designer.save_system, user, system_id, spec=req.spec,
            layout=Layout.model_validate(req.layout) if req.layout else None,
            name=req.name, description=req.description,
            status=SystemStatus(req.status) if req.status else None,
            base_version=req.base_version, strategy=req.strategy,
            resolutions=req.resolutions, message=req.message,
        )
        return outcome.as_dict()

    @app.delete("/api/designer/systems/{system_id}")
    def designer_delete_system(system_id: str,
                               user: Principal = Depends(principal)) -> dict:
        return {"deleted": _guard(designer.delete_system, user, system_id)}

    @app.post("/api/designer/systems/{system_id}/lock")
    def designer_acquire_lock(system_id: str, req: LockRequest,
                              user: Principal = Depends(principal)) -> dict:
        return _guard(designer.acquire_lock, user, system_id, target=req.target,
                      scope=req.scope, note=req.note).model_dump(mode="json")

    @app.post("/api/designer/systems/{system_id}/lock/heartbeat")
    def designer_heartbeat(system_id: str, req: LockRequest,
                           user: Principal = Depends(principal)) -> dict:
        lock = designer.heartbeat(user, system_id, req.target)
        return lock.model_dump(mode="json") if lock else {"held": False}

    @app.delete("/api/designer/systems/{system_id}/lock")
    def designer_release_lock(system_id: str, target: str = "*",
                              user: Principal = Depends(principal)) -> dict:
        return {"released": designer.release_lock(user, system_id, target)}

    @app.post("/api/designer/systems/{system_id}/lock/break")
    def designer_break_lock(system_id: str, req: LockRequest,
                            user: Principal = Depends(principal)) -> dict:
        return {"broken": _guard(designer.break_lock, user, system_id, req.target)}

    @app.get("/api/designer/systems/{system_id}/revisions")
    def designer_revisions(system_id: str, limit: int = 50,
                           user: Principal = Depends(principal)) -> list[dict]:
        return [r.model_dump(mode="json")
                for r in _guard(designer.revisions, user, system_id, limit)]

    @app.post("/api/designer/systems/{system_id}/restore/{version}")
    def designer_restore(system_id: str, version: int,
                         user: Principal = Depends(principal)) -> dict:
        return _guard(designer.restore, user, system_id,
                      version).model_dump(mode="json")

    @app.get("/api/designer/audit")
    def designer_audit(system_id: Optional[str] = None, actor: Optional[str] = None,
                       action: Optional[str] = None, since: Optional[str] = None,
                       until: Optional[str] = None, limit: int = 200,
                       user: Principal = Depends(principal)) -> list[dict]:
        """The designer audit log (ADR-0043). Admins and owners only; there is
        no write side, because the log is append-only."""
        return [
            e.model_dump(mode="json")
            for e in _guard(designer.audit_events, user, system_id=system_id,
                            actor=actor, action=action, since=since, until=until,
                            limit=limit)
        ]

    @app.get("/api/designer/palette")
    def designer_palette() -> dict:
        """What the canvas can place, and the fields each kind needs."""
        return PALETTE

    # -- fabric: the command centre's namespace (ADR-0049, ADR-0051) -------
    #
    # Reads here may cross tenants; nothing here may touch a spec. There is no
    # write route into the designer's models from this namespace by
    # construction, which is the only form of that guarantee worth having.

    from dataclasses import dataclass as _dataclass

    from .designer.audit import AuditOutcome
    from .fabric import rbac as fabric_rbac
    from .fabric.audit import FabricAuditAction, FabricAuditLog, scope_detail
    from .fabric.deployments import (
        TRANSITIONS,
        Deployment,
        DeploymentService,
        DeploymentState,
        IllegalTransition,
        OperatorRole,
        TransitionDenied,
        allowed_transitions,
    )
    from .fabric.health import HealthService, StubBackend
    from .fabric.quotas import Entitlements, Quota, QuotaKind, QuotaService
    from .fabric.rbac import FabricPermissionDenied, OperatorRegistry
    from .fabric.services import ServiceRegistry
    from .fabric.tenants import TenantRegistry

    fabric_tenants = TenantRegistry(platform.store)
    fabric_deployments = DeploymentService(platform.store)
    fabric_quotas = QuotaService(platform.store)
    fabric_services = ServiceRegistry(platform.store)
    # No target adapter exists in this environment (WS-030 M5): the backend is
    # injected so an installation — or a test — supplies a real one.
    fabric_health_backend = StubBackend()
    fabric_health = HealthService(platform.store, fabric_deployments,
                                  fabric_health_backend)
    fabric_audit = FabricAuditLog(platform.store)
    fabric_operators = OperatorRegistry(
        platform.store,
        bootstrap=fabric_rbac.parse_bootstrap(
            os.environ.get("ORGAGENTS_FABRIC_OPERATORS", "")
        ),
    )
    app.state.fabric = {
        "tenants": fabric_tenants,
        "deployments": fabric_deployments,
        "quotas": fabric_quotas,
        "services": fabric_services,
        "health": fabric_health,
        "health_backend": fabric_health_backend,
        "audit": fabric_audit,
        "operators": fabric_operators,
    }

    @_dataclass(frozen=True)
    class OperatorContext:
        """The requester, with the operator roles the fabric granted them.

        The designer roles on the principal are not consulted and are not
        carried here: a designer grant must never become a fabric grant
        (ADR-0051).
        """

        principal: Principal
        roles: list[OperatorRole]

    def operator(user: Principal = Depends(principal)) -> OperatorContext:
        return OperatorContext(user, fabric_operators.roles_for(user.user_id))

    def _fabric_record(ctx: OperatorContext, action: FabricAuditAction, *,
                       route: str, outcome: AuditOutcome, permission: str = "",
                       tenant_id: str = "", deployment_id: str = "",
                       cross_tenant: bool = False, reason: str = "",
                       detail: Optional[dict[str, Any]] = None) -> None:
        fabric_audit.record(
            action, actor=ctx.principal.user_id,
            actor_name=getattr(ctx.principal, "label", ""),
            outcome=outcome, route=route, permission=permission,
            tenant_id=tenant_id, deployment_id=deployment_id,
            cross_tenant=cross_tenant, reason=reason,
            operator_roles=[r.value for r in ctx.roles], detail=detail,
        )

    def _fabric_require(ctx: OperatorContext, permission: str,
                        action: FabricAuditAction, route: str, *,
                        tenant_id: str = "", deployment_id: str = "") -> None:
        """Authorize, and record the refusal — a refused read is a fact too."""
        try:
            fabric_rbac.require(ctx.roles, permission)
        except FabricPermissionDenied as e:
            _fabric_record(ctx, action, route=route, outcome=AuditOutcome.DENIED,
                           permission=permission, tenant_id=tenant_id,
                           deployment_id=deployment_id, reason=str(e))
            raise HTTPException(403, str(e)) from e

    def _audited_read(ctx: OperatorContext, permission: str,
                      action: FabricAuditAction, route: str, payload_fn,
                      *, tenant_id: str = "") -> Any:
        """One audit row per request, carrying the scope it covered.

        Not one row per tenant returned: see `fabric/audit.py` on volume.
        """
        _fabric_require(ctx, permission, action, route, tenant_id=tenant_id)
        payload, tenant_ids = payload_fn()
        seen = sorted(set(tenant_ids))
        _fabric_record(
            ctx, action, route=route, outcome=AuditOutcome.SUCCESS,
            permission=permission, tenant_id=tenant_id,
            cross_tenant=len(seen) > 1, detail=scope_detail(seen),
        )
        return payload

    def _require_tenant(tenant_id: str):
        tenant = fabric_tenants.get(tenant_id)
        if tenant is None:
            raise HTTPException(404, f"no such tenant: {tenant_id}")
        return tenant

    def _require_deployment(deployment_id: str) -> Deployment:
        deployment = fabric_deployments.get(deployment_id)
        if deployment is None:
            raise HTTPException(404, f"no such deployment: {deployment_id}")
        return deployment

    def _deployment_view(deployment: Deployment) -> dict:
        return {
            **deployment.model_dump(mode="json"),
            "allowed_transitions": {
                state.value: sorted(r.value for r in roles)
                for state, roles in allowed_transitions(deployment.state).items()
            },
        }

    def _quota_view(tenant_id: str) -> dict:
        entitlements = fabric_quotas.entitlements(tenant_id)
        tenant = fabric_tenants.get(tenant_id)
        if entitlements is None:
            return {
                "tenant_id": tenant_id,
                "quotas": {}, "usage": {}, "breaches": [],
                "catalog_entries": [],
                "tenant_entitlements": list(tenant.entitlements) if tenant else [],
                "recorded": False,
            }
        return {
            **entitlements.model_dump(mode="json"),
            "tenant_entitlements": list(tenant.entitlements) if tenant else [],
            "recorded": True,
        }

    @app.get("/api/fabric/whoami")
    def fabric_whoami(ctx: OperatorContext = Depends(operator)) -> dict:
        """Who the caller is *to the fabric*. Never reports designer roles."""
        return {
            "user_id": ctx.principal.user_id,
            "display_name": getattr(ctx.principal, "label", ""),
            "operator_roles": [r.value for r in ctx.roles],
            "permissions": sorted(fabric_rbac.permissions_for(ctx.roles)),
            "is_operator": bool(ctx.roles),
        }

    @app.get("/api/fabric/tenants")
    def fabric_list_tenants(ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            tenants = fabric_tenants.list()
            rows = []
            for tenant in tenants:
                deployments = fabric_deployments.list(tenant.id)
                rows.append({
                    **tenant.model_dump(mode="json"),
                    "deployment_count": len(deployments),
                    "states": sorted({d.state.value for d in deployments}),
                })
            return rows, [t.id for t in tenants]

        return _audited_read(ctx, fabric_rbac.TENANT_READ,
                             FabricAuditAction.TENANT_READ,
                             "GET /api/fabric/tenants", payload)

    @app.get("/api/fabric/tenants/{tenant_id}")
    def fabric_tenant_detail(tenant_id: str,
                             ctx: OperatorContext = Depends(operator)) -> dict:
        def payload():
            tenant = _require_tenant(tenant_id)
            deployments = fabric_deployments.list(tenant_id)
            checks = [fabric_health.check(d.id) for d in deployments]
            return (
                {
                    "tenant": tenant.model_dump(mode="json"),
                    "deployments": [_deployment_view(d) for d in deployments],
                    "health": [c.model_dump(mode="json") for c in checks],
                    "drift": [s.model_dump(mode="json") for c in checks
                              for s in c.signals],
                    "quotas": _quota_view(tenant_id),
                },
                [tenant_id],
            )

        return _audited_read(ctx, fabric_rbac.TENANT_READ,
                             FabricAuditAction.TENANT_READ,
                             "GET /api/fabric/tenants/{tenant_id}", payload,
                             tenant_id=tenant_id)

    @app.get("/api/fabric/deployments")
    def fabric_list_deployments(tenant_id: Optional[str] = None,
                                ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            if tenant_id is not None:
                _require_tenant(tenant_id)
                deployments = fabric_deployments.list(tenant_id)
            else:
                deployments = [d for t in fabric_tenants.list()
                               for d in fabric_deployments.list(t.id)]
            return ([_deployment_view(d) for d in deployments],
                    [d.tenant_id for d in deployments])

        return _audited_read(ctx, fabric_rbac.DEPLOYMENT_READ,
                             FabricAuditAction.DEPLOYMENT_READ,
                             "GET /api/fabric/deployments", payload,
                             tenant_id=tenant_id or "")

    @app.get("/api/fabric/deployments/{deployment_id}")
    def fabric_deployment_detail(deployment_id: str,
                                 ctx: OperatorContext = Depends(operator)) -> dict:
        def payload():
            deployment = _require_deployment(deployment_id)
            check = fabric_health.last_check(deployment_id)
            return (
                {
                    **_deployment_view(deployment),
                    "health": check.model_dump(mode="json") if check else None,
                },
                [deployment.tenant_id],
            )

        return _audited_read(ctx, fabric_rbac.DEPLOYMENT_READ,
                             FabricAuditAction.DEPLOYMENT_READ,
                             "GET /api/fabric/deployments/{deployment_id}",
                             payload)

    @app.get("/api/fabric/deployments/{deployment_id}/history")
    def fabric_deployment_history(deployment_id: str,
                                  ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            deployment = _require_deployment(deployment_id)
            return ([e.model_dump(mode="json") for e in deployment.history],
                    [deployment.tenant_id])

        return _audited_read(ctx, fabric_rbac.DEPLOYMENT_READ,
                             FabricAuditAction.DEPLOYMENT_READ,
                             "GET /api/fabric/deployments/{deployment_id}/history",
                             payload)

    @app.get("/api/fabric/health")
    def fabric_health_route(tenant_id: Optional[str] = None,
                            ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            tenants = ([_require_tenant(tenant_id)] if tenant_id is not None
                       else fabric_tenants.list())
            checks = [c for t in tenants for c in fabric_health.check_tenant(t.id)]
            return ([c.model_dump(mode="json") for c in checks],
                    [t.id for t in tenants])

        return _audited_read(ctx, fabric_rbac.HEALTH_READ,
                             FabricAuditAction.HEALTH_READ,
                             "GET /api/fabric/health", payload,
                             tenant_id=tenant_id or "")

    @app.get("/api/fabric/drift")
    def fabric_drift(tenant_id: Optional[str] = None,
                     ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            tenants = ([_require_tenant(tenant_id)] if tenant_id is not None
                       else fabric_tenants.list())
            signals = [s for t in tenants
                       for c in fabric_health.check_tenant(t.id) for s in c.signals]
            return ([s.model_dump(mode="json") for s in signals],
                    [t.id for t in tenants])

        return _audited_read(ctx, fabric_rbac.HEALTH_READ,
                             FabricAuditAction.HEALTH_READ,
                             "GET /api/fabric/drift", payload,
                             tenant_id=tenant_id or "")

    @app.get("/api/fabric/quotas")
    def fabric_quota_state(tenant_id: Optional[str] = None,
                           ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            tenants = ([_require_tenant(tenant_id)] if tenant_id is not None
                       else fabric_tenants.list())
            return ([_quota_view(t.id) for t in tenants], [t.id for t in tenants])

        return _audited_read(ctx, fabric_rbac.QUOTA_READ,
                             FabricAuditAction.QUOTA_READ,
                             "GET /api/fabric/quotas", payload,
                             tenant_id=tenant_id or "")

    @app.get("/api/fabric/services")
    def fabric_common_services(ctx: OperatorContext = Depends(operator)) -> list[dict]:
        # The common-service registry is fabric-wide, not per tenant, so there
        # is no tenant scope to record and nothing here crosses a boundary.
        return _audited_read(
            ctx, fabric_rbac.SERVICE_READ, FabricAuditAction.SERVICE_READ,
            "GET /api/fabric/services",
            lambda: ([s.model_dump(mode="json") for s in fabric_services.list()], []),
        )

    @app.get("/api/fabric/services/boundary")
    def fabric_boundary_report(ctx: OperatorContext = Depends(operator)) -> list[dict]:
        return _audited_read(
            ctx, fabric_rbac.SERVICE_READ, FabricAuditAction.SERVICE_READ,
            "GET /api/fabric/services/boundary",
            lambda: (fabric_services.boundary_report(), []),
        )

    @app.get("/api/fabric/audit")
    def fabric_audit_read(tenant_id: Optional[str] = None,
                          actor: Optional[str] = None,
                          cross_tenant_only: bool = False,
                          limit: int = 200,
                          ctx: OperatorContext = Depends(operator)) -> list[dict]:
        """The operator log. Administrative, like the designer's (ADR-0043)."""
        _fabric_require(ctx, fabric_rbac.AUDIT_READ, FabricAuditAction.AUDIT_READ,
                        "GET /api/fabric/audit", tenant_id=tenant_id or "")
        events = fabric_audit.query(tenant_id=tenant_id, actor=actor,
                                    cross_tenant_only=cross_tenant_only,
                                    limit=limit)
        # Recorded after the query so the reader's own read is in the log, but
        # not in the answer it just received.
        _fabric_record(ctx, FabricAuditAction.AUDIT_READ,
                       route="GET /api/fabric/audit", outcome=AuditOutcome.SUCCESS,
                       permission=fabric_rbac.AUDIT_READ, tenant_id=tenant_id or "",
                       detail={"returned": len(events)})
        return [e.model_dump(mode="json") for e in events]

    #: Operator action -> the lifecycle target it asks for. The transition
    #: table in `fabric/deployments.py` decides whether it is legal and who
    #: may make it; nothing here re-implements that judgement.
    OPERATOR_ACTIONS: dict[str, tuple[str, DeploymentState, FabricAuditAction]] = {
        # Compiling a requested deployment is the step before deploying it;
        # exposed so an operator can drive the whole path, audited as a deploy.
        "generate": (fabric_rbac.DEPLOY, DeploymentState.GENERATED,
                     FabricAuditAction.DEPLOY),
        "deploy": (fabric_rbac.DEPLOY, DeploymentState.DEPLOYED,
                   FabricAuditAction.DEPLOY),
        "stop": (fabric_rbac.STOP, DeploymentState.STOPPED,
                 FabricAuditAction.STOP),
        "quarantine": (fabric_rbac.QUARANTINE, DeploymentState.QUARANTINED,
                       FabricAuditAction.QUARANTINE),
        # Re-deploy is stopped -> running. The table refuses it out of
        # quarantine, which is the rule we want and must not restate.
        "redeploy": (fabric_rbac.REDEPLOY, DeploymentState.RUNNING,
                     FabricAuditAction.REDEPLOY),
    }

    def _acting_role(roles: list[OperatorRole], source: DeploymentState,
                     target: DeploymentState) -> OperatorRole:
        """Which granted role to act as for this move.

        A person may hold several. Prefer one the table accepts for *this*
        transition; otherwise act as the strongest held role, so the refusal
        that follows names a role the person actually has.
        """
        allowed = TRANSITIONS.get((source, target)) or frozenset()
        for role in reversed(fabric_rbac.ROLE_ORDER):
            if role in roles and role in allowed:
                return role
        return max(roles, key=fabric_rbac.ROLE_ORDER.index)

    @app.post("/api/fabric/deployments/{deployment_id}/actions/{action}")
    def fabric_operator_action(deployment_id: str, action: str,
                               req: OperatorActionRequest,
                               ctx: OperatorContext = Depends(operator)) -> dict:
        if action not in OPERATOR_ACTIONS:
            raise HTTPException(
                404, f"unknown operator action '{action}'; "
                     f"expected one of {sorted(OPERATOR_ACTIONS)}"
            )
        permission, target_state, audit_action = OPERATOR_ACTIONS[action]
        route = f"POST /api/fabric/deployments/{{deployment_id}}/actions/{action}"
        deployment = _require_deployment(deployment_id)
        _fabric_require(ctx, permission, audit_action, route,
                        tenant_id=deployment.tenant_id,
                        deployment_id=deployment_id)
        role = _acting_role(ctx.roles, deployment.state, target_state)
        try:
            moved = fabric_deployments.transition(
                deployment_id, target_state, actor=ctx.principal.user_id,
                role=role, reason=req.reason,
            )
        except IllegalTransition as e:
            _fabric_record(ctx, audit_action, route=route,
                           outcome=AuditOutcome.CONFLICT, permission=permission,
                           tenant_id=deployment.tenant_id,
                           deployment_id=deployment_id, reason=str(e))
            raise HTTPException(409, str(e)) from e
        except TransitionDenied as e:
            _fabric_record(ctx, audit_action, route=route,
                           outcome=AuditOutcome.DENIED, permission=permission,
                           tenant_id=deployment.tenant_id,
                           deployment_id=deployment_id, reason=str(e))
            raise HTTPException(403, str(e)) from e
        _fabric_record(ctx, audit_action, route=route,
                       outcome=AuditOutcome.SUCCESS, permission=permission,
                       tenant_id=moved.tenant_id, deployment_id=deployment_id,
                       reason=req.reason,
                       detail={"from": deployment.state.value,
                               "to": moved.state.value,
                               "acted_as": role.value})
        return _deployment_view(moved)

    @app.put("/api/fabric/tenants/{tenant_id}/quotas")
    def fabric_requota(tenant_id: str, req: RequotaRequest,
                       ctx: OperatorContext = Depends(operator)) -> dict:
        """Re-quota: capacity, not authorization, and never a spec change."""
        route = "PUT /api/fabric/tenants/{tenant_id}/quotas"
        _fabric_require(ctx, fabric_rbac.REQUOTA, FabricAuditAction.REQUOTA,
                        route, tenant_id=tenant_id)
        _require_tenant(tenant_id)
        current = fabric_quotas.entitlements(tenant_id) or Entitlements(
            tenant_id=tenant_id
        )
        before = {k.value: q.model_dump(mode="json")
                  for k, q in current.quotas.items()}
        try:
            for kind, quota in req.quotas.items():
                parsed = QuotaKind(kind)
                current.quotas[parsed] = Quota(kind=parsed, **quota)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, f"bad quota: {e}") from e
        if req.catalog_entries is not None:
            current.catalog_entries = list(req.catalog_entries)
        saved = fabric_quotas.set_entitlements(current)
        _fabric_record(ctx, FabricAuditAction.REQUOTA, route=route,
                       outcome=AuditOutcome.SUCCESS,
                       permission=fabric_rbac.REQUOTA, tenant_id=tenant_id,
                       reason=req.reason,
                       detail={"before": before,
                               "after": {k.value: q.model_dump(mode="json")
                                         for k, q in saved.quotas.items()}})
        return _quota_view(tenant_id)

    @app.get("/api/fabric/operators")
    def fabric_list_operators(ctx: OperatorContext = Depends(operator)) -> list[dict]:
        _fabric_require(ctx, fabric_rbac.GRANT, FabricAuditAction.GRANT,
                        "GET /api/fabric/operators")
        return [g.model_dump(mode="json") for g in fabric_operators.list()]

    @app.post("/api/fabric/operators")
    def fabric_grant_operator(req: OperatorGrantRequest,
                              ctx: OperatorContext = Depends(operator)) -> dict:
        """Grant an operator role.

        Written here and nowhere else: a fabric grant is never derived from a
        designer role or a workspace membership (ADR-0051).
        """
        route = "POST /api/fabric/operators"
        _fabric_require(ctx, fabric_rbac.GRANT, FabricAuditAction.GRANT, route)
        try:
            roles = [OperatorRole(r) for r in req.roles]
        except ValueError as e:
            raise HTTPException(400, f"unknown operator role: {e}") from e
        grant = fabric_operators.grant(req.user_id, roles,
                                       granted_by=ctx.principal.user_id,
                                       reason=req.reason)
        _fabric_record(ctx, FabricAuditAction.GRANT, route=route,
                       outcome=AuditOutcome.SUCCESS, permission=fabric_rbac.GRANT,
                       reason=req.reason,
                       detail={"user_id": req.user_id,
                               "roles": [r.value for r in roles]})
        return grant.model_dump(mode="json")

    @app.delete("/api/fabric/operators/{user_id}")
    def fabric_revoke_operator(user_id: str,
                               ctx: OperatorContext = Depends(operator)) -> dict:
        route = "DELETE /api/fabric/operators/{user_id}"
        _fabric_require(ctx, fabric_rbac.GRANT, FabricAuditAction.REVOKE, route)
        revoked = fabric_operators.revoke(user_id)
        _fabric_record(ctx, FabricAuditAction.REVOKE, route=route,
                       outcome=AuditOutcome.SUCCESS, permission=fabric_rbac.GRANT,
                       detail={"user_id": user_id, "revoked": revoked})
        return {"revoked": revoked}

    # -- UI ----------------------------------------------------------------

    if WEB_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")

        # The command centre is its own application, not a tab in the designer
        # (ADR-0051): a separate bundle at a separate path, over this API.
        if (WEB_DIR / "command").is_dir():
            app.mount("/command",
                      StaticFiles(directory=str(WEB_DIR / "command"), html=True),
                      name="command")

        @app.get("/")
        def index() -> Any:
            return RedirectResponse("/ui/")

    return app


# What the canvas can place, and the form each component needs (ADR-0034).
# Derived from the spec model so the palette cannot drift from what validates.
PALETTE: dict[str, Any] = {
    "groups": [
        {
            "id": "organization",
            "label": "Organization",
            "kinds": [
                {"kind": "team", "label": "Team", "icon": "▣",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "leader", "type": "string",
                      "help": "agent id; must also be a member"},
                     {"name": "mandate", "type": "list"},
                     {"name": "groups", "type": "list"},
                 ]},
                {"kind": "agent", "label": "Agent", "icon": "◆",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "description", "type": "text"},
                     {"name": "roles", "type": "list"},
                     {"name": "capabilities", "type": "list"},
                     {"name": "knowledge", "type": "list"},
                     {"name": "skills", "type": "list"},
                     {"name": "plugins", "type": "list"},
                     {"name": "tools", "type": "list"},
                     {"name": "endpoints", "type": "list"},
                     {"name": "environment", "type": "string",
                      "help": "environment class id"},
                     {"name": "shared_service", "type": "bool"},
                     {"name": "humans", "type": "humans"},
                 ]},
                {"kind": "subagent", "label": "Sub-agent", "icon": "◇",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "parent", "type": "string", "required": True},
                     {"name": "kind", "type": "enum",
                      "options": ["research", "review", "summarize", "extract",
                                  "critique", "plan", "verify", "custom"]},
                     {"name": "purpose", "type": "text"},
                     {"name": "capabilities", "type": "list"},
                     {"name": "returns", "type": "string"},
                     {"name": "max_runtime_seconds", "type": "number"},
                 ]},
                {"kind": "role", "label": "Role", "icon": "✦",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "kind", "type": "enum", "options": ["agent", "team"]},
                     {"name": "responsibilities", "type": "list"},
                     {"name": "capabilities", "type": "list"},
                 ]},
            ],
        },
        {
            "id": "access",
            "label": "Access",
            "kinds": [
                {"kind": "capability", "label": "Capability", "icon": "⚷",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "action", "type": "enum",
                      "options": ["read", "write", "query", "invoke", "delegate",
                                  "publish", "approve", "administer"]},
                     {"name": "resource_class", "type": "string"},
                     {"name": "data_classes", "type": "list"},
                     {"name": "secret_ref", "type": "string"},
                 ]},
                {"kind": "data_class", "label": "Data class", "icon": "▤",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "scope", "type": "enum",
                      "options": ["private", "protected", "public"]},
                     {"name": "groups", "type": "list"},
                     {"name": "may_leave_region", "type": "bool"},
                     {"name": "may_appear_in_traces", "type": "bool"},
                 ]},
                {"kind": "environment", "label": "Environment", "icon": "▦",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "tier", "type": "enum",
                      "options": ["minimal", "small", "medium", "large",
                                  "accelerated"]},
                     {"name": "network", "type": "enum",
                      "options": ["none", "allowlist", "internal", "open"]},
                     {"name": "mounts", "type": "list"},
                     {"name": "timeout_seconds", "type": "number"},
                 ]},
                {"kind": "endpoint", "label": "External agent", "icon": "⇥",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "trust", "type": "enum",
                      "options": ["internal", "partner", "external"]},
                     {"name": "send_data_classes", "type": "list"},
                     {"name": "requires_approval", "type": "bool"},
                 ]},
            ],
        },
        {
            "id": "operations",
            "label": "Operations",
            "kinds": [
                {"kind": "channel", "label": "Channel", "icon": "✉",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "channel_class", "type": "enum",
                      "options": ["direct", "async_bus", "team_chat", "mail",
                                  "webhook"]},
                     {"name": "human_facing", "type": "bool"},
                     {"name": "purposes", "type": "list"},
                     {"name": "response_sla_minutes", "type": "number"},
                 ]},
                {"kind": "trigger", "label": "Trigger", "icon": "⏱",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "kind", "type": "enum",
                      "options": ["schedule", "event", "webhook", "message",
                                  "manual"]},
                     {"name": "agent", "type": "string", "required": True},
                     {"name": "cadence", "type": "string",
                      "help": "cron or 'every 15 minutes'"},
                     {"name": "deliver_to", "type": "list"},
                 ]},
                {"kind": "knowledge", "label": "Knowledge", "icon": "▥",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "kind", "type": "enum",
                      "options": ["document_store", "wiki", "ticketing", "crm",
                                  "mailbox", "code_repository", "data_warehouse",
                                  "web"]},
                     {"name": "data_classes", "type": "list"},
                 ]},
                {"kind": "memory_namespace", "label": "Memory namespace",
                 "icon": "◈",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "scope", "type": "enum",
                      "options": ["private", "protected", "public"]},
                     {"name": "groups", "type": "list"},
                     {"name": "data_classes", "type": "list"},
                     {"name": "retention_days", "type": "number"},
                 ]},
                {"kind": "workflow", "label": "Workflow", "icon": "⤳",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "description", "type": "text"},
                 ]},
                {"kind": "note", "label": "Note", "icon": "✎",
                 "fields": [{"name": "note", "type": "text"}]},
            ],
        },
    ],
}


# Infrastructure options the designer offers for each concern. These are
# declarative choices rendered as dropdowns; the deployment layer maps the
# selection onto the actual cluster resources.
INFRASTRUCTURE_CATALOG: dict[str, list[dict[str, str]]] = {
    "storage": [
        {"id": "postgres", "name": "PostgreSQL", "use": "Org, agents, sessions, catalog"},
        {"id": "s3", "name": "Object storage", "use": "Session artifacts, sandbox outputs"},
        {"id": "pgvector", "name": "Vector index", "use": "Public/protected knowledge search"},
        {"id": "redis", "name": "Redis", "use": "Session cache, rate limits, locks"},
    ],
    "compute": [
        {"id": "k8s_jobs", "name": "Kubernetes jobs", "use": "Sandbox execution"},
        {"id": "firecracker", "name": "microVM pool", "use": "Untrusted code isolation"},
        {"id": "gpu_pool", "name": "GPU pool", "use": "ml-training sandbox template"},
        {"id": "serverless", "name": "Serverless functions", "use": "Short tool calls"},
    ],
    "communication": [
        {"id": "nats", "name": "NATS / Kafka", "use": "Internal agent message bus"},
        {"id": "slack", "name": "Slack", "use": "Human-facing channels"},
        {"id": "teams", "name": "Microsoft Teams", "use": "Human-facing channels"},
        {"id": "smtp", "name": "SMTP relay", "use": "Email escalation"},
    ],
    "operations": [
        {"id": "otel", "name": "OpenTelemetry", "use": "Session traces and spans"},
        {"id": "loki", "name": "Log aggregation", "use": "Structured agent logs"},
        {"id": "prometheus", "name": "Prometheus", "use": "Metrics and SLOs"},
        {"id": "pagerduty", "name": "PagerDuty", "use": "Alert routing to humans"},
    ],
    "security": [
        {"id": "vault", "name": "Secret manager", "use": "DSNs, API tokens, MCP secrets"},
        {"id": "oidc", "name": "OIDC / SSO", "use": "Human identity on sessions"},
        {"id": "egress_proxy", "name": "Egress proxy", "use": "Sandbox network allowlists"},
        {"id": "audit_log", "name": "Immutable audit log", "use": "Tool calls and approvals"},
    ],
}


app = None  # populated by `uvicorn orgagents.api:get_app` style factories


def get_app() -> FastAPI:  # pragma: no cover - uvicorn entrypoint
    global app
    if app is None:
        app = create_app()
    return app
