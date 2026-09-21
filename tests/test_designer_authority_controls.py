"""The inspector's authority controls: the mandate picker and posture selector.

Both carry a distinction a plain control would flatten, so these tests hold
the distinction rather than the markup. A mandate that is *absent* inherits
its parent's; one that is present and empty decides nothing — and a picker
showing an empty list cannot tell you which it means. A posture may only be
tightened, so the loosening options must not be offered at all: a control that
looks available and is then refused by the gate is the bug.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.spec.model import AUTONOMY_ORDER

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "web"


@pytest.fixture(scope="module")
def canvas_js() -> str:
    return (BUNDLE / "canvas.js").read_text()


@pytest.fixture(scope="module")
def styles() -> str:
    return (BUNDLE / "styles.css").read_text()


@pytest.fixture()
def palette() -> dict:
    client = TestClient(create_app(":memory:"))
    body = client.get("/api/designer/palette", headers={"X-User": "a"}).json()
    return {k["kind"]: k for g in body["groups"] for k in g["kinds"]}


# --------------------------------------------------------------------------
# The palette asks for the right control
# --------------------------------------------------------------------------


def test_a_mandate_and_a_reference_list_are_different_controls(palette):
    """A separation's `decisions` is a plain list of ids. A mandate is an
    object with an inherit-or-empty question, and rendering one as the other
    would ask a question that has no meaning."""
    agent = {f["name"]: f for f in palette["agent"]["fields"]}
    separation = {f["name"]: f for f in palette["separation"]["fields"]}
    assert agent["mandate"]["type"] == "decisions"
    assert separation["decisions"]["type"] == "decision_refs"


def test_teams_and_agents_both_carry_a_mandate(palette):
    for kind in ("agent", "team"):
        names = {f["name"] for f in palette[kind]["fields"]}
        assert "mandate" in names, kind


def test_only_an_agent_carries_a_posture_map(palette):
    """A posture is per activity, and a team holds no capabilities."""
    assert "autonomy" in {f["name"] for f in palette["agent"]["fields"]}
    assert "autonomy" not in {f["name"] for f in palette["team"]["fields"]}


# --------------------------------------------------------------------------
# The controls honour the distinctions
# --------------------------------------------------------------------------


def test_the_inspector_renders_all_three_authority_types(canvas_js):
    for t in ('"decisions"', '"decision_refs"', '"autonomy"'):
        assert t in canvas_js, t
    assert "renderMandate" in canvas_js and "renderAutonomy" in canvas_js


def test_the_mandate_control_asks_the_inherit_question_outright(canvas_js):
    """Corporate Development declaring nothing inherited the root's entire
    authority. The control now asks instead of inferring from emptiness."""
    assert "declare a mandate here" in canvas_js
    assert "inherits its parent's mandate" in canvas_js
    assert "decides nothing" in canvas_js


def test_an_undeclared_mandate_is_null_and_not_an_empty_list(canvas_js):
    """`{decisions: []}` and absent are opposite meanings."""
    assert "current = box.checked ? { decisions: [] } : null;" in canvas_js


def test_the_posture_selector_offers_only_tighter_options(canvas_js):
    """A control that looks available and is then refused is the bug."""
    assert "POSTURE_RANK.slice(floor < 0 ? 0 : floor + 1)" in canvas_js
    assert "as declared" in canvas_js


def test_the_posture_order_matches_the_spec_models(canvas_js):
    """Two orderings that can drift is one that will."""
    declared = [p.value for p in AUTONOMY_ORDER]
    rendered = canvas_js.split("const POSTURE_RANK = [", 1)[1].split("]", 1)[0]
    assert [p.strip().strip('"') for p in rendered.split(",")] == declared


def test_the_tightest_posture_offers_nothing_and_says_so(canvas_js):
    assert "already the tightest" in canvas_js


def test_an_agent_with_no_capabilities_is_told_why_there_is_nothing(canvas_js):
    assert "there is nothing" in canvas_js


def test_the_controls_are_styled_from_tokens(styles):
    assert ".mandate-wrap" in styles and ".autonomy-row" in styles
    block = styles.split("authority form controls", 1)[1]
    assert "#" not in block.split("*/", 1)[1], "colour literals in components"
