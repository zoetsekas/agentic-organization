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

from fastapi import FastAPI, HTTPException, Query
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

    # -- UI ----------------------------------------------------------------

    if WEB_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")

        @app.get("/")
        def index() -> Any:
            return RedirectResponse("/ui/")

    return app


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
