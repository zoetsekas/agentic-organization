"""The command centre front end: its bundle, its routes and its fixture.

WS-029 M2/M4 and ADR-0051. The front end is a separate application at
`/command/`, so these tests assert it is mounted there, that every route it
calls is one `docs/COMMAND_CENTRE_API.md` defines, that it reaches nothing that
could author inside a tenant, and that the fixture it falls back to offline
really carries the states it renders.
"""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "web" / "command"
CONTRACT = ROOT / "docs" / "COMMAND_CENTRE_API.md"
FIXTURE = BUNDLE / "command-centre.sample.json"
APP_JS = BUNDLE / "app.js"


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "ui.db")))


# -- the bundle exists and is mounted --------------------------------------

def test_bundle_files_exist():
    for name in ("index.html", "app.js", "styles.css", "command-centre.sample.json"):
        assert (BUNDLE / name).is_file(), f"web/command/{name} is missing"


def test_bundle_is_mounted_as_its_own_application(client):
    index = client.get("/command/")
    assert index.status_code == 200
    assert "Command Centre" in index.text
    assert client.get("/command/app.js").status_code == 200
    assert client.get("/command/styles.css").status_code == 200
    # Offline development needs the fixture served from the bundle itself.
    assert client.get("/command/command-centre.sample.json").status_code == 200


def test_the_designer_bundle_is_not_reused(app_js):
    # Nothing imported from the designer: one application cannot become the other.
    assert "canvas.js" not in app_js
    assert not re.search(r"""["'`]\.\./(app|canvas|styles)""", app_js)
    assert "../app.js" not in (BUNDLE / "index.html").read_text(encoding="utf-8")


# -- every route the UI calls is one the contract defines ------------------

def documented_routes() -> set[tuple[str, str]]:
    """The route table of docs/COMMAND_CENTRE_API.md, as (method, path) pairs."""
    routes = set()
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        match = re.match(
            r"\|\s*(GET|POST|PUT|DELETE)\s*\|\s*`([^`]+)`\s*\|", line.strip())
        if match:
            method, path = match.group(1), match.group(2).split("?")[0]
            routes.add((method, path))
    return routes


def ui_routes(source: str) -> set[tuple[str, str]]:
    """Every /api/fabric path the bundle fetches, with its method.

    Paths are written as template literals against the `API` constant, so the
    interpolations are normalised back to the contract's `{param}` form.
    """
    api_const = re.search(r'const API = "([^"]+)"', source).group(1)
    found = set()
    for raw, method in _path_literals(source):
        path = api_const + raw if raw.startswith("/") else raw
        path = path.split("?")[0]
        path = re.sub(r"\$\{[^}]*deployment[^}]*\}", "{deployment_id}", path)
        path = re.sub(r"\$\{[^}]*tenant[^}]*\}", "{tenant_id}", path)
        path = re.sub(r"\$\{[^}]*spec\.action[^}]*\}", "{action}", path)
        path = re.sub(r"\$\{[^}]*\}", "{param}", path)
        found.add((method, path.rstrip("/")))
    return found


def _path_literals(source: str) -> list[tuple[str, str]]:
    """(path, method) for every apiGet/apiWrite call in the bundle."""
    calls = []
    for match in re.finditer(r"apiGet\(\s*`([^`]+)`", source):
        calls.append((match.group(1), "GET"))
    for match in re.finditer(r'apiWrite\(\s*"(\w+)",\s*\n?\s*`([^`]+)`', source):
        calls.append((match.group(2), match.group(1)))
    return [(p.replace("${API}", ""), m) for p, m in calls]


def test_contract_route_table_parses():
    routes = documented_routes()
    assert ("GET", "/api/fabric/tenants") in routes
    assert ("POST",
            "/api/fabric/deployments/{deployment_id}/actions/{action}") in routes
    assert len(routes) >= 15


def test_every_route_the_ui_calls_is_documented(app_js):
    called = ui_routes(app_js)
    assert called, "no fabric routes found in the bundle"
    undocumented = called - documented_routes()
    assert not undocumented, f"routes not in the contract: {sorted(undocumented)}"


def test_the_ui_uses_the_routes_the_milestones_need(app_js):
    called = ui_routes(app_js)
    for route in [
        ("GET", "/api/fabric/whoami"),
        ("GET", "/api/fabric/tenants"),
        ("GET", "/api/fabric/tenants/{tenant_id}"),
        ("GET", "/api/fabric/deployments"),
        ("GET", "/api/fabric/deployments/{deployment_id}"),
        ("GET", "/api/fabric/deployments/{deployment_id}/history"),
        ("GET", "/api/fabric/health"),
        ("GET", "/api/fabric/drift"),
        ("GET", "/api/fabric/quotas"),
        ("GET", "/api/fabric/audit"),
        ("POST", "/api/fabric/deployments/{deployment_id}/actions/{action}"),
        ("PUT", "/api/fabric/tenants/{tenant_id}/quotas"),
    ]:
        assert route in called, f"the UI never calls {route[0]} {route[1]}"


def test_the_routes_the_ui_calls_exist_on_the_backend(client, app_js):
    backend = {(method, route.path)
               for route in client.app.routes
               for method in getattr(route, "methods", ())}
    missing = {r for r in ui_routes(app_js) if r not in backend}
    assert not missing, f"the UI calls routes the backend does not serve: {missing}"


# -- read across tenants, author inside none -------------------------------

def test_the_ui_contains_no_spec_editing_route(app_js):
    html = (BUNDLE / "index.html").read_text(encoding="utf-8")
    for forbidden in ("/api/designer", "/api/systems", "/api/workspaces",
                      "/api/spec", "/api/agents", "/api/org", "/api/roles",
                      "/api/canvas", "/api/revisions", "/api/locks"):
        assert forbidden not in app_js, f"{forbidden} has no business here"
        assert forbidden not in html
    # Everything this application fetches lives in the fabric namespace.
    for path in re.findall(r"/api/[a-z/]+", app_js):
        assert path.startswith("/api/fabric"), path


def test_the_only_writes_are_the_operator_actions(app_js):
    writes = {r for r in ui_routes(app_js) if r[0] != "GET"}
    assert writes == {
        ("POST", "/api/fabric/deployments/{deployment_id}/actions/{action}"),
        ("PUT", "/api/fabric/tenants/{tenant_id}/quotas"),
    }


def test_transitions_are_driven_by_the_backend_not_a_local_table(app_js):
    assert "allowed_transitions" in app_js
    # The five action names map to target states; the *legality* of a move is
    # never decided here, so no source state may appear as a transition key.
    assert not re.search(r"\b(requested|generated|deployed)\s*:\s*\[", app_js)
    for action in ("generate", "deploy", "stop", "quarantine", "redeploy"):
        assert f'action: "{action}"' in app_js


def test_api_errors_are_surfaced_rather_than_reworded(app_js):
    assert "body.detail" in app_js          # the backend's own message
    assert "403" in app_js and "409" in app_js


# -- the offline fixture ---------------------------------------------------

def test_bundled_fixture_matches_the_generated_one(fixture):
    assert fixture == json.loads(
        (ROOT / "docs" / "fixtures" / "command-centre.sample.json").read_text(encoding="utf-8"))
    assert fixture["contract"] == "docs/COMMAND_CENTRE_API.md"


def test_the_ui_falls_back_to_the_fixture(app_js):
    assert 'const FIXTURE_URL = "command-centre.sample.json"' in app_js
    assert "degradeToFixture" in app_js
    # And says so on screen rather than passing fixture data off as live.
    assert "source-banner" in app_js
    assert "Fixture data" in (BUNDLE / "index.html").read_text(encoding="utf-8")


def test_fixture_covers_every_route_the_ui_reads(app_js, fixture):
    keys = set(fixture["routes"])
    for method, path in ui_routes(app_js):
        if method != "GET":
            continue        # actions are refused offline; nothing is simulated
        assert f"{method} {path}" in keys, f"no fixture for {method} {path}"


def test_fixture_covers_the_states_the_ui_renders(fixture):
    routes = fixture["routes"]
    health = routes["GET /api/fabric/health"]["response"]
    confidences = {check["confidence"] for check in health}
    assert {"fresh", "stale", "unobserved"} <= confidences
    # A belief we cannot confirm is unknown, never healthy (ADR-0052).
    for check in health:
        if check["confidence"] != "fresh":
            assert check["status"] == "unknown"
    assert {check["status"] for check in health} >= {"unknown", "degraded"}

    quotas = routes["GET /api/fabric/quotas"]["response"]
    assert any(q["recorded"] for q in quotas)
    assert any(not q["recorded"] for q in quotas)
    breaches = [b for q in quotas for b in q["breaches"]]
    assert any(b["decision"] == "allow_degraded" for b in breaches), \
        "the served-but-recorded middle state is the one worth designing for"
    assert any(limit["hard_ceiling"] < 0
               for q in quotas for limit in q["quotas"].values())

    deployments = routes["GET /api/fabric/deployments"]["response"]
    assert {d["state"] for d in deployments} >= {"running", "stopped", "quarantined"}
    assert all(d["allowed_transitions"] for d in deployments)
    assert all(d["history"] for d in deployments)

    drift = routes["GET /api/fabric/drift"]["response"]
    assert {s["kind"] for s in drift} >= {"stale_belief", "missing_on_target"}

    audit = routes["GET /api/fabric/audit"]["response"]
    assert {row["outcome"] for row in audit} >= {"success", "denied"}
    assert any(row["cross_tenant"] for row in audit)

    tenants = routes["GET /api/fabric/tenants"]["response"]
    assert all(t["isolation_domain"]["id"] for t in tenants)
    assert any(t["entitlements"] for t in tenants)

    assert "409 illegal transition" in fixture["errors"]
    assert "403 role refused by the lifecycle table" in fixture["errors"]
