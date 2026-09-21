"""Who enforces a control, and what happens when nobody does (ADR-0073).

An agentic organization does not replace its enterprise applications. The ERP
owns the ledger, the treasury system owns the sweep, the bank owns the payment.
A spec that describes a bound without saying who checks it reads as governed
when it may not be — which is worse than saying nothing.
"""
from __future__ import annotations

import pathlib

import pytest

from orgagents.phases import review, trusted_controls
from orgagents.spec.loader import load_spec
from orgagents.spec.model import (
    PLATFORM_EVALUATED_CONSTRAINTS,
    ControlEnforcement,
    ControlEnforcer,
    SystemSpec,
    condition_is_evaluable,
)
from orgagents.spec.validate import errors, validate_spec


def _spec(*, constraints=None, mandate=None, separations=None) -> SystemSpec:
    return SystemSpec.model_validate({
        "metadata": {"name": "t", "spec_version": "1.1.0", "version": "0.1.0"},
        "decisions": [{"id": "spend"}],
        "capabilities": [{"id": "c", "action": "query", "resource_class": "x",
                          "constraints": constraints or {}}],
        "separations": separations or [],
        "organization": {
            "id": "root", "name": "R", "leader": "a",
            "members": [{"id": "a", "name": "A",
                         "mandate": mandate or {"decisions": []}}],
            "teams": [],
        },
    })


def _codes(spec) -> set[str]:
    return {f.code for f in errors(validate_spec(spec))}


# --------------------------------------------------------------------------
# Rule 3: what we claim, we must be able to check
# --------------------------------------------------------------------------


def test_the_default_claims_the_control_for_this_platform():
    """So declaring a bound obliges us to evaluate it."""
    assert ControlEnforcement().enforced_by is ControlEnforcer.PLATFORM
    assert ControlEnforcement().checked_here


def test_a_constraint_nothing_evaluates_is_refused():
    """`rate_per_minute` is declared in the model and enforced nowhere — the
    exact defect this record exists to catch."""
    assert "rate_per_minute" not in PLATFORM_EVALUATED_CONSTRAINTS
    assert "unenforceable_platform_control" in _codes(
        _spec(constraints={"rate_per_minute": 60})
    )


def test_the_same_constraint_passes_when_left_to_the_application():
    """We may describe a control somebody else enforces. We may not claim it."""
    assert "unenforceable_platform_control" not in _codes(_spec(constraints={
        "rate_per_minute": 60,
        "enforcement": {"enforced_by": "application",
                        "enforced_in": "the API gateway"},
    }))


def test_a_constraint_we_do_evaluate_passes():
    assert "unenforceable_platform_control" not in _codes(
        _spec(constraints={"max_rows": 100})
    )


def test_an_unparseable_mandate_condition_is_refused_at_the_gate():
    """It was already refused at the tool boundary (ADR-0071); now the spec
    does not compile, which is where the author is looking."""
    assert not condition_is_evaluable("only_on_a_tuesday")
    assert "unenforceable_platform_control" in _codes(_spec(
        mandate={"decisions": ["spend"], "conditions": {"only_on_a_tuesday": True}}
    ))


# --------------------------------------------------------------------------
# Rule 4: 'both' names an authoritative side
# --------------------------------------------------------------------------


def test_both_without_an_authoritative_side_is_refused():
    """Two rule sets that can disagree need an answer, not a debate."""
    assert "both_without_authority" in _codes(_spec(constraints={
        "max_rows": 10, "enforcement": {"enforced_by": "both"},
    }))


def test_both_naming_both_as_authoritative_answers_nothing():
    assert "both_without_authority" in _codes(_spec(constraints={
        "max_rows": 10,
        "enforcement": {"enforced_by": "both", "authoritative": "both"},
    }))


def test_both_still_has_to_be_evaluable_here():
    """`both` means we check it too, so our half must be real."""
    assert "unenforceable_platform_control" in _codes(_spec(constraints={
        "rate_per_minute": 60,
        "enforcement": {"enforced_by": "both", "authoritative": "application"},
    }))


# --------------------------------------------------------------------------
# Rule 7: our bound may narrow the application's and never widen it
# --------------------------------------------------------------------------


def test_a_platform_bound_wider_than_the_applications_is_refused():
    assert "platform_bound_wider_than_application" in _codes(_spec(mandate={
        "decisions": ["spend"], "conditions": {"max_value_gbp": 900},
        "enforcement": {"enforced_by": "both", "authoritative": "application",
                        "application_bounds": {"max_value_gbp": 500}},
    }))


def test_a_narrower_platform_bound_is_fine():
    assert "platform_bound_wider_than_application" not in _codes(_spec(mandate={
        "decisions": ["spend"], "conditions": {"max_value_gbp": 100},
        "enforcement": {"enforced_by": "both", "authoritative": "application",
                        "application_bounds": {"max_value_gbp": 500}},
    }))


def test_an_unrecorded_application_bound_is_not_treated_as_permission():
    """We cannot check what nobody wrote down, and we do not assume it is
    wide enough."""
    codes = _codes(_spec(mandate={
        "decisions": ["spend"], "conditions": {"max_value_gbp": 900},
        "enforcement": {"enforced_by": "both", "authoritative": "application"},
    }))
    assert "platform_bound_wider_than_application" not in codes


# --------------------------------------------------------------------------
# Rules 2 and 5: say what we are trusting somebody else for
# --------------------------------------------------------------------------


def test_an_application_control_with_no_named_system_is_reported():
    findings = validate_spec(_spec(constraints={
        "rate_per_minute": 60, "enforcement": {"enforced_by": "application"},
    }))
    assert any(f.code == "application_control_unnamed" for f in findings)


def test_trusted_controls_lists_what_this_deployment_does_not_enforce():
    spec = _spec(constraints={
        "max_rows": 10,
        "enforcement": {"enforced_by": "application",
                        "enforced_in": "the ERP"},
    })
    assert trusted_controls(spec) == [("c", "capability constraints", "the ERP")]


def test_a_deployment_that_enforces_everything_says_so():
    assert trusted_controls(_spec(constraints={"max_rows": 10})) == []


# --------------------------------------------------------------------------
# The worked finance example
# --------------------------------------------------------------------------


@pytest.fixture()
def northwind() -> SystemSpec:
    return load_spec(pathlib.Path("examples/northwind.finance.system.yaml"))


def test_the_finance_example_attributes_its_controls(northwind):
    assert not errors(validate_spec(northwind))
    trusted = {where for where, _, _ in trusted_controls(northwind)}
    # The cumulative treasury limit and document-level segregation are the
    # applications'. We check a per-transaction ceiling and a role-level
    # approximation, and we say so rather than implying more.
    assert {"cash_manager", "payment_control"} <= trusted


def test_the_report_names_the_system_behind_each_trusted_control(northwind):
    report = review(northwind, binding=None, target=None)
    check = next(c for c in report.of("definition")
                 if c.id == "controls_are_attributed")
    assert check.status == "pass"
    assert "treasury management system" in check.title
    assert "ERP" in check.title
