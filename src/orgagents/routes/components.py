"""What the designer's palette can place on the running platform."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..designer.audit import AuditAction as DesignerAuditAction
from ..models import (
    ChannelKind,
    Plugin,
    Runtime,
    SandboxTemplate,
    Skill,
    Visibility,
    WorkflowRef,
)
from ..runtime_access import RUNTIME_MANAGE, Grants
from ..store import PLUGINS, SKILLS, WORKFLOWS
from .context import ApiContext
from .palette import INFRASTRUCTURE_CATALOG


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router
    platform = ctx.platform
    caller = ctx.caller
    _authorize = ctx.authorize
    _allowed = ctx.allowed
    _read_catalog = ctx.read_catalog

    # -- designer component palette ---------------------------------------

    @router.get("/api/components")
    def components(g: Grants = Depends(caller)) -> dict:
        """Everything the designer canvas can place."""
        _read_catalog(g)
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

    @router.get("/api/components/sandbox_templates")
    def sandbox_templates(g: Grants = Depends(caller)) -> list[dict]:
        _read_catalog(g)
        return [t.model_dump() for t in platform.sandboxes.templates()]

    @router.post("/api/components/sandbox_templates")
    def publish_template(template: SandboxTemplate,
                         g: Grants = Depends(caller)) -> dict:
        _authorize(g, RUNTIME_MANAGE, None,
                   DesignerAuditAction.SANDBOX_TEMPLATE_PUBLISH,
                   detail={"template": template.id})
        saved = platform.sandboxes.publish(template)
        _allowed(g, DesignerAuditAction.SANDBOX_TEMPLATE_PUBLISH, RUNTIME_MANAGE,
                 None, {"template": saved.id})
        return saved.model_dump()


    return router
