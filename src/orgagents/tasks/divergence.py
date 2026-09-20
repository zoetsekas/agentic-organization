"""Disagreement between what the task says and what the run did (rule 4).

Intent belongs to the task service; execution belongs to us. When the two
disagree — a task closed while its run is live, a run finished against a task
somebody reopened — the disagreement is *surfaced*, never silently reconciled.
A control plane that quietly makes its own state match somebody else's is a
control plane nobody can trust.

So there is no `reconcile()` here, and there is not going to be one. The output
is a report for a person.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ..ids import now_iso
from ..models import AgentSession, SessionState
from .model import TaskRecord, TaskState

#: A run that has not reached an end state: something may still be happening.
LIVE_SESSION_STATES = frozenset(
    {SessionState.CREATED, SessionState.RUNNING, SessionState.WAITING_HUMAN}
)
FINISHED_SESSION_STATES = frozenset(
    {SessionState.COMPLETED, SessionState.FAILED, SessionState.ARCHIVED}
)
OPEN_TASK_STATES = frozenset(
    {TaskState.OPEN, TaskState.CLAIMED, TaskState.IN_PROGRESS}
)


class DivergenceKind(str, Enum):
    CLOSED_TASK_LIVE_RUN = "closed_task_live_run"
    FINISHED_RUN_OPEN_TASK = "finished_run_open_task"
    MISSING_RUN = "missing_run"


class Divergence(BaseModel):
    """One disagreement, in the terms a human would need to resolve it."""

    kind: DivergenceKind
    task_id: str
    agent_id: str
    tenant_id: str
    session_id: str = ""
    task_state: TaskState
    session_state: Optional[SessionState] = None
    detail: str = ""
    detected_at: str = Field(default_factory=now_iso)

    def describe(self) -> str:
        return f"{self.kind.value}: {self.detail}"


def detect(
    record: TaskRecord, session: Optional[AgentSession]
) -> Optional[Divergence]:
    """Compare one task with its run. `None` means the two agree."""
    if not record.session_id:
        # No run yet is not a disagreement: it is work nobody has started.
        return None

    def made(kind: DivergenceKind, detail: str) -> Divergence:
        return Divergence(
            kind=kind,
            task_id=record.id,
            agent_id=record.agent_id,
            tenant_id=record.tenant_id,
            session_id=record.session_id or "",
            task_state=record.state,
            session_state=session.state if session else None,
            detail=detail,
        )

    if session is None:
        return made(
            DivergenceKind.MISSING_RUN,
            f"task names run '{record.session_id}', which this runtime has no "
            "record of",
        )
    if record.is_closed and session.state in LIVE_SESSION_STATES:
        return made(
            DivergenceKind.CLOSED_TASK_LIVE_RUN,
            f"task is '{record.state.value}' but its run is "
            f"'{session.state.value}' — the work may still be running",
        )
    if record.state in OPEN_TASK_STATES and session.state in FINISHED_SESSION_STATES:
        return made(
            DivergenceKind.FINISHED_RUN_OPEN_TASK,
            f"run is '{session.state.value}' but the task is "
            f"'{record.state.value}' — it was reopened, or the result never "
            "got back",
        )
    return None
