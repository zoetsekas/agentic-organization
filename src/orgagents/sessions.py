"""Agent sessions: creation, addressing, tracing and replay.

Every run of an agent is a session with a unique id, a URL, an append-only
event trace and a rollup of usage. Sub-agent runs are child sessions linked to
the parent, so a delegation tree is reconstructable from the store alone.
"""
from __future__ import annotations

from typing import Any, Optional

from .ids import new_id, now_iso
from .models import AgentSession, SessionEvent, SessionState
from .store import EVENTS, SESSIONS, Store

#: The states a session never leaves. A handle held by a leader is settled
#: exactly when its child session reaches one of these (ADR-0093 rule 5).
TERMINAL_STATES = frozenset({
    SessionState.COMPLETED,
    SessionState.FAILED,
    SessionState.ARCHIVED,
})


class SessionManager:
    def __init__(self, store: Store, base_url: str = "http://localhost:8000") -> None:
        self.store = store
        self.base_url = base_url.rstrip("/")

    # -- lifecycle ---------------------------------------------------------

    def create(
        self,
        agent_id: str,
        *,
        title: str = "",
        created_by: str = "",
        parent_session_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> AgentSession:
        parent = self.get(parent_session_id) if parent_session_id else None
        session = AgentSession(
            agent_id=agent_id,
            title=title or f"session for {agent_id}",
            created_by=created_by,
            parent_session_id=parent_session_id,
            trace_id=parent.trace_id if parent else new_id("trc"),
            tags=tags or [],
        )
        self.store.put(SESSIONS, session, parent=agent_id, name=session.title)
        self.log(session.id, "session_created", actor=created_by, payload={"agent_id": agent_id})
        return session

    def get(self, session_id: str) -> Optional[AgentSession]:
        return self.store.get(SESSIONS, session_id, AgentSession)

    def list(self, agent_id: Optional[str] = None, limit: int = 100) -> list[AgentSession]:
        return self.store.list(SESSIONS, AgentSession, parent=agent_id, limit=limit)

    def children(self, session_id: str) -> list[AgentSession]:
        return [
            s
            for s in self.store.list(SESSIONS, AgentSession, limit=2000)
            if s.parent_session_id == session_id
        ]

    def outstanding(self, session_id: str) -> list[AgentSession]:
        """Child sessions of `session_id` that have not settled.

        Settled means one of the terminal states in :data:`TERMINAL_STATES`.
        ``WAITING_HUMAN`` is deliberately **not** terminal: a child parked on
        an approval is still outstanding work, and a leader that treated it as
        finished would report a task done that nobody has decided (ADR-0093).

        This is the whole store behind asynchronous delegation. There is no
        second work-item table: parent, state, spend and audit already live on
        the session, so the tree answers "what have I got outstanding, and
        what is stuck?" without inventing anything to keep in sync with it.
        """
        return [c for c in self.children(session_id)
                if c.state not in TERMINAL_STATES]

    def url(self, session_id: str) -> str:
        return f"{self.base_url}/sessions/{session_id}"

    def set_state(self, session_id: str, state: SessionState) -> Optional[AgentSession]:
        s = self.get(session_id)
        if not s:
            return None
        s.state = state
        s.updated_at = now_iso()
        self.store.put(SESSIONS, s, parent=s.agent_id)
        self.log(session_id, "state_changed", payload={"state": state.value})
        return s

    def record_usage(self, session_id: str, *, tokens: int = 0, cost_usd: float = 0.0,
                     turns: int = 0) -> None:
        s = self.get(session_id)
        if not s:
            return
        s.token_usage += tokens
        s.cost_usd += cost_usd
        s.turn_count += turns
        s.updated_at = now_iso()
        self.store.put(SESSIONS, s, parent=s.agent_id)

    # -- tracing -----------------------------------------------------------

    def log(
        self,
        session_id: str,
        event_type: str,
        *,
        actor: str = "",
        payload: Optional[dict[str, Any]] = None,
        parent_span_id: str = "",
    ) -> SessionEvent:
        session = self.get(session_id)
        event = SessionEvent(
            session_id=session_id,
            type=event_type,
            actor=actor,
            payload=payload or {},
            trace_id=session.trace_id if session else "",
            span_id=new_id("spn"),
            parent_span_id=parent_span_id,
        )
        self.store.put(EVENTS, event, parent=session_id, name=event_type)
        return event

    def events(self, session_id: str, limit: int = 500) -> list[SessionEvent]:
        events = self.store.list(EVENTS, SessionEvent, parent=session_id, limit=limit)
        return sorted(events, key=lambda e: e.ts)

    def trace(self, session_id: str) -> dict[str, Any]:
        """The full delegation tree and event stream for one session."""
        session = self.get(session_id)
        if session is None:
            return {}
        return {
            "session": session.model_dump(),
            "url": self.url(session_id),
            "events": [e.model_dump() for e in self.events(session_id)],
            "children": [self.trace(c.id) for c in self.children(session_id)],
        }
