"""Deny-by-default, deny-wins, narrow-only inheritance (ADR-0008, ADR-0015)."""
from pathlib import Path

import pytest

from orgagents.compiler import build_ir
from orgagents.security import PolicyEngine, Request, Subject
from orgagents.spec import load_spec
from orgagents.spec.model import Action, Effect, Permission, PolicyRule, ResourceKind

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "acme.system.yaml"


@pytest.fixture(scope="module")
def ir():
    return build_ir(load_spec(EXAMPLE))


@pytest.fixture()
def engine(ir):
    return PolicyEngine(ir.permission_map(), ir.policies)


def _subject(ir, agent_id: str) -> Subject:
    agent = ir.agent(agent_id)
    return Subject(
        id=agent.id,
        team_id=agent.team_id,
        team_path=tuple(agent.team_path),
        role_ids=tuple(agent.role_ids),
        groups=tuple(agent.groups),
        is_leader=bool(agent.leader_of),
    )


def test_default_deny(engine, ir):
    decision = engine.decide(
        _subject(ir, "analyst"),
        Request(Action.WRITE, ResourceKind.DATA_CLASS, "engineering_internal"),
    )
    assert not decision.allowed
    assert "no permission grants" in decision.reason


def test_granted_permission_allows(engine, ir):
    decision = engine.decide(
        _subject(ir, "analyst"),
        Request(Action.QUERY, ResourceKind.CAPABILITY, "warehouse_query"),
    )
    assert decision.allowed
    assert decision.matched_permission == "query:capability:warehouse_query"


def test_deny_wins_over_a_grant(ir):
    """A matching deny cannot be overridden by any allow, for anyone."""
    permissive = [
        Permission(action=Action.QUERY, resource_kind=ResourceKind.DATA_CLASS,
                   resource="*")
    ]
    engine = PolicyEngine({"ceo": permissive}, ir.policies)
    decision = engine.decide(
        _subject(ir, "ceo"),
        Request(Action.QUERY, ResourceKind.DATA_CLASS, "customer_pii",
                context={"environment": "build"}),
    )
    assert not decision.allowed
    assert decision.rule_id == "pii_never_leaves_clean_room"


def test_leaders_do_not_escalate(engine, ir):
    """Being a leader grants no access its roles did not (ADR-0008)."""
    decision = engine.decide(
        _subject(ir, "cfo"),
        Request(Action.QUERY, ResourceKind.CAPABILITY, "pii_reconciliation"),
    )
    assert not decision.allowed


def test_conditions_gate_the_clean_room(engine, ir):
    subject = _subject(ir, "reconciler")
    outside = engine.decide(
        subject,
        Request(Action.QUERY, ResourceKind.CAPABILITY, "pii_reconciliation",
                context={"environment": "analysis", "approved": True}),
    )
    inside = engine.decide(
        subject,
        Request(Action.QUERY, ResourceKind.CAPABILITY, "pii_reconciliation",
                context={"environment": "isolated_review", "approved": True}),
    )
    assert not outside.allowed and inside.allowed


def test_approval_condition_is_enforced(engine, ir):
    decision = engine.decide(
        _subject(ir, "reconciler"),
        Request(Action.QUERY, ResourceKind.CAPABILITY, "pii_reconciliation",
                context={"environment": "isolated_review"}),
    )
    assert not decision.allowed


def test_delegation_depth_limit(engine, ir):
    subject = _subject(ir, "ceo")
    shallow = engine.decide(
        subject,
        Request(Action.DELEGATE, ResourceKind.AGENT, "cfo",
                context={"delegation_depth": 2}),
    )
    deep = engine.decide(
        subject,
        Request(Action.DELEGATE, ResourceKind.AGENT, "cfo",
                context={"delegation_depth": 9}),
    )
    assert shallow.allowed and not deep.allowed


def test_no_agent_holds_a_permission_no_role_granted(ir):
    """Resolution may never invent a permission (ADR-0008)."""
    spec = load_spec(EXAMPLE)
    granted = {p.key() for role in spec.roles for p in role.permissions}
    for agent in ir.agents:
        for perm in agent.permissions:
            assert perm.key() in granted, f"{agent.id} holds unsourced {perm.key()}"


def test_withhold_narrows_an_assignment():
    from orgagents.spec.model import RoleAssignment

    spec = load_spec(EXAMPLE)
    assignment = spec.agent("analyst").roles[0]
    spec.agent("analyst").roles[0] = RoleAssignment(
        role=assignment.role, withhold=["write:data_class:public_knowledge"]
    )
    ir = build_ir(spec)
    keys = {p.key() for p in ir.agent("analyst").permissions}
    assert "write:data_class:public_knowledge" not in keys
    assert "query:capability:warehouse_query" in keys


def test_every_agent_gets_its_own_identity(ir):
    identities = [a.identity.id for a in ir.agents]
    assert len(identities) == len(set(identities)) == len(ir.agents)
    for agent in ir.agents:
        assert set(agent.identity.permissions) == {p.key() for p in agent.permissions}


def test_explain_names_the_deciding_rule(engine, ir):
    explanation = engine.explain(
        _subject(ir, "ceo"),
        Request(Action.QUERY, ResourceKind.DATA_CLASS, "customer_pii"),
    )
    assert explanation["allowed"] is False
    assert explanation["reason"]
