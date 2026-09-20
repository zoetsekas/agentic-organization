"""The designer's visual system, held from outside the browser.

There is no renderer in the test suite, so these assert the things a reviewer
would otherwise have to take on trust: that the palette is a token layer rather
than hexes sprinkled through components, that both typefaces are actually
linked, that the semantic colours stay distinct from the accent, that a node
carries the classification stripe the vocabulary promises, that
`not_evaluated` renders as its own state rather than as a failure, and that the
consequence rail asks for its two routes and survives their absence.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "web"

HEX = re.compile(r"#[0-9A-Fa-f]{3,8}\b")


@pytest.fixture(scope="module")
def css() -> str:
    return (BUNDLE / "styles.css").read_text()


@pytest.fixture(scope="module")
def index_html() -> str:
    return (BUNDLE / "index.html").read_text()


@pytest.fixture(scope="module")
def app_js() -> str:
    return (BUNDLE / "app.js").read_text()


@pytest.fixture(scope="module")
def canvas_js() -> str:
    return (BUNDLE / "canvas.js").read_text()


def _root_block(css: str) -> str:
    start = css.index(":root {")
    return css[start:css.index("}", start)]


# -- the token layer -------------------------------------------------------

PALETTE = {
    "--ground": "#0E1114",
    "--panel": "#12161B",
    "--raised": "#171C22",
    "--line": "#262C34",
    "--line-strong": "#333B45",
    "--ink": "#E6E9EC",
    "--ink-muted": "#9BA5B0",
    "--accent": "#4FB3A3",
    "--accent-deep": "#1F5E57",
    "--moss": "#6FBF73",
    "--amber": "#E0A857",
    "--clay": "#E0705F",
    "--slate": "#9BA5B0",
}


def test_the_palette_is_declared_once_as_tokens(css):
    root = _root_block(css)
    for token, value in PALETTE.items():
        assert f"{token}: {value};" in root, f"{token} is not the approved value"


def test_the_type_scale_and_spacing_are_tokens_too(css):
    root = _root_block(css)
    for token in ("--font-ui", "--font-mono", "--t-body", "--t-micro",
                  "--s1", "--s6", "--r", "--r-pill"):
        assert token in root, f"{token} is missing from the token layer"


def test_components_are_built_from_the_tokens_not_from_literals(css):
    """A hex below :root means a component invented a colour of its own."""
    body = css[css.index("}", css.index(":root {")):]
    stray = {h for h in HEX.findall(body)}
    assert not stray, f"component rules carry colour literals: {sorted(stray)}"


def test_the_components_the_design_names_exist(css):
    for selector in (".contextbar", ".tabs", ".panel", ".card", ".badge",
                     ".canvas-surface .node", "label", "button.primary",
                     ".validation-strip", ".resolves", ".rail", ".gate"):
        assert selector in css, f"no component rule for {selector}"


def test_the_js_carries_no_colour_of_its_own(app_js, canvas_js):
    for name, source in (("app.js", app_js), ("canvas.js", canvas_js)):
        assert not HEX.findall(source), f"{name} hard-codes a colour"


# -- type ------------------------------------------------------------------

def test_both_typefaces_are_linked_from_the_one_allowed_host(index_html):
    link = [line for line in index_html.splitlines()
            if "fonts.googleapis.com/css2" in line]
    assert link, "the Google Fonts stylesheet is not linked"
    assert "family=Archivo" in link[0]
    assert "JetBrains+Mono" in link[0]
    # No other external host is introduced by the bundle.
    hosts = set(re.findall(r"https://([a-z0-9.\-]+)/", index_html))
    assert hosts <= {"fonts.googleapis.com", "fonts.gstatic.com"}, hosts


def test_machine_decided_values_are_set_in_mono(css):
    mono_rule = [line for line in css.splitlines()
                 if "font-family: var(--font-mono)" in line or ".badge, .chip" in line]
    assert mono_rule
    assert "code, .mono, .badge" in css


# -- colour says state, shape says kind ------------------------------------

def test_the_semantic_colours_are_distinct_from_the_accent(css):
    root = _root_block(css)
    values = {}
    for token in ("--accent", "--moss", "--amber", "--clay", "--slate"):
        values[token] = re.search(rf"{token}: (#[0-9A-Fa-f]{{6}});", root).group(1)
    assert len(set(values.values())) == 5, f"a state reuses another hue: {values}"
    # The accent marks selection and the primary action; it is never a state.
    assert values["--accent"] not in (values["--moss"], values["--amber"],
                                      values["--clay"], values["--slate"])


def test_shape_carries_the_kind(css, canvas_js):
    for shape in ("container", "card", "capsule", "sandbox", "chevron"):
        assert f'[data-shape="{shape}"]' in css, f"no rule for the {shape} shape"
    assert "const SHAPES" in canvas_js
    for kind in ("team:", "agent:", "mission:", "endpoint:"):
        assert kind in canvas_js.split("const SHAPES")[1][:300]


def test_an_agent_node_carries_its_classification_stripe(css, canvas_js):
    assert "classificationOf" in canvas_js
    assert '"data-classification": node.kind === "agent"' in canvas_js
    for scope in ("private", "protected", "public", "unclassified"):
        assert f'[data-classification="{scope}"]' in css
    # The stripe is the left border of the agent card, not a background wash.
    assert 'border-left: 3px solid var(--slate)' in css


def test_a_sandbox_shows_its_network_posture_and_a_mission_its_end_date(canvas_js):
    assert "postureOf" in canvas_js
    assert "n-net" in canvas_js
    assert "ends " in canvas_js


def test_edges_are_drawn_by_what_they_are(css, canvas_js):
    assert "EDGE_STYLES" in canvas_js
    styles = canvas_js.split("const EDGE_STYLES")[1][:600]
    assert "--edge-report" in styles          # reporting line, solid
    assert '"--edge-peer", dash' in styles    # mission peer, dashed
    assert '"--edge-egress", dash' in styles  # egress, dotted amber
    for token in ("--edge-report", "--edge-peer", "--edge-egress"):
        assert token in css


# -- the canvas's own promises ---------------------------------------------

def test_the_validation_strip_names_the_rule(index_html, canvas_js):
    assert 'id="validation-strip"' in index_html
    assert "function renderValidationStrip" in canvas_js
    assert "rules" in canvas_js


def test_the_inspector_says_what_the_bindings_resolve_to(index_html, canvas_js):
    assert "function effectivePermissions" in canvas_js
    assert "Resolves to" in canvas_js
    # Resolved from the open spec: a design fact, never a runtime lookup.
    resolves = canvas_js.split("function effectivePermissions")[1][:900]
    assert "spec()" in resolves
    assert "fetch(" not in resolves


# -- the consequence rail --------------------------------------------------

def test_the_rail_calls_the_two_routes_it_was_given(app_js):
    assert "/systems/${systemId}/gate" in app_js
    assert "/systems/${systemId}/diff?from=${from}&to=${to}" in app_js
    assert "/api/designer" in app_js


def test_absence_is_absence_and_not_a_broken_panel(app_js):
    # Intent, not mechanism: whatever the transport, a review route that has
    # nothing to say must yield null rather than propagate. The rail went in
    # before its routes existed and used a bare fetch to dodge a test; it now
    # uses `dapi` like every other call, so assert on the behaviour instead.
    body = app_js.split("async function consequence(")[1][:600]
    assert "return null" in body
    assert "catch" in body
    assert "dapi(" in body
    # Nothing to say means an empty rail, not an error state.
    assert "if (!verdict && !changes.length) return host.replaceChildren();" in app_js


def test_not_evaluated_is_its_own_state_and_not_a_failure(app_js, css):
    words = app_js.split("const GATE_WORDS")[1][:500]
    assert "not_evaluated:" in words and "failed:" in words
    assert "not a failure" in words
    assert '.gate[data-state="not_evaluated"]' in css
    assert '.gate[data-state="failed"]' in css
    # Amber, not clay: the two states are drawn from different grounds.
    amber = css.split('.gate[data-state="not_evaluated"], .gate[data-state="stale"] {')[1]
    assert "--amber-ground" in amber.split("}")[0]
    clay = css.split('.gate[data-state="failed"] {')[1]
    assert "--clay-ground" in clay.split("}")[0]


def test_security_relevant_findings_lead_the_diff(app_js):
    assert "security_relevant" in app_js
    assert "SEVERITY_ORDER" in app_js


# -- accessibility as drawn ------------------------------------------------

def test_focus_is_visible_and_controls_are_real(css, index_html):
    assert ":focus-visible" in css
    assert "outline: 2px solid var(--accent)" in css
    # Every select in the shell is reachable by a label or a title.
    for match in re.finditer(r"<select [^>]*id=\"([\w-]+)\"[^>]*>", index_html):
        tag, ident = match.group(0), match.group(1)
        assert f'for="{ident}"' in index_html or "title=" in tag or "aria-label" in tag


def test_a_refused_control_reads_as_unavailable(css):
    assert "button[disabled], input[disabled]" in css
    assert "cursor: not-allowed" in css


# -- the catalog drawer keeps its refusal ----------------------------------

def test_the_locked_drawer_separates_editorial_from_substantive(app_js, css):
    assert "Editorial — you may change these" in app_js
    assert "Substantive — locked by review" in app_js
    assert "#pc-form .substantive" in css
    # The refusal itself is still the API's own sentence.
    assert "detail.amend_refusal" in app_js
    assert "amend-refusal" in css
