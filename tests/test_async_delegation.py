"""Asynchronous delegation: a handle rather than a result (ADR-0093).

Each test here is one line of that ADR's Verification section. The shape of
the feature is that `assign` hands back a handle immediately, `check` asks
what it is doing and `gather` waits for it — while `delegate` keeps working
exactly as it did, because most delegation genuinely wants an answer now.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from orgagents.models import AgentKind, SessionState
from orgagents.runtime.adapters import TurnBudget
from orgagents.runtime.engine import AgentRuntime


def _tools(platform, agent_id, session_id):
    return platform.runtime._delegation_tools(
        platform.org.agent(agent_id), session_id
    )


def _settled(platform, handle, timeout=10.0):
    """Wait for a child's worker to finish, and return the child's state.

    Waits on the worker's future, not by polling the session: the session
    turns COMPLETED a moment before the future carries the output, and a test
    that raced into that gap under load read an empty result (the old
    flakiness of the lapsed-grant test). The future is the event itself; the
    timeout is only a guard against a hang.
    """
    with platform.runtime._assign_lock:
        assignment = platform.runtime._assignments[handle]
    try:
        assignment.future.result(timeout=timeout)
    except Exception as e:  # noqa: BLE001 - a failed run is still settled
        if type(e).__name__ == "TimeoutError":
            raise AssertionError(f"handle {handle} never settled") from e
    state = platform.sessions.get(handle).state
    assert state in (SessionState.COMPLETED, SessionState.FAILED), state
    return state


# -- rule: the authority model does not move --------------------------------


def test_assign_refuses_exactly_what_delegate_refuses(platform):
    """Who may hand work to whom is one decision, made in one place.

    Every reach rule is exercised — a report, a peer, a subtree skip-level, a
    shared service and an agent with no relationship at all — and `assign` is
    required to agree with `delegate` on each, refusal message included. If
    these two ever diverged, the asynchronous path would be a second authority
    model nobody reviewed.
    """
    pairs = [
        ("agt_cfo", "agt_fin_analyst"),      # a direct report
        ("agt_fin_analyst", "agt_cfo"),      # upward: not delegation
        ("agt_fin_analyst", "agt_platform_eng"),   # across the chart
        ("agt_cto", "agt_sre"),
        ("agt_cfo", "agt_cfo"),              # itself
    ]
    for source, target in pairs:
        allowed = platform.org.can_delegate(source, target)
        session = platform.sessions.create(source)
        tools = _tools(platform, source, session.id)

        sync = tools["delegate"](target, "Do the thing.")
        async_ = tools["assign"](target, "Do the thing.")

        assert sync["ok"] is bool(allowed), (source, target)
        assert async_["ok"] is bool(allowed), (source, target)
        if not allowed:
            assert sync["error"] == async_["error"], (source, target)
        else:
            _settled(platform, async_["handle"])


def test_a_shared_service_is_reachable_asynchronously_too(platform):
    service = next(
        (a for a in platform.org.agents() if a.kind is AgentKind.SERVICE), None
    )
    if service is None:
        pytest.skip("this fixture has no shared-service agent")
    session = platform.sessions.create("agt_fin_analyst")
    out = _tools(platform, "agt_fin_analyst", session.id)["assign"](
        service.id, "Look this up."
    )
    assert out["ok"], out
    _settled(platform, out["handle"])


# -- rule 1: authority is checked at assignment, and the result still returns


def test_a_lapsed_mission_grant_does_not_strand_the_work(platform):
    """Work lawfully commissioned is delivered, and the lapse is recorded.

    A mission grant is date-bounded, so it can close while the work is in
    flight. Re-checking at collection would strand a result the leader was
    entitled to ask for, and would teach everyone to avoid time-bounded grants
    — the opposite of what mission grants are for. So the result comes back,
    and `check` says the window has since closed.
    """
    analyst = platform.org.agent("agt_fin_analyst")
    target = "agt_platform_eng"
    assert not platform.org.can_delegate(analyst.id, target)

    grant = {
        "mission": "quarter-close", "status": "active", "peers": [target],
        "starts_on": "2000-01-01", "ends_on": "2099-01-01",
    }
    analyst.mission_grants = [grant]
    platform.org.add_agent(analyst)
    assert platform.org.can_delegate(analyst.id, target)

    session = platform.sessions.create(analyst.id)
    tools = _tools(platform, analyst.id, session.id)
    handle = tools["assign"](target, "Check the deploy window.")["handle"]
    _settled(platform, handle)

    # The mission's window closes while the work is in flight.
    grant["ends_on"] = "2000-01-02"
    analyst.mission_grants = [grant]
    platform.org.add_agent(analyst)
    assert not platform.org.can_delegate(analyst.id, target)

    out = tools["check"](handle)
    assert out["settled"] and out["ok"], out
    assert out["output"], "the result was stranded rather than delivered"
    assert out["authority_lapsed"] is True
    assert target in out["note"]

    collected = [e for e in platform.sessions.events(session.id)
                 if e.type == "delegation_collected"]
    assert collected and collected[0].payload["authority_lapsed"] is True


def test_a_completed_child_is_not_settled_until_its_result_is_in_hand(platform):
    """The gap the lapsed-grant test used to fall into, held open on purpose:
    the store says COMPLETED, the worker's future has not resolved. `check`
    must not collect (and so lose) an empty result in that gap."""
    from concurrent.futures import Future

    cfo = platform.org.agent("agt_cfo")
    session = platform.sessions.create(cfo.id)
    tools = _tools(platform, cfo.id, session.id)
    handle = tools["assign"]("agt_fin_analyst", "Reconcile.")["handle"]
    _settled(platform, handle)
    assignment = platform.runtime._assignments[handle]
    real = assignment.future.result()
    assignment.future = Future()          # the worker has not handed over yet

    early = tools["check"](handle)
    assert early["state"] == SessionState.COMPLETED.value
    assert early["settled"] is False and "output" not in early

    assignment.future.set_result(real)
    late = tools["check"](handle)
    assert late["settled"] and late["ok"] and late["output"] == real.output


# -- rule 2: depth is a property of the tree --------------------------------


def test_depth_is_read_from_the_parent_chain(platform):
    root = platform.sessions.create("agt_cfo")
    child = platform.sessions.create("agt_fin_analyst",
                                     parent_session_id=root.id)
    grandchild = platform.sessions.create("agt_fin_analyst",
                                          parent_session_id=child.id)
    depth = platform.runtime.delegation_depth
    assert depth(root.id) == 0
    assert depth(child.id) == 1
    assert depth(grandchild.id) == 2


def test_the_depth_bound_still_binds_the_synchronous_path(platform):
    """The number the counter produced, from the tree instead.

    `delegate` bounded depth with a process-local counter. The bound has to
    keep meaning the same thing, or removing the counter would have quietly
    widened it.
    """
    cfo = platform.org.agent("agt_cfo")
    cfo.harness.max_subagent_depth = 0
    platform.org.add_agent(cfo)
    session = platform.sessions.create(cfo.id)
    tools = _tools(platform, cfo.id, session.id)
    assert tools["delegate"]("agt_fin_analyst", "Go.")["error"] == \
        "max sub-agent depth reached"
    assert tools["assign"]("agt_fin_analyst", "Go.")["error"] == \
        "max sub-agent depth reached"


# -- rule 3: max_parallel_subagents starts being enforced -------------------


def test_the_parallel_bound_refuses_rather_than_queues(platform):
    """A declared limit that nothing honours is what this platform refuses to
    ship. Here it is honoured, and it refuses rather than queueing: a queue
    nobody declared is a bound nobody reviewed.
    """
    cfo = platform.org.agent("agt_cfo")
    cfo.harness.max_parallel_subagents = 1
    platform.org.add_agent(cfo)
    session = platform.sessions.create(cfo.id)
    tools = _tools(platform, cfo.id, session.id)

    gate = threading.Event()
    real_run = platform.runtime.run

    def gated(agent_id, prompt, **kw):
        if kw.get("session_id"):          # a child running on a worker thread
            assert gate.wait(timeout=10)
        return real_run(agent_id, prompt, **kw)

    platform.runtime.run = gated
    try:
        first = tools["assign"]("agt_fin_analyst", "Pull invoice aging.")
        assert first["ok"] and first["settled"] is False
        # Held, because the worker is still on it — which is the case the
        # bound exists for.
        assert platform.runtime.unsettled_handles(session.id) == [first["handle"]]

        second = tools["assign"]("agt_fin_analyst", "And the other thing.")
        assert second["ok"] is False
        assert "max_parallel_subagents is 1" in second["error"]
        assert second["outstanding"] == [first["handle"]]
    finally:
        gate.set()
        platform.runtime.run = real_run
    _settled(platform, first["handle"])


def test_fan_out_is_concurrent_not_serial(platform):
    """Four handles are held at once, which serial delegation cannot do."""
    cfo = platform.org.agent("agt_cfo")
    cfo.harness.max_parallel_subagents = 4
    platform.org.add_agent(cfo)
    session = platform.sessions.create(cfo.id)
    tools = _tools(platform, cfo.id, session.id)

    gate = threading.Event()
    real_run = platform.runtime.run

    def gated(agent_id, prompt, **kw):
        if kw.get("session_id"):
            assert gate.wait(timeout=10)
        return real_run(agent_id, prompt, **kw)

    platform.runtime.run = gated
    try:
        handles = [tools["assign"]("agt_fin_analyst", f"Task {i}.")["handle"]
                   for i in range(4)]
        assert len(set(handles)) == 4
        assert sorted(platform.runtime.unsettled_handles(session.id)) == \
            sorted(handles)
    finally:
        gate.set()
        platform.runtime.run = real_run

    out = tools["gather"](handles, timeout_s=15)
    assert out["ok"], out
    assert len(out["results"]) == 4
    assert all(r["settled"] and r["output"] for r in out["results"])
    assert out["outstanding"] == []


# -- rule 4: a child's spend counts against its parent ----------------------


def test_a_childs_spend_is_charged_to_the_parent_at_collection(platform):
    """Otherwise fan-out is a way to leave a ceiling behind.

    Four children at a million tokens each would cost the leader nothing,
    while the same work done serially would have stopped at the leader's own
    budget. The ceiling has to mean the same thing either way.
    """
    session = platform.sessions.create("agt_cfo")
    budget = TurnBudget(tokens=100_000)
    platform.runtime._budgets[session.id] = budget
    try:
        tools = _tools(platform, "agt_cfo", session.id)
        handle = tools["assign"]("agt_fin_analyst", "Pull invoice aging.")["handle"]
        _settled(platform, handle)

        spent_before = budget.tokens_spent
        out = tools["check"](handle)
        child_tokens = platform.sessions.get(handle).token_usage
        assert child_tokens > 0
        assert out["tokens"] == child_tokens
        assert budget.tokens_spent == spent_before + child_tokens

        # Charged once, however often it is collected.
        tools["check"](handle)
        assert budget.tokens_spent == spent_before + child_tokens
    finally:
        platform.runtime._budgets.pop(session.id, None)


# -- rule 5: a handle always settles ----------------------------------------


def test_a_deadline_settles_a_handle_that_is_still_running(platform):
    gate = threading.Event()
    real_run = platform.runtime.run

    def gated(agent_id, prompt, **kw):
        if kw.get("session_id"):
            assert gate.wait(timeout=10)
        return real_run(agent_id, prompt, **kw)

    platform.runtime.run = gated
    session = platform.sessions.create("agt_cfo")
    tools = _tools(platform, "agt_cfo", session.id)
    try:
        handle = tools["assign"]("agt_fin_analyst", "Something slow.")["handle"]
        out = tools["gather"]([handle], timeout_s=0.2)
        assert out["ok"] is False
        result = out["results"][0]
        assert result["settled"] and result["state"] == "failed"
        assert "deadline passed" in result["error"]
        assert any(e.type == "assignment_abandoned"
                   for e in platform.sessions.events(handle))
    finally:
        gate.set()
        platform.runtime.run = real_run


def test_a_handle_no_worker_holds_is_failed_after_a_restart(platform, tmp_path):
    """A restart leaves the store saying "running" and nothing running it.

    A handle that hangs forever is worse than one that fails, because the
    leader waiting on it never gets a turn in which to notice. A fresh runtime
    over the same store settles what it cannot account for.
    """
    session = platform.sessions.create("agt_cfo")
    tools = _tools(platform, "agt_cfo", session.id)
    handle = tools["assign"]("agt_fin_analyst", "Pull invoice aging.")["handle"]
    _settled(platform, handle)
    # The worker is gone and the store still says the child is running.
    platform.sessions.set_state(handle, SessionState.RUNNING)

    restarted = AgentRuntime(platform.store)
    assert restarted.unsettled_handles(session.id) == [handle]
    assert restarted.settle_lost_handles(session.id) == [handle]
    assert platform.sessions.get(handle).state is SessionState.FAILED
    assert restarted.unsettled_handles(session.id) == []
    assert any(e.type == "assignment_lost"
               for e in platform.sessions.events(handle))


def test_a_handle_this_agent_does_not_hold_is_refused(platform):
    mine = platform.sessions.create("agt_cfo")
    theirs = platform.sessions.create("agt_cto")
    handle = _tools(platform, "agt_cto", theirs.id)["assign"](
        "agt_platform_eng", "Deploy this."
    )["handle"]
    _settled(platform, handle)
    out = _tools(platform, "agt_cfo", mine.id)["check"](handle)
    assert out["ok"] is False and "does not hold handle" in out["error"]


# -- rule 6: waiting_human is reported, not waited on -----------------------


def test_a_child_parked_on_a_human_is_reported_and_still_held(platform):
    """The case that most argues for the whole change.

    Today an approval freezes the delegating leader for as long as a person
    takes to read an email. Under a handle, the leader learns the work is
    parked and gets on with something else — and the handle keeps its slot,
    because parked work is still work that was commissioned and not returned.
    """
    session = platform.sessions.create("agt_cfo")
    tools = _tools(platform, "agt_cfo", session.id)
    handle = tools["assign"]("agt_fin_analyst", "Post the accrual journal.")["handle"]
    _settled(platform, handle)
    platform.sessions.set_state(handle, SessionState.WAITING_HUMAN)

    out = tools["check"](handle)
    assert out["state"] == "waiting_human"
    assert out["waiting_human"] is True
    assert out["settled"] is False, "a parked child is not finished work"
    assert "not waited on" in out["note"]
    assert platform.runtime.unsettled_handles(session.id) == [handle]


def test_outstanding_excludes_settled_children(platform):
    session = platform.sessions.create("agt_cfo")
    tools = _tools(platform, "agt_cfo", session.id)
    handle = tools["assign"]("agt_fin_analyst", "Pull invoice aging.")["handle"]
    _settled(platform, handle)
    assert platform.sessions.outstanding(session.id) == []
    platform.sessions.set_state(handle, SessionState.WAITING_HUMAN)
    assert [s.id for s in platform.sessions.outstanding(session.id)] == [handle]


# -- the honesty line -------------------------------------------------------


def test_no_generated_conformance_report_claims_asynchronous_delegation():
    """Asynchronous delegation is a harness behaviour, and nothing else.

    A platform target that emits somebody else's agent definitions carries
    neither the handle nor the parallel bound. A conformance report that named
    them would be claiming a control it does not carry (ADR-0073).
    """
    root = Path(__file__).resolve().parents[1]
    reports = list(root.glob("examples/**/CONFORMANCE.md"))
    assert reports, "no generated conformance reports to check"
    for report in reports:
        text = report.read_text(encoding="utf-8")
        for claim in ("assign(", "gather(", "max_parallel_subagents"):
            assert claim not in text, f"{report} claims {claim}"


# -- the two items ADR-0093 left open (v1.1.0) ------------------------------


def test_an_abandoned_worker_does_not_resurrect_the_handle(platform):
    """What the leader was told and what the store says must not diverge.

    A `gather` deadline cannot stop the worker — nothing here can interrupt a
    thread mid tool call, and a half-executed tool call is worse than a late
    result. So the deadline is honoured on the leader's side and the session
    stays failed, while the work itself is kept as an event: the output is
    real and may be worth reading, it was simply never delivered.
    """
    gate = threading.Event()
    real_run = platform.runtime.run

    def gated(agent_id, prompt, **kw):
        if kw.get("session_id"):
            assert gate.wait(timeout=10)
        return real_run(agent_id, prompt, **kw)

    platform.runtime.run = gated
    session = platform.sessions.create("agt_cfo")
    tools = _tools(platform, "agt_cfo", session.id)
    try:
        handle = tools["assign"]("agt_fin_analyst", "Something slow.")["handle"]
        assert tools["gather"]([handle], timeout_s=0.2)["ok"] is False
    finally:
        gate.set()
        platform.runtime.run = real_run

    # Let the worker finish the run the leader already gave up on.
    with platform.runtime._assign_lock:
        future = platform.runtime._assignments[handle].future
    future.result(timeout=10)

    assert platform.sessions.get(handle).state is SessionState.FAILED, \
        "the leader was told this failed; the store must not say otherwise"
    late = [e for e in platform.sessions.events(handle)
            if e.type == "assignment_late_result"]
    assert late and late[0].payload["output"], \
        "the work was thrown away rather than kept"
    assert "never delivered" in late[0].payload["note"]


def test_a_handle_collected_late_says_the_conversation_has_moved_on(platform):
    """A result returning into a conversation that has moved on.

    Nothing can make an agent re-read what it asked for. What can be done is
    to stop the staleness being something it has to infer from a timestamp:
    the original task comes back with the result, and so does the fact that
    the leader has said other things since.
    """
    session = platform.sessions.create("agt_cfo")
    tools = _tools(platform, "agt_cfo", session.id)
    handle = tools["assign"]("agt_fin_analyst", "Pull invoice aging.")["handle"]
    _settled(platform, handle)

    prompt = tools["check"](handle)
    assert prompt["settled"] and prompt["stale"] is False
    assert prompt["messages_since_assigned"] == 0

    # The leader's conversation carries on while the handle sits collected.
    platform.sessions.log(session.id, "message", actor="human",
                          payload={"role": "user", "content": "Actually, hold that."})
    platform.sessions.log(session.id, "message", actor="agt_cfo",
                          payload={"role": "assistant", "content": "Holding."})

    later = tools["check"](handle)
    assert later["stale"] is True
    assert later["messages_since_assigned"] == 2
    assert "Pull invoice aging." in later["staleness"]
    assert later["task"] == "Pull invoice aging."
    assert later["output"], "a stale result is still delivered, just flagged"
