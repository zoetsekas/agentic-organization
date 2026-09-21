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


# --------------------------------------------------------------------------
# M2: guardrails, output contracts and model policy
# --------------------------------------------------------------------------


def test_guardrails_can_be_authored(kinds):
    """Permissions decide what an agent may reach; a guardrail decides what
    may pass, and the UI could express only the first."""
    fields = {f["name"]: f for f in kinds["guardrail"]["fields"]}
    assert fields["applies_to"]["options"] == [
        "input", "output", "tool_input", "tool_output"
    ]
    assert fields["applies_to"]["required"] and fields["checks"]["required"]


def test_a_closed_vocabulary_is_offered_rather_than_typed(kinds, canvas_js):
    """A text box over a fixed list turns a typo into an unknown-value
    finding, which is a worse way to learn what the boundaries are called."""
    assert kinds["guardrail"]["fields"][2]["type"] == "multi"
    assert 'if (field.type === "multi")' in canvas_js


def test_a_guardrail_authored_in_the_ui_validates():
    spec = {
        **AUTONOMOUS,
        "lifecycle": {"evaluations": [
            {"id": "c", "given": "g", "expect": "contains: x",
             "applies_to": ["a"]}]},
        "guardrails": [{
            "id": "no_secrets_out", "applies_to": ["output", "tool_output"],
            "checks": ["secrets"], "on_violation": "block",
        }],
    }
    assert "no_guardrails" not in _codes(spec)


def test_an_escalating_guardrail_picks_a_channel_from_the_design(canvas_js):
    """The validator refuses an escalation with no channel, and a free-text
    field would let somebody name one that does not exist."""
    assert 'escalate_channel: { mode: "ref",     col: "channels" }' in canvas_js


def test_output_contracts_can_be_authored(kinds, canvas_js):
    fields = {f["name"]: f for f in kinds["output_contract"]["fields"]}
    assert fields["schema"]["type"] == "json"
    assert 'if (field.type === "json")' in canvas_js


def test_invalid_json_keeps_the_text_and_does_not_reach_the_spec(canvas_js):
    """Discarding what somebody typed mid-keystroke is how a schema gets
    silently emptied."""
    assert "not valid JSON yet" in canvas_js


def test_a_model_policy_is_a_field_not_a_kind(kinds):
    """It has no id, so it is not a thing you place on a canvas."""
    assert "model_policy" not in kinds
    agent = {f["name"]: f for f in kinds["agent"]["fields"]}
    assert agent["model_policy"]["type"] == "object"
    subs = {f["name"] for f in agent["model_policy"]["fields"]}
    assert {"classes", "allow", "deny", "allow_fallback"} <= subs


def test_an_unset_model_policy_inherits_rather_than_permitting_nothing(canvas_js):
    """Absent and empty differ here the way they do for a mandate."""
    assert 'if (field.type === "object")' in canvas_js
    assert "Not set: inherits the system's." in canvas_js


# --------------------------------------------------------------------------
# M3: skills, plugins, tools and operating principles
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app_js() -> str:
    return (BUNDLE / "app.js").read_text()


@pytest.fixture(scope="module")
def index_html() -> str:
    return (BUNDLE / "index.html").read_text()


def test_the_capability_bundle_can_be_authored(kinds):
    assert {"skill", "plugin", "tool"} <= set(kinds)


def test_a_skill_says_it_changes_how_an_agent_works_not_what_it_reaches(kinds):
    """Declaring `requires_capabilities` grants nothing — it is checked
    against the holder's own grants (ADR-0029)."""
    skill = kinds["skill"]
    assert "never what it may reach" in skill["help"]
    fields = {f["name"]: f for f in skill["fields"]}
    assert "grants nothing" in fields["requires_capabilities"]["help"]


def test_a_tool_wraps_only_what_the_design_already_has(canvas_js):
    """And what it may wrap depends on what it says it wraps, so the picker
    reads the node being edited rather than offering everything."""
    assert "tool: {" in canvas_js
    assert "const kind = tool?.wraps_kind" in canvas_js


def test_skill_resources_and_plugin_hooks_are_maps(kinds, canvas_js):
    assert {f["name"]: f for f in kinds["skill"]["fields"]}["resources"]["type"] == "map"
    assert {f["name"]: f for f in kinds["plugin"]["fields"]}["hooks"]["type"] == "map"
    assert 'if (field.type === "map")' in canvas_js


def test_a_map_value_may_contain_an_equals_sign(canvas_js):
    """A plugin hook's handler and a skill resource's contents both
    legitimately do."""
    assert "line.indexOf(\"=\")" in canvas_js
    assert "line.slice(at + 1)" in canvas_js


def test_operating_principles_belong_to_the_organisation(index_html, app_js):
    """Instructions every agent carries (ADR-0038) are an organisation-wide
    statement, not something dropped on a canvas."""
    assert 'name="operating_principles"' in index_html
    assert "record.spec.operating_principles = values.operating_principles" in app_js


def test_principles_sit_on_the_spec_and_not_in_metadata(app_js):
    """They are part of what the organisation is, not a note about it."""
    assert "not in metadata" in app_js


def test_a_design_with_the_M3_blocks_validates():
    spec = {
        **AUTONOMOUS,
        "operating_principles": ["Cite the source of every figure."],
        "skills": [{"id": "reconcile", "instructions": "Match to the order.",
                    "requires_capabilities": ["filer"]}],
        "tools": [{"id": "file_return", "wraps_kind": "capability",
                   "wraps": "filer"}],
        "plugins": [{"id": "tax_pack", "provides_skills": ["reconcile"],
                     "provides_tools": ["file_return"]}],
        "lifecycle": {"evaluations": [
            {"id": "c", "given": "g", "expect": "contains: x",
             "applies_to": ["a"]}]},
    }
    assert not [c for c in _codes(spec) if c.startswith("unknown_")]


def test_every_palette_kind_survives_a_save(tmp_path):
    """The palette and the store drifted, and only a browser noticed.

    `CanvasNode.kind` was a closed enum written when the palette had fifteen
    kinds. It has twenty-four. Everything added since could be dragged onto
    the canvas and edited, and then the save returned a 500 — so a person
    could do a minute's work and lose it, with the UI reporting a JSON parse
    error because the 500 was not JSON.

    This is WS-032 M7's drift check, in the form that would have caught it:
    place one node of every kind the palette offers and save them all.
    """
    from fastapi.testclient import TestClient

    from orgagents.api import create_app

    client = TestClient(create_app(str(tmp_path / "drift.db")))
    headers = {"X-User": "alice"}
    palette = client.get("/api/designer/palette", headers=headers).json()
    kinds = [k["kind"] for group in palette["groups"] for k in group["kinds"]]
    assert len(kinds) > 15, "the palette shrank; this test assumes it grew"

    workspace = client.post("/api/designer/workspaces", json={"name": "w"},
                            headers=headers).json()
    created = client.post(
        "/api/designer/systems",
        json={"workspace_id": workspace["id"], "name": "drift",
              "spec": {"metadata": {"name": "drift"}}},
        headers=headers,
    ).json()

    nodes = {
        f"{kind}_1": {"id": f"{kind}_1", "kind": kind, "x": 10 * i, "y": 10,
                      "width": 200, "height": 80, "collapsed": False, "note": ""}
        for i, kind in enumerate(kinds)
    }
    saved = client.put(
        f"/api/designer/systems/{created['id']}",
        json={"layout": {"nodes": nodes, "edges": []},
              "version": created["version"]},
        headers=headers,
    )
    assert saved.status_code == 200, (
        f"a layout using the palette's own kinds was refused: {saved.text[:400]}"
    )
    stored = client.get(f"/api/designer/systems/{created['id']}",
                        headers=headers).json()["record"]["layout"]["nodes"]
    assert set(stored) == set(nodes), "kinds were dropped on the way through"


def test_a_node_must_still_say_what_kind_it_is(tmp_path):
    """Opening the field is not the same as accepting anything."""
    import pytest
    from pydantic import ValidationError

    from orgagents.designer.models import CanvasNode

    with pytest.raises(ValidationError):
        CanvasNode(id="x", kind="   ")
