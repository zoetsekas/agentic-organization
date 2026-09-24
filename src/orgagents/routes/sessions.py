"""Sessions, their events and traces, resuming one, and its human URL."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from ..designer.audit import AuditAction as DesignerAuditAction
from ..runtime_access import RUNTIME_READ, RUNTIME_RESUME, Grants
from .context import ApiContext
from .models import ResumeRequest


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router
    platform = ctx.platform
    caller = ctx.caller
    _authorize = ctx.authorize
    _allowed = ctx.allowed
    _scope_of_agent_id = ctx.scope_of_agent_id
    _require_read_anywhere = ctx.require_read_anywhere
    _acting_as = ctx.acting_as
    _session_scope_check = ctx.session_scope_check

    # -- sessions ----------------------------------------------------------

    @router.get("/api/sessions")
    def list_sessions(agent_id: Optional[str] = None, limit: int = 100,
                      g: Grants = Depends(caller)) -> list[dict]:
        _require_read_anywhere(g)
        if agent_id:
            _authorize(g, RUNTIME_READ, _scope_of_agent_id(agent_id),
                       DesignerAuditAction.RUNTIME_READ, detail={"agent": agent_id})
        scopes: dict[str, Optional[str]] = {}
        out = []
        for s in platform.sessions.list(agent_id, limit):
            if s.agent_id not in scopes:
                scopes[s.agent_id] = _scope_of_agent_id(s.agent_id)
            if g.allows(RUNTIME_READ, scopes[s.agent_id]):
                out.append(s.model_dump() | {"url": platform.sessions.url(s.id)})
        return out

    @router.get("/api/sessions/{session_id}")
    def get_session(session_id: str, g: Grants = Depends(caller)) -> dict:
        _session_scope_check(g, session_id, RUNTIME_READ,
                             DesignerAuditAction.RUNTIME_READ)
        s = platform.sessions.get(session_id)
        return s.model_dump() | {"url": platform.sessions.url(session_id)}

    @router.get("/api/sessions/{session_id}/events")
    def session_events(session_id: str, limit: int = 500,
                       g: Grants = Depends(caller)) -> list[dict]:
        _session_scope_check(g, session_id, RUNTIME_READ,
                             DesignerAuditAction.RUNTIME_READ)
        return [e.model_dump() for e in platform.sessions.events(session_id, limit)]

    @router.get("/api/sessions/{session_id}/trace")
    def session_trace(session_id: str, g: Grants = Depends(caller)) -> dict:
        _session_scope_check(g, session_id, RUNTIME_READ,
                             DesignerAuditAction.RUNTIME_READ)
        trace = platform.sessions.trace(session_id)
        if not trace:
            raise HTTPException(404, "session not found")
        return trace

    @router.post("/api/sessions/{session_id}/resume")
    def resume_session(session_id: str, req: ResumeRequest,
                       g: Grants = Depends(caller)) -> dict:
        scope = _session_scope_check(g, session_id, RUNTIME_RESUME,
                                     DesignerAuditAction.RUNTIME_RESUME)
        try:
            r = platform.runtime.resume(session_id, req.response,
                                        _acting_as(g, req.actor))
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        _allowed(g, DesignerAuditAction.RUNTIME_RESUME, RUNTIME_RESUME, scope,
                 {"session": session_id})
        return {"output": r.output, "state": r.state.value, "session_url": r.session_url}

    @router.get("/sessions/{session_id}")
    def session_page(session_id: str) -> Any:
        """Human-addressable session URL; the UI deep-links to it.

        A browser following a link carries no bearer token, so this does not
        look the session up: it says nothing about whether it exists, and the
        UI's authenticated read decides what is shown (ADR-0116).
        """
        return RedirectResponse(f"/ui/#/sessions/{session_id}")


    return router
