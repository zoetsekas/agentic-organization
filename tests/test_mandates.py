"""Authority: what a unit may decide, and what happens when it may not.

Permission and mandate answer different questions (ADR-0065). Permission is
whether the door opens; a mandate is whether you were the one to open it. The
difference shows up on a "no": a missing permission is a dead end, while a
decision above your authority belongs to somebody.
"""
from __future__ import annotations

import pytest

from orgagents.compiler.ir import build_ir
from orgagents.mandates import EffectiveMandate, resolve, root_mandate
from orgagents.models import ToolBinding
from orgagents.spec.model import Mandate, SystemSpec
from orgagents.spec.validate import errors, validate_spec


# --------------------------------------------------------------------------
# Resolution: authority narrows downward
# --------------------------------------------------------------------------


def _org(spec_dict) -> SystemSpec:
    return SystemSpec.model_validate(spec_dict)


BASE = {
    "metadata": {"name": "t", "spec_version": "1.1.0", "version": "0.1.0"},
    "decisions": [{"id": "spend"}, {"id": "deploy"}, {"id": "close"}],
    "organization": {
        "id": "root",
        "name": "Root",
        "mandate": {"decisions": ["spend", "deploy", "close"]},
        "members": [{"id": "ceo", "name": "CEO"}],
        "teams": [
            {
                "id": "fin",
                "name": "Finance",
                "mandate": {"decisions": ["spend", "close"],
                            "conditions": {"max_value": 250}},
                "members": [
                    {"id": "cfo", "name": "CFO"},
                    {"id": "clerk", "name": "Clerk",
                     "mandate": {"decisions": ["close"]}},
                ],
            }
        ],
    },
}


def test_a_unit_declaring_nothing_inherits_rather_than_holding_everything():
    """Silence is the one thing that must never read as authority."""
    m = resolve(_org(BASE).organization)
    assert m.for_agent("cfo").decisions == {"spend", "close"}
    assert m.for_agent("ceo").decisions == {"spend", "deploy", "close"}


def test_a_child_narrows_and_cannot_widen():
    m = resolve(_org(BASE).organization)
    assert m.for_agent("clerk").decisions == {"close"}
    assert "deploy" not in m.teams["fin"].decisions


def test_claiming_more_than_the_line_holds_is_narrowed_not_honoured():
    spec = dict(BASE)
    spec["organization"] = {
        **BASE["organization"],
        "teams": [{**BASE["organization"]["teams"][0],
                   "mandate": {"decisions": ["spend", "deploy"]}}],
    }
    m = resolve(_org(spec).organization)
    # 'deploy' *is* held by the root, so Finance claiming it is legal.
    assert m.teams["fin"].decisions == {"spend", "deploy"}

    # Claiming something nobody above holds is a different matter.
    spec["organization"]["teams"][0]["mandate"] = {"decisions": ["spend", "hire"]}
    m = resolve(_org(spec).organization)
    assert m.teams["fin"].decisions == {"spend"}
    assert m.overreach["fin"] == ["hire"]


def test_conditions_accumulate_so_a_child_cannot_loosen_a_parents():
    m = resolve(_org(BASE).organization)
    assert {"max_value": 250} in m.for_agent("clerk").conditions


def test_the_holder_of_a_decision_is_the_smallest_unit_that_has_it():
    m = resolve(_org(BASE).organization)
    assert m.holder("clerk", "close") == "clerk"        # holds it itself
    assert m.holder("clerk", "spend") == "fin"          # nearest unit above
    assert m.holder("clerk", "hire") is None            # nobody, at all


# --------------------------------------------------------------------------
# The root is where authority enters
# --------------------------------------------------------------------------


def test_a_root_without_a_mandate_is_a_spec_error():
    spec = {**BASE, "organization": {**BASE["organization"], "mandate": None}}
    found = errors(validate_spec(_org(spec)))
    assert any(f.code == "root_without_mandate" for f in found)


def test_an_empty_root_mandate_is_the_same_error_not_unlimited_authority():
    spec = {**BASE,
            "organization": {**BASE["organization"], "mandate": {"decisions": []}}}
    assert any(f.code == "root_without_mandate" for f in errors(validate_spec(_org(spec))))


def test_a_mandate_naming_an_undeclared_decision_is_refused():
    spec = {**BASE, "decisions": [{"id": "spend"}]}
    found = errors(validate_spec(_org(spec)))
    assert any(f.code == "undeclared_decision" for f in found)


def test_a_list_of_sentences_is_refused_with_a_reason():
    """`mandate` used to be prose. Coercing it would have manufactured a
    machine-readable wrong term, which is worse than the prose was."""
    with pytest.raises(ValueError) as excinfo:
        Mandate.model_validate(["Run the workforce safely."])
    assert "scope of decision" in str(excinfo.value)


# --------------------------------------------------------------------------
# Resolved once, at the phase gate
# --------------------------------------------------------------------------


def test_the_ir_carries_effective_authority_not_the_declaration():
    ir = build_ir(_org(BASE))
    clerk = next(a for a in ir.agents if a.id == "clerk")
    assert clerk.mandate.decisions == ["close"]
    assert clerk.mandate.conditions == [{"max_value": 250}]
    # The line explains a refusal to somebody who did not write the spec.
    assert clerk.mandate.line == ["root", "fin", "clerk"]


def test_root_authority_is_taken_as_declared_because_nothing_narrows_it():
    eff = root_mandate(Mandate(decisions=["a", "b"]), "root")
    assert eff.decisions == {"a", "b"}
    assert root_mandate(None).decisions == frozenset()


# --------------------------------------------------------------------------
# Runtime: permission refuses, mandate escalates
# --------------------------------------------------------------------------


def _wire(platform, agent_id: str, tool: str, decision: str, mandate: list[str]):
    agent = platform.org.agent(agent_id)
    agent.mandate = mandate
    agent.harness.tools.append(ToolBinding(name=tool, source="builtin",
                                           decision=decision))
    return _save(platform, agent)


def _save(platform, agent):
    """The org chart reads from the store, so a holder must be persisted."""
    from orgagents.store import AGENTS

    platform.store.put(AGENTS, agent, parent=agent.manager_agent_id)
    return agent


def test_an_action_inside_permission_and_inside_mandate_proceeds(platform):
    analyst = _wire(platform, "agt_fin_analyst", "db_warehouse__query",
                    "read_warehouse", ["read_warehouse"])
    out = platform.harness.call(analyst, "db_warehouse__query",
                                sql="SELECT name FROM customers")
    assert out.ok
    assert out.escalate_to is None


def test_an_action_outside_the_mandate_escalates_to_whoever_holds_it(platform):
    analyst = _wire(platform, "agt_fin_analyst", "db_warehouse__query",
                    "close_period", [])
    holder = platform.org.agent(analyst.manager_agent_id)
    holder.mandate = ["close_period"]
    _save(platform, holder)

    out = platform.harness.call(analyst, "db_warehouse__query",
                                sql="SELECT name FROM customers")
    assert not out.ok
    assert out.decision == "close_period"
    assert out.escalate_to == holder.id
    assert holder.name in out.error


def test_a_decision_nobody_holds_is_refused_rather_than_promoted(platform):
    """Silence never promotes: walking off the top of the chain is a refusal,
    not an escalation to the root."""
    analyst = _wire(platform, "agt_fin_analyst", "db_warehouse__query",
                    "dissolve_the_company", [])
    out = platform.harness.call(analyst, "db_warehouse__query",
                                sql="SELECT name FROM customers")
    assert not out.ok
    assert out.escalate_to is None
    assert "no unit in the organization" in out.error


def test_a_missing_permission_is_refused_without_escalating(platform):
    """Sending a human an action the agent could never perform spends their
    attention on nothing, so permission is checked first."""
    analyst = platform.org.agent("agt_fin_analyst")
    analyst.mandate = ["anything"]
    out = platform.harness.call(analyst, "no_such_tool")
    assert not out.ok
    assert out.escalate_to is None and out.decision is None


def test_a_tool_that_decides_nothing_never_consults_a_mandate(platform):
    """Most work is not a decision; requiring a mandate for all of it would
    make the mandate meaningless."""
    analyst = platform.org.agent("agt_fin_analyst")
    analyst.mandate = []
    out = platform.harness.call(analyst, "db_warehouse__query",
                                sql="SELECT name FROM customers")
    assert out.ok


# --------------------------------------------------------------------------
# Missions cannot leave standing authority behind
# --------------------------------------------------------------------------


def test_a_mission_mandate_is_bounded_by_the_unit_accountable_for_it():
    from orgagents.mandates import mission_mandate
    from orgagents.spec.model import Mission

    leader = EffectiveMandate(frozenset({"spend"}), (), ("root", "fin"))
    mission = Mission(id="m1", mandate=Mandate(decisions=["spend", "deploy"]))
    lent = mission_mandate(mission, leader)
    assert lent.decisions == {"spend"}, "a mission cannot create authority"
