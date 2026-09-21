"""Placement: a sandbox environment keyed by whose work it is (ADR-0069).

An environment class is a profile — toolchain, tier, network posture, egress.
Keying sandbox environments by it puts an HR agent and a Finance agent that
both need `analysis` in one place, because a profile says what an agent needs
and not whose work it is. A placement is an org unit crossed with an
environment class, which is the namespace model an enterprise already has.

The rules that are easy to lose, and are therefore what these tests hold down:
placement is opt-in and inherits; the shared volume is the PROTECTED plane and
never a new grant; only standing structure becomes a network rule, so
mission-lent reach never does; and cross-placement traffic is denied unless
something declared permits it.
"""
from __future__ import annotations

import pathlib

import pytest

from orgagents.compiler.ir import build_ir
from orgagents.placements import resolve, standing_reach
from orgagents.spec.loader import load_spec
from orgagents.spec.model import SystemSpec
from orgagents.spec.validate import errors, validate_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]


BASE = {
    "metadata": {"name": "t", "spec_version": "1.1.0", "version": "0.1.0"},
    "data_classes": [
        {"id": "hr_records", "scope": "protected", "groups": ["hr"]},
        {"id": "ledger", "scope": "protected", "groups": ["finance"]},
        {"id": "public_notes", "scope": "public"},
    ],
    "environments": [
        {"id": "analysis", "network": "allowlist",
         "egress_allowlist": ["api.acme.example"]},
        {"id": "quiet", "network": "none"},
    ],
    "organization": {
        "id": "root",
        "name": "Root",
        "leader": "ceo",
        "members": [
            {"id": "ceo", "name": "CEO",
             "mandate": {"decisions": []},
             "environment": {"environment": "analysis"}},
        ],
        "teams": [
            {
                "id": "hr", "name": "HR", "leader": "hr_lead",
                "groups": ["hr"], "placement": True,
                "members": [
                    {"id": "hr_lead", "name": "HR lead",
                     "environment": {"environment": "analysis"}},
                    {"id": "recruiter", "name": "Recruiter",
                     "environment": {"environment": "analysis"}},
                ],
            },
            {
                "id": "finance", "name": "Finance", "leader": "cfo",
                "groups": ["finance"], "placement": True,
                "members": [
                    {"id": "cfo", "name": "CFO",
                     "environment": {"environment": "analysis"}},
                ],
                "teams": [
                    {
                        "id": "payables", "name": "Payables", "leader": "ap",
                        "members": [
                            {"id": "ap", "name": "AP",
                             "environment": {"environment": "analysis"}},
                        ],
                    }
                ],
            },
        ],
    },
}


def _spec(**overrides) -> SystemSpec:
    import copy

    d = copy.deepcopy(BASE)
    d.update(copy.deepcopy(overrides))
    return SystemSpec.model_validate(d)


# --------------------------------------------------------------------------
# Placement = (org unit, environment class)
# --------------------------------------------------------------------------


def test_the_same_profile_in_two_units_is_two_places():
    """The defect this record exists to fix.

    HR and Finance both need `analysis`. Keyed by the profile they share one
    sandbox environment, one volume and one process namespace.
    """
    resolved = resolve(_spec())
    assert resolved.home["recruiter"] == "hr--analysis"
    assert resolved.home["cfo"] == "finance--analysis"
    assert not resolved.same_placement("recruiter", "cfo")


def test_an_agent_is_placed_by_its_unit_and_never_by_its_profile():
    resolved = resolve(_spec())
    hr = resolved.for_agent("recruiter")
    assert hr is not None and hr.unit == "hr"
    assert hr.environment == "analysis"


# --------------------------------------------------------------------------
# Opt-in, and it inherits
# --------------------------------------------------------------------------


def test_a_team_declaring_nothing_lands_in_its_nearest_declaring_ancestor():
    """Payables declares nothing, so it is Finance's place, not its own."""
    resolved = resolve(_spec())
    assert resolved.boundary["payables"] == "finance"
    assert resolved.home["ap"] == "finance--analysis"
    assert resolved.same_placement("ap", "cfo")


def test_the_root_always_declares_so_every_placed_agent_has_one_answer():
    resolved = resolve(_spec())
    assert resolved.boundary["root"] == "root"
    assert resolved.home["ceo"] == "root--analysis"
    for team in _spec().teams():
        assert team.id in resolved.boundary


def test_declaring_nothing_anywhere_gives_one_place_for_everybody():
    """The widest arrangement, and the one a design gets by not deciding."""
    import copy

    d = copy.deepcopy(BASE)
    for team in d["organization"]["teams"]:
        team.pop("placement", None)
    resolved = resolve(SystemSpec.model_validate(d))
    assert set(resolved.placements) == {"root--analysis"}
    assert len(resolved.placements["root--analysis"].agents) == 5


def test_the_widest_default_is_reported_rather_than_left_quiet():
    import copy

    d = copy.deepcopy(BASE)
    for team in d["organization"]["teams"]:
        team.pop("placement", None)
    codes = [f.code for f in validate_spec(SystemSpec.model_validate(d))]
    assert "single_placement" in codes


def test_an_agent_with_no_environment_class_is_placed_nowhere():
    """It has no sandbox environment, so inventing one for it would lie."""
    import copy

    d = copy.deepcopy(BASE)
    d["organization"]["teams"][0]["members"][1].pop("environment")
    resolved = resolve(SystemSpec.model_validate(d))
    assert "recruiter" not in resolved.home
    assert resolved.for_agent("recruiter") is None


# --------------------------------------------------------------------------
# The shared volume is the PROTECTED plane, and never a grant
# --------------------------------------------------------------------------


def test_a_volume_carries_only_what_the_units_groups_already_share():
    resolved = resolve(_spec())
    assert resolved.placements["hr--analysis"].data_classes == ("hr_records",)
    assert resolved.placements["finance--analysis"].data_classes == ("ledger",)


def test_a_volume_never_carries_a_class_from_another_units_groups():
    """Sharing a disk is not a grant. HR's volume has no ledger on it."""
    resolved = resolve(_spec())
    for placement in resolved.placements.values():
        assert "public_notes" not in placement.data_classes, (
            "a public class needs no department volume to reach anybody"
        )
    assert "ledger" not in resolved.placements["hr--analysis"].data_classes


def test_a_placement_of_one_shares_no_volume():
    import copy

    d = copy.deepcopy(BASE)
    d["organization"]["teams"][0]["members"].pop()
    resolved = resolve(SystemSpec.model_validate(d))
    assert not resolved.placements["hr--analysis"].shares_a_volume


# --------------------------------------------------------------------------
# Only standing structure becomes a network rule
# --------------------------------------------------------------------------


def test_cross_placement_traffic_is_denied_unless_something_declares_it():
    resolved = resolve(_spec())
    assert not resolved.permits("recruiter", "cfo"), (
        "two leaves in different departments reach each other over the bus, "
        "not by a generated network rule"
    )
    assert resolved.permits("recruiter", "hr_lead"), "same placement"


def test_a_declared_flow_opens_a_path_between_two_placements():
    spec = _spec(interaction_flows=[
        {"source": "recruiter", "target": "cfo", "kind": "consult"},
    ])
    resolved = resolve(spec)
    assert resolved.permits("recruiter", "cfo")
    assert not resolved.permits("cfo", "recruiter"), (
        "a consult flow is directional; the reverse does not hold"
    )


def test_a_declared_channel_with_members_on_both_sides_opens_a_path():
    spec = _spec(channels=[
        {"id": "hr_finance", "members": ["recruiter", "cfo"]},
    ])
    assert resolve(spec).permits("recruiter", "cfo")


def test_the_manager_chain_reaches_skip_level():
    """A Docker network is not transitive, so the closure must be generated.

    Permitting only leader-to-adjacent-leader left a chief executive unable to
    reach a team two levels down while the org chart said otherwise — the
    silent disagreement this record names as a disadvantage.
    """
    resolved = resolve(_spec())
    assert resolved.permits("ceo", "ap")


def test_no_rule_is_derived_from_a_mission_grant():
    """A generated rule does not expire and a mission window does.

    Baking one in converts temporary reach into standing reach, which is the
    accident ADR-0065 rule 8 exists to prevent.
    """
    spec = _spec(missions=[{
        "id": "m1", "name": "Audit", "leader": "recruiter",
        "members": ["recruiter", "cfo"],
        "starts_on": "2026-01-01", "ends_on": "2099-01-01",
    }])
    resolved = resolve(spec)
    assert not resolved.permits("recruiter", "cfo")
    assert not any("mission" in rule.via for rule in resolved.rules)


def test_the_org_chart_and_the_generated_policy_are_checked_against_each_other():
    """Drift is a finding, because at run time it is silent."""
    spec = _spec()
    resolved = resolve(spec)
    drift = [
        (a, b) for a, b, _ in standing_reach(spec)
        if a in resolved.home and b in resolved.home
        and not resolved.permits(a, b)
    ]
    assert not drift, drift
    assert not [f for f in validate_spec(spec)
                if f.code == "placement_denies_delegation"]


# --------------------------------------------------------------------------
# Separation and co-residency
# --------------------------------------------------------------------------


def test_two_agents_a_separation_keeps_apart_are_flagged_when_co_resident():
    """The rule still holds; the control is weaker than it reads.

    Neither agent holds both sides, so separation passes. They share a volume
    and a process namespace, which ADR-0068 rule 7 records as unfixed.
    """
    spec = _spec(
        decisions=[{"id": "raise_payment"}, {"id": "approve_payment"}],
        separations=[{
            "id": "payment_control",
            "decisions": ["raise_payment", "approve_payment"],
        }],
    )
    spec.organization.teams[1].mandate = None
    finance = spec.organization.teams[1]
    finance.members[0].mandate = {"decisions": ["approve_payment"]}
    finance.teams[0].members[0].mandate = {"decisions": ["raise_payment"]}
    found = [f for f in validate_spec(spec)
             if f.code == "separated_agents_co_resident"]
    assert found and found[0].where == "finance--analysis"
    assert "process namespace" in found[0].message


# --------------------------------------------------------------------------
# It reaches the compiled artifact
# --------------------------------------------------------------------------


def test_the_ir_carries_placements_and_what_each_may_reach():
    ir = build_ir(_spec())
    ids = {p.id for p in ir.placements}
    assert ids == {"root--analysis", "hr--analysis", "finance--analysis"}
    assert ir.agent("recruiter").placement == "hr--analysis"
    # Its own, plus the root's: a recruiter reports up the manager chain, and
    # that is standing structure. It does not reach Finance.
    assert ir.agent("recruiter").reaches == ["hr--analysis", "root--analysis"]
    assert "root--analysis" in ir.agent("cfo").reaches


def test_the_compose_file_gives_each_placement_its_own_internal_network():
    import tempfile

    import yaml

    from orgagents.compiler.base import register_builtin_targets
    from orgagents.compiler.engine import compile_system

    register_builtin_targets()
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(
            _spec(), targets=["local"], out_dir=pathlib.Path(tmp)
        )[0]
    compose = yaml.safe_load(
        next(f for f in result.files if f.path.endswith("compose.yaml")).content
    )
    for pid in ("hr--analysis", "finance--analysis"):
        assert compose["networks"][f"place-{pid}"] == {"internal": True}
        assert f"sandbox-{pid}" in compose["services"]
    # Default deny is what a network the agent is not on means.
    recruiter = compose["services"]["agent-recruiter"]["networks"]
    assert "place-hr--analysis" in recruiter
    assert "place-finance--analysis" not in recruiter
    # Skip-level reach is generated, so the chief executive is on both.
    ceo = compose["services"]["agent-ceo"]["networks"]
    assert "place-hr--analysis" in ceo and "place-finance--analysis" in ceo


def test_the_readme_names_who_is_co_resident():
    import tempfile

    from orgagents.compiler.base import register_builtin_targets
    from orgagents.compiler.engine import compile_system

    register_builtin_targets()
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(
            _spec(), targets=["local"], out_dir=pathlib.Path(tmp)
        )[0]
    readme = next(f for f in result.files if f.path.endswith("README.md")).content
    assert "## Placements" in readme
    assert "`hr_lead`, `recruiter`" in readme
    assert "not** a security boundary" in readme


# --------------------------------------------------------------------------
# The worked example
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def northwind() -> SystemSpec:
    return load_spec(ROOT / "examples" / "northwind.finance.system.yaml")


def test_northwind_keeps_internal_audit_out_of_the_function_it_audits(northwind):
    resolved = resolve(northwind)
    assert resolved.home["audit_lead"] == "internal_audit--analysis"
    assert not resolved.same_placement("audit_lead", "controller")
    assert not resolved.permits("audit_lead", "controller"), (
        "audit reports to the audit committee, not to the function it tests"
    )


def test_northwind_keeps_treasury_apart_from_the_ledger(northwind):
    resolved = resolve(northwind)
    assert resolved.home["treasurer"] == "treasury--payments_isolated"
    assert not resolved.same_placement("treasurer", "payables")


def test_northwind_still_validates(northwind):
    assert not errors(validate_spec(northwind))
