"""The designer front end: what it reads, what it writes, and what it must not.

WS-009 M3 and ADR-0004. The designer's *design* views (org chart, agent editor,
workspace) edit one System Spec through `/api/designer`; its *runtime* views
(sessions, operations) observe the running system. These tests hold that line
from the outside: they parse the bundle for the paths it fetches and assert
each one exists on the app, that no design view reaches a runtime endpoint for
a design fact, that the runtime views still do, and that the designer stays
clear of the command centre's application (ADR-0051).
"""
import pathlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "web"


@pytest.fixture(scope="module")
def app_js() -> str:
    return (BUNDLE / "app.js").read_text()


@pytest.fixture(scope="module")
def canvas_js() -> str:
    return (BUNDLE / "canvas.js").read_text()


@pytest.fixture(scope="module")
def index_html() -> str:
    return (BUNDLE / "index.html").read_text()


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "ui.db")))


# -- the paths the bundle fetches ------------------------------------------

def _literals(source: str, call: str, prefix: str) -> set[tuple[str, str]]:
    """(method, path) for every `call(...)` in the bundle.

    Paths are template literals; the interpolations are normalised back to the
    `{param}` form the route table uses.
    """
    found = set()
    pattern = re.compile(call + r"\(\s*[`\"]([^`\"]+)[`\"]((?:[^;]|\n){0,400}?)\)")
    for match in pattern.finditer(source):
        raw, tail = match.group(1), match.group(2)
        method = "GET"
        verb = re.search(r'method:\s*"(\w+)"', tail)
        if verb:
            method = verb.group(1)
        path = prefix + raw.split("?")[0]
        path = re.sub(r"\$\{[^}]*workspaceId[^}]*\}", "{workspace_id}", path)
        path = re.sub(r"\$\{[^}]*systemId[^}]*\}", "{system_id}", path)
        path = re.sub(r"\$\{[^}]*[Uu]serId[^}]*\}", "{user_id}", path)
        path = re.sub(r"\$\{[^}]*version[^}]*\}", "{version}", path)
        path = re.sub(r"\$\{[^}]*\}", "{param}", path)
        found.add((method, path.rstrip("/")))
    return found


def designer_routes(source: str) -> set[tuple[str, str]]:
    return _literals(source, r"(?<!\w)dapi", "/api/designer")


def runtime_routes(source: str) -> set[tuple[str, str]]:
    return _literals(source, r"(?<!\w)api", "/api")


def _canonical(routes: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Path parameters are named differently either side; the shape is what
    matters here."""
    return {(m, re.sub(r"\{[^}]+\}", "{}", p)) for m, p in routes}


def test_the_designer_routes_the_bundle_calls_exist(client, app_js, canvas_js):
    backend = _canonical({(method, route.path)
                          for route in client.app.routes
                          for method in getattr(route, "methods", ())})
    called = designer_routes(app_js) | designer_routes(canvas_js)
    assert called, "no /api/designer routes found in the bundle"
    missing = _canonical(called) - backend
    assert not missing, f"the UI calls designer routes that do not exist: {missing}"


def test_the_runtime_routes_the_bundle_calls_exist(client, app_js):
    backend = _canonical({(method, route.path.rstrip("/") or "/")
                          for route in client.app.routes
                          for method in getattr(route, "methods", ())})
    called = _canonical({r for r in runtime_routes(app_js)
                         if not r[1].startswith("/api/designer")})
    missing = called - backend
    assert not missing, f"the UI calls runtime routes that do not exist: {missing}"


# -- design facts come from the spec ---------------------------------------

DESIGN_RUNTIME_ENDPOINTS = (
    "/org/tree", "/org/units", "/agents", "/components",
)


def test_no_design_view_reads_the_runtime_model(app_js):
    """The org chart, the agent editor and the harness form are design views.

    Reading `/org/tree` or `/agents` there is the bug this milestone removes: a
    field that silently reports a different document from the one being edited.
    """
    for path in DESIGN_RUNTIME_ENDPOINTS:
        assert f'api("{path}' not in app_js and f"api(`{path}" not in app_js, \
            f"a design view still reads {path}"


def test_the_design_views_read_the_open_spec(app_js):
    # One document in the browser: the record canvas.js holds.
    assert "window.designer" in app_js
    assert "design().spec()" in app_js or "design().find(" in app_js
    for marker in ("renderOrg", "renderAgentView", "applyAgentForm"):
        assert f"function {marker}" in app_js


def test_the_canvas_publishes_the_open_record_once(canvas_js):
    assert "window.designer = {" in canvas_js
    assert 'document.dispatchEvent(new CustomEvent("designer:changed"' in canvas_js
    # ... and announces every change, so no view can drift from the record.
    for site in ("markDirty", "openSystem"):
        assert site in canvas_js


def test_the_design_views_say_so_when_no_system_is_open(app_js):
    assert "No organisation open" in app_js
    assert "NO_SYSTEM" in app_js


def test_runtime_views_stay_runtime_backed(app_js):
    """Sessions, traces, alerts and metrics are observations, not design."""
    called = runtime_routes(app_js)
    assert ("GET", "/api/sessions") in called
    assert ("GET", "/api/ops/metrics") in called
    assert ("GET", "/api/ops/alerts") in called
    assert ("GET", "/api/sessions/{param}/trace") in called
    # The marketplace and the platform catalog are platform facts.
    assert ("GET", "/api/catalog") in called
    assert ("GET", "/api/catalogs/kinds") in called


# -- identity, workspace and audit are reachable ---------------------------

def test_identity_workspaces_and_audit_are_called(app_js, canvas_js):
    called = designer_routes(app_js) | designer_routes(canvas_js)
    for route in [
        ("GET", "/api/designer/whoami"),
        ("GET", "/api/designer/workspaces"),
        ("POST", "/api/designer/workspaces"),
        ("POST", "/api/designer/workspaces/{workspace_id}/members"),
        ("DELETE", "/api/designer/workspaces/{workspace_id}/members/{user_id}"),
        ("GET", "/api/designer/audit"),
        ("GET", "/api/designer/systems"),
        ("PUT", "/api/designer/systems/{system_id}"),
    ]:
        assert route in called, f"the UI never calls {route[0]} {route[1]}"


def test_identity_is_persistent_and_shows_the_role(index_html, app_js):
    assert 'id="whoami"' in index_html
    assert 'id="role-badge"' in index_html
    # The bar sits outside <main>, so it is on screen in every view.
    assert index_html.index('id="contextbar"') < index_html.index("<main>")
    assert "function renderIdentity" in app_js
    assert "no role in this workspace" in app_js


def test_membership_is_offered_only_to_a_role_that_holds_it(app_js):
    assert 'may("workspace.members")' in app_js
    assert "does not manage membership" in app_js


def test_a_refusal_is_shown_in_the_api_s_own_words(app_js):
    # err.message carries the API's detail, which names the role and the reason.
    assert app_js.count("err.message") >= 3
    assert "members-error" in app_js and "audit-error" in app_js


def test_denied_audit_entries_are_visibly_distinct(app_js, index_html):
    assert 'event.outcome === "denied"' in app_js
    assert "denied" in (BUNDLE / "styles.css").read_text()
    assert 'id="audit"' in index_html


def test_the_audit_and_workspace_routes_answer(client):
    headers = {"X-User": "ana", "X-User-Name": "Ana"}
    created = client.post("/api/designer/workspaces",
                          json={"name": "Design"}, headers=headers)
    assert created.status_code == 200
    workspace = created.json()["id"]

    whoami = client.get("/api/designer/whoami", headers=headers).json()
    assert whoami["user_id"] == "ana"
    membership = [m for m in whoami["workspaces"] if m["workspace_id"] == workspace]
    assert membership and "workspace.members" in membership[0]["permissions"]

    added = client.post(f"/api/designer/workspaces/{workspace}/members",
                        json={"user_id": "bob", "role": "viewer"}, headers=headers)
    assert added.status_code == 200

    # A viewer trying to manage membership is refused, and the refusal is
    # recorded: that entry is the reason the audit view exists.
    bob = {"X-User": "bob", "X-User-Name": "Bob"}
    refused = client.post(f"/api/designer/workspaces/{workspace}/members",
                          json={"user_id": "eve", "role": "admin"}, headers=bob)
    assert refused.status_code == 403
    assert "viewer" in refused.json()["detail"]
    assert client.get("/api/designer/audit", headers=bob).status_code == 403
    events = client.get("/api/designer/audit", headers=headers).json()
    assert any(e["outcome"] == "denied" for e in events), \
        "the log the UI renders must carry the refusals"
    assert {e["action"] for e in events} >= {"workspace.member.add"}

    removed = client.delete(f"/api/designer/workspaces/{workspace}/members/bob",
                            headers=headers)
    assert removed.status_code == 200


# -- two applications, kept apart (ADR-0051) -------------------------------

def test_the_designer_does_not_reach_the_command_centre(app_js, canvas_js,
                                                        index_html):
    for source in (app_js, canvas_js, index_html):
        assert "/api/fabric" not in source
        assert "/command/" not in source
        assert "command/app.js" not in source


def test_the_designer_bundle_is_served(client):
    assert client.get("/ui/").status_code == 200
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/ui/canvas.js").status_code == 200


# -- the inverse: routes the bundle does NOT call --------------------------

#: Designer routes served with no consumer in the bundle, and why that is
#: allowed. Every entry is a deliberate statement, not a backlog: a route that
#: nothing calls is a backend capability with no UI in front of it, which is
#: the defect category `docs/DESIGNER.md` exists to track. `.../placements` sat
#: here for exactly one session before being wired into the Authority view, and
#: this test is what stops the next one lasting longer.
SERVER_ONLY = {
    ("POST", "/api/designer/systems/{}/lock/heartbeat"):
        "Known and documented: no heartbeat is sent, so a long edit can lose "
        "its lock. Tracked in DESIGNER.md's caveat table, not silently absent.",
}


def _referenced_paths(*sources: str) -> set[str]:
    """Every designer path the bundle mentions, however it is built.

    Deliberately looser than `designer_routes`: `gate` and `diff` are reached
    through `gateUrl`/`diffUrl` helpers rather than a literal at the call site,
    and a check for orphaned routes should ask whether the bundle refers to a
    path at all — not whether it does so in one particular shape.
    """
    found: set[str] = set()
    for source in sources:
        for raw in re.findall(r"[`\"']((?:/systems|/workspaces|/palette"
                              r"|/settings|/whoami|/audit)[^`\"'\s]*)", source):
            path = "/api/designer" + raw.split("?")[0]
            path = re.sub(r"\$\{[^}]*\}", "{}", path).rstrip("/")
            found.add(path)
    return found


def test_every_designer_route_has_something_that_calls_it(client, app_js,
                                                          canvas_js):
    """A served route nothing calls is a capability with no UI in front of it.

    The sibling test above checks the bundle does not call routes that do not
    exist. This checks the other direction, which is the one that actually
    went wrong: `.../placements` was built, served, tested, and reachable by
    nobody.
    """
    referenced = _referenced_paths(app_js, canvas_js)
    orphans = {}
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/designer"):
            continue
        shape = re.sub(r"\{[^}]+\}", "{}", path).rstrip("/")
        for method in getattr(route, "methods", ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            if shape in referenced or (method, shape) in SERVER_ONLY:
                continue
            orphans[(method, shape)] = True
    assert not orphans, (
        "designer routes nothing in the bundle calls: "
        f"{sorted(orphans)}. Wire it into a view, or add it to SERVER_ONLY "
        "with the reason."
    )


def test_the_placements_route_reaches_the_view(app_js):
    """The specific orphan, kept closed."""
    assert "/placements`" in app_js
    assert "function renderPlacements" in app_js
    # And its honest caveat travels with it: a list of boxes reads as a wall.
    assert "places.note" in app_js


def test_no_view_reads_a_system_id_the_local_state_does_not_have(app_js):
    """The open design lives on `window.designer.state`, not on this file's.

    `loadAuthority` read a bare `state.systemId` — a field the local `state`
    object has never had — so the Authority view reported "open an
    organisation" with one open, for as long as it existed. Every test passed,
    because they all call the routes directly and assert on JSON. A browser
    found it in the first minute.

    `state` is declared here with its fields, so any bare `state.systemId` is
    reading something that does not exist.
    """
    declared = re.search(r"^const state = \{([^}]*)\}", app_js, re.M)
    assert declared, "app.js no longer declares its local state object"
    assert "systemId" not in declared.group(1), (
        "if `state` gained a systemId, this test needs rewriting rather than "
        "deleting: the point is that two state objects must not drift"
    )
    # Comments talk *about* the bug, so they are stripped before the check:
    # a lint over source should not read prose.
    code = re.sub(r"/\*.*?\*/", "", app_js, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    # `d.state.systemId` and `design()?.state.systemId` are the correct forms.
    bare = re.findall(r"(?<![.\w])state\.systemId", code)
    assert not bare, (
        f"{len(bare)} read(s) of a `systemId` the local state does not have; "
        "reach the open design through `design()`"
    )


def test_a_person_reference_survives_the_agent_form(tmp_path):
    """The agent form writes every field back on each keystroke.

    So a pairing rendered as a blank name and parsed back as a blank name is
    not a display bug — it is data loss. Before ADR-0079 a pairing carried its
    own name; now it may carry a `person` reference instead, and opening a
    migrated agent and typing one character destroyed every reference on it.

    This runs the form's own `humanToLine`/`parseHumans` under node and
    asserts the round trip is lossless for both shapes.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = pathlib.Path(__file__).parent / "humans_roundtrip.mjs"
    out = subprocess.run([node, str(script)], capture_output=True, text=True,
                         cwd=pathlib.Path(__file__).resolve().parents[1],
                         timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "LOSS" not in out.stdout
    assert "Marcus Oyelaran" in out.stdout, "a reference must resolve to a name"
