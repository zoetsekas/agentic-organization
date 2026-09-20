"""HTTP API for the platform and the agentic designer UI.

Route groups:

``/api/org``        org units, agents, hierarchy tree
``/api/agents``     CRUD, harness preview, runs
``/api/sessions``   sessions, events, traces, resume — each session has a URL
``/api/catalog``    marketplace search, detail, install, rate
``/api/components`` the designer's palette: runtimes, sandboxes, workflows,
                    channels, data planes, infrastructure options
``/api/ops``        metrics, alerts, health
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
    from .designer.models import DesignerSettings

    designer_settings = DesignerSettings(
        persistence=os.environ.get("ORGAGENTS_DESIGNER_STORE", "relational"),  # type: ignore[arg-type]
        storage_path=os.environ.get("ORGAGENTS_DESIGNER_PATH", "./designer-data"),
    )
    designer = DesignerService(
        build_repository(designer_settings, platform.store), designer_settings
    )
    app.state.designer = designer

    def principal(
        x_user: str = Header(default="anonymous"),
        x_user_name: str = Header(default=""),
        x_user_email: str = Header(default=""),
    ) -> Principal:
        """Identity comes from a header in dev; an OIDC proxy supplies it in
        production (ADR-0032). The service never trusts a client-sent role."""
        return Principal(user_id=x_user, display_name=x_user_name or x_user,
                         email=x_user_email)

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

    @app.get("/api/designer/palette")
    def designer_palette() -> dict:
        """What the canvas can place, and the fields each kind needs."""
        return PALETTE

    # -- UI ----------------------------------------------------------------

    if WEB_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")

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
