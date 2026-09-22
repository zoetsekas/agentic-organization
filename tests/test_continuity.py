"""Standing in for a leader that cannot run (ADR-0094).

One test per line of that ADR's Verification section. The decision the whole
design turns on: a successor inherits the mandate — but the *default*
successor is the manager, who already held it under ADR-0065 and is therefore
granted nothing. Everything with teeth here is about the other case.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orgagents.continuity import ActingAssignment, open_assignment, split_mandate
from orgagents.models import Agent, SessionState

SEP_PAY = {
    "id": "sep_payments",
    "decisions": ["raise_payment", "approve_payment"],
    "reason": "one hand must not both raise and approve a payment",
}


def _agent(platform, agent_id, **kw):
    agent = Agent(id=agent_id, name=agent_id, **kw)
    platform.org.add_agent(agent)
    return agent


# -- rule 1: the manager stands in, and is granted nothing ------------------


def test_the_default_successor_is_the_manager_and_gains_nothing(platform):
    """The safe case is what you get by saying nothing.

    Under ADR-0065 authority narrows downward, so the manager already holds a
    superset of its report's mandate. Standing in confers nothing, there is
    nothing that could breach a separation, and nothing to time-bound.
    """
    boss = _agent(platform, "agt_boss", mandate=["close_books", "raise_payment"])
    lead = _agent(platform, "agt_lead", manager_agent_id=boss.id,
                  mandate=["raise_payment"])

    assert platform.org.successor_of(lead.id).id == boss.id
    assignment = platform.org.stand_in_for(lead.id, separations=[SEP_PAY])
    assert assignment is not None
    assert assignment.by_hierarchy is True
    assert assignment.decisions == [], "the manager was granted something"
    assert assignment.withheld == []
    assert "already held this mandate" in assignment.describe()


def test_a_hierarchy_stand_in_does_not_redirect_the_mandate_holder(platform):
    """Walking up would have reached the manager anyway."""
    boss = _agent(platform, "agt_boss2", mandate=["raise_payment"])
    lead = _agent(platform, "agt_lead2", manager_agent_id=boss.id,
                  mandate=["raise_payment"])
    platform.org.stand_in_for(lead.id)
    assert platform.org.mandate_holder(lead.id, "raise_payment").id == lead.id


# -- rule 2 and 4: a lateral successor is a real grant, bounded by separation


def test_a_lateral_successor_inherits_the_mandate(platform):
    peer = _agent(platform, "agt_peer", mandate=["close_books"])
    lead = _agent(platform, "agt_lead3", successor_agent_id=peer.id,
                  mandate=["raise_payment", "publish_report"])

    assignment = platform.org.stand_in_for(lead.id, separations=[SEP_PAY])
    assert assignment.by_hierarchy is False
    assert assignment.decisions == ["publish_report", "raise_payment"]
    assert assignment.withheld == []
    # The decision now routes to whoever is standing in.
    assert platform.org.mandate_holder(lead.id, "raise_payment").id == peer.id


def test_a_separation_is_never_inherited_and_the_rest_still_is(platform):
    """The control most under pressure in an outage, and least able to
    defend itself. It wins — but only over the decisions it actually names.
    """
    peer = _agent(platform, "agt_approver", mandate=["approve_payment"])
    lead = _agent(platform, "agt_lead4", successor_agent_id=peer.id,
                  mandate=["raise_payment", "publish_report"])

    assignment = platform.org.stand_in_for(lead.id, separations=[SEP_PAY])
    assert assignment.decisions == ["publish_report"], "the rest was lost"
    assert [w.decision for w in assignment.withheld] == ["raise_payment"]
    assert assignment.withheld[0].separation == "sep_payments"
    assert "one hand must not" in assignment.withheld[0].describe()

    # A withheld decision keeps escalating rather than landing on somebody a
    # control says may not take it.
    assert platform.org.mandate_holder(lead.id, "raise_payment").id == lead.id
    assert platform.org.mandate_holder(lead.id, "publish_report").id == peer.id


def test_split_mandate_cannot_confer_both_halves_of_one_control():
    """Even when the successor held neither side to begin with."""
    granted, withheld = split_mandate(
        ["raise_payment", "approve_payment"], [], [SEP_PAY]
    )
    assert len(granted) == 1 and len(withheld) == 1
    assert granted[0] != withheld[0].decision


# -- validation catches it before the outage --------------------------------


def test_a_successor_that_would_break_a_separation_is_refused_at_validation():
    """A control found at 3am is a control that failed.

    The union of the two mandates is checked when the spec is validated, so a
    succession that could never be safe never reaches a deployment — rather
    than being discovered during the outage it was meant to survive.
    """
    from tests.test_mandates import SEPARATED, _org, errors
    from orgagents.spec.validate import validate_spec

    spec = {
        **SEPARATED,
        "organization": {
            **SEPARATED["organization"],
            "mandate": {"decisions": ["spend", "close", "deploy"]},
            "members": [{"id": "ceo", "name": "CEO",
                         "mandate": {"decisions": ["deploy"]}}],
            "teams": [
                {
                    **SEPARATED["organization"]["teams"][0],
                    "members": [
                        # Each is fine alone; together they are the control.
                        {"id": "cfo", "name": "CFO",
                         "mandate": {"decisions": ["close"]},
                         "successor": "clerk"},
                        {"id": "clerk", "name": "Clerk",
                         "mandate": {"decisions": ["spend"]}},
                    ],
                }
            ],
        },
    }
    found = errors(validate_spec(_org(spec)))
    assert not [f for f in found if f.code == "separation_violated"], \
        "neither agent violates a separation on its own"
    breaks = [f for f in found if f.code == "successor_breaks_separation"]
    assert breaks, "an unsafe succession reached deployment"
    assert "'clerk' is declared successor to 'cfo'" in breaks[0].message
    assert "one principal must not do both" in breaks[0].message
    assert "leave it unset so the manager stands in" in breaks[0].message


def test_a_successor_holding_neither_side_validates():
    from tests.test_mandates import SEPARATED, _org, errors
    from orgagents.spec.validate import validate_spec

    spec = {
        **SEPARATED,
        "organization": {
            **SEPARATED["organization"],
            "mandate": {"decisions": ["spend", "close", "deploy"]},
            "members": [{"id": "ceo", "name": "CEO",
                         "mandate": {"decisions": ["deploy"]}}],
            "teams": [
                {
                    **SEPARATED["organization"]["teams"][0],
                    "members": [
                        {"id": "cfo", "name": "CFO",
                         "mandate": {"decisions": ["close"]},
                         "successor": "aide"},
                        {"id": "aide", "name": "Aide",
                         "mandate": {"decisions": []}},
                        {"id": "clerk", "name": "Clerk",
                         "mandate": {"decisions": ["spend"]}},
                    ],
                }
            ],
        },
    }
    found = errors(validate_spec(_org(spec)))
    assert not [f for f in found if f.code == "successor_breaks_separation"]


def test_an_unknown_or_self_successor_is_refused():
    from tests.test_mandates import SEPARATED, _org, errors
    from orgagents.spec.validate import validate_spec

    org = SEPARATED["organization"]
    team = {**org["teams"][0],
            "members": [{"id": "cfo", "name": "CFO",
                         "mandate": {"decisions": ["close"]},
                         "successor": "nobody"},
                        {"id": "clerk", "name": "Clerk",
                         "mandate": {"decisions": ["spend"]},
                         "successor": "clerk"}]}
    spec = {**SEPARATED, "organization": {
        **org, "mandate": {"decisions": ["spend", "close", "deploy"]},
        "members": [{"id": "ceo", "name": "CEO",
                     "mandate": {"decisions": ["deploy"]}}],
        "teams": [team]}}
    codes = {f.code for f in errors(validate_spec(_org(spec)))}
    assert "unknown_successor" in codes
    assert "successor_is_self" in codes


# -- rule 3: it expires, evaluated per call ---------------------------------


def test_a_lapsed_standing_in_confers_nothing_without_a_sweep(platform):
    peer = _agent(platform, "agt_peer5", mandate=[])
    lead = _agent(platform, "agt_lead5", successor_agent_id=peer.id,
                  mandate=["raise_payment"])
    platform.org.stand_in_for(lead.id, ends_on="2000-01-01")

    assert platform.org.acting_for(lead.id) is None, \
        "a window that closed still conferred authority"
    assert platform.org.mandate_holder(lead.id, "raise_payment").id == lead.id


def test_an_open_window_still_confers(platform):
    peer = _agent(platform, "agt_peer6", mandate=[])
    lead = _agent(platform, "agt_lead6", successor_agent_id=peer.id,
                  mandate=["raise_payment"])
    platform.org.stand_in_for(lead.id, ends_on="2099-01-01")
    assert platform.org.acting_for(lead.id) is not None


# -- rule 5: standing in does not chain -------------------------------------


def test_standing_in_does_not_chain(platform):
    """Each hop is a step further from anyone who reviewed it."""
    third = _agent(platform, "agt_third", mandate=[])
    peer = _agent(platform, "agt_peer7", successor_agent_id=third.id,
                  mandate=[])
    lead = _agent(platform, "agt_lead7", successor_agent_id=peer.id,
                  mandate=["raise_payment"])

    assert platform.org.stand_in_for(peer.id) is not None   # peer stands down
    assert platform.org.stand_in_for(lead.id) is None, \
        "a successor already standing in was allowed to stand in again"


# -- reach: a stand-in can actually lead the team ---------------------------


def test_a_stand_in_may_delegate_to_the_failed_leaders_reports(platform):
    worker = _agent(platform, "agt_worker", mandate=[])
    peer = _agent(platform, "agt_peer8", mandate=[])
    lead = _agent(platform, "agt_lead8", successor_agent_id=peer.id,
                  report_agent_ids=[worker.id], mandate=[])

    assert not platform.org.can_delegate(peer.id, worker.id)
    platform.org.stand_in_for(lead.id)
    assert platform.org.can_delegate(peer.id, worker.id), \
        "the stand-in cannot hand work to the team it is covering"

    platform.org.close_standing_in(lead.id)
    assert not platform.org.can_delegate(peer.id, worker.id), \
        "reach outlived the standing-in that lent it"


# -- the trigger, and what the successor adopts -----------------------------


def test_one_failed_session_stands_nobody_down(platform):
    """One bad turn is not an outage."""
    agent = platform.org.agent("agt_fin_analyst")
    session = platform.sessions.create(agent.id)
    platform.sessions.set_state(session.id, SessionState.FAILED)
    assert platform.runtime.consecutive_failures(agent.id) == 1
    assert agent.harness.escalate_to_human_after_failures > 1
    assert platform.org.acting_for(agent.id) is None


def test_the_declared_threshold_stands_a_leader_down(platform):
    agent = platform.org.agent("agt_fin_analyst")
    for _ in range(agent.harness.escalate_to_human_after_failures):
        s = platform.sessions.create(agent.id)
        platform.sessions.set_state(s.id, SessionState.FAILED)

    assert platform.runtime.stand_down(agent.id) is not None
    assert platform.org.acting_for(agent.id) is not None


def test_a_failed_leaders_outstanding_handles_are_adopted(platform):
    """Otherwise the work settles with nobody to act on what came back."""
    cfo = platform.org.agent("agt_cfo")
    session = platform.sessions.create(cfo.id)
    tools = platform.runtime._delegation_tools(cfo, session.id)
    handle = tools["assign"]("agt_fin_analyst", "Pull invoice aging.")["handle"]
    platform.sessions.set_state(handle, SessionState.WAITING_HUMAN)

    assignment = platform.runtime.stand_down(cfo.id, reason="test")
    assert assignment is not None
    assert handle in assignment.adopted_handles


def test_nobody_to_stand_in_is_a_refusal_not_a_promotion(platform):
    """Silence never grants authority."""
    lonely = _agent(platform, "agt_lonely", mandate=["raise_payment"])
    assert platform.org.successor_of(lonely.id) is None
    assert platform.runtime.stand_down(lonely.id) is None


# -- rule 6: standing in is visible -----------------------------------------


def test_every_run_under_an_inherited_mandate_says_so(platform):
    peer = platform.org.agent("agt_cto")
    lead = _agent(platform, "agt_lead9", successor_agent_id=peer.id,
                  mandate=["raise_payment"])
    platform.org.stand_in_for(lead.id)

    result = platform.runtime.run(peer.id, "Cover the team.")
    acting = [e for e in platform.sessions.events(result.session_id)
              if e.type == "acting_for"]
    assert acting, "a run under an inherited mandate was silent about it"
    assert acting[0].payload["for_agent"] == lead.id
    assert acting[0].payload["decisions"] == ["raise_payment"]


def test_a_leader_that_comes_back_ends_the_standing_in(platform):
    """A standing-in that outlives the outage is a reorganisation nobody
    approved."""
    agent = platform.org.agent("agt_fin_analyst")
    for _ in range(agent.harness.escalate_to_human_after_failures):
        s = platform.sessions.create(agent.id)
        platform.sessions.set_state(s.id, SessionState.FAILED)
    platform.runtime.stand_down(agent.id)
    assert platform.org.acting_for(agent.id) is not None

    platform.runtime.run(agent.id, "I am back.")
    assert platform.org.acting_for(agent.id) is None


# -- the honesty line -------------------------------------------------------


def test_no_generated_conformance_report_claims_succession():
    root = Path(__file__).resolve().parents[1]
    reports = list(root.glob("examples/**/CONFORMANCE.md"))
    assert reports
    for report in reports:
        text = report.read_text()
        for claim in ("successor", "stands in", "acting_for"):
            assert claim not in text, f"{report} claims {claim}"
