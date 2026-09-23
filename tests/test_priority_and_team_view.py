"""Priority is observed, and a leader can see its team (ADR-0097).

One test per line of that ADR's Verification section. The trap this design is
shaped around: a task already has a priority, in the tool the humans who
assigned it use. Inventing a second one here would give two answers to "what
matters most", and the one people trust would not be ours.
"""
from __future__ import annotations

import threading

import pytest

from orgagents.models import SessionState
from orgagents.tasks.model import TaskPriority, TaskRecord
from orgagents.tasks.port import BackendCapabilities, TaskPort


# -- priority is read, never written ----------------------------------------


def test_unknown_is_a_fact_and_normal_is_a_claim():
    """The distinction the whole design rests on.

    A leader sorting by `normal` when nobody said anything is sorting by an
    assumption, which is worse than an obviously empty column.
    """
    record = TaskRecord(tenant_id="t", agent_id="a")
    assert record.priority is TaskPriority.UNKNOWN
    assert record.priority is not TaskPriority.NORMAL


def test_unknown_sorts_last_so_it_never_displaces_a_real_one():
    ordered = sorted(TaskPriority, key=lambda p: p.rank)
    assert ordered[0] is TaskPriority.URGENT
    assert ordered[-1] is TaskPriority.UNKNOWN


def test_the_backends_own_value_survives_beside_the_bucket():
    """Four buckets cannot hold a five-level scheme, and a report that lost
    the backend's own label cannot be checked against the humans' tool."""
    record = TaskRecord(tenant_id="t", agent_id="a",
                        priority=TaskPriority.HIGH, priority_raw="P2")
    assert record.priority is TaskPriority.HIGH
    assert record.priority_raw == "P2"


def test_the_port_offers_no_way_to_write_a_priority():
    """Intent belongs to the people (ADR-0057 rule 4)."""
    for name in ("set_priority", "prioritize", "reprioritize",
                 "update_priority"):
        assert not hasattr(TaskPort, name)


def test_a_backend_declares_whether_it_reports_priority_at_all():
    """Declared rather than inferred from a column of unknowns, so "this
    backend does not do priority" is answerable before the first call."""
    caps = BackendCapabilities()
    assert caps.reports_priority is False
    assert BackendCapabilities(reports_priority=True).reports_priority


# -- priority orders delegation, and does not schedule ----------------------


def _tools(platform, agent_id, session_id):
    return platform.runtime._delegation_tools(
        platform.org.agent(agent_id), session_id
    )


def _gated(platform):
    """Hold assigned children open so handles stay unsettled."""
    gate = threading.Event()
    real = platform.runtime.run

    def gated(agent_id, prompt, **kw):
        if kw.get("session_id"):
            assert gate.wait(timeout=10)
        return real(agent_id, prompt, **kw)

    platform.runtime.run = gated
    return gate, real


def test_outstanding_and_gather_come_back_in_priority_order(platform):
    cfo = platform.org.agent("agt_cfo")
    cfo.harness.max_parallel_subagents = 5
    platform.org.add_agent(cfo)
    session = platform.sessions.create(cfo.id)
    tools = _tools(platform, cfo.id, session.id)

    gate, real = _gated(platform)
    try:
        low = tools["assign"]("agt_fin_analyst", "Tidy the ledger.",
                              priority="low")["handle"]
        urgent = tools["assign"]("agt_fin_analyst", "Regulator is on the phone.",
                                 priority="urgent")["handle"]
        silent = tools["assign"]("agt_fin_analyst", "Nobody said.")["handle"]
        assert platform.runtime._by_priority(
            platform.runtime.unsettled_handles(session.id)
        ) == [urgent, low, silent], "unknown displaced a stated priority"
    finally:
        gate.set()
        platform.runtime.run = real

    out = tools["gather"]([silent, low, urgent], timeout_s=15)
    assert [r["handle"] for r in out["results"]] == [urgent, low, silent]
    assert out["results"][0]["priority"] == "urgent"


def test_the_bound_refusal_names_the_least_important_thing_held(platform):
    """Actionable rather than merely correct: the leader can see what to
    collect or let go of."""
    cfo = platform.org.agent("agt_cfo")
    cfo.harness.max_parallel_subagents = 2
    platform.org.add_agent(cfo)
    session = platform.sessions.create(cfo.id)
    tools = _tools(platform, cfo.id, session.id)

    gate, real = _gated(platform)
    try:
        tools["assign"]("agt_fin_analyst", "Regulator.", priority="urgent")
        low = tools["assign"]("agt_fin_analyst", "Tidy the ledger.",
                              priority="low")["handle"]
        refused = tools["assign"]("agt_fin_analyst", "Something else.",
                                  priority="urgent")
        assert refused["ok"] is False
        assert refused["lowest_priority_handle"] == low
        assert "Tidy the ledger" in refused["error"]
        assert "it does not jump this bound" in refused["error"], \
            "the refusal implied urgency would get past the bound"
    finally:
        gate.set()
        platform.runtime.run = real


def test_priority_is_on_the_audit_event(platform):
    session = platform.sessions.create("agt_cfo")
    handle = _tools(platform, "agt_cfo", session.id)["assign"](
        "agt_fin_analyst", "Pull invoice aging.", priority="high")["handle"]
    events = [e for e in platform.sessions.events(session.id)
              if e.type == "delegation"]
    assert events and events[0].payload["priority"] == "high"


def test_a_priority_nobody_declared_is_refused_rather_than_guessed(platform):
    session = platform.sessions.create("agt_cfo")
    out = _tools(platform, "agt_cfo", session.id)["assign"](
        "agt_fin_analyst", "Go.", priority="critical")
    assert out["ok"] is False
    assert "unknown priority 'critical'" in out["error"]


# -- a leader can see its team ----------------------------------------------


def test_a_leader_sees_unsettled_work_across_its_subtree(platform):
    """The data all existed — every run is a session, every session names an
    agent, the chart says who reports to whom. Nothing joined them."""
    analyst = platform.sessions.create("agt_fin_analyst")
    platform.sessions.set_state(analyst.id, SessionState.RUNNING)

    rows = platform.runtime.team_workload("agt_cfo")
    assert any(r["session_id"] == analyst.id for r in rows)
    assert all("session_url" in r and "running_for_s" in r for r in rows)


def test_settled_work_is_not_outstanding_work(platform):
    done = platform.sessions.create("agt_fin_analyst")
    platform.sessions.set_state(done.id, SessionState.COMPLETED)
    rows = platform.runtime.team_workload("agt_cfo")
    assert not [r for r in rows if r["session_id"] == done.id]


def test_a_leader_sees_nothing_outside_its_subtree(platform):
    """A mission lends the right to hand over work, not the right to watch."""
    outside = platform.sessions.create("agt_platform_eng")
    platform.sessions.set_state(outside.id, SessionState.RUNNING)
    rows = platform.runtime.team_workload("agt_cfo")
    assert not [r for r in rows if r["agent_id"] == "agt_platform_eng"]


def test_parked_work_sorts_first_and_says_so(platform):
    """Work waiting on a person is what a leader most needs to see."""
    parked = platform.sessions.create("agt_fin_analyst")
    platform.sessions.set_state(parked.id, SessionState.WAITING_HUMAN)
    busy = platform.sessions.create("agt_fin_analyst")
    platform.sessions.set_state(busy.id, SessionState.RUNNING)

    rows = platform.runtime.team_workload("agt_cfo")
    assert rows[0]["session_id"] == parked.id
    assert rows[0]["waiting_human"] is True


def test_an_agent_being_stood_in_for_is_marked(platform):
    """Not just busy — covered by somebody else (ADR-0094)."""
    analyst = platform.org.agent("agt_fin_analyst")
    running = platform.sessions.create(analyst.id)
    platform.sessions.set_state(running.id, SessionState.RUNNING)
    platform.org.stand_in_for(analyst.id)

    rows = platform.runtime.team_workload("agt_cfo")
    row = next(r for r in rows if r["session_id"] == running.id)
    assert row["stood_in_for_by"] == "agt_cfo"
