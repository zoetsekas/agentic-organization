"""Layouts that actually place things (ADR-0100).

These exist because layout is the kind of code that rots quietly: a bad result
still renders, so nothing tells you. Computing it in Python makes "no two
nodes overlap" and "a child sits below its parent" assertions rather than
opinions.
"""
from __future__ import annotations

import pytest

from orgagents.designer.layout import (
    GAP_X,
    NODE_H,
    NODE_W,
    LayoutEdge,
    LayoutNode,
    arrange,
    grid,
    layered,
    tree,
)


def _overlaps(positions: dict) -> list[tuple[str, str]]:
    """Every pair of boxes that intersect."""
    out = []
    items = sorted(positions.items())
    for i, (a, pa) in enumerate(items):
        for b, pb in items[i + 1:]:
            if (abs(pa["x"] - pb["x"]) < NODE_W
                    and abs(pa["y"] - pb["y"]) < NODE_H):
                out.append((a, b))
    return out


# -- tree: an organisation --------------------------------------------------


@pytest.fixture
def org_nodes():
    return [
        LayoutNode("ceo"),
        LayoutNode("cfo", parent="ceo"),
        LayoutNode("cto", parent="ceo"),
        LayoutNode("ap", parent="cfo"),
        LayoutNode("ar", parent="cfo"),
        LayoutNode("sre", parent="cto"),
    ]


def test_a_child_sits_below_its_parent(org_nodes):
    p = tree(org_nodes).positions
    for node in org_nodes:
        if node.parent:
            assert p[node.id]["y"] > p[node.parent]["y"], node.id


def test_a_parent_is_centred_over_its_children(org_nodes):
    """The thing the old diagonal cascade was failing to be."""
    p = tree(org_nodes).positions
    kids = [p["ap"]["x"], p["ar"]["x"]]
    assert min(kids) < p["cfo"]["x"] < max(kids)


def test_nothing_overlaps(org_nodes):
    assert _overlaps(tree(org_nodes).positions) == []


def test_a_wide_team_pushes_its_sibling_aside_rather_than_over_it():
    nodes = [LayoutNode("root")]
    nodes += [LayoutNode(f"a{i}", parent="wide") for i in range(6)]
    nodes += [LayoutNode("wide", parent="root"), LayoutNode("thin", parent="root")]
    result = tree(nodes)
    assert _overlaps(result.positions) == []
    assert result.positions["thin"]["x"] > result.positions["wide"]["x"]


def test_a_node_whose_parent_is_not_here_is_a_root_not_an_error():
    """The common case when a diagram is scoped to one team; refusing it
    would make a drill-down unlayoutable."""
    result = tree([LayoutNode("orphan", parent="elsewhere")])
    assert result.positions["orphan"]["y"] == 0


def test_a_node_under_two_parents_is_drawn_once_and_said_so():
    nodes = [LayoutNode("a"), LayoutNode("b"), LayoutNode("shared", parent="a")]
    nodes.append(LayoutNode("shared", parent="b"))
    result = tree(nodes)
    assert "shared" in result.positions
    assert any("more than one parent" in n for n in result.notes)


# -- layered: a process -----------------------------------------------------


def test_a_pipeline_ranks_in_order():
    nodes = [LayoutNode(i) for i in ("a", "b", "c")]
    edges = [LayoutEdge("a", "b"), LayoutEdge("b", "c")]
    p = layered(nodes, edges).positions
    assert p["a"]["y"] < p["b"]["y"] < p["c"]["y"]


def test_a_loop_back_does_not_push_its_target_below_its_source():
    """A review loop that runs until it passes is a legal process. Ranking
    naively over the back edge would draw a process that runs upward."""
    nodes = [LayoutNode(i) for i in ("survey", "gate", "order")]
    edges = [LayoutEdge("survey", "gate"), LayoutEdge("gate", "order"),
             LayoutEdge("order", "survey")]
    result = layered(nodes, edges)
    p = result.positions
    assert p["survey"]["y"] < p["gate"]["y"] < p["order"]["y"]
    assert any("loop back" in n for n in result.notes)


def test_a_branch_puts_its_arms_side_by_side():
    nodes = [LayoutNode(i) for i in ("start", "left", "right", "join")]
    edges = [LayoutEdge("start", "left"), LayoutEdge("start", "right"),
             LayoutEdge("left", "join"), LayoutEdge("right", "join")]
    p = layered(nodes, edges).positions
    assert p["left"]["y"] == p["right"]["y"]
    assert p["left"]["x"] != p["right"]["x"]
    assert p["join"]["y"] > p["left"]["y"]


def test_a_process_layout_overlaps_nothing():
    nodes = [LayoutNode(i) for i in "abcdefgh"]
    edges = [LayoutEdge("a", "b"), LayoutEdge("a", "c"), LayoutEdge("b", "d"),
             LayoutEdge("c", "d"), LayoutEdge("d", "e"), LayoutEdge("e", "a")]
    assert _overlaps(layered(nodes, edges).positions) == []


def test_a_step_nothing_connects_still_lands_somewhere():
    nodes = [LayoutNode("a"), LayoutNode("island")]
    assert "island" in layered(nodes, [LayoutEdge("a", "a")]).positions


# -- grid: a flat collection ------------------------------------------------


def test_a_flat_set_is_placed_in_reading_order():
    nodes = [LayoutNode(f"p{i}") for i in range(6)]
    p = grid(nodes, columns=3).positions
    assert p["p0"]["y"] == p["p1"]["y"] == p["p2"]["y"]
    assert p["p3"]["y"] > p["p0"]["y"]
    assert p["p0"]["x"] < p["p1"]["x"] < p["p2"]["x"]


def test_a_long_list_does_not_become_a_strip():
    p = grid([LayoutNode(f"n{i}") for i in range(16)]).positions
    assert max(v["x"] for v in p.values()) < 4 * (NODE_W + GAP_X)


def test_an_empty_diagram_lays_out_to_nothing():
    assert grid([]).positions == {}


# -- the properties every algorithm owes ------------------------------------


@pytest.mark.parametrize("name", ["tree", "layered", "grid"])
def test_every_layout_is_deterministic(name, org_nodes):
    """A diagram that moved every time somebody opened it would be unusable."""
    edges = [LayoutEdge("ceo", "cfo"), LayoutEdge("cfo", "ap")]
    first = arrange(org_nodes, edges, algorithm=name)
    second = arrange(org_nodes, edges, algorithm=name)
    assert first.positions == second.positions


@pytest.mark.parametrize("name", ["tree", "layered", "grid"])
def test_every_layout_places_every_node(name, org_nodes):
    result = arrange(org_nodes, [], algorithm=name)
    assert set(result.positions) == {n.id for n in org_nodes}


def test_the_default_follows_what_the_diagram_is():
    """A process is a flow and an organisation is a hierarchy; defaulting
    either to the other produces a picture that argues with the model."""
    assert arrange([LayoutNode("a")], kind="organisation").algorithm == "tree"
    assert arrange([LayoutNode("a")], kind="process").algorithm == "layered"


def test_an_unknown_algorithm_names_the_ones_that_exist():
    with pytest.raises(ValueError) as caught:
        arrange([], algorithm="spiral")
    assert "grid" in str(caught.value) and "layered" in str(caught.value)


# -- a canvas declares what it draws ----------------------------------------


def test_an_existing_diagram_loads_as_an_organisation():
    """Nothing migrates: every diagram that existed before is one."""
    from orgagents.designer.models import Diagram, DiagramKind

    assert Diagram().kind is DiagramKind.ORGANISATION
    assert Diagram.model_validate({"name": "Old"}).kind is DiagramKind.ORGANISATION


def test_a_process_canvas_must_be_rooted_at_a_workflow():
    """A kind is a claim about what a canvas contains, and a canvas that
    quietly drew the wrong thing would be worse than one that refused."""
    from orgagents.designer.models import Diagram, DiagramKind, Layout
    from orgagents.designer.service import (
        DiagramKindMismatch,
        _check_diagram_kinds,
    )

    spec = {"workflows": [{"id": "restock"}]}
    good = Layout(diagrams={"d": Diagram(kind=DiagramKind.PROCESS,
                                         root="restock")})
    _check_diagram_kinds(good, spec)          # no raise

    rootless = Layout(diagrams={"d": Diagram(kind=DiagramKind.PROCESS)})
    with pytest.raises(DiagramKindMismatch) as caught:
        _check_diagram_kinds(rootless, spec)
    assert "names no workflow" in str(caught.value)

    wrong = Layout(diagrams={"d": Diagram(kind=DiagramKind.PROCESS,
                                          root="finance")})
    with pytest.raises(DiagramKindMismatch) as caught:
        _check_diagram_kinds(wrong, spec)
    assert "not a workflow in this design" in str(caught.value)


def test_an_organisation_canvas_may_not_be_rooted_at_a_workflow():
    from orgagents.designer.models import Diagram, Layout
    from orgagents.designer.service import (
        DiagramKindMismatch,
        _check_diagram_kinds,
    )

    layout = Layout(diagrams={"d": Diagram(root="restock")})
    with pytest.raises(DiagramKindMismatch) as caught:
        _check_diagram_kinds(layout, {"workflows": [{"id": "restock"}]})
    assert "Set its kind to process" in str(caught.value)


def test_a_process_diagram_stores_no_edges():
    """The one rule the diagram model has. A process canvas is exactly where
    a second copy of the edges would drift from the spec."""
    from orgagents.designer.models import Diagram

    assert "edges" not in Diagram.model_fields
