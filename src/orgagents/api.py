"""HTTP API for the platform and the agentic designer UI.

Route groups, one router module each under `orgagents.routes`:

``/api/org``        org units, agents, hierarchy tree
``/api/agents``     CRUD, harness preview, runs
``/api/sessions``   sessions, events, traces, resume — each session has a URL
``/api/catalog``    marketplace search, detail, install, rate
``/api/components`` the designer's palette: runtimes, sandboxes, workflows,
                    channels, data planes, infrastructure options
``/api/ops``        metrics, alerts, health
``/api/catalogs``   the governed platform catalog
``/api/designer``   workspaces, designs, review surfaces, the UML profile
``/api/fabric``     the command centre: tenants, deployments, health, quotas,
                    common services, operator actions and the operator audit
                    log (ADR-0051, docs/COMMAND_CENTRE_API.md)

`create_app` builds the shared services once (`routes.context.ApiContext`),
includes each router in the order the routes have always been registered in,
and applies the public contract last (ADR-0115).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI

from .platform import Platform
from .routes import DESIGN_ROUTERS, RUNTIME_ROUTERS, ApiContext
from .routes import ui as _ui
from .routes.models import (
    CatalogAmendRequest,
    CatalogEditRequest,
    CatalogSendBackRequest,
    CreateSystemRequest,
    InstallRequest,
    LockRequest,
    MemberRequest,
    OperatorActionRequest,
    OperatorGrantRequest,
    PublishRequest,
    RequotaRequest,
    ResumeRequest,
    RunRequest,
    SaveSystemRequest,
    TenantRegistrationRequest,
    WorkspaceRequest,
)
from .routes.palette import (
    INFRASTRUCTURE_CATALOG,
    LINK_RULES,
    PALETTE,
    PALETTE_TREE,
    palette_kinds,
    palette_tree,
)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def create_app(
    db_path: Optional[str] = None, base_url: str = "http://localhost:8000"
) -> FastAPI:
    platform = Platform(db_path or os.environ.get("ORGAGENTS_DB", "orgagents.db"),
                        base_url=base_url)
    app = FastAPI(title="Organizational Agentic System", version="0.1.0")
    app.state.platform = platform
    ctx = ApiContext(app, platform)

    for module in RUNTIME_ROUTERS:
        module.build(ctx)

    @app.get("/healthz")
    def healthz() -> dict:
        """Liveness, for probes: public, and so it says nothing more."""
        return {"status": "ok"}

    for module in DESIGN_ROUTERS:
        module.build(ctx)

    _ui.mount(app, WEB_DIR)

    # Tags, the /api/v1 aliases and the API description (ADR-0115). Last, so
    # it sees every route registered above.
    from .api_contract import apply_contract
    return apply_contract(app)


app = None  # populated by `uvicorn orgagents.api:get_app` style factories


def get_app() -> FastAPI:  # pragma: no cover - uvicorn entrypoint
    global app
    if app is None:
        app = create_app()
    return app


__all__ = [
    "create_app", "get_app", "WEB_DIR",
    "PALETTE", "PALETTE_TREE", "LINK_RULES", "INFRASTRUCTURE_CATALOG",
    "palette_kinds", "palette_tree",
    "CatalogAmendRequest", "CatalogEditRequest", "CatalogSendBackRequest",
    "CreateSystemRequest", "InstallRequest", "LockRequest", "MemberRequest",
    "OperatorActionRequest", "OperatorGrantRequest", "PublishRequest",
    "RequotaRequest", "ResumeRequest", "RunRequest", "SaveSystemRequest",
    "TenantRegistrationRequest", "WorkspaceRequest",
]
