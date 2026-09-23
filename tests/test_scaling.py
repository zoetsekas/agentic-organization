"""Scale is declared, not inherited from a provider default (ADR-0095).

One test per line of that ADR's Verification section. The thing being
defended is narrow and specific: a generated workload must never carry a
ceiling that nobody in the organization chose and nothing in the output names.
"""
from __future__ import annotations

import pytest

from pathlib import Path

from orgagents.compiler import build_ir
from orgagents.compiler.targets.terraform import PROFILES, TerraformTarget
from orgagents.spec import load_spec
from orgagents.spec.model import DEFAULT_MAX_INSTANCES, ScalingPolicy

ROOT = Path(__file__).resolve().parents[1]
ALL_PROFILES = sorted(PROFILES)


@pytest.fixture(scope="module")
def ayc_ir():
    return build_ir(load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml"))


def _files(ir, profile):
    return {f.path: f.content for f in TerraformTarget(profile).generate(ir)}


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_no_workload_is_emitted_without_a_bound(ayc_ir, profile):
    """The one outcome ruled out.

    A Cloud Run service with no `scaling` block still scales — to whatever the
    provider defaults to. That is a ceiling nobody reviewed and the generated
    Terraform does not name, which is the shape of control this platform
    refuses everywhere else.
    """
    agents_tf = _files(ayc_ir, profile)["agents.tf"]
    services = agents_tf.count("resource \"")
    assert services, "no workloads were emitted at all"
    assert agents_tf.count("# scale:") + agents_tf.count("# NOT BOUNDED HERE") \
        >= len(ayc_ir.agents), "a workload shipped without a stated bound"


def test_an_undeclared_policy_is_emitted_and_marked_as_a_default(ayc_ir):
    agents_tf = _files(ayc_ir, "gcp")["agents.tf"]
    assert "PLATFORM DEFAULT — no scaling was declared" in agents_tf
    assert f"max_instance_count = {DEFAULT_MAX_INSTANCES}" in agents_tf


def test_a_declared_policy_says_it_was_declared(ayc_ir):
    ir = ayc_ir.model_copy(deep=True)
    ir.agents[0].scaling = ScalingPolicy(
        min_instances=2, max_instances=10, concurrent_sessions_per_instance=4
    )
    agents_tf = _files(ir, "gcp")["agents.tf"]
    assert "(declared by the design)" in agents_tf
    assert "min_instance_count = 2" in agents_tf
    assert "max_instance_count = 10" in agents_tf
    assert "max_instance_request_concurrency = 4" in agents_tf


def test_a_bound_the_cloud_cannot_express_is_reported_not_dropped(ayc_ir):
    """AWS carries a desired count and no ceiling, so the ceiling is the
    design's and nothing in the stack's — and the stack says so."""
    files = _files(ayc_ir, "aws")
    assert "NOT CARRIED by Amazon Web Services" in files["agents.tf"]
    mapping = files["MAPPING.md"]
    assert "### Bounds this cloud does not carry" in mapping
    assert "`max_instances`" in mapping
    assert "would read as bounded" in mapping


def test_a_cloud_that_carries_everything_reports_no_gap(ayc_ir):
    files = _files(ayc_ir, "gcp")
    assert "NOT CARRIED" not in files["agents.tf"]
    assert "### Bounds this cloud does not carry" not in files["MAPPING.md"]


def test_the_report_names_both_ceilings_and_confuses_neither(ayc_ir):
    """`max_parallel_subagents` bounds one leader's fan-out inside one
    process. It is not how much work the agent can have in flight."""
    files = _files(ayc_ir, "gcp")
    assert "It is **not** `max_parallel_subagents`" in files["MAPPING.md"]
    assert "max_parallel_subagents bounds one leader's fan-out" \
        in files["agents.tf"]


def test_scale_to_zero_carries_its_consequence(ayc_ir):
    """The person choosing zero to save money is usually not the person who
    will read the failed handles."""
    mapping = _files(ayc_ir, "gcp")["MAPPING.md"]
    assert "### Agents that scale to zero" in mapping
    assert "ADR-0093" in mapping and "ADR-0094" in mapping
    assert "becomes the normal one" in mapping


def test_a_warm_floor_is_not_listed_as_scaling_to_zero(ayc_ir):
    ir = ayc_ir.model_copy(deep=True)
    for agent in ir.agents:
        agent.scaling = ScalingPolicy(min_instances=1, max_instances=4)
    mapping = _files(ir, "gcp")["MAPPING.md"]
    assert "### Agents that scale to zero" not in mapping


# -- the neutral vocabulary itself ------------------------------------------


def test_scale_to_zero_is_the_floor_and_not_a_second_field():
    """A flag that could disagree with the number beside it is a bug waiting."""
    assert ScalingPolicy(min_instances=0).scales_to_zero is True
    assert ScalingPolicy(min_instances=1).scales_to_zero is False
    assert not hasattr(ScalingPolicy(), "scale_to_zero")


def test_the_concurrency_ceiling_is_a_product():
    policy = ScalingPolicy(max_instances=10, concurrent_sessions_per_instance=4)
    assert policy.concurrency_ceiling == 40
    assert "40 concurrent" in policy.describe()


# -- bounds that cannot hold ------------------------------------------------


def _spec_with(scaling: dict):
    from spec_fixtures import BASE
    spec = {k: v for k, v in BASE.items()}
    org = spec["organization"]
    team = {**org["teams"][0],
            "members": [{**org["teams"][0]["members"][0], "scaling": scaling}]}
    spec["organization"] = {**org, "teams": [team]}
    return spec


@pytest.mark.parametrize("scaling,code", [
    ({"max_instances": 0}, "scale_ceiling_below_one"),
    ({"min_instances": 5, "max_instances": 2}, "scale_floor_above_ceiling"),
    ({"concurrent_sessions_per_instance": 0}, "scale_concurrency_below_one"),
])
def test_impossible_bounds_are_refused_at_validation(scaling, code):
    """A design that says something impossible about its own capacity should
    not reach a deployment to find out from the cloud."""
    from spec_fixtures import org as _org
    from orgagents.spec.validate import errors, validate_spec

    codes = {f.code for f in errors(validate_spec(_org(_spec_with(scaling))))}
    assert code in codes
