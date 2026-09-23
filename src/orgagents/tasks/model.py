"""What a task is, and the only ways it may move (ADR-0057, WS-031 M1).

A task is work a human assigned to an agent. The backend that stores it is a
binding concern, so this record carries the fields no backend can be relied on
to hold — which agent, which run, which tenant, which mission, where approval
got to — alongside the obvious ones.

The lifecycle is an explicit table for the same reason the deployment machine
is (`fabric/deployments.py`): an illegal move is refused, never coerced into
the nearest legal state. A control plane that repairs its own inputs stops
being a record of what happened.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ..ids import new_id, now_iso

TASKS = "tasks"


class TaskState(str, Enum):
    OPEN = "open"                # assigned, nobody has picked it up
    CLAIMED = "claimed"          # the agent has taken it
    IN_PROGRESS = "in_progress"  # the agent is working, and says so
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskActor(str, Enum):
    """Who is making a move. Not a job title — a side of the boundary.

    Intent belongs to the people, execution belongs to the agent (ADR-0057
    rule 4), and the transition table is where that split is written down.
    """

    HUMAN = "human"    # a paired human, acting in their own tool
    AGENT = "agent"    # the assignee, acting as itself


class TaskPriority(str, Enum):
    """What the assigning humans said matters, in four neutral buckets.

    Read from the backend and never written by this platform. A task lives in
    the tool the people who assigned it already use, where it already has a
    priority they set and look at; a second one owned here would diverge from
    it the moment either was edited, and the one they trust is not ours
    (ADR-0097).

    `UNKNOWN` is the important member. A backend that does not report priority
    yields it, never `NORMAL`: "nobody said" and "somebody said it is
    ordinary" are different facts, and a leader sorting by the second when it
    has the first is sorting by an assumption.
    """

    UNKNOWN = "unknown"
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    @property
    def rank(self) -> int:
        """Sort key: urgent first, unknown last.

        Unknown sorts last rather than in the middle, so an unreported
        priority never displaces something a person actually marked.
        """
        return {"urgent": 0, "high": 1, "normal": 2, "low": 3,
                "unknown": 4}[self.value]


class ApprovalState(str, Enum):
    """Where this piece of work got to in the approval policy (ADR-0026)."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


#: Legal transitions, and who may make each one. A side is listed only where it
#: is *sufficient*. Note what is absent: an agent cannot cancel its own work,
#: and it cannot reopen a task it failed — both are the assigner's call.
TRANSITIONS: dict[tuple[TaskState, TaskState], frozenset[TaskActor]] = {
    (TaskState.OPEN, TaskState.CLAIMED): frozenset({TaskActor.AGENT}),
    (TaskState.OPEN, TaskState.CANCELLED): frozenset({TaskActor.HUMAN}),
    (TaskState.CLAIMED, TaskState.IN_PROGRESS): frozenset({TaskActor.AGENT}),
    # Released: an agent that cannot do the work puts it back rather than
    # failing it, and a human may take it back at any point.
    (TaskState.CLAIMED, TaskState.OPEN): frozenset({TaskActor.AGENT, TaskActor.HUMAN}),
    (TaskState.CLAIMED, TaskState.FAILED): frozenset({TaskActor.AGENT}),
    (TaskState.CLAIMED, TaskState.CANCELLED): frozenset({TaskActor.HUMAN}),
    (TaskState.IN_PROGRESS, TaskState.DONE): frozenset({TaskActor.AGENT}),
    (TaskState.IN_PROGRESS, TaskState.FAILED): frozenset({TaskActor.AGENT}),
    (TaskState.IN_PROGRESS, TaskState.CANCELLED): frozenset({TaskActor.HUMAN}),
    # Reopening is how the two sides come to disagree, which is why it is
    # legal: the divergence report exists to surface exactly this.
    (TaskState.DONE, TaskState.OPEN): frozenset({TaskActor.HUMAN}),
    (TaskState.FAILED, TaskState.OPEN): frozenset({TaskActor.HUMAN}),
}

#: Cancelled is terminal: re-asking for cancelled work is a new task, so the
#: history of the work that was done stays the history of the work that was done.
TERMINAL_STATES = frozenset({TaskState.CANCELLED})

#: States in which the work is no longer expected to move on our side.
CLOSED_STATES = frozenset({TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED})


class IllegalTaskTransition(ValueError):
    """Raised for a transition that is not in the machine."""

    def __init__(self, source: TaskState, target: TaskState) -> None:
        super().__init__(
            f"'{source.value}' -> '{target.value}' is not a legal task transition"
        )
        self.source = source
        self.target = target


class TaskTransitionDenied(PermissionError):
    """Raised for a legal transition attempted from the wrong side."""

    def __init__(
        self,
        source: TaskState,
        target: TaskState,
        actor: TaskActor,
        allowed: frozenset[TaskActor],
    ) -> None:
        names = ", ".join(sorted(a.value for a in allowed))
        super().__init__(
            f"'{actor.value}' may not move a task from '{source.value}' to "
            f"'{target.value}'; allowed: {names}"
        )
        self.source = source
        self.target = target
        self.actor = actor
        self.allowed = allowed


def allowed_transitions(state: TaskState) -> dict[TaskState, frozenset[TaskActor]]:
    return {t: who for (s, t), who in TRANSITIONS.items() if s is state}


def check_transition(source: TaskState, target: TaskState, actor: TaskActor) -> None:
    """Refuse anything off the machine. Callers move the record themselves."""
    allowed = TRANSITIONS.get((source, target))
    if allowed is None:
        raise IllegalTaskTransition(source, target)
    if actor not in allowed:
        raise TaskTransitionDenied(source, target, actor, allowed)


class TaskEvent(BaseModel):
    """One recorded move or note. Append-only: the history is the audit trail."""

    at: str = Field(default_factory=now_iso)
    kind: str = "transition"   # transition | progress | run_linked
    actor: str = ""
    side: TaskActor = TaskActor.AGENT
    source: Optional[TaskState] = None
    target: Optional[TaskState] = None
    detail: str = ""


class TaskComment(BaseModel):
    """A note on the task, by either side."""

    id: str = Field(default_factory=lambda: new_id("tcm"))
    at: str = Field(default_factory=now_iso)
    author: str
    side: TaskActor = TaskActor.AGENT
    body: str = ""


class TaskRecord(BaseModel):
    """Work a human assigned to an agent, and what we know about it.

    `external_id` is the backend's own key. Everything above it is ours,
    because a task service cannot be relied on to hold an agent id, a session
    id, a tenant or an approval state — and rule 6 means the tenant is not
    optional bookkeeping.
    """

    id: str = Field(default_factory=lambda: new_id("tsk"))
    external_id: str = ""
    #: A task backend instance belongs to one tenant (ADR-0050, rule 6).
    tenant_id: str
    agent_id: str
    #: The one run this task produced, if it has produced one (rule 3).
    session_id: Optional[str] = None
    mission_id: str = ""
    approval_state: ApprovalState = ApprovalState.NOT_REQUIRED
    title: str = ""
    #: What the assigning humans said matters (ADR-0097). Observed, never
    #: written: there is no way to set this through the port, on purpose.
    priority: TaskPriority = TaskPriority.UNKNOWN
    #: The backend's own value, kept verbatim. Four buckets cannot hold a
    #: five-level scheme, and a report that lost the backend's own label to
    #: say `high` cannot be checked against the tool the humans actually use.
    priority_raw: str = ""
    #: Written by a human in another system: untrusted input (ADR-0035).
    description: str = ""
    assigner: str = ""            # the paired human's contact/user id
    state: TaskState = TaskState.OPEN
    events: list[TaskEvent] = Field(default_factory=list)
    comments: list[TaskComment] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_closed(self) -> bool:
        return self.state in CLOSED_STATES

    def prompt(self) -> str:
        """The assigned work as text, before any boundary has looked at it."""
        return f"{self.title}\n\n{self.description}".strip()
