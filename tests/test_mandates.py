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
                "leader": "cfo",
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


def test_the_holder_of_a_decision_is_an_agent_never_a_unit():
    """A team is a scope, not a principal (ADR-0070).

    This used to return a team id, which named something that cannot act and
    disagreed with `OrgChart.mandate_holder`, which walks agents. The walk is
    leader to leader, and what is tested is the leader's own effective
    mandate — so a leader narrowed for separation of duties does not hold what
    its unit merely bounds.
    """
    m = resolve(_org(BASE).organization)
    assert m.holder("clerk", "close") == "clerk"        # holds it itself
    assert m.holder("clerk", "spend") == "cfo"          # the unit's leader
    assert m.holder("clerk", "hire") is None            # nobody, at all


def test_a_narrowed_leader_is_walked_past_rather_than_treated_as_the_holder():
    """The whole point of separation: escalation must not find the one
    principal who holds both sides of a control."""
    spec = {
        **BASE,
        "organization": {
            **BASE["organization"],
            "teams": [
                {
                    **BASE["organization"]["teams"][0],
                    # The leader declares less than the unit bounds.
                    "members": [
                        {"id": "cfo", "name": "CFO",
                         "mandate": {"decisions": ["close"]}},
                        {"id": "clerk", "name": "Clerk",
                         "mandate": {"decisions": ["close"]}},
                    ],
                }
            ],
        },
    }
    m = resolve(_org(spec).organization)
    assert "spend" in m.teams["fin"].decisions, "the unit still bounds it"
    assert m.holder("clerk", "spend") is None, "but no principal in the line holds it"


def test_holders_names_who_can_take_a_decision_without_routing_to_them():
    m = resolve(_org(BASE).organization)
    assert m.holders("spend") == ["ceo", "cfo"]
    # Naming a holder is not reaching one: `holder` stays confined to the line.
    assert m.holder("clerk", "hire") is None


# --------------------------------------------------------------------------
# The root is where authority enters
# --------------------------------------------------------------------------


def test_a_root_team_without_a_mandate_holds_the_declared_vocabulary():
    """A team is a scope and nobody exercises it (ADR-0070), so the root may
    default (ADR-0071).

    Requiring it to enumerate every decision is what made authority accumulate
    upward, and what made adding one function to a leaf an edit of every unit
    between it and the root.
    """
    spec = {**BASE, "organization": {**BASE["organization"], "mandate": None,
                                     "leader": "ceo"}}
    parsed = _org(spec)
    m = resolve(parsed.organization, [d.id for d in parsed.decisions])
    assert m.teams["root"].decisions == {"spend", "deploy", "close"}


def test_the_root_leader_must_declare_because_a_principal_is_never_silent():
    """The root team may default; the agent at the top may not.

    It is a principal, it inherits its unit's mandate, and at the root that is
    everything.
    """
    spec = {**BASE, "organization": {**BASE["organization"], "mandate": None,
                                     "leader": "ceo"}}
    found = errors(validate_spec(_org(spec)))
    assert any(f.code == "root_leader_without_mandate" for f in found)


def test_an_explicit_empty_mandate_says_it_decides_nothing():
    spec = {
        **BASE,
        "organization": {
            **BASE["organization"], "mandate": None, "leader": "ceo",
            "members": [{"id": "ceo", "name": "CEO", "mandate": {"decisions": []}}],
        },
    }
    parsed = _org(spec)
    assert not [f for f in errors(validate_spec(parsed))
                if f.code == "root_leader_without_mandate"]
    m = resolve(parsed.organization, [d.id for d in parsed.decisions])
    assert m.for_agent("ceo").decisions == frozenset()


def test_a_claim_no_ancestor_holds_is_an_error_not_a_warning():
    """Silently deciding nothing is the worst of the three outcomes (ADR-0071).

    Before this, a leaf that declared authority its line excluded produced a
    warning and an agent that quietly decided nothing — which in a finance
    function reads as work stopping for no stated reason.
    """
    spec = {
        **BASE,
        "organization": {
            **BASE["organization"], "leader": "ceo",
            "members": [{"id": "ceo", "name": "CEO", "mandate": {"decisions": []}}],
            "teams": [{**BASE["organization"]["teams"][0],
                       "mandate": {"decisions": ["close"]},
                       "members": [{"id": "clerk", "name": "Clerk",
                                    "mandate": {"decisions": ["spend"]}}]}],
        },
    }
    found = errors(validate_spec(_org(spec)))
    assert any(f.code == "mandate_overreach" and f.where == "clerk" for f in found)


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
    assert "outside every mandate above this agent" in out.error
    assert "no agent in the organization holds it" in out.error


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


# --------------------------------------------------------------------------
# The framework must not be able to route around the policy (ADR-0067 rule 5)
# --------------------------------------------------------------------------


def test_the_toolset_handed_to_a_framework_carries_the_policy_with_it(platform):
    """The runtime hands callables to deep agents or the OpenAI SDK, which
    invoke them directly and never through `HarnessBuilder.call`.

    Until these were wrapped, every mandate and approval gate bound only
    callers that were already going through the front door — so an actual
    agent run enforced nothing.
    """
    analyst = _wire(platform, "agt_fin_analyst", "db_warehouse__query",
                    "close_period", [])
    raw = platform.harness.build(analyst)["db_warehouse__query"]
    guarded = platform.harness.guarded(
        analyst, {"db_warehouse__query": raw}
    )["db_warehouse__query"]

    out = guarded(sql="SELECT name FROM customers")
    assert out["ok"] is False
    assert out["decision"] == "close_period"


def test_a_guarded_tool_still_works_when_policy_allows_it(platform):
    analyst = _wire(platform, "agt_fin_analyst", "db_warehouse__query",
                    "close_period", ["close_period"])
    tools = platform.harness.guarded(analyst, platform.harness.build(analyst))
    out = tools["db_warehouse__query"](sql="SELECT name FROM customers")
    assert "rows" in out


def test_an_approval_gate_also_survives_the_framework_path(platform):
    analyst = platform.org.agent("agt_fin_analyst")
    analyst.harness.interrupt_on = ["db_warehouse__query"]
    _save(platform, analyst)
    tools = platform.harness.guarded(analyst, platform.harness.build(analyst))
    out = tools["db_warehouse__query"](sql="SELECT name FROM customers")
    assert out["ok"] is False and out["requires_approval"] is True


def test_no_generated_artifact_names_a_framework():
    """ADR-0067 rule 1: the spec names no framework, and neither does the IR.

    `Runtime` is a binding concern. A framework name reaching the IR would
    mean a design had been made to depend on one.
    """
    import pathlib

    from orgagents.compiler.ir import build_ir
    from orgagents.spec.loader import load_spec

    ir = build_ir(load_spec(pathlib.Path("examples/acme.system.yaml")))
    blob = ir.model_dump_json()
    for name in ("deepagents", "deep_agents", "langchain", "langgraph",
                 "openai_agents", "agents_sdk"):
        assert name not in blob.lower(), f"the IR names {name}"


# --------------------------------------------------------------------------
# Separation of duties (ADR-0070)
# --------------------------------------------------------------------------


SEPARATED = {
    **BASE,
    "separations": [
        {"id": "payment_control", "decisions": ["spend", "close"],
         "reason": "one principal must not do both"},
    ],
}


def test_a_leader_inheriting_its_units_mandate_violates_separation():
    """This is the finance case, and it is the whole finding.

    Narrowing requires a parent to hold at least the union of its children.
    Separation requires that no principal holds both sides. Both cannot govern
    the same objects, so the leader must declare a narrower mandate and the
    gate refuses the spec that does not.
    """
    found = errors(validate_spec(_org(SEPARATED)))
    violations = [f for f in found if f.code == "separation_violated"]
    assert violations, "an inheriting leader holds both sides and nothing noticed"
    assert any(f.where == "cfo" for f in violations)
    assert "declare a narrower mandate" in violations[0].message


def test_a_narrowed_leader_passes():
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
                         "mandate": {"decisions": ["close"]}},
                        {"id": "clerk", "name": "Clerk",
                         "mandate": {"decisions": ["spend"]}},
                    ],
                }
            ],
        },
    }
    assert not [f for f in errors(validate_spec(_org(spec)))
                if f.code == "separation_violated"]


def test_a_team_may_hold_both_sides_because_a_team_cannot_act():
    """A unit's mandate bounds its members; nobody exercises it (ADR-0070)."""
    spec = {
        **SEPARATED,
        "organization": {
            **SEPARATED["organization"],
            "members": [{"id": "ceo", "name": "CEO",
                         "mandate": {"decisions": []}}],
            "teams": [
                {
                    **SEPARATED["organization"]["teams"][0],
                    "members": [
                        {"id": "cfo", "name": "CFO",
                         "mandate": {"decisions": ["close"]}},
                        {"id": "clerk", "name": "Clerk",
                         "mandate": {"decisions": ["spend"]}},
                    ],
                }
            ],
        },
    }
    parsed = _org(spec)
    m = resolve(parsed.organization)
    assert {"spend", "close"} <= m.teams["fin"].decisions, "the scope holds both"
    assert not [f for f in errors(validate_spec(parsed))
                if f.code == "separation_violated"]


def test_a_separation_naming_an_undeclared_decision_is_refused():
    spec = {**BASE, "separations": [{"id": "x", "decisions": ["spend", "hire"]}]}
    found = errors(validate_spec(_org(spec)))
    assert any(f.code == "undeclared_decision" for f in found)


def test_the_worked_finance_example_holds_its_own_controls():
    """Northwind exists to be the counterexample, so it must stay one."""
    import pathlib

    from orgagents.spec.loader import load_spec

    spec = load_spec(pathlib.Path("examples/northwind.finance.system.yaml"))
    assert spec.separations, "the example must declare the controls it tests"
    assert not errors(validate_spec(spec))

    m = resolve(spec.organization, [d.id for d in spec.decisions])
    for rule in spec.separations:
        for agent_id, effective in m.agents.items():
            held = set(rule.decisions) & effective.decisions
            assert len(held) <= 1, f"{agent_id} holds {sorted(held)}"

    # And escalation cannot route a payment release around the control.
    assert m.holder("payables", "release_payment") is None
    assert m.holders("release_payment") == ["treasurer"]


# --------------------------------------------------------------------------
# ADR-0071: conditions are evaluated, and separation survives the binding
# --------------------------------------------------------------------------


def _bounded(platform, agent_id, tool, decision, mandate, conditions):
    """An agent with a bounded mandate, and a tool that takes the bounded field.

    A condition checks an argument the *call* carries — the amount on a payment,
    the currency on a placement — so the tool has to accept it. Policy that
    reads a field the tool does not take would bound nothing.
    """
    agent = platform.org.agent(agent_id)
    agent.mandate = mandate
    agent.mandate_conditions = conditions
    agent.harness.tools.append(ToolBinding(name=tool, source="builtin",
                                           decision=decision))
    _save(platform, agent)

    def stub(**kwargs):
        return {"ok": True, "args": kwargs}

    return agent, platform.harness.guarded(agent, {tool: stub})[tool]


def test_a_ceiling_refuses_a_call_that_exceeds_it(platform):
    """`max_value_gbp: 250000` used to bound nothing at all."""
    _, tool = _bounded(platform, "agt_fin_analyst", "approve", "approve_spend",
                       ["approve_spend"], [{"max_value_gbp": 250000}])
    out = tool(value_gbp=400000)
    assert out["ok"] is False
    assert "exceeds max_value_gbp" in out["error"]


def test_a_call_within_the_ceiling_proceeds(platform):
    _, tool = _bounded(platform, "agt_fin_analyst", "approve", "approve_spend",
                       ["approve_spend"], [{"max_value_gbp": 250000}])
    assert tool(value_gbp=1000)["ok"] is True


def test_omitting_the_field_is_a_refusal_not_a_pass(platform):
    """Otherwise leaving out the amount removes the ceiling."""
    _, tool = _bounded(platform, "agt_fin_analyst", "approve", "approve_spend",
                       ["approve_spend"], [{"max_value_gbp": 250000}])
    out = tool()
    assert out["ok"] is False
    assert "supplies no 'value_gbp'" in out["error"]


def test_a_condition_we_cannot_evaluate_is_a_refusal(platform):
    """Silently ignoring an unparseable bound is how these became decorative."""
    _, tool = _bounded(platform, "agt_fin_analyst", "approve", "approve_spend",
                       ["approve_spend"], [{"only_on_a_tuesday": True}])
    out = tool()
    assert out["ok"] is False
    assert "cannot evaluate" in out["error"]


def test_conditions_chain_so_a_child_cannot_loosen_a_parents(platform):
    _, tool = _bounded(platform, "agt_fin_analyst", "approve", "approve_spend",
                       ["approve_spend"],
                       [{"max_value_gbp": 5000}, {"max_value_gbp": 1000000}])
    out = tool(value_gbp=9000)
    assert out["ok"] is False, "the tighter bound in the line still applies"


def test_a_membership_condition_is_evaluated(platform):
    _, tool = _bounded(platform, "agt_fin_analyst", "approve", "approve_spend",
                       ["approve_spend"], [{"currency_in": ["GBP", "EUR"]}])
    assert tool(currency="JPY")["ok"] is False
    assert tool(currency="GBP")["ok"] is True


def test_the_worked_binding_keeps_the_two_sides_of_the_payment_control_apart():
    """Separation decided in the spec means nothing if the ERP and the bank
    turn out to be one connection (ADR-0071)."""
    import pathlib

    from orgagents.phases import review
    from orgagents.spec import load_binding, load_spec

    spec = load_spec(pathlib.Path("examples/northwind.finance.system.yaml"))
    binding = load_binding(pathlib.Path("examples/northwind.binding.yaml"))
    report = review(spec, binding=binding, target="local")
    checks = [c for c in report.of("implementation")
              if c.id.startswith("separation_survives_binding")]
    assert checks, "the binding was never checked against the separations"
    failed = [c.detail for c in checks if c.status != "pass"]
    assert not failed, failed
