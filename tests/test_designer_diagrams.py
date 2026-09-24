"""One model, many diagrams (the split Sirius makes, ADR-0034's sibling).

A design had exactly one picture of itself, so an organisation of any size
was a single canvas holding every team, agent, capability, policy and
endpoint. That is not a diagram, it is a haystack.

The risk in adding diagrams is the one this project keeps finding: two
mechanisms for one thing. So `Layout.nodes` is *gone* rather than kept
alongside `diagrams`, and every design that existed before reads as a design
with one diagram called Organisation.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.designer.models import MAIN_DIAGRAM, CanvasNode, Diagram, Layout
from orgagents.designer.service import _merge_layout

ANA = {"X-User": "ana", "X-User-Name": "Ana"}


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "d.db")))


def a_node(node_id: str, **kw) -> CanvasNode:
    return CanvasNode(id=node_id, kind=kw.pop("kind", "team"), **kw)


# ---------------------------------------------------------------- migration


def test_a_layout_written_before_diagrams_reads_as_one_diagram():
    layout = Layout.model_validate({
        "nodes": {"finance": {"id": "finance", "kind": "team", "x": 40}},
        "viewport": {"x": 120.0, "y": 60.0, "zoom": 1.0},
    })
    assert list(layout.diagrams) == [MAIN_DIAGRAM]
    assert layout.active == MAIN_DIAGRAM
    assert layout.diagram.name == "Organisation"
    assert layout.diagram.nodes["finance"].x == 40
    # Where the reader was looking came with it.
    assert layout.diagram.viewport["x"] == 120.0


def test_migrating_twice_does_not_grow_a_second_copy():
    """The main diagram's id is named rather than generated for this reason."""
    once = Layout.model_validate({"nodes": {"a": {"id": "a", "kind": "team"}}})
    twice = Layout.model_validate(once.model_dump())
    assert list(twice.diagrams) == [MAIN_DIAGRAM]
    assert set(twice.diagram.nodes) == {"a"}


def test_the_old_edges_key_is_read_without_complaint():
    """`edges` was removed before diagrams existed, and stored documents
    still carry it. Reading one is not an error."""
    layout = Layout.model_validate({"nodes": {}, "edges": []})
    assert layout.diagram.nodes == {}


def test_there_is_always_a_diagram_and_active_always_names_one():
    assert Layout().diagram is not None
    # A diagram can be deleted while somebody has it open.
    layout = Layout(diagrams={"a": Diagram(id="a", name="A")}, active="gone")
    assert layout.active == "a"


def test_the_layout_has_no_second_way_to_reach_the_nodes():
    """The defect class this project keeps finding is two mechanisms for one
    thing. `nodes` on the layout would be exactly that."""
    assert "nodes" not in Layout.model_fields
    assert "viewport" not in Layout.model_fields


# ------------------------------------------------------------------ merging


def test_merging_keeps_our_positions_and_adopts_their_new_nodes():
    ours = Layout(diagrams={MAIN_DIAGRAM: Diagram(
        id=MAIN_DIAGRAM, nodes={"a": a_node("a", x=10)})})
    theirs = Layout(diagrams={MAIN_DIAGRAM: Diagram(
        id=MAIN_DIAGRAM,
        nodes={"a": a_node("a", x=999), "b": a_node("b", x=50)})})
    merged = _merge_layout(theirs, ours)
    assert merged.diagrams[MAIN_DIAGRAM].nodes["a"].x == 10    # ours wins
    assert merged.diagrams[MAIN_DIAGRAM].nodes["b"].x == 50    # theirs adopted


def test_merging_keeps_a_diagram_the_other_side_added():
    """Two people on one design are usually looking at different parts of it.

    Dropping somebody's whole new diagram because you did not have it is the
    worst available resolution.
    """
    ours = Layout(diagrams={MAIN_DIAGRAM: Diagram(id=MAIN_DIAGRAM)})
    theirs = Layout(diagrams={
        MAIN_DIAGRAM: Diagram(id=MAIN_DIAGRAM),
        "dia_1": Diagram(id="dia_1", name="Treasury", root="treasury",
                         nodes={"treasury": a_node("treasury")}),
    })
    merged = _merge_layout(theirs, ours)
    assert merged.diagrams["dia_1"].name == "Treasury"
    assert merged.diagrams["dia_1"].root == "treasury"


def test_where_you_are_looking_is_never_merged():
    """`active` is this reader's, and taking somebody else's would move the
    canvas under them mid-edit."""
    ours = Layout(diagrams={"a": Diagram(id="a"), "b": Diagram(id="b")},
                  active="a")
    theirs = Layout(diagrams={"a": Diagram(id="a"), "b": Diagram(id="b")},
                    active="b")
    assert _merge_layout(theirs, ours).active == "a"


# ------------------------------------------------------------------- the API


def test_diagrams_ride_on_the_layout_and_survive_a_save(client):
    """A diagram is presentation (ADR-0034), so it needs no route of its own."""
    workspace = client.post("/api/designer/workspaces", json={"name": "w"},
                            headers=ANA).json()
    made = client.post("/api/designer/systems",
                       json={"workspace_id": workspace["id"], "name": "s",
                             "spec": {"metadata": {"name": "s"}}},
                       headers=ANA).json()
    saved = client.put(
        f"/api/designer/systems/{made['id']}",
        json={"version": made["version"], "layout": {
            "active": "dia_1",
            "diagrams": {
                MAIN_DIAGRAM: {"id": MAIN_DIAGRAM, "name": "Organisation",
                               "root": "", "nodes": {}},
                "dia_1": {"id": "dia_1", "name": "Treasury", "root": "treasury",
                          "nodes": {"treasury": {"id": "treasury",
                                                 "kind": "team"}}},
            }}},
        headers=ANA)
    assert saved.status_code == 200, saved.text

    layout = client.get(f"/api/designer/systems/{made['id']}",
                        headers=ANA).json()["record"]["layout"]
    assert sorted(layout["diagrams"]) == sorted([MAIN_DIAGRAM, "dia_1"])
    assert layout["diagrams"]["dia_1"]["root"] == "treasury"
    assert layout["active"] == "dia_1"


def test_a_diagram_still_never_reaches_the_spec(client):
    """ADR-0034 held: more pictures is still no picture in the design."""
    workspace = client.post("/api/designer/workspaces", json={"name": "w"},
                            headers=ANA).json()
    made = client.post("/api/designer/systems",
                       json={"workspace_id": workspace["id"], "name": "s",
                             "spec": {"metadata": {"name": "s"}}},
                       headers=ANA).json()
    client.put(f"/api/designer/systems/{made['id']}",
               json={"version": made["version"], "layout": {"diagrams": {
                   "dia_1": {"id": "dia_1", "name": "Treasury",
                             "root": "treasury", "nodes": {}}}}},
               headers=ANA)
    spec = client.get(f"/api/designer/systems/{made['id']}",
                      headers=ANA).json()["record"]["spec"]
    assert "diagrams" not in str(spec)
    assert "layout" not in spec


def test_the_canvas_reads_the_nodes_through_one_accessor():
    """A second way to reach the nodes is a second thing to keep in step."""
    import pathlib

    canvas_js = (pathlib.Path(__file__).resolve().parents[1]
                 / "web" / "canvas.js").read_text(encoding="utf-8")
    assert "function layoutNodes()" in canvas_js
    # `layout` inside a function is the *diagram*; reaching through the
    # record to a `nodes` that no longer exists is the thing to keep out.
    assert "record.layout.nodes" not in canvas_js
    assert "record?.layout?.nodes" not in canvas_js
