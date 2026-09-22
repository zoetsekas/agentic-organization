"""What actually enforces the tenant boundary on each target (WS-028 M3, M4).

The claim under test is narrow on purpose: the generated artifacts *draw* the
boundary and *say* what draws it. Whether a real host or a real cloud account
honours it is WS-028 M6, and nothing here can answer that.
"""
from pathlib import Path

import pytest
import yaml

from orgagents.compiler import compile_system
from orgagents.compiler.base import register_builtin_targets
from orgagents.fabric.tenants import TenantRegistry
from orgagents.spec import load_spec
from orgagents.store import Store

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme" / "acme.system.yaml"
CLOUD_TARGETS = ["terraform:gcp", "terraform:aws", "terraform:azure"]


@pytest.fixture(scope="module")
def spec():
    return load_spec(EXAMPLE)


@pytest.fixture()
def tenants(tmp_path):
    registry = TenantRegistry(Store(tmp_path / "fabric.db"))
    return (
        registry.register(id="northwind", name="Northwind",
                          cloud_boundary="proj-northwind"),
        registry.register(id="globex", name="Globex",
                          cloud_boundary="proj-globex"),
    )


def _files(spec, tenant, target, tmp_path) -> dict[str, str]:
    result = compile_system(spec, targets=[target], out_dir=tmp_path,
                            tenant=tenant.to_ir())[0]
    return {f.path: f.content for f in result.files}


# -- M3: one host, two stacks that cannot see each other ------------------


def test_each_tenant_gets_its_own_compose_project_networks_and_volumes(
    spec, tenants, tmp_path
):
    north, globex = tenants
    a = yaml.safe_load(_files(spec, north, "local", tmp_path)["docker-compose.yaml"])
    b = yaml.safe_load(_files(spec, globex, "local", tmp_path)["docker-compose.yaml"])

    assert a["name"].startswith("northwind-") and b["name"].startswith("globex-")
    assert a["name"] != b["name"]
    assert set(a["networks"]) & set(b["networks"]) == set()
    assert set(a["volumes"]) & set(b["volumes"]) == set()
    # Every service is on this tenant's networks only.
    for service in a["services"].values():
        assert all(n.startswith("northwind-") for n in service.get("networks", []))


def test_named_volumes_are_mounted_under_the_tenants_own_name(spec, tenants, tmp_path):
    north, _ = tenants
    compose = yaml.safe_load(
        _files(spec, north, "local", tmp_path)["docker-compose.yaml"]
    )
    mounts = [
        m for s in compose["services"].values() for m in s.get("volumes", [])
        if not str(m).startswith(".")
    ]
    assert mounts
    assert all(m.startswith("northwind-") for m in mounts)


def test_the_local_readme_says_what_enforces_the_boundary_and_what_does_not(
    spec, tenants, tmp_path
):
    north, _ = tenants
    readme = _files(spec, north, "local", tmp_path)["README.md"]
    assert "## Tenant isolation" in readme
    assert "project name" in readme and "networks" in readme and "volumes" in readme
    # A shared kernel and a shared Docker socket are not a tenant boundary, and
    # the report has to say so rather than imply one (ADR-0050).
    assert "coarser than the model" in readme
    assert "Docker socket" in readme


# -- M4: one cloud boundary per tenant, named in the mapping report -------


@pytest.mark.parametrize("target", CLOUD_TARGETS)
def test_each_cloud_target_pins_a_per_tenant_boundary(spec, tenants, tmp_path, target):
    north, _ = tenants
    files = _files(spec, north, target, tmp_path)
    profile = register_builtin_targets().get(target).profile
    main = files["main.tf"]
    assert f"{profile.boundary_argument} = " in main
    assert 'tenant      = "northwind"' in main
    # An apply aimed at another tenant's boundary fails before it creates
    # anything, because the fabric already knows which one is this tenant's.
    assert 'var.project == "proj-northwind"' in files["variables.tf"]


@pytest.mark.parametrize("target", CLOUD_TARGETS)
def test_the_mapping_report_names_the_boundary_and_flags_the_coarseness(
    spec, tenants, tmp_path, target
):
    north, _ = tenants
    mapping = _files(spec, north, target, tmp_path)["MAPPING.md"]
    profile = register_builtin_targets().get(target).profile
    assert "## Tenant boundary" in mapping
    assert "**What enforces the boundary here:**" in mapping
    assert profile.boundary_kind in mapping
    assert "coarser than the model" in mapping
    assert profile.boundary_coarser_than_model.split(".")[0] in mapping
    assert "it is not an access control" in mapping


@pytest.mark.parametrize("target", CLOUD_TARGETS)
def test_two_tenants_share_no_terraform_object_name(spec, tenants, tmp_path, target):
    north, globex = tenants
    a = _files(spec, north, target, tmp_path)
    b = _files(spec, globex, target, tmp_path)

    def object_names(files: dict[str, str]) -> set[str]:
        names = set()
        for path, content in files.items():
            if not path.endswith(".tf"):
                continue
            for line in content.splitlines():
                # A resource's own attributes sit at two spaces; anything
                # deeper is an env var or a label, not an object name.
                if not line.startswith("  ") or line.startswith("   "):
                    continue
                stripped = line.strip()
                for attr in ("name", "account_id", "secret_id"):
                    if stripped.startswith(f"{attr} ") and '"' in stripped:
                        value = stripped.split('"')[1]
                        if value and not value.startswith("${"):
                            names.add(value)
        return names

    left, right = object_names(a), object_names(b)
    assert left and right
    assert left & right == set()


def test_the_terraform_state_prefix_is_per_tenant(spec, tenants, tmp_path):
    north, _ = tenants
    backend = _files(spec, north, "terraform:gcp", tmp_path)["backend.tf.example"]
    assert 'prefix = "northwind/' in backend


def test_the_targets_declare_the_boundary_in_their_caveats():
    for target in register_builtin_targets().targets.values():
        caveats = " ".join(target.describe().get("caveats", []))
        assert "tenant" in caveats.lower()
