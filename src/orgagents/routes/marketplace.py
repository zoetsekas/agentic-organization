"""The marketplace of skills, plugins and workflows agents install."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..designer.audit import AuditAction as DesignerAuditAction
from ..runtime_access import CATALOG_PUBLISH, CATALOG_READ, RUNTIME_MANAGE, Grants
from .context import ApiContext
from .models import InstallRequest, PublishRequest


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router
    platform = ctx.platform
    caller = ctx.caller
    _authorize = ctx.authorize
    _allowed = ctx.allowed
    _scope_of_agent_id = ctx.scope_of_agent_id
    _read_catalog = ctx.read_catalog

    # -- catalog / marketplace --------------------------------------------

    @router.get("/api/catalog")
    def search_catalog(
        q: str = "",
        kind: Optional[str] = None,
        tags: Optional[list[str]] = Query(default=None),
        groups: Optional[list[str]] = Query(default=None),
        sort: str = "recent",
        limit: int = 50,
        g: Grants = Depends(caller),
    ) -> list[dict]:
        _read_catalog(g)
        entries = platform.catalog.search(
            q, kind=kind or None, tags=tags or None, viewer_groups=groups,  # type: ignore[arg-type]
            sort=sort, limit=limit,
        )
        return [
            e.model_dump() | {"rating": e.rating, "url": platform.catalog.url(e)}
            for e in entries
        ]

    @router.get("/api/catalog/stats")
    def catalog_stats(g: Grants = Depends(caller)) -> dict:
        _read_catalog(g)
        return platform.catalog.stats()

    @router.get("/api/catalog/{entry_id}")
    def catalog_detail(entry_id: str, g: Grants = Depends(caller)) -> dict:
        _read_catalog(g)
        detail = platform.catalog.detail(entry_id)
        if detail is None:
            raise HTTPException(404, "catalog entry not found")
        return detail

    @router.post("/api/catalog/publish")
    def publish(req: PublishRequest, g: Grants = Depends(caller)) -> dict:
        detail = {"kind": req.kind, "ref": req.ref_id}
        _authorize(g, CATALOG_PUBLISH, None, DesignerAuditAction.CATALOG_PUBLISH,
                   anywhere=True, detail=detail)
        try:
            entry = platform.catalog.publish(
                req.kind, req.ref_id, owner=req.owner or g.principal.user_id,  # type: ignore[arg-type]
                tags=req.tags, visibility=req.visibility, groups=req.groups,
            )
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        _allowed(g, DesignerAuditAction.CATALOG_PUBLISH, CATALOG_PUBLISH, None,
                 detail | {"entry": entry.id})
        return entry.model_dump()

    @router.post("/api/catalog/{entry_id}/install")
    def install(entry_id: str, req: InstallRequest,
                g: Grants = Depends(caller)) -> dict:
        # Installing changes what an agent can do: managing that agent.
        scope = _scope_of_agent_id(req.agent_id)
        detail = {"entry": entry_id, "agent": req.agent_id}
        _authorize(g, RUNTIME_MANAGE, scope, DesignerAuditAction.CATALOG_INSTALL,
                   detail=detail)
        try:
            result = platform.catalog.install(entry_id, req.agent_id)
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        _allowed(g, DesignerAuditAction.CATALOG_INSTALL, RUNTIME_MANAGE, scope,
                 detail)
        return result

    @router.post("/api/catalog/{entry_id}/rate")
    def rate(entry_id: str, stars: float, g: Grants = Depends(caller)) -> dict:
        _authorize(g, CATALOG_READ, None, DesignerAuditAction.CATALOG_RATE,
                   anywhere=True, detail={"entry": entry_id})
        try:
            rated = platform.catalog.rate(entry_id, stars).model_dump()
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        _allowed(g, DesignerAuditAction.CATALOG_RATE, CATALOG_READ, None,
                 {"entry": entry_id, "stars": stars})
        return rated


    return router
