"""What the designer can author, against what a spec can contain (WS-032).

The review of 2026-09-21 found the palette offered 16 kinds against roughly 30
authored blocks, and that two of the gaps interacted: a design built in the UI
could not declare an evaluation case, while ADR-0072 rule 5 requires evaluation
evidence before an activity may run unattended. The designer could set a
posture it had no way to satisfy.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.spec.model import SystemSpec
from orgagents.spec.validate import validate_spec

BUNDLE = Path(__file__).resolve().parents[1] / "web"


@pytest.fixture(scope="module")
def canvas_js() -> str:
    return (BUNDLE / "canvas.js").read_text()


@pytest.fixture()
def kinds() -> dict:
    client = TestClient(create_app(":memory:"))
    body = client.get("/api/designer/palette", headers={"X-User": "a"}).json()
    return {k["kind"]: k for g in body["groups"] for k in g["kinds"]}


def _codes(spec: dict) -> set[str]:
    return {f.code for f in validate_spec(SystemSpec.model_validate(spec))}


AUTONOMOUS = {
    "metadata": {"name": "t", "spec_version": "1.1.0", "version": "0.1.0"},
    "decisions": [{"id": "file_it"}],
    "capabilities": [{"id": "filer", "action": "write", "resource_class": "x",
                      "decision": "file_it", "autonomy": "autonomous"}],
    "roles": [{"id": "clerk", "title": "Clerk", "capabilities": ["filer"]}],
    "organization": {
        "id": "root", "name": "R", "leader": "a",
        "members": [{"id": "a", "name": "A", "roles": ["clerk"],
                     "mandate": {"decisions": ["file_it"]}}],
        "teams": [],
    },
}


# --------------------------------------------------------------------------
# M1: the contradiction
# --------------------------------------------------------------------------


def test_an_autonomous_agent_without_evidence_is_reported():
    """The rule that made the gap matter — never-evaluated is not
    safe-unattended (ADR-0072 rule 5)."""
    assert "autonomy_without_evidence" in _codes(AUTONOMOUS)


def test_the_palette_can_now_declare_what_that_rule_requires(kinds):
    assert "evaluation" in kinds
    fields = {f["name"]: f for f in kinds["evaluation"]["fields"]}
    assert fields["given"]["required"] and fields["expect"]["required"]


def test_a_case_shaped_like_the_palette_satisfies_the_rule(kinds):
    """The palette's fields and the rule's requirement have to line up, or the
    designer offers a control that does not help."""
    case = {"id": "files_the_right_form", "given": "A late return",
            "expect": "contains: form CT600", "applies_to": ["a"]}
    assert set(case) <= {f["name"] for f in kinds["evaluation"]["fields"]}
    with_case = {**AUTONOMOUS, "lifecycle": {"evaluations": [case]}}
    assert "autonomy_without_evidence" not in _codes(with_case)


def test_applies_to_is_picked_from_the_agents_in_the_design(canvas_js):
    """An evaluation naming an agent that does not exist checks nothing."""
    assert "evaluation: {" in canvas_js
    assert "applies_to: { mode: \"reflist\", fn: agentIds }" in canvas_js


def test_a_new_case_applies_to_nobody_until_somebody_says(canvas_js):
    """An empty `applies_to` applies to *every* agent, which is a wider claim
    than anybody means by dropping one on a canvas."""
    assert "given: \"\", expect: \"\", applies_to: []" in canvas_js


# --------------------------------------------------------------------------
# The kinds the palette offers must be placeable
# --------------------------------------------------------------------------


def test_every_palette_kind_can_be_placed(kinds, canvas_js):
    """A kind the canvas cannot place is a button that throws.

    `decision` and `separation` were added to the palette with the authority
    work and never wired into the canvas, so the palette offered two kinds
    nothing could create.
    """
    collections = set(re.findall(r"^\s+(\w+): \"\w+\",$", canvas_js, re.M))
    nested = set(re.findall(r"^  (\w+): \{$", canvas_js, re.M))
    special = {"team", "agent", "subagent", "note"}
    for kind in kinds:
        assert kind in collections | nested | special, kind


def test_the_nested_blocks_share_one_shape(canvas_js):
    """Memory namespaces and evaluation cases are both a list inside a block,
    and were two special cases until the second one arrived."""
    assert "const NESTED = {" in canvas_js
    assert "memory_namespace: {" in canvas_js and "evaluation: {" in canvas_js
    assert "function nestedList(" in canvas_js
