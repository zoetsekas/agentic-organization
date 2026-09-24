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


@pytest.mark.parametrize("name", ["tree", "layered", "grid", "lanes", "derivation",
                                  "deployment"])
def test_every_layout_is_deterministic(name, org_nodes):
    """A diagram that moved every time somebody opened it would be unusable."""
    edges = [LayoutEdge("ceo", "cfo"), LayoutEdge("cfo", "ap")]
    first = arrange(org_nodes, edges, algorithm=name)
    second = arrange(org_nodes, edges, algorithm=name)
    assert first.positions == second.positions


@pytest.mark.parametrize("name", ["tree", "layered", "grid", "lanes", "derivation",
                                  "deployment"])
def test_every_layout_places_every_node(name, org_nodes):
    result = arrange(org_nodes, [], algorithm=name)
    assert set(result.positions) == {n.id for n in org_nodes}


def test_the_default_follows_what_the_diagram_is():
    """A process is a flow and an organisation is a hierarchy; defaulting
    either to the other produces a picture that argues with the model."""
    assert arrange([LayoutNode("a")], kind="organisation").algorithm == "tree"
    assert arrange([LayoutNode("a")], kind="process").algorithm == "lanes"
    assert arrange([LayoutNode("a")], kind="data").algorithm == "derivation"
    assert arrange([LayoutNode("a")], kind="deployment").algorithm == "deployment"


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


# -- lanes: a governed process, one swimlane per owner (ADR-0110) -------------

def _p2p():
    nodes = [LayoutNode("raise", lane="purchasing"), LayoutNode("approve", lane="operations"),
             LayoutNode("fork"), LayoutNode("receive", lane="purchasing"),
             LayoutNode("capture", lane="finance"), LayoutNode("join"),
             LayoutNode("pay", lane="finance")]
    edges = [LayoutEdge(s, t) for s, t in [
        ("raise", "approve"), ("approve", "fork"), ("fork", "receive"),
        ("fork", "capture"), ("receive", "join"), ("capture", "join"),
        ("join", "pay"), ("pay", "raise")]]
    return nodes, edges


def test_lanes_put_each_step_in_its_owners_lane_in_the_order_first_reached():
    result = arrange(*_p2p(), algorithm="lanes")
    assert [b["id"] for b in result.lanes] == ["purchasing", "operations", "finance"]
    band = {s: b for b in result.lanes for s in b["steps"]}
    # A fork sits with the step that leads to it; a join with its first branch.
    assert band["fork"]["id"] == "operations" and band["join"]["id"] == "purchasing"
    for step, pos in result.positions.items():
        b = band[step]
        assert b["y"] <= pos["y"] and pos["y"] + NODE_H <= b["y"] + b["height"]


def test_lanes_flow_left_to_right_and_a_loop_back_is_not_ranked():
    result = arrange(*_p2p(), algorithm="lanes")
    x = {k: v["x"] for k, v in result.positions.items()}
    assert x["raise"] < x["approve"] < x["fork"] < x["receive"] == x["capture"] < x["join"] < x["pay"]
    assert any("'pay' → 'raise' is a loop back" in n for n in result.notes)


def test_lanes_never_overlap_two_steps():
    result = arrange(*_p2p(), algorithm="lanes")
    boxes = list(result.positions.values())
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert abs(a["x"] - b["x"]) >= NODE_W or abs(a["y"] - b["y"]) >= NODE_H


# -- derivation: the Data diagram, lineage left to right (ADR-0111) ----------


@pytest.fixture
def lineage():
    """AYC's shape: a producer, two bases, a class derived from both, a part
    and its whole, a reliant agent, and a class nothing relates to."""
    nodes = [LayoutNode(i) for i in (
        "public_knowledge", "customer_pii", "customer_account", "order_data",
        "sales_report", "ar_agent", "marketing_agent")]
    edges = [
        LayoutEdge("order_data", "sales_report"),       # base, derived
        LayoutEdge("customer_pii", "sales_report"),
        LayoutEdge("order_data", "customer_account"),   # part, whole
        LayoutEdge("customer_pii", "customer_account"),
        LayoutEdge("ar_agent", "sales_report"),         # producer, data
        LayoutEdge("sales_report", "marketing_agent"),  # data, reliant
    ]
    return nodes, edges


def test_derivation_reads_left_to_right(lineage):
    nodes, edges = lineage
    p = arrange(nodes, edges, kind="data").positions
    for e in edges:
        assert p[e.source]["x"] < p[e.target]["x"], (e.source, e.target)


def test_derivation_overlaps_nothing(lineage):
    nodes, edges = lineage
    assert _overlaps(arrange(nodes, edges, algorithm="derivation").positions) == []


def test_a_producer_sits_level_with_what_it_produces(lineage):
    """The leftward sweep: the agent that produces the report is not left at
    the bottom of the first column under every unrelated class."""
    nodes, edges = lineage
    p = arrange(nodes, edges, algorithm="derivation").positions
    assert p["ar_agent"]["y"] <= p["public_knowledge"]["y"]


def test_a_class_nothing_relates_to_sinks_to_the_bottom(lineage):
    nodes, edges = lineage
    p = arrange(nodes, edges, algorithm="derivation").positions
    first = [i for i, at in p.items() if at["x"] == 0]
    assert max(first, key=lambda i: p[i]["y"]) == "public_knowledge"


def test_a_derivation_cycle_is_named_not_ranked():
    nodes = [LayoutNode("a"), LayoutNode("b")]
    result = arrange(nodes, [LayoutEdge("a", "b"), LayoutEdge("b", "a")],
                     algorithm="derivation")
    assert result.notes and "cycle" in result.notes[0]
    assert _overlaps(result.positions) == []


# -- deployment: the binding, a node over what runs on it (ADR-0112 M7) ------


@pytest.fixture
def binding():
    return [
        LayoutNode("target:local"),
        LayoutNode("server:shopify", parent="target:local"),
        LayoutNode("product_publishing", parent="server:shopify"),
        LayoutNode("order_query", parent="server:shopify"),
        LayoutNode("refund_processing", parent="server:shopify"),
        LayoutNode("server:fishbowl", parent="target:local"),
        LayoutNode("stock_check", parent="server:fishbowl"),
        LayoutNode("engine:langflow", parent="target:local"),
        LayoutNode("invoice_capture", parent="engine:langflow"),
        *[LayoutNode(f"env_{i}", parent="target:local") for i in range(6)],
    ]


def test_deployment_stacks_what_runs_on_a_server_under_it(binding):
    p = arrange(binding, kind="deployment").positions
    column = [p[i] for i in ("product_publishing", "order_query",
                             "refund_processing")]
    assert len({at["x"] for at in column}) == 1
    assert column[0]["x"] > p["server:shopify"]["x"]          # indented
    assert all(at["y"] > p["server:shopify"]["y"] for at in column)
    assert [at["y"] for at in column] == sorted(at["y"] for at in column)


def test_deployment_puts_the_target_above_everything(binding):
    p = arrange(binding, kind="deployment").positions
    assert all(at["y"] > p["target:local"]["y"]
               for i, at in p.items() if i != "target:local")


def test_deployment_packs_leaves_into_columns(binding):
    """Six environments are two columns, not six: fourteen capabilities on
    one row was the picture `tree` drew for AYC, five screens wide."""
    p = arrange(binding, kind="deployment").positions
    assert len({p[f"env_{i}"]["x"] for i in range(6)}) == 2
    assert _overlaps(p) == []


def test_the_layout_route_lays_out_a_data_diagram_by_default(tmp_path):
    from fastapi.testclient import TestClient

    from orgagents.api import create_app
    client = TestClient(create_app(str(tmp_path / "d.db")))
    body = client.post("/api/designer/layout", headers={"X-User": "ana"}, json={
        "kind": "data",
        "nodes": [{"id": "base"}, {"id": "derived"}],
        "edges": [{"source": "base", "target": "derived"}],
    }).json()
    assert body["algorithm"] == "derivation"
    assert body["positions"]["base"]["x"] < body["positions"]["derived"]["x"]


def test_data_and_deployment_diagrams_are_kinds_a_layout_may_save():
    from orgagents.designer.models import Diagram, DiagramKind

    assert Diagram.model_validate({"kind": "data"}).kind is DiagramKind.DATA
    assert Diagram.model_validate({"kind": "deployment"}).kind is DiagramKind.DEPLOYMENT
