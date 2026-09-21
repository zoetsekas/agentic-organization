"""A policy condition nothing evaluates is not inert (ADR-0008).

The designer had no form for a policy rule because its `conditions` and
`unless` are `dict[str, Any]` and the vocabulary lived in the evaluator's
docstring — a list a human can read is not one a picker can offer.

Chasing that found the reason it mattered. An unknown key was **silently
ignored**, and ignoring is not neutral: on an `unless` guard it disapplies the
whole rule. One transposed letter — `reqiures_approval` — turned a deny on PII
into an allow.
"""
from __future__ import annotations

import pytest

from orgagents.security.rbac import (
    PolicyEngine,
    Request,
    Subject,
    UnevaluableCondition,
    unknown_condition_keys,
)
from orgagents.spec.model import (
    POLICY_CONDITION_KEYS,
    Action,
    Effect,
    Permission,
    PolicyRule,
    ResourceKind,
    SystemSpec,
)
from orgagents.spec.validate import validate_spec

GRANTED = {
    "analyst": [Permission(action=Action.WRITE,
                           resource_kind=ResourceKind.DATA_CLASS, resource="*")]
}


def _decide(unless=None, conditions=None):
    rule = PolicyRule(
        id="no_pii_out", effect=Effect.DENY, description="PII may not leave",
        actions=[Action.WRITE], resource_kinds=[ResourceKind.DATA_CLASS],
        resources=["*"], subjects=["*"],
        conditions=conditions or {}, unless=unless or {},
    )
    return PolicyEngine(permissions=GRANTED, rules=[rule]).decide(
        Subject(id="analyst", groups=[]),
        Request(action=Action.WRITE, resource_kind=ResourceKind.DATA_CLASS,
                resource="customer_pii", context={}),
    )


# --------------------------------------------------------------------------
# The fail-open
# --------------------------------------------------------------------------


def test_a_deny_with_no_guard_denies():
    assert _decide().allowed is False


def test_a_deny_with_a_guard_that_does_not_hold_still_denies():
    """`unless` disapplies the rule only where it actually holds."""
    assert _decide(unless={"groups": ["clean_room"]}).allowed is False


def test_one_transposed_letter_no_longer_switches_a_deny_off():
    """This is the defect, in one line.

    `reqiures_approval` is not a key anything evaluates. It used to be
    ignored, which made the `unless` guard hold trivially, which disapplied
    the deny — and the PII write was allowed by the permission underneath.
    """
    decision = _decide(unless={"reqiures_approval": True})
    assert decision.allowed is False
    assert "cannot be evaluated" in decision.reason
    assert "reqiures_approval" in decision.reason


def test_an_unknown_key_in_conditions_is_refused_too():
    decision = _decide(conditions={"data_clases": ["pii"]})
    assert decision.allowed is False
    assert "data_clases" in decision.reason


def test_the_refusal_says_what_the_platform_does_understand():
    reason = _decide(unless={"tenant": "acme"}).reason
    for key in POLICY_CONDITION_KEYS:
        assert key in reason


def test_an_unevaluable_condition_raises_rather_than_returning_false():
    """Because `False` is not uniformly safe.

    An ignored condition under-applies an allow and over-applies a deny, and
    an ignored `unless` switches a deny off. The only answer that is safe
    whatever it guards is "this rule cannot be evaluated".
    """
    from orgagents.security.rbac import _conditions_hold

    with pytest.raises(UnevaluableCondition):
        _conditions_hold({"nonsense": 1}, Subject(id="a", groups=[]),
                         Request(action=Action.READ,
                                 resource_kind=ResourceKind.DATA_CLASS,
                                 resource="x", context={}))


def test_the_known_keys_all_evaluate_without_raising():
    from orgagents.security.rbac import _conditions_hold

    subject = Subject(id="a", groups=["ops"])
    request = Request(action=Action.READ, resource_kind=ResourceKind.DATA_CLASS,
                      resource="x",
                      context={"delegation_depth": 0, "approved": True,
                               "environment": "analysis", "data_class": "x",
                               "hour": 10})
    for key, value in (("max_delegation_depth", 3), ("requires_approval", True),
                       ("environments", ["analysis"]), ("data_classes", ["x"]),
                       ("groups", ["ops"]), ("time_window", [9, 17])):
        assert _conditions_hold({key: value}, subject, request) is True, key
    assert unknown_condition_keys({"groups": []}) == []


# --------------------------------------------------------------------------
# The gate refuses it long before the runtime sees it
# --------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["conditions", "unless"])
def test_the_phase_gate_refuses_a_key_nothing_evaluates(where):
    spec = SystemSpec.model_validate({
        "metadata": {"name": "t", "spec_version": "1.1.0", "version": "0.1.0"},
        "policies": [{"id": "p", "effect": "deny", "actions": ["write"],
                      "resource_kinds": ["data_class"],
                      where: {"reqiures_approval": True}}],
        "organization": {"id": "o", "name": "o", "leader": "a",
                         "members": [{"id": "a", "name": "a",
                                      "mandate": {"decisions": []}}]},
    })
    found = [f for f in validate_spec(spec)
             if f.code == "unknown_policy_condition"]
    assert found and found[0].severity == "error"
    assert "reqiures_approval" in found[0].message
    if where == "unless":
        assert "would not deny" in found[0].message


def test_a_policy_using_the_vocabulary_passes():
    spec = SystemSpec.model_validate({
        "metadata": {"name": "t", "spec_version": "1.1.0", "version": "0.1.0"},
        "policies": [{"id": "p", "effect": "deny", "actions": ["write"],
                      "resource_kinds": ["data_class"],
                      "conditions": {"data_classes": ["pii"]},
                      "unless": {"groups": ["clean_room"],
                                 "requires_approval": True}}],
        "organization": {"id": "o", "name": "o", "leader": "a",
                         "members": [{"id": "a", "name": "a",
                                      "mandate": {"decisions": []}}]},
    })
    assert not [f for f in validate_spec(spec)
                if f.code == "unknown_policy_condition"]
