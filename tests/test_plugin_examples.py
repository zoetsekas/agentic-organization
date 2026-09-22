"""The shipped plugin examples must actually work (ADR-0091, ADR-0092).

`examples/plugins/` claims three ways to extend the implementation phase
without forking: a template directory, a Compose overlay, and a third-party
Python target packaged as its own distribution. An example that does not run is
worse than no example — it is a worked answer that is wrong, and it is the first
thing somebody copies — so each one is exercised here against the `ayc` design,
exactly as its README says to.

The Acme target is also the reference implementation of the target contract, so
the interesting assertions are about the contract rather than about Nomad: it
consumes the IR only, it publishes an honest descriptor, and it reports per
agent what it cannot carry.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
import sys
import tempfile

import pytest

from orgagents.compiler.base import REGISTRY, register_builtin_targets
from orgagents.compiler.engine import compile_system
from orgagents.compiler.targets.template import TemplateTarget
from orgagents.spec.loader import load_binding, load_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "examples" / "plugins"
ACME_SRC = PLUGINS / "acme-onprem" / "src"


@pytest.fixture(scope="module")
def design():
    return (load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml"),
            load_binding(PLUGINS / "plugins.binding.yaml"))


def _compile(spec, binding, target_id, target):
    """Register, compile, then take the target back out again.

    The registry is global, so a target left behind would follow the suite into
    other files.
    """
    register_builtin_targets()
    REGISTRY.register(target, replace=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_system(spec, targets=[target_id],
                                    out_dir=pathlib.Path(tmp), binding=binding)[0]
            return {f.path: f.content for f in result.files}, result
    finally:
        REGISTRY.registry.items.pop(target_id, None)
        REGISTRY.registry.descriptors.pop(target_id, None)


# -- route 1: templates, no Python -----------------------------------------

def test_the_template_example_renders_a_job_per_agent(design):
    spec, binding = design
    emitted, result = _compile(
        spec, binding, "template",
        TemplateTarget(str(PLUGINS / "nomad-templates")))
    jobs = [p for p in emitted if p.startswith("jobs/")]
    assert len(jobs) == len(result.ir.agents)
    assert "jobs/ecommerce_agent.nomad" in emitted
    assert "README.md" in emitted and "inventory.csv" in emitted


def test_the_shipped_templates_have_no_typos(design):
    """Every `$placeholder` the example uses must be one the target supplies —
    otherwise the example teaches a name that does not exist."""
    spec, binding = design
    emitted, _ = _compile(spec, binding, "template",
                          TemplateTarget(str(PLUGINS / "nomad-templates")))
    assert "None — every `$placeholder` resolved." in emitted["CONFORMANCE.md"]


def test_the_template_example_is_bound_not_defaulted(design):
    """plugins.binding.yaml binds the template target, so the output names a
    real runtime rather than the neutral `echo` fallback."""
    spec, binding = design
    emitted, _ = _compile(spec, binding, "template",
                          TemplateTarget(str(PLUGINS / "nomad-templates")))
    job = emitted["jobs/ecommerce_agent.nomad"]
    assert 'ORGAGENTS_RUNTIME  = "langchain_deepagents"' in job
    assert "anthropic:claude-sonnet-5" in job


def test_the_template_example_names_what_nomad_cannot_hold(design):
    spec, binding = design
    emitted, _ = _compile(spec, binding, "template",
                          TemplateTarget(str(PLUGINS / "nomad-templates")))
    job = emitted["jobs/ecommerce_agent.nomad"]
    assert "NOT enforced by Nomad" in job
    assert "product_publishing" in job          # the gated action is named


# -- route 3: a third-party Python target ----------------------------------

@pytest.fixture(scope="module")
def acme_target():
    """Import the example distribution without installing it.

    Installing would prove entry-point discovery, which `test_plugins.py`
    already covers; what matters here is that the example's own code satisfies
    the target contract.
    """
    sys.path.insert(0, str(ACME_SRC))
    try:
        from acme_onprem import AcmeOnPremTarget
        yield AcmeOnPremTarget
    finally:
        sys.path.remove(str(ACME_SRC))
        sys.modules.pop("acme_onprem", None)
        sys.modules.pop("acme_onprem.target", None)


def test_the_acme_example_compiles_the_design(design, acme_target):
    spec, binding = design
    emitted, result = _compile(spec, binding, "acme:onprem", acme_target())
    assert "CONFORMANCE.md" in emitted
    assert "gateway-routes.json" in emitted
    jobs = [p for p in emitted if p.startswith("jobs/")]
    assert len(jobs) == len(result.ir.agents)


def test_the_acme_example_never_imports_the_spec_package():
    """The rule that keeps every target agreeing about who may do what — and
    the example has to model it, since people will copy this file."""
    source = (ACME_SRC / "acme_onprem" / "target.py").read_text()
    for node in ast.walk(ast.parse(source)):
        module = (node.module if isinstance(node, ast.ImportFrom)
                  else ",".join(a.name for a in node.names)
                  if isinstance(node, ast.Import) else None)
        if module:
            assert "spec" not in module.split("."), module


def test_the_acme_example_declares_an_honest_descriptor(acme_target):
    """It claims what Nomad really carries, and pointedly not an approval gate
    it has nowhere to route."""
    descriptor = acme_target().descriptor()
    assert descriptor.claims("instructions") and descriptor.claims("tools")
    assert not descriptor.claims("interrupt_on")
    assert not descriptor.claims("filesystem_permissions")


def test_the_acme_example_reports_its_gaps_per_agent(design, acme_target):
    """A reader of one job file should not have to cross-reference a report to
    learn the approval it mentions is not enforced there."""
    spec, binding = design
    emitted, _ = _compile(spec, binding, "acme:onprem", acme_target())
    job = emitted["jobs/ecommerce_agent.nomad"]
    assert "NOT enforced by Nomad" in job
    assert "mandate: may decide" in job
    assert "nowhere to route it" in job


def test_the_acme_example_transcribes_delegation_rather_than_deciding_it(
        design, acme_target):
    spec, binding = design
    emitted, result = _compile(spec, binding, "acme:onprem", acme_target())
    routes = json.loads(emitted["gateway-routes.json"])
    assert routes["system"] == "ayc"
    by_agent = {r["from"]: r["to"] for r in routes["routes"]}
    expected = {a.id: sorted(a.delegates_to)
                for a in result.ir.agents if a.delegates_to}
    assert by_agent == expected          # the IR's edges, not the target's


def test_the_acme_conformance_names_the_authority_model_as_unheld(
        design, acme_target):
    spec, binding = design
    emitted, _ = _compile(spec, binding, "acme:onprem", acme_target())
    report = emitted["CONFORMANCE.md"]
    for owed in ("Roles and permissions", "Mandates",
                 "Separation of duties", "Approvals"):
        assert owed in report
    assert "no model" in report


def test_the_acme_example_declares_the_tenant_boundary(acme_target):
    """Every target owes this, plugin or not."""
    caveats = " ".join(acme_target().describe().get("caveats", []))
    assert "tenant" in caveats.lower()


# -- the same distribution also extends the other two registries -----------

@pytest.fixture(scope="module")
def acme_modules():
    """Import the example distribution's runtime and cloud modules."""
    sys.path.insert(0, str(ACME_SRC))
    try:
        from acme_onprem import cloud, runtime
        yield runtime, cloud
    finally:
        sys.path.remove(str(ACME_SRC))
        for name in ("acme_onprem", "acme_onprem.runtime", "acme_onprem.cloud",
                     "acme_onprem.target"):
            sys.modules.pop(name, None)


def test_one_distribution_declares_all_three_entry_point_groups():
    """The realistic case: a vendor ships how they deploy, the framework they
    run and the cloud they run it on, together."""
    pyproject = (PLUGINS / "acme-onprem" / "pyproject.toml").read_text()
    for group in ("orgagents.targets", "orgagents.runtime_adapters",
                  "orgagents.provider_profiles"):
        assert f'[project.entry-points."{group}"]' in pyproject


def test_the_example_runtime_works_despite_the_closed_enum(acme_modules):
    """`Runtime` can only name what ships in orgagents; the registry is keyed
    by the string it carries, so a plugin gets an id of its own."""
    from orgagents.runtime.adapters import ADAPTERS, adapter_for

    runtime, _ = acme_modules
    runtime.register()
    try:
        agent = type("A", (), {"harness": type("H", (), {
            "runtime": runtime.RUNTIME_ID})()})()
        assert adapter_for(agent) is runtime.AcmeRuntimeAdapter
        assert ADAPTERS.descriptors[runtime.RUNTIME_ID].third_party
    finally:
        ADAPTERS.items.pop(runtime.RUNTIME_ID, None)
        ADAPTERS.descriptors.pop(runtime.RUNTIME_ID, None)


def test_the_example_runtime_distinguishes_handoffs_from_subagents(acme_modules):
    """The two are different mechanisms — a handoff does not return — so a
    runtime that has one must not claim the other."""
    runtime, _ = acme_modules
    descriptor = runtime.AcmeRuntimeAdapter.descriptor()
    assert descriptor.claims("handoffs")
    assert not descriptor.claims("subagents")
    assert not descriptor.claims("planning")
    assert not descriptor.claims("interrupt_on")


def test_the_example_cloud_becomes_a_terraform_target(design, acme_modules):
    from orgagents.compiler.base import REGISTRY
    from orgagents.compiler.targets.terraform import PROFILES

    spec, binding = design
    _, cloud = acme_modules
    cloud.register()
    try:
        assert "terraform:acme_cloud" in REGISTRY.ids()
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_system(spec, targets=["terraform:acme_cloud"],
                                    out_dir=pathlib.Path(tmp), binding=binding)[0]
        emitted = {f.path: f.content for f in result.files}
        assert "iam.tf" in emitted and "MAPPING.md" in emitted
        # Its own IAM mapping was used, not a built-in cloud's.
        assert "openstack_identity_role_assignment_v3" in emitted["iam.tf"]
    finally:
        REGISTRY.registry.items.pop("terraform:acme_cloud", None)
        REGISTRY.registry.descriptors.pop("terraform:acme_cloud", None)
        PROFILES.pop("acme_cloud", None)


def test_a_plugin_cloud_carries_its_own_iam_mapping(acme_modules):
    """The built-in role table cannot know about a cloud shipped elsewhere, so
    a plugin profile brings its own or fails saying so."""
    from orgagents.compiler.targets.terraform import ProviderProfile, action_roles

    _, cloud = acme_modules
    assert action_roles(cloud.ACME_CLOUD)["administer"] == "admin"

    bare = dataclasses.replace(cloud.ACME_CLOUD, id="nobody", action_roles={})
    with pytest.raises(ValueError, match="no `action_roles`"):
        action_roles(bare)


def test_a_cloud_may_honestly_omit_a_resource_it_lacks(design, acme_modules):
    """MAPPING.md always listed unmapped resources, but the emitters indexed
    `resources` directly — so an honest omission used to crash the compile."""
    from orgagents.compiler.base import REGISTRY
    from orgagents.compiler.targets.terraform import PROFILES

    spec, binding = design
    _, cloud = acme_modules
    cloud.register()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_system(spec, targets=["terraform:acme_cloud"],
                                    out_dir=pathlib.Path(tmp), binding=binding)[0]
        emitted = {f.path: f.content for f in result.files}
        # It says what it skipped, rather than emitting nothing or dying.
        assert "NOT generated" in emitted["channels.tf"]
        assert "knowledge_index" in emitted["channels.tf"]
        assert "knowledge_index" in emitted["MAPPING.md"]
        # And it declares no network isolation rather than implying some.
        assert "NOT" in emitted["network.tf"]
    finally:
        REGISTRY.registry.items.pop("terraform:acme_cloud", None)
        REGISTRY.registry.descriptors.pop("terraform:acme_cloud", None)
        PROFILES.pop("acme_cloud", None)


# -- route 2: the overlay --------------------------------------------------

def test_the_overlay_example_is_valid_compose_and_changes_only_deployment():
    import yaml

    overlay = yaml.safe_load(
        (PLUGINS / "compose-overlay" / "10-local-dev.yaml").read_text())
    assert "services" in overlay
    assert "designer" in overlay["services"]
    # An overlay may add a sidecar the design never mentioned; that is fine,
    # because it changes the deployment and not the design.
    assert "mailhog" in overlay["services"]


def test_the_overlay_lands_where_the_makefile_looks(design):
    """The example says to copy it into overlays/; the generated Makefile must
    actually glob that."""
    spec, _ = design
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(spec, targets=["local"],
                                out_dir=pathlib.Path(tmp))[0]
    makefile = next(f for f in result.files if f.path == "Makefile").content
    assert "wildcard overlays/*.yaml" in makefile


# -- the committed output stays in step ------------------------------------

def test_the_checked_in_output_exists_for_both_routes():
    """The README links to it, so it has to be there."""
    generated = PLUGINS / "generated"
    assert (generated / "nomad-from-templates" / "jobs").is_dir()
    assert (generated / "acme-onprem" / "jobs").is_dir()
    assert (generated / "acme-onprem" / "CONFORMANCE.md").is_file()
