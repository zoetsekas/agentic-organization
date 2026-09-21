"""The fabric's own rules about designs (ADR-0076).

Not `spec.policies`, which are a design's RBAC allow and deny rules. This is
the layer above: what the platform requires of any design before it may be
built, owned by the fabric and never by the design.
"""
from __future__ import annotations

import pathlib

import pytest

from orgagents.compiler.engine import CompileError, compile_system
from orgagents.compiler.ir import build_ir
from orgagents.platform_policy import PlatformPolicy, load
from orgagents.phases import review
from orgagents.spec.loader import load_spec
from orgagents.spec.model import AutonomyPosture
from orgagents.spec.validate import errors, validate_spec

EXAMPLE = pathlib.Path("examples/northwind.finance.system.yaml")


@pytest.fixture()
def spec():
    return load_spec(EXAMPLE)


@pytest.fixture()
def house() -> PlatformPolicy:
    return load("examples/house.platform-policy.yaml")


def _codes(spec, policy=None) -> list[str]:
    return [f.code for f in errors(validate_spec(spec, platform_policy=policy))]


# --------------------------------------------------------------------------
# Strictness is the fabric's call
# --------------------------------------------------------------------------


def test_a_design_no_longer_declares_the_strictness_it_is_judged_by(spec):
    """`metadata.environment` is a field the design declares about itself, so
    a design calling itself development escaped every strict rule."""
    assert spec.metadata.environment == "development"
    assert not _codes(spec)
    strict = PlatformPolicy(id="house", version="1.0.0", treat_as="production")
    assert _codes(spec, strict), "the fabric's strictness did not apply"


def test_the_design_cannot_supply_its_own_policy(spec):
    """A spec that could name the rules it is judged by is not judged."""
    assert not hasattr(spec, "platform_policy")


# --------------------------------------------------------------------------
# What a policy may require
# --------------------------------------------------------------------------


def test_a_required_block_a_design_omits_is_refused():
    spec = load_spec(pathlib.Path("examples/acme.system.yaml"))
    policy = PlatformPolicy(id="p", require_declared=["separations"])
    assert "platform_policy_requires" in _codes(spec, policy)


def test_a_required_block_a_design_declares_passes(spec, house):
    assert "platform_policy_requires" not in _codes(spec, house)


def test_a_block_this_platform_cannot_check_is_refused_in_the_policy():
    """A rule nobody can evaluate is not a rule — the same discipline
    ADR-0073 applies to controls."""
    with pytest.raises(ValueError) as excinfo:
        PlatformPolicy(id="p", require_declared=["vibes"])
    assert "cannot check" in str(excinfo.value)


def test_unattended_work_over_a_forbidden_decision_is_refused(spec, house):
    """An autonomy posture is a judgement about risk, and this one is not the
    design's to make."""
    cap = next(c for c in spec.capabilities if c.id == "payment_release")
    cap.autonomy = AutonomyPosture.AUTONOMOUS
    found = [f for f in errors(validate_spec(spec, platform_policy=house))
             if f.code == "platform_policy_forbids_autonomy"]
    assert found and "release_payment" in found[0].message


def test_an_autonomy_ceiling_applies_across_the_whole_design(spec):
    policy = PlatformPolicy(id="p", max_autonomy="supervised")
    assert "platform_policy_autonomy_ceiling" in _codes(spec, policy)


# --------------------------------------------------------------------------
# A weakened rule is loud
# --------------------------------------------------------------------------


def test_raising_a_severity_needs_no_justification():
    policy = PlatformPolicy(id="p", severity={"unused_capability": "error"})
    assert policy.lowered == {}


def test_lowering_one_without_a_reason_is_refused():
    with pytest.raises(ValueError) as excinfo:
        PlatformPolicy(id="p", severity={"wildcard_resource": "ignore"})
    assert "gives no reason" in str(excinfo.value)


def test_a_lowered_rule_travels_with_every_verdict(spec):
    policy = PlatformPolicy(
        id="p", version="1.0.0",
        severity={"wildcard_resource": "ignore"},
        reasons={"wildcard_resource": "accepted for the pilot"},
    )
    assert policy.lowered == {"wildcard_resource": "accepted for the pilot"}
    report = review(spec, platform_policy=policy)
    check = next(c for c in report.of("definition") if c.id == "platform_policy")
    assert check.status == "warn"
    assert "accepted for the pilot" in check.detail


def test_an_ignored_rule_really_stops_being_reported(spec):
    policy = PlatformPolicy(
        id="p", severity={"unused_capability": "ignore"},
        reasons={"unused_capability": "the catalogue is shared"},
    )
    findings = validate_spec(spec, platform_policy=policy)
    assert not [f for f in findings if f.code == "unused_capability"]


# --------------------------------------------------------------------------
# The verdict is recorded
# --------------------------------------------------------------------------


def test_the_ir_records_which_rules_judged_the_design(spec, house):
    ir = build_ir(spec, platform_policy=house)
    assert ir.platform_policy.stamp == "house/1.0.0"
    assert ir.platform_policy.treat_as == "production"


def test_an_unpoliced_compile_is_visibly_unpoliced(spec):
    """Absent a stamp, a policed and an unpoliced design would look the same."""
    assert build_ir(spec).platform_policy.stamp == "none"


def test_the_stamp_carries_what_the_policy_let_through(spec):
    policy = PlatformPolicy(
        id="p", version="2.0.0",
        severity={"wildcard_resource": "warning"},
        reasons={"wildcard_resource": "pilot"},
    )
    ir = build_ir(spec, platform_policy=policy)
    assert ir.platform_policy.lowered == {"wildcard_resource": "pilot"}


# --------------------------------------------------------------------------
# It blocks before anything is built
# --------------------------------------------------------------------------


def test_a_design_that_fails_the_house_rules_produces_no_artifacts(spec, house, tmp_path):
    with pytest.raises(CompileError) as excinfo:
        compile_system(spec, targets=["local"], out_dir=tmp_path,
                       platform_policy=house)
    assert "house/1.0.0" in str(excinfo.value), "the verdict names its rules"
    assert not list(tmp_path.glob("**/*")), "artifacts were written anyway"


def test_the_same_design_builds_without_the_policy(spec, tmp_path):
    results = compile_system(spec, targets=["local"], out_dir=tmp_path)
    assert results and list(tmp_path.glob("**/*"))


def test_the_gate_says_when_no_policy_is_in_force(spec):
    report = review(spec)
    check = next(c for c in report.of("definition") if c.id == "platform_policy")
    assert "no platform policy" in check.title
