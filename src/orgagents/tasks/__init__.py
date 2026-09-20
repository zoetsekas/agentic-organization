"""Human-assigned work reaching agents through a port (ADR-0057, WS-031).

The port and its record are here; the product is not, and deliberately so —
the decisive capability of the leading candidate could not be verified, so the
choice waits for evidence while everything that does not depend on it exists.
"""
from .authorization import ASSIGNING_ROLES, AgentPairing, AssignmentDenied
from .binding import BackendRefused, BoundTaskBackend, bind
from .divergence import Divergence, DivergenceKind, detect
from .local import LocalTaskBackend
from .model import (
    TASKS,
    ApprovalState,
    IllegalTaskTransition,
    TaskActor,
    TaskComment,
    TaskEvent,
    TaskRecord,
    TaskState,
    TaskTransitionDenied,
    TRANSITIONS,
    allowed_transitions,
    check_transition,
)
from .port import (
    BackendCapabilities,
    PrincipalKind,
    RunAlreadyLinked,
    TaskBackendError,
    TaskPort,
    TenantMismatch,
    UnknownTask,
)
from .service import TaskRun, TaskRunner, TaskService

__all__ = [
    "ASSIGNING_ROLES",
    "AgentPairing",
    "AssignmentDenied",
    "ApprovalState",
    "BackendCapabilities",
    "BackendRefused",
    "BoundTaskBackend",
    "Divergence",
    "DivergenceKind",
    "IllegalTaskTransition",
    "LocalTaskBackend",
    "PrincipalKind",
    "RunAlreadyLinked",
    "TASKS",
    "TRANSITIONS",
    "TaskActor",
    "TaskBackendError",
    "TaskComment",
    "TaskEvent",
    "TaskPort",
    "TaskRecord",
    "TaskRun",
    "TaskRunner",
    "TaskService",
    "TaskState",
    "TaskTransitionDenied",
    "TenantMismatch",
    "UnknownTask",
    "allowed_transitions",
    "bind",
    "check_transition",
    "detect",
]
