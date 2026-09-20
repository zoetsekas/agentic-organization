"""The task port: how assigned work reaches an agent (ADR-0057).

The surface is deliberately boring — list assigned work, get one, claim it,
report progress, complete, fail, comment, and link the one run it produced.
It is narrow on purpose. A port written to one imagined backend is worse than
a port that refuses to guess: the first real adapter will move this shape, and
a small shape moves cheaply.

Creating a task is **not** on the port. Work is assigned by a person in the
tool they already use; our side receives it. A backend used as a test double
may offer its own way to open a task, but that is scaffolding, not port.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .model import TaskRecord, TaskState


class PrincipalKind(str, Enum):
    """What kind of identity a backend can give the thing that acts on it."""

    NONE = "none"        # anonymous or shared token; nobody is accountable
    HUMAN = "human"      # a person's account, borrowed by software
    SERVICE = "service"  # a non-human principal of its own


class BackendCapabilities(BaseModel):
    """What a backend declares about itself, checked once at bind time.

    Declared rather than probed because rule 2 has to be answerable *before*
    the first call. A backend that discovers at call time that it only has a
    person's credentials has already made the audit trail a lie.
    """

    #: Binding-layer name. Product names live here and nowhere in the spec.
    name: str = ""
    principal_kind: PrincipalKind = PrincipalKind.NONE
    #: The non-human principal this backend will act as, if it has one.
    agent_principal_id: str = ""
    #: The single tenant this backend instance serves (ADR-0050, rule 6).
    tenant_id: str = ""
    #: Whether a non-human principal can be the assignee of a task at all.
    supports_non_human_assignee: bool = False
    notes: str = ""


class TaskBackendError(RuntimeError):
    """Base class for refusals raised by a backend or the port's own rules."""


class UnknownTask(KeyError):
    """Raised when a task id is not known to the backend."""


class RunAlreadyLinked(TaskBackendError):
    """Raised when a second run is linked to a task (ADR-0057 rule 3).

    One task, one traceable execution. A second run against the same task
    would leave a human unable to say which execution answered their request,
    which is the whole point of writing the session id back.
    """

    def __init__(self, task_id: str, existing: str, attempted: str) -> None:
        super().__init__(
            f"task '{task_id}' is already linked to run '{existing}'; "
            f"refusing to link '{attempted}' — a task maps to at most one run"
        )
        self.task_id = task_id
        self.existing = existing
        self.attempted = attempted


class TenantMismatch(TaskBackendError):
    """Raised when a task is touched across a tenant boundary (ADR-0050)."""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(
            f"task belongs to tenant '{actual}', not '{expected}'; a task "
            "backend instance serves exactly one tenant"
        )
        self.expected = expected
        self.actual = actual


@runtime_checkable
class TaskPort(Protocol):
    """What every task backend must provide. Nine methods, no more."""

    def capabilities(self) -> BackendCapabilities:
        """What this backend is, and what identity it can act as."""
        ...

    def get(self, task_id: str) -> Optional[TaskRecord]:
        ...

    def assigned(
        self,
        agent_id: str,
        *,
        state: Optional[TaskState] = None,
        limit: int = 100,
    ) -> list[TaskRecord]:
        """The work assigned to this agent, newest first."""
        ...

    def claim(self, task_id: str, *, agent_id: str) -> TaskRecord:
        ...

    def link_run(self, task_id: str, *, session_id: str) -> TaskRecord:
        """Write the run's session id back. Refuses a second run."""
        ...

    def progress(self, task_id: str, *, note: str) -> TaskRecord:
        ...

    def complete(self, task_id: str, *, summary: str = "") -> TaskRecord:
        ...

    def fail(self, task_id: str, *, reason: str) -> TaskRecord:
        ...

    def comment(self, task_id: str, *, author: str, body: str) -> TaskRecord:
        ...
