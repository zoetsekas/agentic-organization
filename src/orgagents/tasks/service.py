"""Turning assigned work into a run, and back again (WS-031 M3/M4).

One task becomes at most one agent run, and the run's session id is written
back to the task so a human can get from the work they asked for to what the
agent actually did (ADR-0057 rule 3).

Nothing here screens the task's text itself: the description is handed to the
runtime as a prompt, and the runtime's existing input guardrail is what it
crosses (ADR-0035). A second, task-shaped guardrail path would be a second
place for the boundary to be wrong.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ..models import SessionState
from .authorization import AgentPairing, AssignmentDenied
from .binding import BoundTaskBackend
from .divergence import Divergence, detect
from .model import TaskRecord, TaskState
from .port import RunAlreadyLinked, TenantMismatch, UnknownTask


class TaskRunner(Protocol):
    """The slice of `AgentRuntime` this needs, and no more."""

    sessions: Any

    def run(self, agent_id: str, prompt: str, *, created_by: str = "") -> Any: ...


@dataclass
class TaskRun:
    """What happened to one assigned task."""

    task: TaskRecord
    session_id: str
    state: SessionState
    output: str = ""
    error: Optional[str] = None

    @property
    def refused(self) -> bool:
        return self.error is not None


class TaskService:
    """Drives the port on behalf of one agent runtime."""

    def __init__(
        self,
        backend: BoundTaskBackend,
        runtime: TaskRunner,
        *,
        pairings: Optional[dict[str, AgentPairing]] = None,
    ) -> None:
        self.backend = backend
        self.port = backend.port
        self.runtime = runtime
        self.pairings = dict(pairings or {})

    def pair(self, pairing: AgentPairing) -> None:
        self.pairings[pairing.agent_id] = pairing

    # -- reading -----------------------------------------------------------

    def inbox(self, agent_id: str) -> list[TaskRecord]:
        return self.port.assigned(agent_id, state=TaskState.OPEN)

    def actionable(self, agent_id: str) -> list[TaskRecord]:
        """Open work whose assigner may actually direct this agent."""
        pairing = self.pairings.get(agent_id)
        if pairing is None:
            return []
        return [t for t in self.inbox(agent_id) if pairing.may_assign(t.assigner)]

    # -- running -----------------------------------------------------------

    def start(self, task_id: str) -> TaskRun:
        """Claim a task, run it once, and write the session id back."""
        record = self.port.get(task_id)
        if record is None:
            raise UnknownTask(f"no such task '{task_id}'")
        if record.tenant_id != self.backend.tenant_id:
            raise TenantMismatch(self.backend.tenant_id, record.tenant_id)

        pairing = self.pairings.get(record.agent_id)
        if pairing is None:
            raise AssignmentDenied(
                record.agent_id, record.assigner,
                "no pairing is known for this agent, so nobody is authorized "
                "to direct it",
            )
        pairing.check(record.assigner)

        # At most one run per task, checked before anything is spent on it.
        if record.session_id:
            raise RunAlreadyLinked(task_id, record.session_id, "<new run>")

        record = self.port.claim(task_id, agent_id=record.agent_id)
        record = self.port.progress(task_id, note="starting agent run")

        result = self.runtime.run(
            record.agent_id, record.prompt(), created_by=record.assigner
        )
        session_id = getattr(result, "session_id", "") or ""
        error = getattr(result, "error", None)
        output = getattr(result, "output", "") or ""
        state = getattr(result, "state", SessionState.FAILED)

        # Linked before the outcome is recorded: a failed run is still the run
        # this task produced, and is still the only one it may produce.
        if session_id:
            record = self.port.link_run(task_id, session_id=session_id)

        if error:
            record = self.port.fail(task_id, reason=error)
        else:
            record = self.port.complete(task_id, summary=output[:2000])

        return TaskRun(
            task=record, session_id=session_id, state=state,
            output=output, error=error,
        )

    # -- divergence (rule 4) -----------------------------------------------

    def divergences(self, agent_id: Optional[str] = None) -> list[Divergence]:
        """Report disagreements. Nothing here changes any state."""
        records: list[TaskRecord] = []
        if agent_id is not None:
            records = self.port.assigned(agent_id, limit=1000)
        else:
            for aid in {p.agent_id for p in self.pairings.values()}:
                records.extend(self.port.assigned(aid, limit=1000))

        found: list[Divergence] = []
        for record in records:
            if not record.session_id:
                continue
            session = self.runtime.sessions.get(record.session_id)
            divergence = detect(record, session)
            if divergence is not None:
                found.append(divergence)
        return found
