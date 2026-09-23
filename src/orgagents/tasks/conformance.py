"""The conformance suite every task adapter must pass (WS-031 M2).

An adapter claims to implement `TaskPort` by subclassing `TaskPortConformance`
in its own test module and answering two questions: how to build the backend,
and how a human opens work on it. Everything else is inherited, so the suite is
run *against the adapter*, not re-written for it.

It lives in `src/` rather than `tests/` on purpose: an adapter written outside
this repository must be able to import it.

What the suite requires of an adapter:

* it declares its capabilities, and the declaration is stable;
* the lifecycle is a machine — illegal moves are refused, not coerced;
* a task maps to at most one run, and linking a second one is refused;
* the tenant on a record is the tenant the backend serves;
* listing returns only work assigned to the agent asked about;
* comments and progress are additive and never lose what was there.
"""
from __future__ import annotations

from typing import Any

import pytest

from .model import (
    IllegalTaskTransition,
    TaskPriority,
    TaskRecord,
    TaskState,
    TaskTransitionDenied,
)
from .port import BackendCapabilities, RunAlreadyLinked, TaskPort

TENANT = "tnt_conformance"
AGENT = "agt_conformance"
ASSIGNER = "owner@example.com"


class TaskPortConformance:
    """Subclass this in an adapter's tests. Implement the two hooks."""

    # -- hooks an adapter must provide -------------------------------------

    def make_backend(self, tmp_path) -> TaskPort:
        raise NotImplementedError("an adapter must build its own backend")

    def open_task(self, backend: TaskPort, **kwargs: Any) -> TaskRecord:
        """Stand in for a human creating work — not part of the port."""
        raise NotImplementedError("an adapter must say how work is opened")

    # -- fixtures ----------------------------------------------------------

    @pytest.fixture()
    def backend(self, tmp_path) -> TaskPort:
        return self.make_backend(tmp_path)

    @pytest.fixture()
    def task(self, backend) -> TaskRecord:
        return self.open_task(
            backend,
            agent_id=AGENT,
            assigner=ASSIGNER,
            title="Reconcile the September ledger",
            description="Close out the month and flag anything unexplained.",
        )

    # -- the suite ---------------------------------------------------------

    def test_satisfies_the_port(self, backend):
        assert isinstance(backend, TaskPort)

    def test_declares_capabilities(self, backend):
        caps = backend.capabilities()
        assert isinstance(caps, BackendCapabilities)
        assert caps == backend.capabilities(), "capabilities must be stable"

    def test_opened_work_is_open_and_unlinked(self, task):
        assert task.state is TaskState.OPEN
        assert task.session_id is None
        assert task.agent_id == AGENT

    def test_record_carries_the_backend_tenant(self, backend, task):
        assert task.tenant_id == backend.capabilities().tenant_id

    def test_get_returns_the_record_and_none_for_strangers(self, backend, task):
        assert backend.get(task.id).id == task.id
        assert backend.get("tsk_does_not_exist") is None

    def test_assigned_lists_only_this_agent(self, backend, task):
        other = self.open_task(
            backend, agent_id="agt_somebody_else", assigner=ASSIGNER, title="Not ours"
        )
        ids = {t.id for t in backend.assigned(AGENT)}
        assert task.id in ids
        assert other.id not in ids

    def test_assigned_filters_by_state(self, backend, task):
        backend.claim(task.id, agent_id=AGENT)
        assert task.id not in {t.id for t in backend.assigned(AGENT, state=TaskState.OPEN)}
        assert task.id in {t.id for t in backend.assigned(AGENT, state=TaskState.CLAIMED)}

    def test_the_happy_path_walks_the_machine(self, backend, task):
        assert backend.claim(task.id, agent_id=AGENT).state is TaskState.CLAIMED
        assert backend.progress(task.id, note="reading the ledger").state is (
            TaskState.IN_PROGRESS
        )
        done = backend.complete(task.id, summary="reconciled, two items flagged")
        assert done.state is TaskState.DONE

    def test_failing_is_a_legal_end(self, backend, task):
        backend.claim(task.id, agent_id=AGENT)
        backend.progress(task.id, note="started")
        assert backend.fail(task.id, reason="source system unavailable").state is (
            TaskState.FAILED
        )

    def test_illegal_transition_is_refused_not_coerced(self, backend, task):
        with pytest.raises(IllegalTaskTransition):
            backend.complete(task.id, summary="skipping the middle")
        assert backend.get(task.id).state is TaskState.OPEN

    def test_an_agent_may_not_claim_work_assigned_elsewhere(self, backend, task):
        with pytest.raises(PermissionError):
            backend.claim(task.id, agent_id="agt_interloper")

    def test_a_task_maps_to_at_most_one_run(self, backend, task):
        backend.claim(task.id, agent_id=AGENT)
        linked = backend.link_run(task.id, session_id="ses_first")
        assert linked.session_id == "ses_first"
        # Re-linking the same run is idempotent; a different one is refused.
        assert backend.link_run(task.id, session_id="ses_first").session_id == "ses_first"
        with pytest.raises(RunAlreadyLinked):
            backend.link_run(task.id, session_id="ses_second")
        assert backend.get(task.id).session_id == "ses_first"

    def test_comments_accumulate_from_both_sides(self, backend, task):
        backend.comment(task.id, author=ASSIGNER, body="priority is the ledger")
        record = backend.comment(task.id, author=AGENT, body="on it")
        assert [c.body for c in record.comments] == [
            "priority is the ledger",
            "on it",
        ]

    def test_progress_is_recorded_without_losing_history(self, backend, task):
        backend.claim(task.id, agent_id=AGENT)
        backend.progress(task.id, note="first")
        record = backend.progress(task.id, note="second")
        details = [e.detail for e in record.events]
        assert "first" in details and "second" in details

    def test_transitions_are_on_the_record_as_history(self, backend, task):
        backend.claim(task.id, agent_id=AGENT)
        record = backend.get(task.id)
        move = [e for e in record.events if e.kind == "transition"][-1]
        assert (move.source, move.target) == (TaskState.OPEN, TaskState.CLAIMED)

    # -- priority (ADR-0097) -----------------------------------------------

    def test_priority_is_reported_or_honestly_unknown(self, backend, task):
        """`unknown` is a fact, and `normal` is a claim.

        A backend that does not do priority must say `unknown` rather than
        defaulting to `normal`, because a leader sorting by an assumption is
        worse off than one looking at an obviously empty column.
        """
        caps = backend.capabilities()
        record = backend.get(task.id)
        assert isinstance(record.priority, TaskPriority)
        if not caps.reports_priority:
            assert record.priority is TaskPriority.UNKNOWN, (
                "a backend that does not declare reports_priority must not "
                "guess a priority"
            )

    def test_a_reported_priority_keeps_the_backends_own_value(self, backend,
                                                              task):
        """Four buckets cannot hold a five-level scheme. The raw value is
        what lets a report be checked against the tool the humans use."""
        if not backend.capabilities().reports_priority:
            pytest.skip("this backend does not report priority")
        record = backend.get(task.id)
        if record.priority is not TaskPriority.UNKNOWN:
            assert record.priority_raw, (
                "a reported priority lost the backend's own value"
            )

    def test_the_port_offers_no_way_to_write_a_priority(self, backend):
        """Intent belongs to the people (ADR-0057 rule 4). A priority this
        platform could write would be a second source of truth for it."""
        for name in ("set_priority", "prioritize", "reprioritize",
                     "update_priority"):
            assert not hasattr(TaskPort, name), (
                f"TaskPort grew {name}; priority is observed, never written"
            )

    def test_unknown_task_raises_rather_than_returning_a_blank(self, backend):
        with pytest.raises(KeyError):
            backend.claim("tsk_does_not_exist", agent_id=AGENT)


__all__ = ["TaskPortConformance", "TENANT", "AGENT", "ASSIGNER",
           "TaskTransitionDenied"]
