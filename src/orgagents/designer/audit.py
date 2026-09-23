"""An append-only record of what people did in the designer (ADR-0043).

Revision history answers "what does this system look like now, and what did it
look like before". It cannot answer "who tried to delete this and was refused",
"who broke my lock", or "who turned merge-on-conflict on last Tuesday" —
because none of those produce a revision. This module answers those.

Two properties shape it. It is **append-only**: there is no update and no
delete on this API, so the only way to change history is to reach past the
product into the storage backend, which is exactly where that decision
belongs. And it rides the existing `Repository` protocol, so the log lives
wherever the designs live — memory, a git-tracked folder, or the relational
store — rather than in a fourth place a deployment has to remember to back up.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field

from ..ids import new_id, now_iso

logger = logging.getLogger(__name__)


class AuditAction(str, Enum):
    """What was attempted. One value per thing a reviewer would ask about."""

    SYSTEM_VIEW = "system.view"
    SYSTEM_CREATE = "system.create"
    SYSTEM_SAVE = "system.save"
    SYSTEM_DELETE = "system.delete"
    SYSTEM_RESTORE = "system.restore"
    SYSTEM_PUBLISH = "system.publish"
    MERGE_APPLIED = "merge.applied"
    MERGE_CONFLICT = "merge.conflict"
    MERGE_RESOLVED = "merge.resolved"
    LOCK_ACQUIRE = "lock.acquire"
    LOCK_RELEASE = "lock.release"
    LOCK_BREAK = "lock.break"
    LOCK_EXPIRE = "lock.expire"
    SETTINGS_UPDATE = "designer.settings"
    MEMBER_ADD = "workspace.member.add"
    MEMBER_REMOVE = "workspace.member.remove"
    WORKSPACE_CREATE = "workspace.create"
    WORKSPACE_UPDATE = "workspace.update"
    WORKSPACE_DELETE = "workspace.delete"
    SYSTEM_IMPORT = "system.import"
    AUDIT_READ = "audit.read"
    # Authentication happens before any of the above, and its failures leave no
    # other trace (ADR-0047). Successes are not logged: they are every request.
    AUTH_FAILED = "auth.failed"


class AuditOutcome(str, Enum):
    """How it ended. `DENIED` is the reason this log exists at all."""

    SUCCESS = "success"
    DENIED = "denied"
    CONFLICT = "conflict"
    FAILED = "failed"


class AuditEvent(BaseModel):
    """One thing that happened, written once and never rewritten."""

    id: str = Field(default_factory=lambda: new_id("aud"))
    timestamp: str = Field(default_factory=now_iso)
    actor: str = ""
    actor_name: str = ""
    action: AuditAction
    outcome: AuditOutcome = AuditOutcome.SUCCESS
    workspace_id: str = ""
    system_id: str = ""
    # "what changed", without duplicating the revision itself.
    version_before: Optional[int] = None
    version_after: Optional[int] = None
    merged: bool = False
    conflict_paths: list[str] = Field(default_factory=list)
    lock_target: str = ""
    lock_holder: str = ""
    permission: str = ""
    reason: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def order_key(self) -> tuple[str, str]:
        """Stable total order: time first, id to break ties deterministically."""
        return (self.timestamp, self.id)


class AuditSink(Protocol):
    """The slice of `Repository` an audit log needs."""

    def append_audit(self, event: AuditEvent) -> AuditEvent: ...
    def audit_events(self, system_id: Optional[str] = None,
                     limit: int = 1000) -> list[AuditEvent]: ...


class AuditLog:
    """Append and query. Deliberately no update and no delete (ADR-0043)."""

    def __init__(self, repository: AuditSink) -> None:
        self.repository = repository

    def record(self, action: AuditAction, actor: Any = None, **fields: Any
               ) -> Optional[AuditEvent]:
        """Write an event.

        Auditing must not take down the thing it observes — a full disk should
        not stop an editor saving their work — so a failure here is swallowed
        for the caller. It is *not* swallowed silently: it goes to the logger at
        ERROR with the event that was lost, which is what an operator needs to
        notice that the log has gaps. A deployment that must fail closed raises
        the logging config, not this line.
        """
        event = AuditEvent(
            action=action,
            actor=getattr(actor, "user_id", "") or "",
            actor_name=getattr(actor, "label", "") or "",
            **fields,
        )
        try:
            return self.repository.append_audit(event)
        except Exception:
            logger.error("designer audit event was not persisted: %s",
                         event.model_dump(mode="json"), exc_info=True)
            return None

    def query(
        self,
        *,
        system_id: Optional[str] = None,
        actor: Optional[str] = None,
        action: Optional[AuditAction] = None,
        outcome: Optional[AuditOutcome] = None,
        workspace_ids: Optional[set[str]] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 200,
    ) -> list[AuditEvent]:
        """Newest first. `since` is inclusive, `until` exclusive, both ISO-8601."""
        events = self.repository.audit_events(system_id=system_id, limit=5000)
        found = [
            e for e in events
            if (actor is None or e.actor == actor)
            and (action is None or e.action is action)
            and (outcome is None or e.outcome is outcome)
            and (workspace_ids is None or e.workspace_id in workspace_ids)
            and (since is None or e.timestamp >= since)
            and (until is None or e.timestamp < until)
        ]
        found.sort(key=lambda e: e.order_key, reverse=True)
        return found[:limit]
