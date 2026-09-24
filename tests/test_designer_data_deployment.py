"""The Data and Deployment diagrams, and the palette by profile (ADR-0111,
ADR-0112 M7).

Three layers, like the rest of the designer suite. The bundle is read as
text for its structural promises: each aspect declares its profiles, the
palette is grouped by profile, the new relationships reach the relationship
filter, the edges keep the incremental renderer's per-edge update, the
Deployment diagram is read-only and says so when there is nothing to draw.
The canvas's own derivations are run under node over AYC — every data class
and relation drawn, the restriction inherited along «derive» shown on the
derived class with its source, the binding's servers and deployments. And a
real browser (Playwright, marked `e2e`) opens both diagrams and walks them
from the keyboard.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

# The live server the accessibility suite's browser tests use: AYC loaded.
from test_designer_accessibility import live_designer  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


@pytest.fixture(scope="module")
def canvas_js() -> str:
    return (WEB / "canvas.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    start = re.search(rf"^(async )?function {name}\(", source, re.M)
    assert start, f"{name} is gone"
    end = re.search(r"^(async )?function \w+\(|^const \w+ =", source[start.end():], re.M)
    return source[start.start():start.end() + (end.start() if end else len(source))]


def _aspects(canvas_js: str) -> dict[str, list[str]]:
    block = canvas_js[canvas_js.index("const ASPECTS = {"):]
    block = block[:block.index("\n};") + 3]
    return {name: re.findall(r'"(\w+)"', profiles)
            for name, profiles in re.findall(
                r"^  (\w+): \{.*?profiles: \[([^\]]*)\]", block, re.M | re.S)}


# -- aspects and the palette ---------------------------------------------------

def test_each_aspect_declares_the_profiles_it_draws(canvas_js):
    assert _aspects(canvas_js) == {
        "organisation": ["Organisation", "Authority", "Access"],
        "process": ["Process"],
        "data": ["Data"],
        "deployment": ["Deployment"],
    }


def test_every_declared_profile_exists():
    from orgagents.metamodel import PROFILES
    names = {p.name for p in PROFILES}
    for profiles in _aspects((WEB / "canvas.js").read_text("utf-8")).values():
        assert set(profiles) <= names


def test_the_palette_is_grouped_by_profile(canvas_js):
    load = _function(canvas_js, "loadPalette")
    assert "canvas.palette.profiles" in load, "the palette ignores each kind's profile"
    assert '"data-profile": name' in load
    sync = _function(canvas_js, "syncPalette")
    assert "aspectOf()" in sync and "canvas.paletteFor(mode)" in sync


def test_the_palette_route_sends_each_kinds_profile(tmp_path):
    from fastapi.testclient import TestClient

    from orgagents.api import create_app
    client = TestClient(create_app(str(tmp_path / "d.db")))
    body = client.get("/api/designer/palette", headers={"X-User": "ana"}).json()
    assert body["profiles"]["data_class"] == "Data"
    assert body["profiles"]["team"] == "Organisation"
    assert body["profiles"]["decision"] == "Authority"
    assert body["profiles"]["capability"] == "Access"


def test_a_drop_outside_the_open_aspects_profiles_is_refused(canvas_js):
    wire = _function(canvas_js, "wireDropTarget")
    assert 'diagram()?.kind === "deployment"' in wire
    assert "canvas.palette?.profiles?.[kind]" in wire


# -- the Data diagram -------------------------------------------------------------

def test_the_data_relations_are_drawn_in_uml_notation(canvas_js):
    notation = canvas_js[canvas_js.index("const DATA_NOTATION = {"):]
    notation = notation[:notation.index("};")]
    derive = re.search(r"derived_from: \{([^}]*)\}", notation).group(1)
    assert 'head: "open"' in derive and 'dash: "6 4"' in derive and "derive" in derive
    assert 'head: "diamond"' in re.search(r"part_of: \{([^}]*)\}", notation).group(1)
    for plain in ("identifies", "references"):
        assert 'head: ""' in re.search(rf"{plain}: \{{([^}}]*)\}}", notation).group(1)


def test_a_dependency_is_drawn_as_an_association_class(canvas_js):
    edges = _function(canvas_js, "dataEdges")
    assert "assocClass" in edges and "max_age_seconds" in edges and "dep.fields" in edges
    label = _function(canvas_js, "umlLabel")
    assert "assoc-box" in label and "assoc-tether" in label


def test_a_drawn_relation_is_created_by_the_model_operation(canvas_js):
    """`relations` holds four relationships, so the link names the one by
    its stereotype and the model writes the selecting `kind`."""
    apply = _function(canvas_js, "applyLink")
    assert "rule.selector ? rule.relationship : rule.field" in apply
    assert "modelOperation(" in apply


def test_the_relationship_filter_lists_the_new_relationships(canvas_js):
    order = re.search(r"const UML_ORDER = \[([^\]]*)\]", canvas_js).group(1)
    assert '"abstraction"' in order and '"deployment"' in order
    edges = _function(canvas_js, "derivedEdges")
    assert 'open?.kind === "data"' in edges and "dataEdges()" in edges


def test_the_org_diagram_draws_only_each_rules_own_relations(canvas_js):
    """`modelEdges` reads `relations` once per rule; without the selector it
    drew every relation four times, once under each label."""
    assert "rule.selector[0]" in _function(canvas_js, "modelEdges")


def test_schema_ref_is_a_link_out_and_in_the_profile(canvas_js):
    from orgagents.metamodel import PROFILE
    from orgagents.spec.model import DataClass
    assert "schema_ref" in DataClass.model_fields
    assert any(p.owner == "data_class" and p.name == "schema_ref"
               for p in PROFILE.properties)
    body = _function(canvas_js, "dataClassBody")
    assert 'target: "_blank"' in body and "noopener" in body and "schema_ref" in body


def test_the_data_class_face_carries_what_it_inherits(canvas_js):
    assert "inheritedRestrictions(node.id)" in _function(canvas_js, "nodeSignature"), (
        "a class's box must redraw when a class it derives from changes")
    assert "inheritedRestrictions(dc.id)" in _function(canvas_js, "dataClassBody")


# -- the Deployment diagram ---------------------------------------------------------

def test_the_deployment_diagram_is_read_only(canvas_js):
    node = _function(canvas_js, "renderNode")
    assert "ASPECTS[aspectOf()].readOnly" in node
    assert "readOnly: true" in canvas_js[canvas_js.index("deployment: {"):][:400]
    assert "BINDING_KINDS[kind]" in _function(canvas_js, "renderInspector")


def test_no_binding_says_so(canvas_js):
    empty = _function(canvas_js, "syncEmptyState")
    assert "No binding to draw." in empty


# -- incremental rendering and the keyboard (ADR-0117) ---------------------------

def test_a_drag_on_the_new_diagrams_moves_only_its_own_edges(canvas_js):
    update = _function(canvas_js, "updateEdgesFor")
    assert "geometryFor(layout.kind, a, b)" in update
    assert "placeEdgeLabel(" in update
    # The new diagrams' edges carry their ends, so a drag can find them.
    uml = _function(canvas_js, "renderUmlEdges")
    assert '"data-source": edge.source' in uml and '"data-target": edge.target' in uml


def test_the_new_nodes_are_ordinary_nodes(canvas_js):
    """Keyboard navigation and the kept-element renderer come from renderNode;
    the diagrams add no node renderer of their own."""
    assert canvas_js.count("function renderNode(") == 1
    assert "dataClassBody(component)" in _function(canvas_js, "renderNode")


# -- legend and guide -------------------------------------------------------------

def test_each_aspect_has_a_legend_row(index_html):
    legend = index_html[index_html.index('<div class="canvas-legend"'):]
    legend = legend[:legend.index("</div>")]
    for aspect in ("organisation", "process", "data", "deployment"):
        assert f'data-aspects="{aspect}"' in legend
    assert "«derive»" in legend and "«deploy»" in legend


def test_the_guide_explains_both_diagrams_with_a_figure(index_html):
    for anchor in ("g-data", "g-deployment"):
        section = index_html[index_html.index(f'id="{anchor}"'):]
        section = section[:section.index("</section>")]
        assert '<svg class="gd"' in section and "<title" in section, anchor


# -- the canvas's derivations over AYC, under node ---------------------------------

@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    from orgagents.metamodel import link_rules
    spec = yaml.safe_load((ROOT / "examples/ayc/ayc.system.yaml").read_text("utf-8"))
    binding = yaml.safe_load(
        (ROOT / "examples/ayc/ayc.local.binding.yaml").read_text("utf-8"))
    path = tmp_path_factory.mktemp("dd") / "in.json"
    path.write_text(json.dumps({"spec": spec, "links": link_rules(),
                                "binding": binding}), encoding="utf-8")
    out = subprocess.run([node, "tests/data_diagram_check.mjs", str(path)],
                         capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.returncode == 0, out.stderr
    return {"spec": spec, **json.loads(out.stdout)}


def test_every_ayc_relation_is_drawn(drawn):
    classes = drawn["spec"]["organization"]["data_classes"]
    want = {(d["id"], r["kind"], r["target"])
            for d in classes for r in d.get("relations", [])}
    got = {(e["source"], e["relation"], e["target"])
           for e in drawn["edges"] if e.get("relation")}
    assert want == got and len(want) >= 5
    kinds = {k for _s, k, _t in want}
    assert {"derived_from", "part_of", "identifies", "references"} <= kinds


def test_ayc_producers_and_consumers_are_on_the_data_diagram(drawn):
    assert {"ar_agent", "marketing_agent", "inventory_agent",
            "ecommerce_agent"} <= set(drawn["agents"])
    reliance = next(e for e in drawn["edges"] if e["source"] == "marketing_agent")
    assert reliance["target"] == "sales_report"
    assert "sku, region, units, revenue" in reliance["assocClass"]["lines"]
    assert any("7 d" in line and "degrade" in line
               for line in reliance["assocClass"]["lines"])


def test_a_restriction_inherited_along_derive_is_shown_with_its_source(drawn):
    inherited = drawn["inherited"]["sales_report"]
    traces = next(r for r in inherited if r["key"] == "traces")
    assert traces["from"] == "customer_pii" and traces["dropped"] is False
    # Nothing is invented for a class derived from nothing.
    assert drawn["inherited"]["customer_pii"] == []


def test_lineage_is_ranked_base_first(drawn):
    ranked = {(e["source"], e["target"]) for e in drawn["layoutEdges"]}
    assert ("order_data", "sales_report") in ranked       # base → derived
    assert ("order_data", "customer_account") in ranked   # part → whole
    assert ("ar_agent", "sales_report") in ranked         # producer → data
    assert ("sales_report", "marketing_agent") in ranked  # data → reliant


def test_the_binding_is_drawn_as_deployment(drawn):
    nodes = {n["id"]: n for n in drawn["deployment"]["nodes"]}
    assert nodes["target:local"]["kind"] == "target"
    assert nodes["server:shopify"]["parent"] == "target:local"
    assert nodes["product_publishing"]["parent"] == "server:shopify"
    assert nodes["stock_check"]["parent"] == "server:fishbowl"
    assert nodes["invoice_capture"]["parent"] == "engine:langflow"
    assert "FISHBOWL_READ_TOKEN" in nodes["stock_check"]["subtitle"]
    deploy = {(e["source"], e["target"]) for e in drawn["deployment"]["edges"]
              if e["uml"] == "deployment"}
    assert ("product_publishing", "server:shopify") in deploy
    assert ("invoice_capture", "engine:langflow") in deploy


# -- in a browser ---------------------------------------------------------------------

@pytest.mark.e2e
def test_the_data_and_deployment_diagrams_open_and_walk_from_the_keyboard(
        live_designer):
    sync_api = pytest.importorskip("playwright.sync_api")
    base = live_designer
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:              # no browser downloaded here
            pytest.skip(f"no Playwright browser: {exc}")
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(f"{base}/ui/#/canvas")
        page.wait_for_function("document.querySelector('#status').textContent === 'ready'")
        page.wait_for_selector("#canvas-nodes .node")

        page.click('.dia-aspect[data-aspect="data"]')
        page.wait_for_selector('#canvas-nodes .node[data-id="sales_report"]')
        assert page.evaluate("aspectOf()") == "data"
        assert "Data" in page.inner_text("#palette-groups")
        assert "Team" not in page.inner_text("#palette-groups")
        inherit = page.inner_text('.node[data-id="sales_report"] .n-inherit')
        assert "customer_pii" in inherit
        assert page.query_selector('#canvas-edges path[data-rel="derive"]')
        page.focus('#canvas-nodes .node[data-id="sales_report"]')
        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement.dataset.id") != "sales_report"
        before = page.evaluate("canvas.renderStats.built")
        page.keyboard.press("Enter")                 # select: nothing rebuilt
        assert page.evaluate("canvas.renderStats.built") == before

        page.click('.dia-aspect[data-aspect="deployment"]')
        page.wait_for_selector('#canvas-nodes .node[data-id="server:shopify"]')
        assert page.query_selector('#canvas-edges path[data-uml="deployment"]')
        page.focus('#canvas-nodes .node[data-id="server:shopify"]')
        page.keyboard.press("Delete")                # read-only: nothing goes
        assert page.query_selector('#canvas-nodes .node[data-id="server:shopify"]')
        browser.close()
