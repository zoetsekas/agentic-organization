"""Tenancy: fabric-assigned isolation domains and their enforcement (WS-028).

These tests prove *generation*, not resistance: nothing here attacks a running
deployment, because there is no Docker daemon and no cloud account in this
environment (WS-028 M6). What they do prove is that two tenants compiled from
one spec share nothing nameable, and that a compile which fails to draw the
boundary is refused rather than written out.
"""
import ast
import json
from pathlib import Path

import pytest
import yaml

from orgagents.compiler import build_ir, compile_system
from orgagents.compiler.ir import TenantIR
from orgagents.compiler.tenancy import (
    TenantIsolationError,
    artifact_violations,
    cross_tenant_references,
)
from orgagents.fabric.tenants import (
    IsolationDomain,
    PrefixError,
    Tenant,
    TenantRegistry,
    TenantStatus,
    normalize_prefix,
    validate_prefix,
)
from orgagents.spec import load_spec
from orgagents.store import Store

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme" / "acme.system.yaml"


@pytest.fixture(scope="module")
def spec():
    return load_spec(EXAMPLE)


@pytest.fixture()
def registry(tmp_path):
    return TenantRegistry(Store(tmp_path / "fabric.db"))


@pytest.fixture()
def two_tenants(registry):
    north = registry.register(id="northwind", name="Northwind Trading",
                              cloud_boundary="proj-northwind")
    globex = registry.register(id="globex", name="Globex",
                               cloud_boundary="proj-globex")
    return north, globex


# -- M1: the tenant model and its registry --------------------------------


def test_the_spec_layer_knows_nothing_about_tenants():
    """Tenancy is assigned by the fabric, never declared by a design (ADR-0050)."""
    source = (ROOT / "src" / "orgagents" / "spec" / "model.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        target.target.id if isinstance(target, ast.AnnAssign)
        and isinstance(target.target, ast.Name) else ""
        for target in ast.walk(tree)
    }
    assert not {n for n in names if "tenant" in n.lower()}
    assert "tenant" not in source.lower()


def test_registration_assigns_the_domain_rather_than_accepting_one(registry):
    tenant = registry.register(id="northwind", name="Northwind Trading")
    assert tenant.namespace_prefix == "northwind"
    assert tenant.isolation_domain == IsolationDomain.derive("northwind")
    assert tenant.isolation_domain.secret_scope == "northwind-secrets"
    assert registry.get("northwind") == tenant
    assert registry.by_prefix("northwind").id == "northwind"


def test_the_registry_persists_through_the_store(tmp_path):
    store = Store(tmp_path / "fabric.db")
    TenantRegistry(store).register(id="globex", name="Globex",
                                   entitlements=["cloud_targets"])
    reopened = TenantRegistry(Store(tmp_path / "fabric.db"))
    tenant = reopened.require("globex")
    assert tenant.entitled_to("cloud_targets")
    assert tenant.status is TenantStatus.ACTIVE


@pytest.mark.parametrize(
    "prefix",
    [
        "",             # nothing to qualify with
        "ab",           # too short to be distinctive
        "9north",       # a Terraform identifier may not start with a digit
        "North",        # a DNS label is lowercase only
        "north_wind",   # a DNS label rejects underscores
        "north.wind",   # so does a Compose project name
        "north-",       # a DNS label must end alphanumeric
        "-north",       # and start alphanumeric
        "north--wind",  # reserved by IDN, and unreadable in a joined name
        "orgagents",    # reserved by the platform
        "control",      # collides with a generated network name
        "n" * 40,       # leaves no room inside a 63-character DNS label
    ],
)
def test_unsafe_namespace_prefixes_are_rejected(prefix):
    with pytest.raises(PrefixError):
        validate_prefix(prefix)


def test_a_prefix_that_swallows_another_is_a_collision(registry):
    registry.register(id="north", name="North")
    with pytest.raises(PrefixError):
        registry.register(id="north-wind", name="North Wind")
    with pytest.raises(PrefixError):
        registry.register(id="second", name="Second", namespace_prefix="north")


def test_a_retired_tenant_keeps_its_prefix(registry):
    registry.register(id="globex", name="Globex")
    registry.retire("globex")
    assert registry.require("globex").status is TenantStatus.RETIRED
    assert not registry.require("globex").may_deploy()
    with pytest.raises(PrefixError):
        registry.register(id="globex2", name="Globex again",
                          namespace_prefix="globex")


def test_qualification_is_idempotent_and_ownership_is_decidable():
    tenant = Tenant(id="globex", name="Globex", namespace_prefix="globex",
                    isolation_domain=IsolationDomain.derive("globex"))
    assert tenant.qualify("state-data") == "globex-state-data"
    assert tenant.qualify(tenant.qualify("state-data")) == "globex-state-data"
    assert tenant.owns("globex-state-data")
    assert not tenant.owns("northwind-state-data")
    assert normalize_prefix("  North Wind  ") == "north-wind"


# -- M2: tenant-scoped compilation ----------------------------------------


def test_an_untenanted_compile_still_works(spec, tmp_path):
    """Single-tenant installations are not forced to acquire a tenant."""
    result = compile_system(spec, targets=["local"], out_dir=tmp_path)[0]
    assert result.ir.tenant is None
    assert result.ir.qualified("state-data") == "state-data"


def test_a_tenanted_ir_qualifies_every_generated_name(spec, two_tenants):
    north, _ = two_tenants
    ir = build_ir(spec, tenant=north.to_ir())
    assert ir.tenant.id == "northwind"
    assert not artifact_violations(ir, [])
    assert all(i.id.startswith("northwind-") for i in ir.identities)
    assert all(r.id.startswith("northwind-") for r in ir.resources)
    refs = {r for i in ir.identities for r in i.secret_refs}
    assert refs and all(r.startswith("northwind-") for r in refs)


def test_compiling_for_one_tenant_does_not_mutate_the_spec(spec, two_tenants):
    """The same spec object compiles for the next tenant, unchanged."""
    north, globex = two_tenants
    before = spec.model_dump_json()
    build_ir(spec, tenant=north.to_ir())
    build_ir(spec, tenant=globex.to_ir())
    assert spec.model_dump_json() == before


def test_an_artifact_claiming_a_tenant_but_unqualified_is_refused(spec, two_tenants):
    north, _ = two_tenants
    ir = build_ir(spec, tenant=north.to_ir())
    ir.identities[0].id = "id-ceo"                  # as an untenanted build named it
    ir.resources[0].id = "acme-state"
    violations = artifact_violations(ir, [])
    assert any("identity 'id-ceo'" in v for v in violations)
    assert any("acme-state" in v for v in violations)


def test_a_target_that_forgets_the_boundary_stops_the_compile(spec, two_tenants,
                                                              monkeypatch, tmp_path):
    from orgagents.compiler.base import register_builtin_targets

    north, _ = two_tenants
    target = register_builtin_targets().get("local")
    original = target.generate

    def forgetful(ir):
        files = original(ir)
        for gf in files:
            if gf.path == "docker-compose.yaml":
                doc = yaml.safe_load(gf.content)
                doc["volumes"] = {"state-data": {}}
                files[files.index(gf)] = type(gf)(gf.path, yaml.safe_dump(doc))
                break
        return files

    monkeypatch.setattr(target, "generate", forgetful)
    with pytest.raises(TenantIsolationError) as excinfo:
        compile_system(spec, targets=["local"], out_dir=tmp_path,
                       tenant=north.to_ir())
    assert "state-data" in str(excinfo.value)
    assert not (tmp_path / "northwind").exists()  # refused, not emitted


# -- M5: two tenants, one spec, nothing shared ----------------------------


def _names(result) -> dict[str, set[str]]:
    compose = yaml.safe_load(
        next(f.content for f in result.files if f.path == "docker-compose.yaml")
    )
    ir = result.ir
    return {
        "identifiers": {ir.name, compose["name"]}
                       | {r.id for r in ir.resources},
        "networks": set(compose["networks"]),
        "volumes": set(compose["volumes"]),
        "identities": {i.id for i in ir.identities},
        "secret_refs": {r for i in ir.identities for r in i.secret_refs}
                       | {c.bot_identity_ref for c in ir.channels
                          if c.bot_identity_ref},
    }


def test_two_tenants_compiled_from_one_spec_share_nothing(spec, two_tenants, tmp_path):
    north, globex = two_tenants
    a = _names(compile_system(spec, targets=["local"], out_dir=tmp_path,
                              tenant=north.to_ir())[0])
    b = _names(compile_system(spec, targets=["local"], out_dir=tmp_path,
                              tenant=globex.to_ir())[0])
    for kind in a:
        assert a[kind], f"nothing of kind {kind} was generated to compare"
        assert not a[kind] & b[kind], f"shared {kind}: {a[kind] & b[kind]}"


def test_two_tenants_do_not_share_an_output_directory(spec, two_tenants, tmp_path):
    north, globex = two_tenants
    for tenant in (north, globex):
        compile_system(spec, targets=["local"], out_dir=tmp_path,
                       tenant=tenant.to_ir())
    assert (tmp_path / "northwind" / "local" / "docker-compose.yaml").is_file()
    assert (tmp_path / "globex" / "local" / "docker-compose.yaml").is_file()


def test_the_generated_ir_json_carries_the_tenant(spec, two_tenants, tmp_path):
    north, _ = two_tenants
    compile_system(spec, targets=["local"], out_dir=tmp_path, tenant=north.to_ir())
    ir = json.loads(
        (tmp_path / "northwind" / "local" / "system.ir.json").read_text(encoding="utf-8")
    )
    assert ir["tenant"]["id"] == "northwind"
    assert ir["tenant"]["isolation_domain"] == "northwind-domain"


# -- validation: cross-tenant reach is denied, not narrowed ---------------


def test_a_spec_reaching_at_another_tenant_is_refused(spec, two_tenants, tmp_path):
    north, globex = two_tenants
    reaching = spec.model_copy(deep=True)
    reaching.knowledge[0].secret_ref = "globex-finance-token"
    with pytest.raises(TenantIsolationError) as excinfo:
        compile_system(reaching, targets=["local"], out_dir=tmp_path,
                       tenant=north.to_ir(), foreign_prefixes={"globex"})
    assert "globex" in str(excinfo.value)


def test_a_reference_to_another_tenant_by_id_is_refused():
    tenant = TenantIR(id="northwind", namespace_prefix="northwind",
                      isolation_domain="northwind-domain")
    doc = {"knowledge": [{"uri": "s3://shared/tenant/globex/reports"}]}
    assert cross_tenant_references(doc, tenant, []) == [
        "knowledge[0].uri: 's3://shared/tenant/globex/reports' refers to tenant "
        "'globex'"
    ]


def test_a_spec_naming_only_its_own_tenant_passes(spec, two_tenants, tmp_path):
    north, _ = two_tenants
    own = spec.model_copy(deep=True)
    own.knowledge[0].secret_ref = "northwind-finance-token"
    result = compile_system(own, targets=["local"], out_dir=tmp_path,
                            tenant=north.to_ir(), foreign_prefixes={"globex"})
    assert result[0].ir.tenant.id == "northwind"
