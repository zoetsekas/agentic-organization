"""An agent can leave, and leaving takes effect (ADR-0098).

One test per line of that ADR's Verification section. The bug this closes,
reproduced before it was fixed: an agent removed from the design kept its
mandate and its delegation reach in the running system. Somebody left the
company and their badge still opened the door.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from orgagents.compiler.ir import build_ir
from orgagents.models import Agent, AgentKind, SessionState
from orgagents.platform import Platform
from orgagents.runtime.loader import load_system
from orgagents.spec.loader import load_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _agent(platform, agent_id, **kw):
    agent = Agent(id=agent_id, name=agent_id, **kw)
    platform.org.add_agent(agent)
    return agent


# -- removal from the design takes effect -----------------------------------


@pytest.fixture(scope="module")
def reconciled():
    """Load AYC, then load it again with one agent removed."""
    ir = build_ir(load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml"))
    tmp = tempfile.TemporaryDirectory()
    platform = Platform(str(pathlib.Path(tmp.name) / "ayc.db"),
                        configure_logs=False)
    load_system(platform, ir)
    smaller = ir.model_copy(deep=True)
    smaller.agents = [a for a in smaller.agents if a.id != "cs_agent"]
    report = load_system(platform, smaller)
    yield platform, report
    platform.close()  # Windows will not delete the open database file.
    tmp.cleanup()


def test_an_agent_the_design_dropped_is_decommissioned_on_load(reconciled):
    """Removal was the one change that did not propagate."""
    platform, report = reconciled
    assert "cs_agent" in report["departed"]
    agent = platform.org.agent("cs_agent")
    assert agent is not None, "the record was deleted rather than retired"
    assert agent.is_active is False
    assert "design no longer contains" in agent.decommission_reason


def test_a_departed_agent_may_not_be_delegated_to(reconciled):
    platform, _ = reconciled
    assert not platform.org.can_delegate("cgo_agent", "cs_agent")


def test_a_departed_agent_holds_no_mandate(reconciled):
    """A mandate is the right to decide, and an agent that has left deciding
    anything is the failure this is named for."""
    platform, _ = reconciled
    assert platform.org.mandate_holder("cs_agent", "issue_refund") is None


def test_its_sessions_still_resolve(reconciled):
    """A trace whose agent cannot be resolved is a worse record than one
    naming an agent that has left."""
    platform, _ = reconciled
    session = platform.sessions.create("cs_agent", title="before it left")
    assert platform.sessions.trace(session.id)["session"]["agent_id"] == "cs_agent"


def test_agents_still_present_are_untouched(reconciled):
    platform, _ = reconciled
    assert platform.org.agent("ecommerce_agent").is_active
    assert len(platform.org.active_agents()) == 11


# -- both directions, including the path that would slip through ------------


def test_a_departed_agent_may_not_delegate_to_anybody(platform):
    worker = _agent(platform, "agt_w1", mandate=[])
    leaver = _agent(platform, "agt_leaver", report_agent_ids=[worker.id])
    assert platform.org.can_delegate(leaver.id, worker.id)
    platform.org.decommission(leaver.id)
    assert not platform.org.can_delegate(leaver.id, worker.id)


def test_a_departed_shared_service_is_not_callable_by_anyone(platform):
    """The case a reach rule would otherwise let through: anybody may call a
    shared service, so the check has to come before the rules, not after."""
    service = _agent(platform, "agt_svc", kind=AgentKind.SERVICE)
    caller = _agent(platform, "agt_caller")
    assert platform.org.can_delegate(caller.id, service.id)
    platform.org.decommission(service.id)
    assert not platform.org.can_delegate(caller.id, service.id)


def test_mandate_resolution_walks_past_it_to_the_next_holder(platform):
    boss = _agent(platform, "agt_boss_d", mandate=["approve_spend"])
    leaver = _agent(platform, "agt_mid_d", manager_agent_id=boss.id,
                    mandate=["approve_spend"])
    assert platform.org.mandate_holder(leaver.id, "approve_spend").id == leaver.id
    platform.org.decommission(leaver.id)
    assert platform.org.mandate_holder(leaver.id, "approve_spend").id == boss.id


# -- what it was holding ----------------------------------------------------


def test_leaving_settles_work_in_flight_and_names_it(platform):
    """A session left running forever is the hang rule 5 exists to prevent."""
    leaver = _agent(platform, "agt_busy")
    running = platform.sessions.create(leaver.id, title="half done")
    platform.sessions.set_state(running.id, SessionState.RUNNING)

    report = platform.runtime.offboard(leaver.id, reason="role retired")
    assert report["ok"]
    assert running.id in report["abandoned_sessions"]
    assert platform.sessions.get(running.id).state is SessionState.FAILED
    assert any(e.type == "agent_departed"
               for e in platform.sessions.events(running.id))


def test_leaving_says_what_it_cannot_revoke(platform):
    """Identities and secrets are the generated infrastructure's. An
    organization believing more happened than did is the failure mode."""
    leaver = _agent(platform, "agt_ext")
    report = platform.runtime.offboard(leaver.id)
    assert "applying it is what closes those doors" in \
        report["external_access_note"]


def test_a_broken_succession_is_reported_not_repaired(platform):
    """Choosing somebody's replacement is not a thing a loader should do
    quietly."""
    leaver = _agent(platform, "agt_gone")
    depends = _agent(platform, "agt_depends", successor_agent_id=leaver.id)
    report = platform.org.decommission(leaver.id)
    assert report["successions_now_broken"] == [depends.id]
    assert platform.org.agent(depends.id).successor_agent_id == leaver.id


def test_standing_in_ends_in_both_directions(platform):
    """It cannot cover for anybody, and nobody is covering a post that no
    longer exists."""
    failed = _agent(platform, "agt_failed_x", mandate=["decide_x"])
    cover = _agent(platform, "agt_cover_x", mandate=[])
    failed.successor_agent_id = cover.id
    platform.org.add_agent(failed)
    platform.org.stand_in_for(failed.id)
    assert platform.org.acting_for(failed.id) is not None

    report = platform.org.decommission(cover.id)
    assert report["stopped_covering"] == [failed.id]
    assert platform.org.acting_for(failed.id) is None


def test_leaving_twice_is_not_an_error(platform):
    leaver = _agent(platform, "agt_twice")
    assert platform.org.decommission(leaver.id)["ok"]
    again = platform.org.decommission(leaver.id)
    assert again["ok"] and again["already"]
