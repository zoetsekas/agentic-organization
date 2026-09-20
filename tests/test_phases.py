"""The definition/implementation gate (ADR-0019)."""
from pathlib import Path

import pytest

from orgagents.phases import review
from orgagents.spec import load_binding, load_spec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme.system.yaml"
BINDING = ROOT / "examples" / "acme.binding.yaml"


@pytest.fixture(scope="module")
def spec():
    return load_spec(EXAMPLE)


@pytest.fixture(scope="module")
def binding():
    return load_binding(BINDING)


def test_example_passes_both_phases(spec, binding):
    for target in ("local", "terraform:gcp", "terraform:aws", "terraform:azure"):
        report = review(spec, binding=binding, target=target)
        assert report.ready_to_compile, [str(c) for c in report.failures()]


def test_definition_phase_needs_no_binding(spec):
    report = review(spec)
    assert report.definition_complete
    assert report.of("implementation") == []


def test_definition_gaps_are_named_with_a_fix(spec):
    broken = spec.model_copy(deep=True)
    broken.lifecycle.owner = ""
    broken.budgets = []
    report = review(broken)
    codes = {c.id for c in report.failures("definition")}
    assert {"lifecycle_owner", "budget_declared"} <= codes
    assert all(c.fix for c in report.failures("definition"))


def test_agent_without_a_human_fails_the_definition_phase(spec):
    broken = spec.model_copy(deep=True)
    broken.agent("analyst").human = None
    assert "agents_have_humans" in {c.id for c in review(broken).failures("definition")}


def test_approval_without_a_channel_fails(spec):
    broken = spec.model_copy(deep=True)
    broken.channels = [c for c in broken.channels if "approve" not in
                       [p.value for p in c.purposes]]
    assert "approval_route_exists" in {
        c.id for c in review(broken).failures("definition")
    }


def test_unbound_capability_fails_the_implementation_phase(spec, binding):
    partial = binding.model_copy(deep=True)
    target = partial.for_target("local")
    target.capabilities = target.capabilities[:1]
    report = review(spec, binding=partial, target="local")
    assert report.definition_complete
    assert "capabilities_bound" in {c.id for c in report.failures("implementation")}


def test_missing_binding_fails_cleanly(spec):
    report = review(spec, binding=None, target="terraform:azure")
    assert "binding_exists" in {c.id for c in report.failures("implementation")}


def test_region_must_satisfy_declared_residency(spec, binding):
    offshore = binding.model_copy(deep=True)
    offshore.for_target("terraform:gcp").infrastructure.region = "us-central1"
    report = review(spec, binding=offshore, target="terraform:gcp")
    assert "region_matches_residency" in {
        c.id for c in report.failures("implementation")
    }


def test_triggers_require_a_scheduler_binding(spec, binding):
    unscheduled = binding.model_copy(deep=True)
    unscheduled.for_target("local").scheduler = None
    report = review(spec, binding=unscheduled, target="local")
    assert "scheduler_bound" in {c.id for c in report.failures("implementation")}


def test_summary_and_phase_split(spec, binding):
    report = review(spec, binding=binding, target="local")
    assert "definition:" in report.summary() and "implementation:" in report.summary()
    assert all(c.phase == "definition" for c in report.of("definition"))
    assert all(c.phase == "implementation" for c in report.of("implementation"))
