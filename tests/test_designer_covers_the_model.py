"""The designer offers what the model carries.

The palette drifted from the spec silently: five capabilities were added to
the model over several ADRs and none of them reached the canvas, so a design
that used them could only be written by hand. Nothing noticed, because nothing
compared the two.

This is that comparison. It is deliberately about *reachability in the UI*
rather than about field-by-field parity — some spec fields are genuinely not
for the canvas — but a capability somebody is told the platform has must be
designable in the tool they are given to design with.
"""
from __future__ import annotations

import pytest

from orgagents.api import palette_kinds, palette_tree


@pytest.fixture(scope="module")
def kinds():
    return {k["kind"]: k for k in palette_kinds(palette_tree())}


def _fields(kind) -> dict:
    return {f["name"]: f for f in kind["fields"]}


def test_an_agent_can_be_given_a_successor(kinds):
    """ADR-0094. Unset means the manager, and the control says so rather than
    leaving somebody to guess that a blank is safe."""
    field = _fields(kinds["agent"])["successor"]
    assert "manager" in field["blank"]
    assert "ADR-0094" in field["help"]


def test_an_agent_can_be_given_a_scaling_policy(kinds):
    """ADR-0095. All three bounds, or the design cannot say what it means."""
    scaling = _fields(kinds["agent"])["scaling"]
    assert scaling["type"] == "object"
    assert {f["name"] for f in scaling["fields"]} == {
        "min_instances", "max_instances", "concurrent_sessions_per_instance"}


def test_the_scaling_control_names_what_zero_costs(kinds):
    """The person choosing zero to save money is usually not the person who
    will read the failed handles, so the control tells them."""
    fields = {f["name"]: f for f in _fields(kinds["agent"])["scaling"]["fields"]}
    assert "settle as failed" in fields["min_instances"]["help"]
    assert "max_parallel_subagents" in fields["max_instances"]["help"]


def test_an_agent_can_be_granted_a_workflow(kinds):
    """A trigger may only run a workflow its agent holds, and the grant was
    not offered anywhere."""
    assert "workflows" in _fields(kinds["agent"])


def test_a_workflow_can_have_a_process(kinds):
    """The palette carried id, name and description — so the nodes and edges
    that *are* the workflow had to be written by hand."""
    graph = _fields(kinds["workflow"])["graph"]
    assert graph["type"] == "graph"
    assert "branch is the only step" in graph["help"]
    assert "interrupt_before" in _fields(kinds["workflow"])


def test_the_canvas_can_render_every_field_type_the_palette_declares():
    """A palette that offers a type the canvas cannot draw is a field nobody
    can fill in."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    canvas = (root / "web" / "canvas.js").read_text(encoding="utf-8")
    handled = set(re.findall(r'field\.type === "(\w+)"', canvas))
    # Types the renderer reaches through the reference pickers rather than by
    # name, plus the plain input fallback.
    handled |= {"string", "ref", "reflist"}

    declared = {
        field["type"]
        for kind in palette_kinds(palette_tree())
        for field in kind["fields"]
    }
    missing = declared - handled
    assert not missing, f"the palette offers types the canvas cannot draw: {missing}"
