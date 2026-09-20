"""A mapping report says which sandbox boundary is in force, and never oversells it.

The tenant boundary and the sandbox boundary are separate claims, and a reader
who trusts one will trust the other unless the report says which is which
(ADR-0054). Nothing here has been run against a real provider, and the report
has to say so — a boundary statement that reads as verified is worse than none.
"""
from pathlib import Path

import pytest

from orgagents.compiler import compile_system
from orgagents.spec import load_binding, load_spec

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ["terraform:gcp", "terraform:aws", "terraform:azure"]


@pytest.fixture(scope="module")
def reports(tmp_path_factory) -> dict[str, str]:
    out = tmp_path_factory.mktemp("mapping")
    spec = load_spec(str(ROOT / "examples" / "acme.system.yaml"))
    binding = load_binding(str(ROOT / "examples" / "acme.binding.yaml"))
    found: dict[str, str] = {}
    for target in TARGETS:
        target_dir = out / target.replace(":", "-")
        compile_system(spec, targets=[target], out_dir=target_dir, binding=binding)
        found[target] = next(target_dir.rglob("MAPPING.md")).read_text()
    return found


@pytest.mark.parametrize("target", TARGETS)
def test_the_report_names_the_provider_in_force(reports, target):
    report = reports[target]
    assert "## Sandbox execution" in report
    assert "target_native" in report


@pytest.mark.parametrize("target", TARGETS)
def test_the_report_never_claims_the_boundary_was_verified(reports, target):
    report = reports[target]
    section = report[report.index("## Sandbox execution"):report.index("## Resource mapping")]
    assert "**Verified here:** no" in section
    for overclaim in ("guaranteed", "proven", "verified against"):
        assert overclaim not in section.lower(), (
            f"the sandbox section claims '{overclaim}'; nothing here has been run"
        )


@pytest.mark.parametrize("target", TARGETS)
def test_the_report_states_what_the_environment_class_cannot_express(reports, target):
    section = reports[target]
    assert "cannot express" in section


@pytest.mark.parametrize("target", TARGETS)
def test_the_sandbox_claim_is_kept_apart_from_the_tenant_claim(reports, target):
    report = reports[target]
    # Both sections exist and are distinct: conflating them is the failure this
    # guards against.
    assert report.index("## Tenant boundary") < report.index("## Sandbox execution")


@pytest.mark.parametrize("target", TARGETS)
def test_an_identical_gap_is_stated_once_not_per_environment(reports, target):
    section = reports[target]
    section = section[section.index("## Sandbox execution"):]
    section = section[:section.index("## Resource mapping")]
    assert section.count("handed to the target unchanged") == 1
