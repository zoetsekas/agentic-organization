"""Tests for the sandbox provider seam (ADR-0054).

No provider is exercised for real here: there is no Docker daemon, no `sbx`
binary and no `openshell` binary in this environment, so availability is always
described through an injected `DetectionContext`.
"""

from pathlib import Path

import pytest

from orgagents import sandboxes
from orgagents.sandboxes import openshell as openshell_mod

SPEC_DIR = Path(__file__).resolve().parents[1] / "src" / "orgagents" / "spec"


def facts(**kw):
    base = dict(
        environment_id="analysis",
        tier="medium",
        network="allowlist",
        egress_allowlist=("api.example.com",),
        mounts=("finance_reports",),
        persistence="workspace",
        timeout_seconds=600,
        secret_refs=("warehouse_dsn",),
        toolchains=("python",),
        tenant_id="t-acme",
    )
    base.update(kw)
    return sandboxes.EnvironmentFacts(**base)


@pytest.fixture(autouse=True)
def _clean_degradations():
    sandboxes.clear_degradations()
    yield
    sandboxes.clear_degradations()


def linux_ctx(**kw):
    return sandboxes.DetectionContext(target="local", os_name="linux", **kw)


# --- seam and default ------------------------------------------------------

def test_default_provider_is_container():
    assert sandboxes.DEFAULT_PROVIDER == "container"
    res = sandboxes.resolve_provider(None, facts(), linux_ctx())
    assert res.provider_name == "container"
    assert not res.degraded


def test_all_four_providers_are_registered():
    assert sandboxes.provider_names() == [
        "container",
        "microvm_sbx",
        "openshell",
        "target_native",
    ]


def test_providers_satisfy_the_protocol():
    for name in sandboxes.provider_names():
        assert isinstance(sandboxes.get_provider(name), sandboxes.SandboxProvider)


def test_no_provider_name_appears_in_the_spec_layer():
    names = ["container", "microvm_sbx", "openshell", "target_native", "sbx"]
    for path in SPEC_DIR.rglob("*.py"):
        text = path.read_text()
        for name in names:
            assert name not in text, f"{name} leaked into spec layer file {path}"


# --- availability and loud degradation -------------------------------------

def test_sbx_unavailable_degrades_to_container_and_records_why():
    res = sandboxes.resolve_provider("microvm_sbx", facts(), linux_ctx())
    assert res.provider_name == "container"
    assert res.degraded
    assert "sbx" in res.degradation.reason
    assert res.requested == "microvm_sbx"


def test_degradation_is_retrievable_after_the_fact():
    sandboxes.resolve_provider("openshell", facts(), linux_ctx())
    recorded = sandboxes.degradations()
    assert len(recorded) == 1
    assert recorded[0].requested == "openshell"
    assert recorded[0].used == "container"
    assert recorded[0].tenant_id == "t-acme"
    assert "degraded to 'container'" in recorded[0].as_text()


def test_degradation_is_visible_in_the_report_lines():
    res = sandboxes.resolve_provider("microvm_sbx", facts(), linux_ctx())
    text = "\n".join(res.report_lines())
    assert "degraded to 'container'" in text
    assert "shared host kernel" in text.lower() or "share" in text.lower()


def test_sbx_on_an_unsupported_os_degrades():
    ctx = sandboxes.DetectionContext(os_name="freebsd", binaries=("sbx",))
    res = sandboxes.resolve_provider("microvm_sbx", facts(), ctx)
    assert res.provider_name == "container"
    assert "freebsd" in res.degradation.reason


def test_sbx_available_on_a_supported_os_is_used():
    ctx = sandboxes.DetectionContext(os_name="ubuntu", binaries=("sbx",))
    res = sandboxes.resolve_provider("microvm_sbx", facts(), ctx)
    assert res.provider_name == "microvm_sbx"
    assert not res.degraded
    assert res.boundary.kernel_boundary is True


def test_openshell_without_a_gateway_degrades():
    ctx = sandboxes.DetectionContext(binaries=("openshell",))
    res = sandboxes.resolve_provider("openshell", facts(), ctx)
    assert res.provider_name == "container"
    assert "gateway" in res.degradation.reason


def test_openshell_available_is_used():
    ctx = sandboxes.DetectionContext(
        binaries=("openshell",), openshell_gateway_url="https://gw.internal"
    )
    res = sandboxes.resolve_provider("openshell", facts(), ctx)
    assert res.provider_name == "openshell"


def test_target_native_is_only_available_on_a_cloud_target():
    assert sandboxes.resolve_provider(
        "target_native", facts(), sandboxes.DetectionContext(target="terraform:gcp")
    ).provider_name == "target_native"
    res = sandboxes.resolve_provider("target_native", facts(), linux_ctx())
    assert res.provider_name == "container"
    assert res.degraded


def test_an_unknown_provider_degrades_rather_than_raising():
    res = sandboxes.resolve_provider("firecracker", facts(), linux_ctx())
    assert res.provider_name == "container"
    assert "unknown sandbox provider" in res.degradation.reason


# --- boundary statements ---------------------------------------------------

def test_every_provider_returns_a_boundary_statement():
    for name in sandboxes.provider_names():
        stmt = sandboxes.boundary_statement(name)
        assert stmt.provider == name
        assert stmt.summary
        assert stmt.enforces and stmt.does_not_enforce
        assert stmt.verified is False  # nothing was run against a real provider
        assert "not verified here" in stmt.as_text()


def test_container_statement_admits_shared_kernel_and_docker_socket():
    stmt = sandboxes.boundary_statement("container")
    assert stmt.kernel_boundary is False
    text = stmt.as_text().lower()
    assert "shares this host's kernel" in text or "shared" in text
    assert "docker socket" in text


def test_only_sbx_claims_a_kernel_boundary():
    claimed = [
        n for n in sandboxes.provider_names()
        if sandboxes.boundary_statement(n).kernel_boundary
    ]
    assert claimed == ["microvm_sbx"]


def test_openshell_statement_says_alpha_and_makes_no_kernel_claim():
    stmt = sandboxes.boundary_statement("openshell")
    assert "alpha" in stmt.maturity
    assert stmt.kernel_boundary is False
    assert any("alpha" in d for d in stmt.does_not_enforce)


def test_target_native_claims_nothing_of_its_own():
    stmt = sandboxes.boundary_statement("target_native")
    assert stmt.tenant_scoping is sandboxes.TenantScoping.DELEGATED_TO_TARGET
    assert stmt.kernel_boundary is False


# --- tenant awareness (ADR-0050) -------------------------------------------

def test_openshell_cannot_express_tenancy_and_says_so():
    stmt = sandboxes.boundary_statement("openshell")
    assert stmt.tenant_scoping is sandboxes.TenantScoping.NOT_EXPRESSIBLE
    assert "must not be assumed" in stmt.tenant_note


def test_container_and_sbx_scope_tenants_by_name_only():
    for name in ("container", "microvm_sbx"):
        assert (
            sandboxes.boundary_statement(name).tenant_scoping
            is sandboxes.TenantScoping.NAME_SCOPED_ONLY
        )


def test_a_missing_tenant_id_is_a_stated_gap_on_every_provider():
    for name in sandboxes.provider_names():
        mapping = sandboxes.get_provider(name).map_environment(facts(tenant_id=""))
        assert any("tenant" in g for g in mapping.unexpressible), name


# --- environment-class mapping ---------------------------------------------

def test_container_mapping_carries_the_class_through():
    mapping = sandboxes.get_provider("container").map_environment(facts())
    assert mapping.settings["egress_allowlist"] == ["api.example.com"]
    assert mapping.settings["mounts"] == ["finance_reports"]
    assert mapping.settings["timeout_seconds"] == 600
    assert mapping.tenant_id == "t-acme"


def test_sbx_mapping_builds_a_kit_reference_and_cli_argv():
    provider = sandboxes.get_provider("microvm_sbx")
    mapping = provider.map_environment(facts())
    assert mapping.settings["kit"] == "sandbox-kit:python"
    assert mapping.settings["run_argv"] == ["sbx", "run", "--kit", "sandbox-kit:python"]
    assert provider.exec_argv("sb1", ["pytest"]) == ["sbx", "exec", "sb1", "pytest"]


def test_openshell_mapping_fills_the_four_policy_domains():
    policy = sandboxes.get_provider("openshell").policy_for(facts())
    assert policy.network.posture == "allowlist"
    assert policy.network.allowed_hosts == ("api.example.com",)
    assert policy.filesystem.readable == ("finance_reports",)
    assert policy.filesystem.persistent is True
    assert policy.process.timeout_seconds == 600
    assert policy.providers.secret_refs == ("warehouse_dsn",)


def test_openshell_isolated_class_carries_no_hosts():
    policy = sandboxes.get_provider("openshell").policy_for(
        facts(network="none", egress_allowlist=("api.example.com",))
    )
    assert policy.network.allowed_hosts == ()


def test_openshell_ephemeral_class_is_not_writable():
    policy = sandboxes.get_provider("openshell").policy_for(facts(persistence="ephemeral"))
    assert policy.filesystem.writable == ()
    assert policy.filesystem.persistent is False


def test_openshell_domain_split_matches_the_documented_engine():
    assert openshell_mod.HOT_RELOADABLE_DOMAINS == ("network", "providers")
    assert openshell_mod.LOCKED_AT_CREATION_DOMAINS == ("filesystem", "process")


def test_target_native_mapping_delegates():
    mapping = sandboxes.get_provider("target_native").map_environment(facts())
    assert mapping.settings["delegated"] is True


def test_facts_can_be_adapted_from_a_spec_environment_class():
    from orgagents.spec.model import EnvironmentClass

    env = EnvironmentClass(id="build", timeout_seconds=120)
    adapted = sandboxes.EnvironmentFacts.from_environment_class(env, tenant_id="t-1")
    assert adapted.environment_id == "build"
    assert adapted.timeout_seconds == 120
    assert adapted.tenant_id == "t-1"


# --- honest gaps -----------------------------------------------------------

def test_openshell_gaps_name_hot_reload_and_l7():
    mapping = sandboxes.get_provider("openshell").map_environment(facts())
    joined = " ".join(mapping.unexpressible)
    assert "hot-reloadable" in joined
    assert "method and path" in joined
    assert "not documented" in joined


def test_openshell_gaps_reach_the_report_lines():
    ctx = sandboxes.DetectionContext(
        binaries=("openshell",), openshell_gateway_url="https://gw.internal"
    )
    res = sandboxes.resolve_provider("openshell", facts(), ctx)
    text = "\n".join(res.report_lines())
    assert "cannot express:" in text
    assert "hot-reloadable" in text


def test_the_openshell_adapter_refuses_to_guess_a_schema():
    adapter = sandboxes.OpenShellAdapter()
    policy = sandboxes.get_provider("openshell").policy_for(facts())
    with pytest.raises(NotImplementedError) as exc:
        adapter.to_policy_document(policy)
    assert "not documented" in str(exc.value)
    with pytest.raises(NotImplementedError):
        adapter.apply(policy, "sandbox-1")


def test_resolution_serializes_for_a_mapping_report():
    res = sandboxes.resolve_provider("microvm_sbx", facts(), linux_ctx())
    data = res.as_dict()
    assert data["provider"] == "container"
    assert data["degraded"] is True
    assert data["boundary"]["verified"] is False
    assert data["degradation"]["requested"] == "microvm_sbx"
