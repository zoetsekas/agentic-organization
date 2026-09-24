"""The canvas draws placements, and draws the ones that would compile.

The canvas's standing contract is that the picture matches what compiles:
edges are derived from the spec rather than stored in the layout, and regions
now are too. That only holds if the JavaScript that derives them and the Python
resolver that compiles them agree, so this runs both over the worked example
and compares — the drift check the repo would otherwise be trusting a comment
for.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from orgagents.placements import resolve
from orgagents.spec.loader import load_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]
CANVAS = ROOT / "web" / "canvas.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _js_placements(spec_dict: dict) -> list[dict]:
    """Run `derivedPlacements` against a spec, in node."""
    script = f"""
const path = {json.dumps(str(CANVAS))};
const mod = require(path);
// The canvas reads the open record; this is the smallest thing that is one.
mod.__canvas.record = {{ spec: {json.dumps(spec_dict)}, layout: {{ nodes: {{}} }} }};
process.stdout.write(JSON.stringify(mod.derivedPlacements()));
"""
    # On stdin, not `node -e`: a spec inlined into the command line passes
    # Windows' 32 767-character limit (WinError 206).
    out = subprocess.run(
        ["node", "-"], input=script, capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def northwind_dict() -> dict:
    return json.loads(
        load_spec(
            ROOT / "examples" / "northwind" / "northwind.finance.system.yaml"
        ).model_dump_json()
    )


def test_the_canvas_and_the_compiler_place_agents_identically(northwind_dict):
    """If these disagree, the picture is a lie about what would deploy."""
    from orgagents.spec.model import SystemSpec

    expected = resolve(SystemSpec.model_validate(northwind_dict))
    drawn = {p["id"]: p for p in _js_placements(northwind_dict)}

    assert set(drawn) == set(expected.placements), (
        "the canvas and the resolver disagree about which placements exist"
    )
    for pid, placement in expected.placements.items():
        assert drawn[pid]["agents"] == list(placement.agents), pid
        assert drawn[pid]["unit"] == placement.unit
        assert drawn[pid]["environment"] == placement.environment


def test_an_agent_without_an_environment_class_is_in_no_region(northwind_dict):
    """It has no sandbox environment, so a box around it would invent one."""
    import copy

    spec = copy.deepcopy(northwind_dict)
    spec["organization"]["members"][0].pop("environments", None)
    placed = {a for p in _js_placements(spec) for a in p["agents"]}
    assert "ceo" not in placed


def test_the_region_carries_the_network_posture_it_was_made_from(northwind_dict):
    drawn = {p["id"]: p for p in _js_placements(northwind_dict)}
    assert drawn["treasury--payments_isolated"]["network"] in (
        "none", "allowlist", "internal", "open"
    )


def test_the_canvas_source_says_a_region_is_not_a_boundary():
    """A picture of boxes reads as a wall unless the source says otherwise."""
    source = CANVAS.read_text(encoding="utf-8")
    assert "derivedPlacements" in source
    assert "function renderRegions" in source
    css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    assert ".region {" in css
    assert "not a security boundary" in css
