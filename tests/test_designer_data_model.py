"""The designer's own data model: nothing declared that nothing reads.

`CanvasNode.kind` was a closed set that the palette outgrew, and a person
could lose a minute's work to it. That defect class rarely appears once, so
this audits the rest of `designer/models.py` and holds the answers down.

Three fields and one whole model were declared and read by nothing:
`DesignerSettings.lock_break_requires`, `Layout.viewport`, `CanvasNode.color`,
and `CanvasEdge`. Two are now wired and two are gone. These tests are what
stop them drifting back.
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.designer import models as dm
from orgagents.designer.models import UserRole
from orgagents.designer.rbac import BREAK_LOCK, decide, permissions_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
ALICE = {"X-User": "alice"}


# --------------------------------------------------------------------------
# A setting on a security surface that configured nothing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "requires, role, allowed",
    [
        (UserRole.EDITOR, UserRole.EDITOR, True),    # lowered, and it lowers
        (UserRole.EDITOR, UserRole.REVIEWER, False),
        (UserRole.OWNER, UserRole.ADMIN, False),     # raised, and it raises
        (UserRole.OWNER, UserRole.OWNER, True),
        (UserRole.ADMIN, UserRole.ADMIN, True),      # the default, unchanged
        (UserRole.ADMIN, UserRole.EDITOR, False),
    ],
)
def test_lock_break_requires_actually_moves_the_line(requires, role, allowed):
    """It is settable through the API and used to be read by nothing.

    An operator could move it to `editor` or to `owner` and breaking a lock
    kept answering from the role table. A knob on a security surface that
    quietly configures nothing is worse than one that is not offered.
    """
    workspace = dm.Workspace(name="w", members=[
        dm.Member(user_id="u", role=role),
    ])
    principal = type("P", (), {"user_id": "u", "label": "u",
                               "granted_roles": {}})()
    decision = decide(workspace, principal, BREAK_LOCK,
                      break_lock_requires=requires)
    assert decision.allowed is allowed, decision.reason
    assert requires.value in decision.reason


def test_what_the_ui_is_told_matches_what_the_server_will_do():
    """A button the server refuses is a worse lie than either half alone."""
    lowered = permissions_for(UserRole.EDITOR, break_lock_requires=UserRole.EDITOR)
    assert BREAK_LOCK in lowered
    raised = permissions_for(UserRole.ADMIN, break_lock_requires=UserRole.OWNER)
    assert BREAK_LOCK not in raised
    # Untouched, the table still answers.
    assert BREAK_LOCK in permissions_for(UserRole.ADMIN)
    assert BREAK_LOCK not in permissions_for(UserRole.EDITOR)


def test_the_setting_reaches_the_service_through_the_api(tmp_path):
    client = TestClient(create_app(str(tmp_path / "dm.db")))
    workspace = client.post("/api/designer/workspaces", json={"name": "w"},
                            headers=ALICE).json()
    client.post(f"/api/designer/workspaces/{workspace['id']}/members",
                json={"user_id": "edith", "role": "editor"}, headers=ALICE)

    def may_break(user: str) -> bool:
        who = client.get("/api/designer/whoami", headers={"X-User": user}).json()
        return any(BREAK_LOCK in w.get("permissions", [])
                   for w in who.get("workspaces", []))

    assert may_break("edith") is False
    client.put("/api/designer/settings",
               json={"lock_break_requires": "editor"}, headers=ALICE)
    assert may_break("edith") is True, (
        "the setting was changed and the answer did not move"
    )


# --------------------------------------------------------------------------
# Two structures that nothing read
# --------------------------------------------------------------------------


def test_the_layout_has_no_stored_edges():
    """Edges are derived from the spec so the picture matches what compiles.

    A stored edge is a second source that can disagree with the first. There
    was a `CanvasEdge` model and a `Layout.edges` list; only the seeder ever
    wrote one and the canvas never read them.
    """
    assert not hasattr(dm, "CanvasEdge")
    assert "edges" not in dm.Layout.model_fields
    canvas_js = (ROOT / "web" / "canvas.js").read_text(encoding="utf-8")
    assert "derivedEdges" in canvas_js, "something must still draw the edges"


def test_a_node_carries_no_colour_nobody_sets():
    assert "color" not in dm.CanvasNode.model_fields


def test_the_viewport_is_restored_rather_than_only_stored(tmp_path):
    """It was persisted from the day it was declared and restored by nothing.

    Reopening a large design always put you back at the origin.
    """
    # It moved onto the diagram when a design gained more than one of them:
    # where you were looking is a property of the picture, not of the design.
    assert "viewport" in dm.Diagram.model_fields
    canvas_js = (ROOT / "web" / "canvas.js").read_text(encoding="utf-8")
    assert "function restoreViewport" in canvas_js
    assert "function rememberViewport" in canvas_js
    assert "scrollLeft = Number(view.x)" in canvas_js

    # And it survives a save, because it rides on the layout.
    client = TestClient(create_app(str(tmp_path / "vp.db")))
    workspace = client.post("/api/designer/workspaces", json={"name": "w"},
                            headers=ALICE).json()
    made = client.post("/api/designer/systems",
                       json={"workspace_id": workspace["id"], "name": "s",
                             "spec": {"metadata": {"name": "s"}}},
                       headers=ALICE).json()
    client.put(f"/api/designer/systems/{made['id']}",
               json={"layout": {"nodes": {},
                                "viewport": {"x": 420.0, "y": 90.0, "zoom": 1.0}},
                     "version": made["version"]}, headers=ALICE)
    layout = client.get(f"/api/designer/systems/{made['id']}",
                        headers=ALICE).json()["record"]["layout"]
    stored = layout["diagrams"][layout["active"]]["viewport"]
    assert stored["x"] == 420.0 and stored["y"] == 90.0


def test_scrolling_is_not_an_edit():
    """It must not mark the design dirty or take a revision."""
    canvas_js = (ROOT / "web" / "canvas.js").read_text(encoding="utf-8")
    remember = canvas_js.split("function rememberViewport")[1].split("\n}")[0]
    assert "markDirty" not in remember
    assert "saveSystem" not in remember


# --------------------------------------------------------------------------
# A typed model that accepted anything
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad, field",
    [
        ({"lock_ttl_seconds": "not-a-number"}, "lock_ttl_seconds"),
        ({"persistence": "carrier-pigeon"}, "persistence"),
        ({"lock_break_requires": "emperor"}, "lock_break_requires"),
    ],
)
def test_a_settings_value_the_model_refuses_is_a_422_not_a_500(tmp_path, bad,
                                                               field):
    """`model_copy(update=...)` does not validate.

    A settings PUT could put a string where an int is declared and a nonsense
    value where a `Literal` is. It surfaced as a 500 from the relational
    repository's own wrapper — and on the other backends it simply persisted,
    so `lock_ttl_seconds` could become a string that the lock service then
    tried to do arithmetic with.
    """
    client = TestClient(create_app(str(tmp_path / f"{field}.db")))
    client.post("/api/designer/workspaces", json={"name": "w"}, headers=ALICE)
    res = client.put("/api/designer/settings", json=bad, headers=ALICE)
    assert res.status_code == 422, res.text[:200]
    assert field in res.json()["detail"]
    # And the refusal changed nothing.
    after = client.get("/api/designer/settings", headers=ALICE).json()
    assert after[field] != list(bad.values())[0]


def test_a_good_settings_change_still_lands(tmp_path):
    client = TestClient(create_app(str(tmp_path / "ok.db")))
    client.post("/api/designer/workspaces", json={"name": "w"}, headers=ALICE)
    res = client.put("/api/designer/settings",
                     json={"lock_ttl_seconds": 300}, headers=ALICE)
    assert res.status_code == 200
    assert res.json()["lock_ttl_seconds"] == 300


def test_the_stored_setting_is_the_declared_type(tmp_path):
    """A str-enum that holds a raw string works by luck, not by design."""
    from orgagents.designer.models import DesignerSettings

    client = TestClient(create_app(str(tmp_path / "typed.db")))
    client.post("/api/designer/workspaces", json={"name": "w"}, headers=ALICE)
    client.put("/api/designer/settings",
               json={"lock_break_requires": "editor"}, headers=ALICE)
    settings = client.app.state.designer.settings
    assert isinstance(settings, DesignerSettings)
    assert isinstance(settings.lock_break_requires, UserRole)
