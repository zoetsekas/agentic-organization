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


# --------------------------------------------------------------------------
# Lifecycle (ADR-0077)
# --------------------------------------------------------------------------


from datetime import date  # noqa: E402

from orgagents.platform_policy import PolicyStatus  # noqa: E402


def _approved(**over) -> PlatformPolicy:
    base = dict(id="h", version="1.0.0", status="approved",
                approved_by="ana", approved_on="2026-09-01",
                treat_as="production")
    base.update(over)
    return PlatformPolicy(**base)


def test_a_new_policy_starts_as_a_draft():
    assert PlatformPolicy(id="h").status is PolicyStatus.DRAFT


def test_an_approval_nobody_signed_is_refused():
    with pytest.raises(ValueError) as excinfo:
        PlatformPolicy(id="h", status="approved")
    assert "by whom or when" in str(excinfo.value)


def test_a_draft_may_be_evaluated_and_may_not_decide_a_build(spec, tmp_path):
    """An author has to see what a rule does before asking anyone to accept
    it, so the two questions are kept apart."""
    draft = PlatformPolicy(id="h", version="1.0.0", treat_as="production")
    assert _codes(spec, draft), "a draft must still be evaluable"
    with pytest.raises(CompileError) as excinfo:
        compile_system(spec, targets=["local"], out_dir=tmp_path,
                       platform_policy=draft)
    assert "has not been approved" in str(excinfo.value)
    assert not list(tmp_path.glob("**/*"))


def test_an_approved_policy_may_decide_a_build():
    assert _approved().refusal() == ""


def test_a_retired_policy_judges_nothing():
    retired = PlatformPolicy(id="h", version="1.0.0", status="retired")
    assert "retired" in retired.refusal()


def test_a_lapsed_approval_stops_deciding_and_says_why():
    """House rules nobody has confirmed still apply are not house rules."""
    stale = _approved(approved_on="2025-01-01", review_interval_days=90)
    assert stale.is_stale(date(2026, 9, 21))
    assert "due for review by" in stale.refusal(date(2026, 9, 21))


def test_a_policy_with_no_interval_does_not_lapse():
    """Opting in is choosing the behaviour; a fabric that sets one means it."""
    assert _approved(approved_on="2020-01-01").refusal() == ""


def test_an_interval_that_is_not_an_interval_is_refused():
    with pytest.raises(ValueError):
        _approved(review_interval_days=0)


# --------------------------------------------------------------------------
# A version is a name somebody types
# --------------------------------------------------------------------------


def test_the_same_version_over_changed_substance_has_a_different_fingerprint():
    """This is what makes "it passed house/1.0.0" checkable rather than
    asserted."""
    a = _approved(treat_as="production")
    b = _approved(treat_as="development")
    assert a.stamp == b.stamp
    assert a.fingerprint != b.fingerprint


def test_editorial_changes_do_not_move_the_fingerprint():
    """Following ADR-0062: describing a policy differently is not changing it."""
    a = _approved(description="house rules")
    b = _approved(description="the rules of this house", approved_by="bo")
    assert a.fingerprint == b.fingerprint


def test_the_stamp_records_the_lifecycle_not_only_the_name(spec, house):
    ir = build_ir(spec, platform_policy=house)
    assert ir.platform_policy.status == "approved"
    assert ir.platform_policy.approved_by == "Security Engineering"
    assert ir.platform_policy.fingerprint == house.fingerprint


def test_the_gate_reports_why_a_policy_may_not_decide(spec):
    draft = PlatformPolicy(id="h", version="1.0.0")
    report = review(spec, platform_policy=draft)
    check = next(c for c in report.of("definition")
                 if c.id == "platform_policy_usable")
    assert check.status == "fail"
    assert "has not been approved" in check.detail


def test_the_worked_house_policy_is_approved_and_current(house):
    assert house.refusal() == "", house.refusal()
    assert house.review_interval_days, "house rules should be revisited"
