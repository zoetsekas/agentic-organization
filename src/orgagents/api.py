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

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
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
# Imported at module level, not inside `create_app`: this module uses
# `from __future__ import annotations`, so FastAPI resolves a route's
# annotations as strings against the *module* globals. A model imported inside
# the factory is invisible there, and FastAPI silently degrades the parameter
# to a query field — which is how POST /api/catalogs came to be uncallable.
from .catalogs import CatalogEntry as PlatformCatalogEntry
from .catalogs import Entitlement as PlatformEntitlement
from .platform import Platform
from .store import PLUGINS, SKILLS, WORKFLOWS

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class CatalogEditRequest(BaseModel):
    """An editorial edit (ADR-0062 rule 1). Omitted fields are left alone."""

    name: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    tags: Optional[list[str]] = None
    documentation_url: Optional[str] = None
    note: str = ""


class CatalogAmendRequest(BaseModel):
    """A substantive edit (ADR-0062 rule 2), refused on an approved entry."""

    kind: Optional[str] = None
    version: Optional[str] = None
    attributes: Optional[dict[str, Any]] = None
    note: str = ""


class CatalogSendBackRequest(BaseModel):
    note: str = ""


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


class TenantRegistrationRequest(BaseModel):
    """What an operator supplies to mint a tenant.

    The isolation domain is absent on purpose: it is derived from the prefix
    by the fabric (ADR-0050), never asked for.
    """

    id: str
    name: str = ""
    namespace_prefix: Optional[str] = None
    entitlements: list[str] = []
    cloud_boundary: str = ""
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
        CatalogKind,
        CatalogService,
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
             "count": len(catalog_service.list(k)),
             # The declared attributes of the kind, so the designer renders one
             # generated form for all twelve rather than twelve written ones.
             "attributes": catalog_service.attribute_schema(k)}
            for k in CatalogKind
        ]

    @app.get("/api/catalogs/{entry_id}")
    def catalogs_detail(entry_id: str) -> dict:
        try:
            return catalog_service.describe(entry_id)
        except Exception as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/api/catalogs")
    def catalogs_publish(entry: PlatformCatalogEntry,
                         x_user: str = Header(default="anonymous")) -> dict:
        return catalog_service.publish(entry, actor=x_user).model_dump(mode="json")

    @app.patch("/api/catalogs/{entry_id}")
    def catalogs_update(entry_id: str, req: CatalogEditRequest,
                        x_user: str = Header(default="anonymous")) -> dict:
        try:
            return catalog_service.update(
                entry_id, req.model_dump(exclude={"note"}), actor=x_user,
                note=req.note).model_dump(mode="json")
        except Exception as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/catalogs/{entry_id}/amend")
    def catalogs_amend(entry_id: str, req: CatalogAmendRequest,
                       x_user: str = Header(default="anonymous")) -> dict:
        try:
            return catalog_service.amend(
                entry_id, req.model_dump(exclude={"note"}), actor=x_user,
                note=req.note).model_dump(mode="json")
        except Exception as e:
            # The service's own message names both ways forward; rewriting it
            # here would lose them.
            raise HTTPException(400, str(e)) from e

    @app.post("/api/catalogs/{entry_id}/send_back")
    def catalogs_send_back(entry_id: str, req: CatalogSendBackRequest,
                           x_user: str = Header(default="anonymous")) -> dict:
        try:
            return catalog_service.send_back(
                entry_id, actor=x_user, note=req.note).model_dump(mode="json")
        except Exception as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/catalogs/{entry_id}/retire")
    def catalogs_retire(entry_id: str, superseded_by: str = "",
                        force: bool = False,
                        x_user: str = Header(default="anonymous")) -> dict:
        try:
            return catalog_service.retire(
                entry_id, reviewer=x_user, superseded_by=superseded_by or None,
                force=force).model_dump(mode="json")
        except Exception as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/catalogs/{entry_id}")
    def catalogs_delete(entry_id: str,
                        x_user: str = Header(default="anonymous")) -> dict:
        try:
            return {"deleted": catalog_service.delete(entry_id, actor=x_user)}
        except Exception as e:
            raise HTTPException(400, str(e)) from e

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
    def catalogs_entitle(entry_id: str, entitlement: PlatformEntitlement,
                         x_user: str = Header(default="anonymous")) -> dict:
        try:
            return catalog_service.entitle(
                entry_id, entitlement, actor=x_user).model_dump(mode="json")
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
    from .designer.audit import AuditOutcome as DesignerAuditOutcome
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
        except ValueError as e:
            # A value the model refuses is the caller's mistake, not a missing
            # thing: 422 with the reason, never a 500 from a store.
            raise HTTPException(422, str(e)) from e
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

    # -- the shipped examples, loadable (UI and CLI share one module) -------

    @app.get("/api/designer/examples")
    def designer_examples() -> list[dict]:
        """Every shipped example organisation, for the Load example picker."""
        from .designer.examples import list_examples

        return [e.summary() for e in list_examples()]

    @app.post("/api/designer/examples/load")
    def designer_load_example(body: dict = Body(...),
                              user: Principal = Depends(principal)) -> dict:
        """Create a design from an example, laid out, in the given workspace.

        Through the designer service, so a reader who may not create in that
        workspace is refused here exactly as they would be by New…
        """
        from .designer.examples import UnknownExample, load_example

        example_id = str(body.get("example") or "")
        try:
            return _guard(load_example, designer, user, example_id,
                          workspace_id=str(body.get("workspace_id") or ""),
                          name=str(body.get("name") or ""))
        except UnknownExample as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc

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

    @app.post("/api/designer/operations")
    def designer_operation(req: dict[str, Any],
                           user: Principal = Depends(principal)) -> dict:
        """One gesture, as one model operation on the draft the canvas holds
        (ADR-0103). Stateless: nothing is stored — the canvas keeps its own
        draft, undo and save. 200 with `accepted: false` and the violations
        when the model refuses; 422 for a request naming no such operation,
        element or relationship, or a draft the model cannot read."""
        from .designer.gestures import evaluate
        return _guard(evaluate, req.get("spec") or {}, req.get("request") or {})

    @app.get("/api/designer/gestures")
    def designer_gestures() -> dict:
        """The designer's specification: every gesture and its operation."""
        from .designer.gestures import gestures
        return {"gestures": [g.__dict__ for g in gestures()]}

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

    # -- designer: review surfaces (WS-009) --------------------------------
    #
    # Both routes are read-only. The gate answers "what does the evidence say
    # today" and must never run evaluations on the way: a route that quietly
    # produced the evidence it then reported on would defeat the gate. The diff
    # only reads revisions that are already stored.

    from .compiler import build_ir as _build_ir
    from .compiler.diff import IncomparableIRError as _IncomparableIRError
    from .compiler.diff import diff_ir as _diff_ir
    from .evaluations import EvaluationService as _EvaluationService
    from .spec.binding import TargetBinding as _TargetBinding
    from .spec.loader import load_spec_text as _load_spec_text

    designer_evaluations = _EvaluationService(platform.store)

    def _designer_spec(raw: dict[str, Any]):
        """Parse a stored spec, or refuse in the UI's own terms.

        A design mid-edit is routinely incomplete, so "this does not compile
        yet" is the normal answer here, not a failure: 422 with the loader's
        own message, which the UI shows in place of the review panel.
        """
        import yaml

        try:
            return _load_spec_text(yaml.safe_dump(raw))
        except Exception as e:
            raise HTTPException(
                422, f"this design does not compile yet: {type(e).__name__}: {e}"
            ) from e

    def _designer_ir(raw: dict[str, Any], binding: Optional[dict[str, Any]]):
        spec = _designer_spec(raw)
        # The binding a revision was saved with decides its target, so a
        # re-target shows up as the incomparability it is rather than silently
        # diffing two different compilations.
        bound = None
        if binding and binding.get("target"):
            try:
                bound = _TargetBinding.model_validate(binding)
            except Exception as e:
                raise HTTPException(422, f"stored binding is unusable: {e}") from e
        try:
            return _build_ir(spec, target=bound.target if bound else "local",
                             binding=bound)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                422, f"this design does not compile yet: {type(e).__name__}: {e}"
            ) from e

    @app.get("/api/designer/systems/{system_id}/gate")
    def designer_gate(system_id: str, stage: str = "production",
                      user: Principal = Depends(principal)) -> dict:
        """The evaluation gate for every agent in a design. Reads only."""
        from .spec.model import LifecycleStage

        raw, _binding, _version = _guard(designer.spec_at, user, system_id)
        spec = _designer_spec(raw)
        try:
            to_stage = LifecycleStage(stage)
        except ValueError as e:
            raise HTTPException(422, f"unknown lifecycle stage '{stage}'") from e
        verdicts = designer_evaluations.gate_states(spec, to_stage=to_stage)
        return {
            "agents": {
                agent_id: {
                    "state": v.state.value,
                    "reason": v.reason,
                    "required": v.required,
                }
                for agent_id, v in verdicts.items()
            }
        }

    @app.get("/api/designer/systems/{system_id}/authority")
    def designer_authority(system_id: str,
                           user: Principal = Depends(principal)) -> dict:
        """What each agent may decide, and how much it does alone. Reads only.

        Effective authority, never what a unit declared: a mandate is the
        intersection with every unit above it (ADR-0065), and showing the
        declaration would let a reader believe an agent holds something its
        line excludes. `line` names the units that produced it, so a refusal
        can be explained to somebody who did not write the spec.
        """
        from .mandates import resolve as resolve_mandates
        from .spec.model import AutonomyPosture
        from .spec.validate import validate_spec

        raw, _binding, _version = _guard(designer.spec_at, user, system_id)
        spec = _designer_spec(raw)
        vocabulary = [d.id for d in spec.decisions]
        resolved = resolve_mandates(spec.organization, vocabulary, spec.people)

        caps = {c.id: c for c in spec.capabilities}
        roles = {r.id: r for r in spec.roles}

        def postures(agent, team_roles: list) -> list[dict]:
            held: set[str] = set(agent.capabilities)
            for assignment in list(agent.roles) + list(team_roles):
                role = roles.get(getattr(assignment, "role", assignment))
                if role:
                    held.update(role.capabilities)
            out = []
            for cap_id in sorted(c for c in held if c in caps):
                cap = caps[cap_id]
                declared = agent.autonomy.get(cap_id)
                posture: AutonomyPosture = declared or cap.autonomy
                out.append({
                    "capability": cap_id,
                    "posture": posture.value,
                    "tightened": bool(declared and declared != cap.autonomy),
                    "decision": cap.decision,
                    "requires_approval": bool(cap.constraints.requires_approval),
                    "enforced_by": cap.constraints.enforcement.enforced_by.value,
                    "enforced_in": cap.constraints.enforcement.enforced_in,
                })
            return out

        agents: dict[str, dict] = {}

        def walk(team, inherited: list) -> None:
            team_roles = inherited + [r.role for r in team.roles]
            for agent in team.members:
                effective = resolved.for_agent(agent.id)
                agents[agent.id] = {
                    "name": agent.name or agent.id,
                    "team": team.id,
                    "decisions": sorted(effective.decisions),
                    "conditions": [dict(c) for c in effective.conditions],
                    # Root first: the units whose declarations produced this.
                    "line": list(effective.line),
                    "declared": sorted(agent.mandate.decisions)
                    if agent.mandate else None,
                    "activities": postures(agent, team_roles),
                }
            for child in team.teams:
                walk(child, team_roles)

        walk(spec.organization, [])

        # Findings the authority model produces, so the UI can show a refusal
        # where the thing it refuses is being edited rather than in a log.
        codes = {
            "separation_violated", "mandate_overreach", "undeclared_decision",
            "root_leader_without_mandate", "advisory_mutates",
            "person_holds_access", "duplicate_person",
            "owner_approves_own_agent",
            "autonomous_without_decision", "autonomous_without_mandate",
            "supervised_without_approval", "human_decides_but_agent_holds",
            "autonomy_widened", "unenforceable_platform_control",
            "both_without_authority", "platform_bound_wider_than_application",
            "autonomy_without_evidence", "supervised_by_its_own_owner",
            "human_decides_with_no_holder", "application_control_unnamed",
        }
        findings = [
            {"severity": f.severity, "code": f.code, "where": f.where,
             "message": f.message}
            for f in validate_spec(spec) if f.code in codes
        ]

        return {
            "vocabulary": [
                {"id": d.id, "title": d.title or d.id} for d in spec.decisions
            ],
            "separations": [
                {"id": r.id, "decisions": list(r.decisions), "reason": r.reason,
                 "enforced_by": r.enforcement.enforced_by.value,
                 "enforced_in": r.enforcement.enforced_in}
                for r in spec.separations
            ],
            "teams": {
                tid: {"decisions": sorted(eff.decisions), "line": list(eff.line)}
                for tid, eff in resolved.teams.items()
            },
            "agents": agents,
            # People are principals for authority (ADR-0079), so a reader can
            # see where an escalation lands. No capabilities or permissions
            # appear here because a person holds none — their access is their
            # employer's to mediate, not ours.
            "people": {
                person.id: {
                    "name": person.name or person.id,
                    "position": person.position,
                    "unit": resolved.person_unit.get(person.id, ""),
                    "decisions": sorted(resolved.for_person(person.id).decisions),
                    "line": list(resolved.for_person(person.id).line),
                    "declared": sorted(person.mandate.decisions)
                    if person.mandate else None,
                    "pairings": sorted(
                        {
                            f"{a.id}:{r.value}"
                            for a in spec.agents()
                            for h in a.humans
                            if h.principal() == person.id
                            for r in h.roles
                        }
                    ),
                }
                for person in spec.people
            },
            "findings": findings,
        }

    @app.get("/api/designer/systems/{system_id}/placements")
    def designer_placements(system_id: str,
                            user: Principal = Depends(principal)) -> dict:
        """Where each agent's work lives, and what may cross. Reads only.

        A placement is an org unit crossed with an environment class
        (ADR-0069) — the namespace model an enterprise already has. What is
        drawn from this is a *region*, so it must be the thing that compiles:
        `agents` is who shares a volume and a process namespace, and `rules` is
        the whole of what crosses. Everything absent is denied.

        A placement is **not** a security boundary. The tenant is (ADR-0050).
        The response says so rather than leaving a reader to infer it from a
        picture of boxes.
        """
        from .placements import resolve as resolve_placements
        from .spec.validate import validate_spec

        raw, _binding, _version = _guard(designer.spec_at, user, system_id)
        spec = _designer_spec(raw)
        resolved = resolve_placements(spec)

        codes = {
            "single_placement", "placement_denies_delegation",
            "separated_agents_co_resident", "placement_violation",
            "egress_on_isolated_env", "environment_widened",
        }
        environments = {e.id: e for e in spec.environments}

        return {
            "is_a_security_boundary": False,
            "note": (
                "A placement is a naming and policy scope. The tenant is the "
                "absolute boundary; namespaces share a kernel."
            ),
            "declared": list(resolved.declared),
            "placements": [
                {
                    "id": placement.id,
                    "unit": placement.unit,
                    "environment": placement.environment,
                    "network": (
                        environments[placement.environment].network.value
                        if placement.environment in environments else "none"
                    ),
                    "egress_allowlist": (
                        list(environments[placement.environment].egress_allowlist)
                        if placement.environment in environments else []
                    ),
                    "agents": list(placement.agents),
                    "groups": list(placement.groups),
                    # What the shared volume carries. Never a grant.
                    "data_classes": list(placement.data_classes),
                    "shares_a_volume": placement.shares_a_volume,
                    "reaches": sorted(
                        r.target for r in resolved.rules
                        if r.source == placement.id
                    ),
                }
                for placement in sorted(
                    resolved.placements.values(), key=lambda p: p.id
                )
            ],
            "rules": [
                {"source": r.source, "target": r.target, "via": r.via,
                 "reason": r.reason}
                for r in resolved.rules
            ],
            # Agents with no environment class have no sandbox environment, so
            # they sit in no region. Saying which is better than a picture that
            # quietly omits them.
            "unplaced": sorted(
                a.id for a in spec.agents() if a.id not in resolved.home
            ),
            "findings": [
                {"severity": f.severity, "code": f.code, "where": f.where,
                 "message": f.message}
                for f in validate_spec(spec) if f.code in codes
            ],
        }

    def _active_platform_policy():
        """The fabric's house rules, if this installation has any (ADR-0076).

        Read from the environment because the policy belongs to the fabric and
        not to a design: a designer who could choose the policy their design is
        judged against is not being judged. `None` means no policy is
        configured, which is a real state and not an error — but a preflight
        that ran without one while the real compile applies one would be
        flattering, so the answer says which was used.
        """
        path = os.environ.get("ORGAGENTS_PLATFORM_POLICY", "")
        if not path:
            return None
        try:
            from .platform_policy import load as _load_policy

            return _load_policy(path)
        except Exception:
            # A broken policy file must not silently become "no policy".
            raise HTTPException(
                503,
                "the configured platform policy could not be read, so no "
                "design can be judged against it",
            )

    def _preflight(spec_dict: dict, binding_dict: Optional[dict],
                   target: str) -> dict:
        """Would this design compile, and what would it produce?

        The phase gate, run without deploying anything (ADR-0005). It compiles
        into a temporary directory that is discarded: a preflight that wrote
        artifacts somewhere would be a deployment nobody asked for.

        It applies the fabric's platform policy when there is one, because a
        preflight that passes and a real compile that refuses is worse than no
        preflight at all — the second time that happens, nobody reads the
        first one again (ADR-0076).
        """
        import tempfile

        from .compiler.base import register_builtin_targets
        from .compiler.engine import CompileError, compile_system
        from .spec.model import SystemSpec
        from .spec.validate import validate_spec

        register_builtin_targets()
        policy = _active_platform_policy()
        try:
            spec = SystemSpec.model_validate(spec_dict)
        except Exception as exc:                  # a draft mid-edit
            return {"ok": False, "stage": "spec",
                    "refusals": [{"severity": "error", "code": "spec_invalid",
                                  "where": "", "message": str(exc)}],
                    "warnings": [], "files": [], "target": target}

        findings = validate_spec(spec, platform_policy=policy)
        refusals = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        view = lambda f: {"severity": f.severity, "code": f.code,
                          "where": f.where, "message": f.message}
        if refusals:
            return {"ok": False, "stage": "validate",
                    "refusals": [view(f) for f in refusals],
                    "warnings": [view(f) for f in warnings],
                    "files": [], "target": target}

        binding = None
        if binding_dict:
            from .spec.binding import Binding

            try:
                binding = Binding.model_validate(binding_dict)
            except Exception as exc:
                return {"ok": False, "stage": "binding",
                        "refusals": [{"severity": "error",
                                      "code": "binding_invalid",
                                      "where": "", "message": str(exc)}],
                        "warnings": [view(f) for f in warnings],
                        "files": [], "target": target}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = compile_system(
                    spec, targets=[target], out_dir=Path(tmp), binding=binding,
                    platform_policy=policy,
                )[0]
                files = sorted(f.path for f in result.files)
        except CompileError as exc:
            return {"ok": False, "stage": "compile",
                    "refusals": [{"severity": "error", "code": "compile_failed",
                                  "where": target, "message": str(exc)}],
                    "warnings": [view(f) for f in warnings],
                    "files": [], "target": target}
        return {"ok": True, "stage": "compiled", "refusals": [],
                "warnings": [view(f) for f in warnings],
                "files": files, "target": target,
                "platform_policy": policy.stamp if policy else None}

    @app.post("/api/designer/systems/{system_id}/preflight")
    def designer_preflight(system_id: str, body: Optional[dict] = None,
                           user: Principal = Depends(principal)) -> dict:
        """Validate and compile the stored design, deploying nothing.

        A reviewer's act, not an editor's: it answers "would this be refused"
        before anybody asks for it to be run, which is the question the UI
        could not put to the platform at all.
        """
        target = (body or {}).get("target", "local")
        spec_dict, binding_dict, version = _guard(
            designer.spec_at, user, system_id
        )
        return {**_preflight(spec_dict, binding_dict, target),
                "version": version}

    @app.post("/api/designer/systems/{system_id}/publish")
    def designer_publish(system_id: str, body: Optional[dict] = None,
                         user: Principal = Depends(principal)) -> dict:
        """Ask the fabric to run a design, or say exactly why it refused.

        The designer **requests**; it does not deploy. The deployment is
        created in `requested`, and the fabric compiles it for real and moves
        it on under its own authority (ADR-0049). Two reasons, both load
        bearing: the tenant is assigned by the fabric and never named by a
        design, because a design that could name its own tenant could widen
        its own boundary (ADR-0050); and the platform policy is the fabric's
        to apply.

        So what happens here is a preflight and a request. A refusal is
        returned as the reason with the gate's own findings, and is written to
        the audit log — a refused publish is the phase gate saying no to a
        named person about a named design, and nothing else would keep that.
        """
        payload = body or {}
        tenant_id = str(payload.get("tenant_id") or "").strip()
        target = payload.get("target", "local")
        spec_dict, binding_dict, version, name = _guard(
            designer.publish_candidate, user, system_id
        )
        if not tenant_id:
            designer.record_publish(
                user, system_id, outcome=DesignerAuditOutcome.FAILED,
                reason="no tenant named", version=version,
            )
            raise HTTPException(422, {
                "error": "a publish names the tenant it is for",
                "reason": "The tenant is assigned by the fabric and never by "
                          "a design (ADR-0050), so it has to be named here.",
            })
        if fabric_tenants.get(tenant_id) is None:
            designer.record_publish(
                user, system_id, outcome=DesignerAuditOutcome.FAILED,
                reason=f"unknown tenant '{tenant_id}'", version=version,
            )
            raise HTTPException(404, f"no tenant '{tenant_id}'")

        verdict = _preflight(spec_dict, binding_dict, target)
        if not verdict["ok"]:
            designer.record_publish(
                user, system_id, outcome=DesignerAuditOutcome.DENIED,
                reason=f"refused at {verdict['stage']}", version=version,
                tenant_id=tenant_id,
            )
            raise HTTPException(422, {
                "error": f"this design is refused at the {verdict['stage']} "
                         "stage and was not requested",
                "verdict": verdict,
            })

        deployment = fabric_deployments.request(
            tenant_id, name=name or system_id, system_id=system_id,
            revision=str(version), target=target,
        )
        designer.record_publish(
            user, system_id, outcome=DesignerAuditOutcome.SUCCESS,
            version=version, tenant_id=tenant_id,
            deployment_id=deployment.id, target=target,
        )
        return {
            "deployment": _deployment_view(deployment),
            "verdict": verdict,
            "version": version,
            "note": (
                "Requested, not deployed. The fabric compiles this for the "
                "tenant under its own authority and moves it on from "
                "`requested`; nothing has been built yet."
            ),
        }

    @app.get("/api/designer/systems/{system_id}/diff")
    def designer_diff(system_id: str,
                      from_version: Optional[int] = Query(default=None, alias="from"),
                      to_version: Optional[int] = Query(default=None, alias="to"),
                      user: Principal = Depends(principal)) -> dict:
        """What moved between two revisions of one design, ranked by consequence."""
        left, right = _guard(designer.review_pair, user, system_id,
                             left=from_version, right=to_version)
        try:
            result = _diff_ir(_designer_ir(left[0], left[1]),
                              _designer_ir(right[0], right[1]))
        except _IncomparableIRError as e:
            # A refusal, not a failure to find the pair: 409 carries the
            # refusal's own reasoning so the UI can show it verbatim.
            raise HTTPException(409, str(e)) from e
        payload = result.to_dict()
        return {"changes": payload["changes"], "summary": payload["summary"]}

    @app.post("/api/designer/layout")
    def designer_layout(body: dict = Body(...)) -> dict:
        """Place a set of nodes, by algorithm or by what the diagram is.

        Server-side because a layout is a function from a graph to coordinates,
        which makes "no two nodes overlap" and "a child sits below its parent"
        assertions rather than opinions (ADR-0100). A layout that ran only in
        the canvas would be the one part of this platform nothing checked.
        """
        from .designer.layout import LayoutEdge, LayoutNode, arrange

        nodes = [
            LayoutNode(id=str(n["id"]), parent=n.get("parent") or None,
                       label=str(n.get("label") or ""))
            for n in body.get("nodes", []) if n.get("id")
        ]
        edges = [
            LayoutEdge(source=str(e["source"]), target=str(e["target"]))
            for e in body.get("edges", [])
            if e.get("source") and e.get("target")
        ]
        try:
            result = arrange(nodes, edges,
                             algorithm=str(body.get("algorithm") or ""),
                             kind=str(body.get("kind") or "organisation"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "algorithm": result.algorithm,
            "positions": result.positions,
            "notes": result.notes,
            "width": result.width,
            "height": result.height,
        }

    @app.get("/api/designer/metamodel")
    def designer_metamodel() -> dict:
        """The UML profile the designer is built on (ADR-0101): every kind as a
        stereotype of a UML metaclass, and every relationship as a UML kind
        with multiplicities and the spec field it lives in."""
        from .metamodel import describe

        return describe()

    @app.get("/api/designer/palette")
    def designer_palette() -> dict:
        """What the canvas can place, the fields each kind needs, and what
        may be linked to what.

        The link rules travel with the palette because they are the same kind
        of fact: a canvas that decided for itself which components connect
        could draw a relationship the spec has no field for.
        """
        return {"groups": palette_tree(), "links": LINK_RULES}

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
    from .fabric.tenants import (
        TENANT_TRANSITIONS,
        PrefixError,
        TenantIllegalTransition,
        TenantRegistry,
        TenantRetirementBlocked,
        TenantStatus,
        TenantTransitionDenied,
        allowed_tenant_transitions,
    )

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

    def _tenant_view(tenant) -> dict:
        return {
            **tenant.model_dump(mode="json"),
            "allowed_transitions": {
                status.value: sorted(r.value for r in roles)
                for status, roles in allowed_tenant_transitions(tenant.status).items()
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

    #: Tenant action -> the status it asks for. As with deployments, the
    #: transition table decides legality and who may make the move; this map
    #: only names the door.
    TENANT_ACTIONS: dict[str, TenantStatus] = {
        "activate": TenantStatus.ACTIVE,
        "suspend": TenantStatus.SUSPENDED,
        "resume": TenantStatus.ACTIVE,
        "retire": TenantStatus.RETIRED,
    }

    def _acting_tenant_role(roles: list[OperatorRole], source: TenantStatus,
                            target: TenantStatus) -> OperatorRole:
        """As `_acting_role`, over the tenant table."""
        allowed = TENANT_TRANSITIONS.get((source, target)) or frozenset()
        for role in reversed(fabric_rbac.ROLE_ORDER):
            if role in roles and role in allowed:
                return role
        return max(roles, key=fabric_rbac.ROLE_ORDER.index)

    @app.post("/api/fabric/tenants")
    def fabric_register_tenant(req: TenantRegistrationRequest,
                               ctx: OperatorContext = Depends(operator)) -> dict:
        """Mint a tenant and its isolation domain (ADR-0050).

        Every prefix rule lives in `fabric/tenants.py` and is called, not
        copied: collisions, reserved names and the shape of a safe identifier
        are decided in one place, including against retired tenants whose
        prefixes stay spent.
        """
        route = "POST /api/fabric/tenants"
        _fabric_require(ctx, fabric_rbac.TENANT_REGISTER,
                        FabricAuditAction.TENANT_REGISTER, route,
                        tenant_id=req.id)
        try:
            tenant = fabric_tenants.register(
                id=req.id, name=req.name or req.id,
                namespace_prefix=req.namespace_prefix,
                entitlements=req.entitlements,
                cloud_boundary=req.cloud_boundary,
            )
        except PrefixError as e:
            _fabric_record(ctx, FabricAuditAction.TENANT_REGISTER, route=route,
                           outcome=AuditOutcome.CONFLICT,
                           permission=fabric_rbac.TENANT_REGISTER,
                           tenant_id=req.id, reason=str(e))
            raise HTTPException(409, str(e)) from e
        _fabric_record(ctx, FabricAuditAction.TENANT_REGISTER, route=route,
                       outcome=AuditOutcome.SUCCESS,
                       permission=fabric_rbac.TENANT_REGISTER,
                       tenant_id=tenant.id, reason=req.reason,
                       detail={"namespace_prefix": tenant.namespace_prefix,
                               "isolation_domain": tenant.isolation_domain.id,
                               "status": tenant.status.value})
        return _tenant_view(tenant)

    @app.post("/api/fabric/tenants/{tenant_id}/actions/{action}")
    def fabric_tenant_action(tenant_id: str, action: str,
                             req: OperatorActionRequest,
                             ctx: OperatorContext = Depends(operator)) -> dict:
        if action not in TENANT_ACTIONS:
            raise HTTPException(
                404, f"unknown tenant action '{action}'; "
                     f"expected one of {sorted(TENANT_ACTIONS)}"
            )
        target = TENANT_ACTIONS[action]
        route = f"POST /api/fabric/tenants/{{tenant_id}}/actions/{action}"
        _fabric_require(ctx, fabric_rbac.TENANT_LIFECYCLE,
                        FabricAuditAction.TENANT_LIFECYCLE, route,
                        tenant_id=tenant_id)
        tenant = _require_tenant(tenant_id)
        role = _acting_tenant_role(ctx.roles, tenant.status, target)
        source = tenant.status
        try:
            moved = fabric_tenants.transition(
                tenant_id, target, actor=ctx.principal.user_id, role=role,
                reason=req.reason,
                # The registry cannot see deployments; the command centre can,
                # so it is the one that can honestly answer "is anything of
                # this tenant still up?".
                deployments=fabric_deployments.list(tenant_id),
            )
        except (TenantIllegalTransition, TenantRetirementBlocked) as e:
            _fabric_record(ctx, FabricAuditAction.TENANT_LIFECYCLE, route=route,
                           outcome=AuditOutcome.CONFLICT,
                           permission=fabric_rbac.TENANT_LIFECYCLE,
                           tenant_id=tenant_id, reason=str(e))
            raise HTTPException(409, str(e)) from e
        except TenantTransitionDenied as e:
            _fabric_record(ctx, FabricAuditAction.TENANT_LIFECYCLE, route=route,
                           outcome=AuditOutcome.DENIED,
                           permission=fabric_rbac.TENANT_LIFECYCLE,
                           tenant_id=tenant_id, reason=str(e))
            raise HTTPException(403, str(e)) from e
        _fabric_record(ctx, FabricAuditAction.TENANT_LIFECYCLE, route=route,
                       outcome=AuditOutcome.SUCCESS,
                       permission=fabric_rbac.TENANT_LIFECYCLE,
                       tenant_id=tenant_id, reason=req.reason,
                       detail={"from": source.value, "to": moved.status.value,
                               "acted_as": role.value})
        return _tenant_view(moved)

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
from .spec.model import (  # noqa: E402  (the palette is data, built below)
    POLICY_CONDITION_KEYS,
    Action,
    Effect,
    FlowKind,
    MissionStatus,
    ResourceKind,
    UnitLinkKind,
)

# Which components may be linked to which, and what the spec calls it
# (ADR-0006, ADR-0024). The canvas asks rather than guesses: a drop used to
# nest whatever you dropped near whatever was nearest, which wrote a parent
# into the spec nobody asked for, and `nearestNode` had no distance limit so
# "near" meant "anywhere".
#
# Every rule names the spec field it writes, because a link a reader cannot
# trace to a field is a link the compiler will not see.
# What may be linked to what, derived from the UML profile (ADR-0101)
# rather than written by hand here. The nine rules this table used to hold
# are all still produced, under the same words, so every canvas code path
# that knew them still does; the rest of what the spec can hold is now
# drawable too.
from .metamodel import link_rules as _link_rules  # noqa: E402

LINK_RULES: list[dict[str, Any]] = _link_rules()


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
                     {"name": "description", "type": "text",
                      "help": "the unit's charter, in prose"},
                     {"name": "mandate", "type": "decisions",
                      "help": "what this unit may decide; empty inherits its "
                              "parent's, never everything"},
                     {"name": "groups", "type": "list"},
                     {"name": "placement", "type": "bool",
                      "help": "make this unit a placement boundary: its own "
                              "sandbox environment, shared volume and network "
                              "policy. Off means it sits in the nearest "
                              "ancestor that is one — and if nothing is, the "
                              "whole organisation shares one place"},
                 ]},
                {"kind": "skill", "label": "Skill", "icon": "◇",
                 "help": "instructions plus resources. Changes how an agent "
                         "works, never what it may reach",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "version", "type": "string"},
                     {"name": "instructions", "type": "text", "required": True},
                     {"name": "triggers", "type": "list",
                      "help": "phrases that invoke it"},
                     {"name": "requires_capabilities", "type": "list",
                      "help": "assumed, and checked against the holder's "
                              "grants — declaring one grants nothing"},
                     {"name": "resources", "type": "map",
                      "help": "files shipped with the skill: path = content"},
                 ]},
                {"kind": "plugin", "label": "Plugin", "icon": "❖",
                 "help": "a bundle of skills and tools",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "version", "type": "string"},
                     {"name": "provides_skills", "type": "list"},
                     {"name": "provides_tools", "type": "list"},
                     {"name": "requires_capabilities", "type": "list"},
                     {"name": "hooks", "type": "map",
                      "help": "event = handler, resolved by the target"},
                 ]},
                {"kind": "tool", "label": "Tool", "icon": "⚙",
                 "help": "a thin wrapper that narrows and names something "
                         "already granted; it grants nothing itself",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "wraps_kind", "type": "enum", "required": True,
                      "options": ["capability", "subagent", "workflow",
                                  "endpoint"]},
                     {"name": "wraps", "type": "string", "required": True},
                     {"name": "input_schema", "type": "json"},
                     {"name": "output_schema", "type": "json"},
                     {"name": "idempotent", "type": "bool"},
                 ]},
                {"kind": "guardrail", "label": "Guardrail", "icon": "⛨",
                 "help": "permissions decide what an agent may reach; a "
                         "guardrail decides what may pass",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "applies_to", "type": "multi", "required": True,
                      "options": ["input", "output", "tool_input",
                                  "tool_output"],
                      "help": "which of the four boundaries"},
                     {"name": "checks", "type": "multi", "required": True,
                      "options": ["pii", "secrets", "prompt_injection",
                                  "data_class", "url_allowlist", "schema",
                                  "pattern", "max_length"]},
                     {"name": "on_violation", "type": "enum",
                      "options": ["block", "redact", "flag", "escalate"]},
                     {"name": "data_classes", "type": "list",
                      "help": "for the data_class check"},
                     {"name": "patterns", "type": "list",
                      "help": "for the pattern check"},
                     {"name": "allowed_urls", "type": "list",
                      "help": "for the url_allowlist check"},
                     {"name": "max_length", "type": "number"},
                     {"name": "escalate_channel", "type": "string",
                      "help": "required when on_violation is escalate"},
                     {"name": "classifier", "type": "enum",
                      "options": ["", "frontier_reasoning", "balanced",
                                  "fast_cheap"],
                      "help": "a model class for judgement checks; empty is "
                              "the deterministic pattern floor"},
                     {"name": "enabled", "type": "bool"},
                 ]},
                {"kind": "output_contract", "label": "Output contract",
                 "icon": "▤",
                 "help": "a checkable shape, not a sentence about one",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "schema", "type": "json",
                      "help": "a small JSON-Schema subset: type, properties, "
                              "required, items, enum"},
                     {"name": "required", "type": "list"},
                     {"name": "on_violation", "type": "enum",
                      "options": ["retry", "block", "flag"]},
                 ]},
                {"kind": "evaluation", "label": "Evaluation case", "icon": "✓",
                 "help": "what an agent must get right before it may be "
                         "promoted — and before it may run unattended",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "given", "type": "text", "required": True,
                      "help": "the situation or prompt"},
                     {"name": "expect", "type": "text", "required": True,
                      "help": "what a correct answer must contain or do; "
                              "`contains: ...` is checkable, prose is not"},
                     {"name": "must_not", "type": "list"},
                     {"name": "applies_to", "type": "list",
                      "help": "which agents; empty applies to every one of "
                              "them, which is wider than most people mean"},
                     {"name": "weight", "type": "number"},
                 ]},
                {"kind": "separation", "label": "Separation of duties",
                 "icon": "⊘",
                 "help": "decisions no single agent may hold together",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     # A plain list of decision ids, not a mandate: a
                     # separation has no inherit-or-empty question to ask.
                     {"name": "decisions", "type": "decision_refs",
                      "required": True},
                     {"name": "reason", "type": "text",
                      "help": "a rule without one is a rule nobody defends"},
                 ]},
                {"kind": "policy", "label": "Policy rule", "icon": "⊙",
                 "help": "an explicit allow or deny on top of the roles. A "
                         "deny always wins and cannot be overridden",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "effect", "type": "enum",
                      "options": [e.value for e in Effect], "required": True},
                     {"name": "description", "type": "text",
                      "help": "shown in the refusal, so write the sentence "
                              "somebody refused should read"},
                     {"name": "actions", "type": "multi",
                      "options": [a.value for a in Action],
                      "help": "empty means every action"},
                     {"name": "resource_kinds", "type": "multi",
                      "options": [r.value for r in ResourceKind],
                      "help": "empty means every kind"},
                     {"name": "resources", "type": "list",
                      "help": "ids or globs within the kind; `*` is all"},
                     {"name": "subjects", "type": "list",
                      "help": "agent, team or role ids; `*` is everyone"},
                     # The keys come from the model, not from the UI: a form
                     # that offered its own list could offer one nothing
                     # evaluates, which is the defect these fields exist to
                     # make impossible.
                     {"name": "conditions", "type": "conditions",
                      "options": sorted(POLICY_CONDITION_KEYS),
                      "help": "all of these must hold for the rule to apply"},
                     {"name": "unless", "type": "conditions",
                      "options": sorted(POLICY_CONDITION_KEYS),
                      "help": "where this holds, the rule does not apply — "
                              "how 'deny everywhere except the clean room' is "
                              "written. A key nothing evaluates would switch "
                              "the whole rule off, so only these are offered"},
                 ]},
                {"kind": "person", "label": "Person", "icon": "☺",
                 "help": "a human principal: what they may decide, never "
                         "what they may reach. A person's access is their "
                         "employer's to mediate (ADR-0079)",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "contact", "type": "string",
                      "help": "two people sharing one is refused: one human "
                              "is one principal"},
                     {"name": "position", "type": "string",
                      "help": "their job title, as prose. Authority attaches "
                              "to the person, not the position, so it rots "
                              "when they move"},
                     {"name": "unit", "type": "string",
                      "help": "the org unit that bounds their authority; "
                              "empty sits them under the root"},
                     {"name": "mandate", "type": "decisions",
                      "help": "what they may decide. There is deliberately "
                              "no field here for what they may reach"},
                 ]},
                {"kind": "decision", "label": "Decision class", "icon": "§",
                 "help": "what a unit may decide, referenced by a mandate",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "title", "type": "string"},
                     {"name": "description", "type": "text"},
                 ]},
                {"kind": "agent", "label": "Agent", "icon": "◆",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "description", "type": "text",
                      "help": "what it is for — the blurb another agent reads "
                              "to decide when to delegate to it"},
                     {"name": "instructions", "type": "text",
                      "help": "how it operates — its system prompt, in your "
                              "words. The org context is added automatically; "
                              "this does not replace it (ADR-0083)"},
                     {"name": "roles", "type": "list"},
                     {"name": "capabilities", "type": "list"},
                     {"name": "knowledge", "type": "list"},
                     {"name": "skills", "type": "list"},
                     {"name": "plugins", "type": "list"},
                     {"name": "tools", "type": "list"},
                     {"name": "endpoints", "type": "list"},
                     {"name": "environments", "type": "list",
                      "help": "the sandbox class(es) it runs in; one or more "
                              "(ADR-0082)"},
                     {"name": "mandate", "type": "decisions",
                      "help": "what this agent may decide alone; empty "
                              "inherits its team's"},
                     {"name": "autonomy", "type": "autonomy",
                      "help": "per capability, and may only tighten what the "
                              "capability declares"},
                     {"name": "model_policy", "type": "object",
                      "help": "which models this agent may run on; says a "
                              "class, never a vendor",
                      "fields": [
                          {"name": "classes", "type": "multi",
                           "options": ["frontier_reasoning", "balanced",
                                       "fast_cheap"]},
                          {"name": "allow", "type": "list",
                           "help": "catalog entry ids, when an organization "
                                   "names models directly"},
                          {"name": "deny", "type": "list"},
                          {"name": "max_cost_per_million_tokens",
                           "type": "number"},
                          {"name": "min_context_tokens", "type": "number"},
                          {"name": "require_no_training_on_data",
                           "type": "bool"},
                          {"name": "require_regions", "type": "list"},
                          {"name": "subagent_classes", "type": "multi",
                           "options": ["frontier_reasoning", "balanced",
                                       "fast_cheap"]},
                          {"name": "allow_fallback", "type": "bool",
                           "help": "off by default: a fallback is a quiet "
                                   "change of model, so it is opted into"},
                      ]},
                     {"name": "shared_service", "type": "bool"},
                     {"name": "leads_team", "type": "bool",
                      "help": "whether this agent leads the team it is in. "
                              "Not a field on the agent: it sets the team's "
                              "leader, so ticking it here hands leadership "
                              "over from whoever held it"},
                     {"name": "workflows", "type": "list",
                      "help": "encoded processes this agent may invoke; a "
                              "trigger can only run one the agent holds"},
                     {"name": "produces_data", "type": "list",
                      "help": "data classes this agent produces, so a "
                              "dependency on one has a named other party "
                              "rather than an assumption (ADR-0099)"},
                     {"name": "data_dependencies", "type": "json",
                      "help": "what this agent *relies on*, as distinct from "
                              "what it may touch: {data_class, fields, "
                              "max_age_seconds, on_stale, produced_by}. "
                              "A grant says it may read; this says what it is "
                              "counting on, and what to do when that does not "
                              "hold"},
                     {"name": "successor", "type": "string",
                      "blank": "— its manager stands in —",
                      "help": "who stands in when this agent cannot run. "
                              "Leave unset and its manager does — already "
                              "holding this mandate, so nothing is granted. "
                              "Naming a peer lends authority the chart did "
                              "not, so it is bounded, recorded, and refused "
                              "if it would collapse a separation (ADR-0094)"},
                     {"name": "scaling", "type": "object",
                      "help": "how many of this agent run. Leave unset and "
                              "the platform default is still emitted "
                              "explicitly — the one thing ruled out is a "
                              "ceiling nobody chose (ADR-0095)",
                      "fields": [
                          {"name": "min_instances", "type": "number",
                           "help": "kept warm. 0 scales to zero, which drops "
                                   "any asynchronous work this agent was "
                                   "holding — they settle as failed, not "
                                   "silently"},
                          {"name": "max_instances", "type": "number",
                           "help": "the ceiling. Times the per-instance "
                                   "figure below, this is how much work can "
                                   "be in flight — a different bound from "
                                   "max_parallel_subagents"},
                          {"name": "concurrent_sessions_per_instance",
                           "type": "number"},
                      ]},
                     {"name": "humans", "type": "humans"},
                 ]},
                # A mission is a short-lived team drawn from the standing
                # organization (ADR-0039). It was missing here, so the canvas
                # could draw one and nobody could place one — the end date is
                # required because a mission that never ends is a
                # reorganization and belongs in the org chart.
                {"kind": "mission", "label": "Mission", "icon": "◍",
                 "help": "a short-lived team drawn from the standing "
                         "organisation. It always ends (ADR-0039)",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "objective", "type": "text", "required": True},
                     {"name": "deliverables", "type": "list"},
                     {"name": "success_criteria", "type": "list"},
                     {"name": "status", "type": "enum",
                      "options": [m.value for m in MissionStatus]},
                     {"name": "leader", "type": "string",
                      "help": "agent id; must also be a member"},
                     {"name": "members", "type": "list",
                      "help": "agent ids drawn from the standing organization. "
                              "They keep their home team and their own "
                              "permissions"},
                     {"name": "starts_on", "type": "string", "help": "ISO date"},
                     {"name": "ends_on", "type": "string", "required": True,
                      "help": "ISO date; a mission always ends"},
                     {"name": "internal_delegation", "type": "bool",
                      "help": "members may hand work to each other for the "
                              "mission's duration — declared, not assumed"},
                     {"name": "mandate", "type": "decisions",
                      "help": "authority lent for the window, bounded by the "
                              "line it is drawn from and expiring with it "
                              "(ADR-0065 rule 8). Without it a mission is an "
                              "authority hole"},
                 ]},
                {"kind": "subagent", "label": "Sub-agent", "icon": "◇",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "parent", "type": "string", "required": True,
                      "help": "the agent that calls this sub-agent. Not a key "
                              "on the sub-agent — it is which agent's list "
                              "holds it, so changing it here moves it"},
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
                     {"name": "semantics", "type": "enum",
                      "options": ["unspecified", "subject", "event",
                                  "reference", "derived", "aggregate"],
                      "help": "what this data *is*, as distinct from how it "
                              "is protected (ADR-0099)"},
                     {"name": "relations", "type": "json",
                      "help": "how it relates to other classes: "
                              "{kind, target}. `derived_from` carries "
                              "restrictions along it, so a class derived from "
                              "data that may not leave its region may not say "
                              "that it may"},
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
                     {"name": "graph", "type": "graph",
                      "help": "the process itself: steps, and what follows "
                              "what. A branch is the only step that may have "
                              "several ways out, because it is the only one "
                              "that chooses; a step nothing reaches is "
                              "refused, and a loop back to an earlier step is "
                              "allowed and bounded (ADR-0096)"},
                     {"name": "interrupt_before", "type": "list",
                      "help": "step ids to pause at for a person"},
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


# How the palette reads, and what nests under what (ADR-0034).
#
# The groups above were the order things were added in: Team, then skills and
# plugins, then guardrails, then a person, then finally Agent — fifteen kinds
# in one list with no shape. A palette is the first thing somebody meets, and
# a jumble teaches nothing about the model.
#
# The nesting is not decoration: a child is a component the parent *contains*
# in the spec. A Tool nested under Agent says `agent.tools`, the same fact
# `LINK_RULES` states for linking. Where both speak they agree, and a test
# holds them to it.
PALETTE_TREE: list[dict[str, Any]] = [
    {
        "id": "organisation",
        "label": "Organisation",
        "help": "the standing structure: who exists and who they answer to",
        "kinds": [
            {"kind": "team", "children": [
                {"kind": "agent", "children": [
                    {"kind": "subagent"},
                    {"kind": "skill"},
                    {"kind": "plugin"},
                    {"kind": "tool"},
                ]},
            ]},
            # Declared once at the top level and referenced by the agents they
            # are paired with, so a person is not nested under one (ADR-0079).
            {"kind": "person"},
            {"kind": "role"},
        ],
    },
    {
        "id": "authority",
        "label": "Authority",
        "help": "what may be decided, and what may not be decided together",
        "kinds": [
            {"kind": "decision"},
            {"kind": "separation"},
            {"kind": "policy"},
        ],
    },
    {
        "id": "access",
        "label": "Access",
        "help": "what may be reached, and from where",
        "kinds": [
            {"kind": "capability"},
            {"kind": "data_class"},
            {"kind": "environment"},
            {"kind": "endpoint"},
        ],
    },
    {
        "id": "work",
        "label": "Work",
        "help": "what actually happens, and what wakes it",
        "kinds": [
            {"kind": "mission"},
            {"kind": "workflow"},
            {"kind": "trigger"},
            {"kind": "channel"},
            {"kind": "knowledge"},
            {"kind": "memory_namespace"},
        ],
    },
    {
        "id": "assurance",
        "label": "Assurance",
        "help": "what must hold before this runs unattended",
        "kinds": [
            {"kind": "guardrail"},
            {"kind": "output_contract"},
            {"kind": "evaluation"},
        ],
    },
    {
        "id": "canvas",
        "label": "Canvas",
        "help": "annotation; nothing here reaches the spec",
        "kinds": [{"kind": "note"}],
    },
]


def _palette_definitions() -> dict[str, dict[str, Any]]:
    """Every kind's fields, by kind, from the flat groups above."""
    return {k["kind"]: k for group in PALETTE["groups"] for k in group["kinds"]}


def _compose(node: dict[str, Any],
             defs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    definition = defs.get(node["kind"])
    if definition is None:                      # a tree entry with no fields
        raise KeyError(f"the palette tree names '{node['kind']}', "
                       "which no group defines")
    composed = dict(definition)
    if node.get("children"):
        composed["children"] = [_compose(c, defs) for c in node["children"]]
    return composed


def palette_tree() -> list[dict[str, Any]]:
    """The palette as a tree, in a deliberate order."""
    defs = _palette_definitions()
    return [
        {**group, "kinds": [_compose(k, defs) for k in group["kinds"]]}
        for group in PALETTE_TREE
    ]


def palette_kinds(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every kind in a palette, nested ones included."""
    out: list[dict[str, Any]] = []

    def walk(kinds: list[dict[str, Any]]) -> None:
        for kind in kinds:
            out.append(kind)
            walk(kind.get("children") or [])

    for group in groups:
        walk(group["kinds"])
    return out
