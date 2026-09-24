"""The governed platform catalog (ADR-0041, ADR-0062)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..catalogs import ApprovalStatus, CatalogKind
from ..catalogs import CatalogEntry as PlatformCatalogEntry
from ..catalogs import Entitlement as PlatformEntitlement
from ..designer.audit import AuditAction as DesignerAuditAction
from ..designer.audit import AuditOutcome as DesignerAuditOutcome
from ..runtime_access import (
    CATALOG_DELETE,
    CATALOG_ENTITLE,
    CATALOG_PUBLISH,
    CATALOG_REVIEW,
    Grants,
)
from .context import ApiContext
from .models import CatalogAmendRequest, CatalogEditRequest, CatalogSendBackRequest


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router
    caller = ctx.caller
    _authorize = ctx.authorize
    _allowed = ctx.allowed
    _read_catalog = ctx.read_catalog
    _record = ctx.record
    catalog_service = ctx.catalog_service

    # -- platform catalog (ADR-0041) --------------------------------------

    @router.get("/api/catalogs")
    def catalogs_search(
        q: str = "",
        kind: Optional[str] = None,
        status: Optional[str] = None,
        groups: Optional[list[str]] = Query(default=None),
        environment: str = "development",
        selectable_only: bool = False,
        g: Grants = Depends(caller),
    ) -> list[dict]:
        _read_catalog(g)
        entries = catalog_service.search(
            q, kind=CatalogKind(kind) if kind else None,
            status=ApprovalStatus(status) if status else None,
            groups=groups, environment=environment,
            selectable_only=selectable_only,
        )
        return [e.model_dump(mode="json") for e in entries]

    @router.get("/api/catalogs/stats")
    def catalogs_stats(g: Grants = Depends(caller)) -> dict:
        _read_catalog(g)
        return catalog_service.stats()

    @router.get("/api/catalogs/kinds")
    def catalogs_kinds(g: Grants = Depends(caller)) -> list[dict]:
        _read_catalog(g)
        return [
            {"id": k.value, "label": k.value.replace("_", " ").title(),
             "count": len(catalog_service.list(k)),
             # The declared attributes of the kind, so the designer renders one
             # generated form for all twelve rather than twelve written ones.
             "attributes": catalog_service.attribute_schema(k)}
            for k in CatalogKind
        ]

    @router.get("/api/catalogs/{entry_id}")
    def catalogs_detail(entry_id: str, g: Grants = Depends(caller)) -> dict:
        _read_catalog(g)
        try:
            return catalog_service.describe(entry_id)
        except Exception as e:
            raise HTTPException(404, str(e)) from e

    def _catalog_act(g: Grants, permission: str, action: DesignerAuditAction,
                     entry_id: str, fn, *, anywhere: bool = False) -> dict:
        """Authorize, act, and record either way. The actor handed to the
        catalog service is the authenticated caller's id, nothing else."""
        detail = {"entry": entry_id}
        _authorize(g, permission, None, action, anywhere=anywhere, detail=detail)
        try:
            result = fn(g.principal.user_id)
        except HTTPException:
            raise
        except Exception as e:
            _record(g, action, DesignerAuditOutcome.FAILED, permission=permission,
                    workspace_id=None, reason=str(e), detail=detail)
            raise HTTPException(400, str(e)) from e
        _allowed(g, action, permission, None, detail)
        return result

    @router.post("/api/catalogs")
    def catalogs_publish(entry: PlatformCatalogEntry,
                         g: Grants = Depends(caller)) -> dict:
        return _catalog_act(
            g, CATALOG_PUBLISH, DesignerAuditAction.CATALOG_PUBLISH, entry.id,
            lambda me: catalog_service.publish(entry, actor=me).model_dump(mode="json"),
            anywhere=True)

    @router.patch("/api/catalogs/{entry_id}")
    def catalogs_update(entry_id: str, req: CatalogEditRequest,
                        g: Grants = Depends(caller)) -> dict:
        return _catalog_act(
            g, CATALOG_PUBLISH, DesignerAuditAction.CATALOG_EDIT, entry_id,
            lambda me: catalog_service.update(
                entry_id, req.model_dump(exclude={"note"}), actor=me,
                note=req.note).model_dump(mode="json"),
            anywhere=True)

    @router.post("/api/catalogs/{entry_id}/amend")
    def catalogs_amend(entry_id: str, req: CatalogAmendRequest,
                       g: Grants = Depends(caller)) -> dict:
        # The service's own refusal names both ways forward; it is passed on
        # as the 400's detail unchanged.
        return _catalog_act(
            g, CATALOG_PUBLISH, DesignerAuditAction.CATALOG_AMEND, entry_id,
            lambda me: catalog_service.amend(
                entry_id, req.model_dump(exclude={"note"}), actor=me,
                note=req.note).model_dump(mode="json"),
            anywhere=True)

    @router.post("/api/catalogs/{entry_id}/send_back")
    def catalogs_send_back(entry_id: str, req: CatalogSendBackRequest,
                           g: Grants = Depends(caller)) -> dict:
        return _catalog_act(
            g, CATALOG_REVIEW, DesignerAuditAction.CATALOG_SEND_BACK, entry_id,
            lambda me: catalog_service.send_back(
                entry_id, actor=me, note=req.note).model_dump(mode="json"))

    @router.post("/api/catalogs/{entry_id}/retire")
    def catalogs_retire(entry_id: str, superseded_by: str = "",
                        force: bool = False,
                        g: Grants = Depends(caller)) -> dict:
        return _catalog_act(
            g, CATALOG_REVIEW, DesignerAuditAction.CATALOG_RETIRE, entry_id,
            lambda me: catalog_service.retire(
                entry_id, reviewer=me, superseded_by=superseded_by or None,
                force=force).model_dump(mode="json"))

    @router.delete("/api/catalogs/{entry_id}")
    def catalogs_delete(entry_id: str, g: Grants = Depends(caller)) -> dict:
        return _catalog_act(
            g, CATALOG_DELETE, DesignerAuditAction.CATALOG_DELETE, entry_id,
            lambda me: {"deleted": catalog_service.delete(entry_id, actor=me)})

    @router.post("/api/catalogs/{entry_id}/review")
    def catalogs_review(entry_id: str, status: str, note: str = "",
                        g: Grants = Depends(caller)) -> dict:
        return _catalog_act(
            g, CATALOG_REVIEW, DesignerAuditAction.CATALOG_REVIEW, entry_id,
            lambda me: catalog_service.review(
                entry_id, ApprovalStatus(status), reviewer=me, note=note
            ).model_dump(mode="json"))

    @router.post("/api/catalogs/{entry_id}/entitle")
    def catalogs_entitle(entry_id: str, entitlement: PlatformEntitlement,
                         g: Grants = Depends(caller)) -> dict:
        return _catalog_act(
            g, CATALOG_ENTITLE, DesignerAuditAction.CATALOG_ENTITLE, entry_id,
            lambda me: catalog_service.entitle(
                entry_id, entitlement, actor=me).model_dump(mode="json"))

    @router.post("/api/catalogs/models/permitted")
    def catalogs_permitted_models(policy: dict[str, Any],
                                  environment: str = "development",
                                  g: Grants = Depends(caller)) -> list[dict]:
        """Which catalogued models an agent's policy permits, cheapest first."""
        from ..spec.model import ModelPolicy

        _read_catalog(g)
        permitted = catalog_service.permitted_models(
            ModelPolicy.model_validate(policy), environment=environment)
        return [
            {"id": e.id, "name": e.name, "summary": e.summary,
             "attributes": e.attributes, "status": e.status.value}
            for e in permitted
        ]


    return router
