"""The running platform's org units and hierarchy tree."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from ..designer.audit import AuditAction as DesignerAuditAction
from ..models import OrgUnit
from ..runtime_access import RUNTIME_MANAGE, Grants
from .context import ApiContext


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router
    platform = ctx.platform
    caller = ctx.caller
    _authorize = ctx.authorize
    _allowed = ctx.allowed
    _visible_agent_ids = ctx.visible_agent_ids
    _require_read_anywhere = ctx.require_read_anywhere

    # -- org ---------------------------------------------------------------

    @router.get("/api/org/units")
    def list_units(g: Grants = Depends(caller)) -> list[dict]:
        _require_read_anywhere(g)
        return [u.model_dump() for u in platform.org.units()]

    @router.post("/api/org/units")
    def create_unit(unit: OrgUnit, g: Grants = Depends(caller)) -> dict:
        _authorize(g, RUNTIME_MANAGE, None, DesignerAuditAction.ORG_UNIT_CREATE,
                   detail={"org_unit": unit.id})
        saved = platform.org.add_unit(unit)
        _allowed(g, DesignerAuditAction.ORG_UNIT_CREATE, RUNTIME_MANAGE, None,
                 {"org_unit": saved.id})
        return saved.model_dump()

    @router.get("/api/org/tree")
    def org_tree(root: Optional[str] = None,
                 g: Grants = Depends(caller)) -> list[dict]:
        _require_read_anywhere(g)
        visible = _visible_agent_ids(g)
        return platform.org.to_tree(
            root, agents=[a for a in platform.org.agents() if a.id in visible])


    return router
