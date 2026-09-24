"""Metrics, alerts and per-agent health."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..designer.audit import AuditAction as DesignerAuditAction
from ..runtime_access import OPS_ACK, RUNTIME_READ, Grants
from .context import ApiContext


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router
    platform = ctx.platform
    caller = ctx.caller
    _authorize = ctx.authorize
    _allowed = ctx.allowed
    _scope_of_agent_id = ctx.scope_of_agent_id
    _visible_agent_ids = ctx.visible_agent_ids
    _require_read_anywhere = ctx.require_read_anywhere

    # -- operations --------------------------------------------------------

    @router.get("/api/ops/metrics")
    def metrics(g: Grants = Depends(caller)) -> dict:
        _require_read_anywhere(g)
        if g.allows(RUNTIME_READ, None):
            return platform.obs.metrics()
        # Scoped to the agents of the caller's workspaces: totals over
        # somebody else's sessions are somebody else's data.
        return platform.obs.metrics(agent_ids=_visible_agent_ids(g))

    @router.get("/api/ops/alerts")
    def alerts(include_acknowledged: bool = False,
               g: Grants = Depends(caller)) -> list[dict]:
        _require_read_anywhere(g)
        platform.obs.evaluate_alerts()
        # An alert about no agent is about the installation.
        return [a.model_dump() for a in platform.obs.alerts(include_acknowledged)
                if g.allows(RUNTIME_READ,
                            _scope_of_agent_id(a.agent_id) if a.agent_id else None)]

    @router.post("/api/ops/alerts/{alert_id}/ack")
    def ack_alert(alert_id: str, g: Grants = Depends(caller)) -> dict:
        existing = next((a for a in platform.obs.alerts(True) if a.id == alert_id),
                        None)
        if existing is None:
            raise HTTPException(404, "alert not found")
        scope = _scope_of_agent_id(existing.agent_id) if existing.agent_id else None
        _authorize(g, OPS_ACK, scope, DesignerAuditAction.OPS_ACK,
                   detail={"alert": alert_id})
        a = platform.obs.acknowledge(alert_id)
        if a is None:
            raise HTTPException(404, "alert not found")
        _allowed(g, DesignerAuditAction.OPS_ACK, OPS_ACK, scope, {"alert": alert_id})
        return a.model_dump()

    @router.get("/api/ops/health/{agent_id}")
    def agent_health(agent_id: str, g: Grants = Depends(caller)) -> dict:
        _authorize(g, RUNTIME_READ, _scope_of_agent_id(agent_id),
                   DesignerAuditAction.RUNTIME_READ, detail={"agent": agent_id})
        return platform.obs.agent_health(agent_id)


    return router
