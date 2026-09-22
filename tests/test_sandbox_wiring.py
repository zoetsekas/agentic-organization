"""The sandbox boundary reaches the artifacts and the runtime, not just a module.

A provider seam nobody consults is decoration. These assert the boundary shows
up where somebody would actually look: the generated stack's README, and the
sandbox template an operator reads at runtime.
"""
from pathlib import Path

import pytest

from orgagents.compiler import compile_system
from orgagents.platform import Platform
from orgagents.runtime.loader import load_system
from orgagents.spec import load_binding, load_spec
from orgagents.store import SANDBOX_TEMPLATES

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def local_stack(tmp_path_factory) -> str:
    out = tmp_path_factory.mktemp("local")
    compile_system(
        load_spec(str(ROOT / "examples" / "acme" / "acme.system.yaml")),
        targets=["local"], out_dir=out,
        binding=load_binding(str(ROOT / "examples" / "acme" / "acme.binding.yaml")),
    )
    return next(out.rglob("README.md")).read_text()


def test_the_generated_readme_names_the_provider(local_stack):
    assert "## Sandbox execution" in local_stack
    assert "container" in local_stack


def test_the_readme_admits_the_shared_kernel(local_stack):
    section = local_stack[local_stack.index("## Sandbox execution"):]
    section = section[:section.index("## Workflow engines")]
    # The uncomfortable half. A reader who has just been reassured by the
    # tenant section must not be left to assume this one is equally strong.
    assert "shared host kernel" in section.lower()
    assert "**Kernel boundary:** no" in section
    assert "**Verified here:** no" in section


def test_the_readme_does_not_oversell_the_boundary(local_stack):
    section = local_stack[local_stack.index("## Sandbox execution"):]
    section = section[:section.index("## Workflow engines")]
    for overclaim in ("guaranteed", "proven", "verified against"):
        assert overclaim not in section.lower()


def test_the_runtime_template_carries_the_boundary(tmp_path):
    ir = compile_system(
        load_spec(str(ROOT / "examples" / "acme" / "acme.system.yaml")),
        targets=["local"], out_dir=tmp_path,
        binding=load_binding(str(ROOT / "examples" / "acme" / "acme.binding.yaml")),
    )[0].ir
    platform = Platform(str(tmp_path / "rt.db"), configure_logs=False)
    load_system(platform, ir)
    from orgagents.models import SandboxTemplate

    # Only the templates this load materialized. Templates seeded into the
    # marketplace catalog predate the provider seam and carry no boundary —
    # a real gap, recorded in WS-004 rather than asserted away here.
    rows = [t for t in platform.store.list(SANDBOX_TEMPLATES, SandboxTemplate)
            if t.id.startswith("sbx_acme")]
    assert rows, "no sandbox templates were materialized"
    for row in rows:
        assert row.provider == "container"
        assert row.boundary_summary
        # Never claim verification the platform has not performed.
        assert row.boundary_verified is False
