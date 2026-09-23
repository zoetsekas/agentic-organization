"""A local reference backend over the existing `Store` (WS-031 M1).

It exists to exercise the port end to end without a vendor, and for nothing
else. WS-031 is explicit: *if the reference backend grows a board, we have gone
wrong.* So there are no projects, no columns, no labels, no priorities, no
sorting and no queries beyond "what is assigned to this agent" — anything more
would be us writing a task manager by accident.

The human side of the boundary (opening, cancelling and reopening work) is on
this class but deliberately **not** on `TaskPort`: in a real deployment those
happen in the tool the person already uses.
"""
from __future__ import annotations

from typing import Optional

from ..ids import now_iso
from ..store import Store
from .model import (
    TASKS,
    ApprovalState,
    TaskActor,
    TaskComment,
    TaskEvent,
    TaskPriority,
    TaskRecord,
    TaskState,
    check_transition,
)
from .port import (
    BackendCapabilities,
    PrincipalKind,
    RunAlreadyLinked,
    TenantMismatch,
    UnknownTask,
)


class LocalTaskBackend:
    """The reference implementation of `TaskPort`, backed by `Store`."""

    def __init__(
        self,
        store: Store,
        *,
        tenant_id: str,
        agent_principal_id: str = "orgagents-tasks",
    ) -> None:
        self.store = store
        self.tenant_id = tenant_id
        # The reference backend mints its own non-human principal, because
        # that is the bar rule 2 sets for any backend we would bind.
        self._principal = agent_principal_id

    # -- capabilities ------------------------------------------------------

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name="local",
            principal_kind=(
                PrincipalKind.SERVICE if self._principal else PrincipalKind.NONE
            ),
            agent_principal_id=self._principal,
            tenant_id=self.tenant_id,
            supports_non_human_assignee=bool(self._principal),
            # The reference backend reports priority so that the conformance
            # suite's rules about it are exercised somewhere. A suite whose
            # only implementation skips a rule is not holding that rule.
            reports_priority=True,
            notes="reference backend; exercises the port, stores nothing else",
        )

    # -- the human side, which a real backend owns -------------------------

    def open_task(
        self,
        *,
        agent_id: str,
        assigner: str,
        title: str = "",
        description: str = "",
        mission_id: str = "",
        approval_state: ApprovalState = ApprovalState.NOT_REQUIRED,
        external_id: str = "",
        priority: TaskPriority = TaskPriority.NORMAL,
        priority_raw: str = "",
    ) -> TaskRecord:
        """Stand-in for a person creating work in their own tool.

        Priority arrives here, on the *human* side, and never through the
        port: this is the person setting it in their own tool, which is the
        only place it is ever set (ADR-0097).
        """
        record = TaskRecord(
            tenant_id=self.tenant_id,
            agent_id=agent_id,
            assigner=assigner,
            title=title,
            description=description,
            mission_id=mission_id,
            approval_state=approval_state,
            external_id=external_id,
            priority=priority,
            priority_raw=priority_raw or priority.value,
        )
        return self._save(record)

    def cancel(self, task_id: str, *, actor: str, reason: str = "") -> TaskRecord:
        return self._move(
            task_id, TaskState.CANCELLED, TaskActor.HUMAN, actor=actor, detail=reason
        )

    def reopen(self, task_id: str, *, actor: str, reason: str = "") -> TaskRecord:
        return self._move(
            task_id, TaskState.OPEN, TaskActor.HUMAN, actor=actor, detail=reason
        )

    # -- the port ----------------------------------------------------------

    def get(self, task_id: str) -> Optional[TaskRecord]:
        record = self.store.get(TASKS, task_id, TaskRecord)
        if record is not None and record.tenant_id != self.tenant_id:
            # Reading across the boundary is already the cross-tenant channel
            # rule 6 forbids, so it is refused rather than filtered.
            raise TenantMismatch(self.tenant_id, record.tenant_id)
        return record

    def assigned(
        self,
        agent_id: str,
        *,
        state: Optional[TaskState] = None,
        limit: int = 100,
    ) -> list[TaskRecord]:
        records = [
            r
            for r in self.store.list(TASKS, TaskRecord, parent=agent_id, limit=limit)
            if r.tenant_id == self.tenant_id
        ]
        if state is not None:
            records = [r for r in records if r.state is state]
        return records

    def claim(self, task_id: str, *, agent_id: str) -> TaskRecord:
        record = self._require(task_id)
        if record.agent_id != agent_id:
            raise PermissionError(
                f"task '{task_id}' is assigned to '{record.agent_id}', "
                f"not '{agent_id}'"
            )
        return self._move(
            task_id, TaskState.CLAIMED, TaskActor.AGENT, actor=agent_id
        )

    def link_run(self, task_id: str, *, session_id: str) -> TaskRecord:
        record = self._require(task_id)
        if record.session_id and record.session_id != session_id:
            raise RunAlreadyLinked(task_id, record.session_id, session_id)
        record.session_id = session_id
        record.events.append(
            TaskEvent(kind="run_linked", actor=record.agent_id, detail=session_id)
        )
        return self._save(record)

    def progress(self, task_id: str, *, note: str) -> TaskRecord:
        record = self._require(task_id)
        if record.state is TaskState.CLAIMED:
            record = self._move(
                task_id, TaskState.IN_PROGRESS, TaskActor.AGENT,
                actor=record.agent_id, detail=note,
            )
        else:
            record.events.append(
                TaskEvent(kind="progress", actor=record.agent_id, detail=note)
            )
            record = self._save(record)
        return record

    def complete(self, task_id: str, *, summary: str = "") -> TaskRecord:
        return self._move(
            task_id, TaskState.DONE, TaskActor.AGENT,
            actor=self._require(task_id).agent_id, detail=summary,
        )

    def fail(self, task_id: str, *, reason: str) -> TaskRecord:
        return self._move(
            task_id, TaskState.FAILED, TaskActor.AGENT,
            actor=self._require(task_id).agent_id, detail=reason,
        )

    def comment(self, task_id: str, *, author: str, body: str) -> TaskRecord:
        record = self._require(task_id)
        side = TaskActor.AGENT if author == record.agent_id else TaskActor.HUMAN
        record.comments.append(TaskComment(author=author, side=side, body=body))
        return self._save(record)

    # -- internals ---------------------------------------------------------

    def _require(self, task_id: str) -> TaskRecord:
        record = self.get(task_id)
        if record is None:
            raise UnknownTask(f"no such task '{task_id}'")
        return record

    def _move(
        self,
        task_id: str,
        target: TaskState,
        side: TaskActor,
        *,
        actor: str,
        detail: str = "",
    ) -> TaskRecord:
        record = self._require(task_id)
        source = record.state
        check_transition(source, target, side)
        record.state = target
        record.events.append(
            TaskEvent(
                kind="transition", actor=actor, side=side,
                source=source, target=target, detail=detail,
            )
        )
        return self._save(record)

    def _save(self, record: TaskRecord) -> TaskRecord:
        record.updated_at = now_iso()
        self.store.put(TASKS, record, parent=record.agent_id, name=record.title)
        return record
