"""Managing an organisation from the designer UI.

One saved design holds exactly one org chart (`SystemSpec.organization`), so
one design *is* one organisation. The designer says so, and offers the whole
life of one: create, rename, duplicate, delete. The wire is unchanged —
`/api/designer/systems`, `workspace_id`, `system_id` — because the protocol is
not the vocabulary the user reads.

Like `tests/test_designer_ui.py`, these tests read the bundle from outside:
they take the routes and methods it calls and assert each exists on the running
app, and they hold the rules that make the affordances honest — one state
behind both selectors, destructive controls gated on the permission the API
reported, and refusal text quoted from the API rather than written here.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "web"


@pytest.fixture(scope="module")
def app_js() -> str:
    return (BUNDLE / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def canvas_js() -> str:
    return (BUNDLE / "canvas.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (BUNDLE / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "orgcrud.db")))


# -- what the bundle calls, and whether it is there ------------------------

def _calls(source: str) -> set[tuple[str, str]]:
    """(method, path) for every `dapi(...)` in the bundle.

    Interpolations are normalised back to the `{param}` form the route table
    uses, the same way `test_designer_ui.py` does it.
    """
    found = set()
    pattern = re.compile(r"(?<!\w)dapi\(\s*[`\"]([^`\"]+)[`\"]((?:[^;]|\n){0,400}?)\)")
    for match in pattern.finditer(source):
        raw, tail = match.group(1), match.group(2)
        verb = re.search(r'method:\s*"(\w+)"', tail)
        path = "/api/designer" + raw.split("?")[0]
        path = re.sub(r"\$\{[^}]*\}", "{}", path)
        found.add((verb.group(1) if verb else "GET", path.rstrip("/")))
    return found


def _backend(client: TestClient) -> set[tuple[str, str]]:
    return {(method, re.sub(r"\{[^}]+\}", "{}", route.path))
            for route in client.app.routes
            for method in getattr(route, "methods", ())}


@pytest.mark.parametrize("route", [
    ("POST", "/api/designer/systems"),        # create
    ("GET", "/api/designer/systems/{}"),      # read, and the source of a copy
    ("PUT", "/api/designer/systems/{}"),      # rename and edit the definition
    ("DELETE", "/api/designer/systems/{}"),   # delete
])
def test_every_org_crud_route_the_ui_calls_exists(client, app_js, canvas_js, route):
    called = _calls(app_js) | _calls(canvas_js)
    assert route in called, f"the UI never calls {route[0]} {route[1]}"
    assert route in _backend(client), f"{route[0]} {route[1]} is not on the app"


def test_duplicate_is_client_side_over_the_existing_routes(app_js):
    """There is no server-side copy route, and the UI does not ask for one."""
    assert "function duplicateOrganisation" in app_js
    assert "/duplicate" not in app_js and "/copy" not in app_js
    # It reads the source record and posts its spec as a new organisation.
    assert "record.spec" in app_js and "(copy)" in app_js


def test_a_duplicate_can_be_made_through_the_existing_api(client):
    """The flow the bundle performs, exercised against the real routes."""
    headers = {"X-User": "ana", "X-User-Name": "Ana"}
    workspace = client.post("/api/designer/workspaces", json={"name": "Design"},
                            headers=headers).json()["id"]
    created = client.post("/api/designer/systems",
                          json={"workspace_id": workspace, "name": "Acme",
                                "description": "the original"},
                          headers=headers)
    assert created.status_code == 200
    source = client.get(f"/api/designer/systems/{created.json()['id']}",
                        headers=headers).json()["record"]

    spec = dict(source["spec"])
    spec["metadata"] = {**spec.get("metadata", {}), "name": "Acme (copy)"}
    copied = client.post("/api/designer/systems",
                         json={"workspace_id": source["workspace_id"],
                               "name": "Acme (copy)",
                               "description": source["description"],
                               "spec": spec},
                         headers=headers)
    assert copied.status_code == 200, copied.text
    # Create takes no layout, so the copy carries it over with the ordinary save.
    laid_out = client.put(f"/api/designer/systems/{copied.json()['id']}",
                          json={"layout": source["layout"],
                                "base_version": copied.json()["version"],
                                "strategy": "merge"},
                          headers=headers)
    assert laid_out.status_code == 200, laid_out.text
    names = {s["name"] for s in client.get(
        f"/api/designer/systems?workspace_id={workspace}", headers=headers).json()}
    assert names == {"Acme", "Acme (copy)"}


def test_create_and_rename_carry_the_metadata_the_spec_defines(client, app_js):
    """`SystemSpec.metadata` is name, description, owner, environment and
    labels; the form offers those and invents none."""
    from orgagents.spec.model import Metadata

    offered = {"name", "description", "owner", "environment", "labels"}
    assert offered <= set(Metadata.model_fields)
    for field in offered:
        assert f"metadata.{field} =" in app_js or f'elements.{field}' in app_js
    assert "function applyOrgMetadata" in app_js
    # spec_version and version are the document's, not the user's, to set.
    assert "metadata.spec_version =" not in app_js


# -- one state behind both selectors ---------------------------------------

def test_the_org_tab_and_the_context_bar_share_one_selector_state(
        index_html, app_js, canvas_js):
    assert 'id="org-select" data-org-select' in index_html
    assert 'id="sys-select" data-org-select' in index_html
    # canvas.js owns the open id and fills every selector from it, so neither
    # can show something the other does not.
    assert 'document.querySelectorAll("[data-org-select]")' in canvas_js
    assert canvas_js.count('document.querySelectorAll("[data-org-select]")') >= 2
    assert "function renderOrgSelectors" in canvas_js
    # app.js renders the toolbar around the selector but never fills it itself.
    assert "fillSelect($(\"#org-select\")" not in app_js
    assert 'fillSelect(select, options)' in canvas_js


def test_switching_in_either_selector_opens_the_same_organisation(canvas_js):
    handler = canvas_js.split('document.querySelectorAll("[data-org-select]")')[-1]
    assert "openSystem(e.target.value)" in handler
    assert "renderOrgSelectors();" in canvas_js.split("async function openSystem")[1]


# -- the gates the API already declares ------------------------------------

def test_destructive_controls_are_gated_on_can_edit(app_js):
    toolbar = app_js.split("function renderOrgToolbar")[1].split("\n}")[0]
    assert "d.canEdit()" in toolbar
    for control in ("#btn-org-edit", "#btn-org-duplicate", "#btn-org-delete"):
        assert f'$("{control}").disabled' in toolbar
    assert "canEdit" in toolbar and "lockedByOther" in app_js


def test_the_lock_and_the_version_are_shown_on_the_org_tab(app_js, index_html):
    assert 'id="org-version"' in index_html and 'id="org-lock"' in index_html
    assert "locked by ${blocked.holder_name || blocked.holder}" in app_js


def test_delete_confirms_by_name_and_quotes_the_api_when_refused(app_js):
    delete = app_js.split("async function deleteOrganisation")[1].split("\n}")[0]
    # The shared dialog (ui.js, ADR-0117), not the browser's confirm().
    assert "ui.confirmDialog" in delete and "${record.name}" in delete
    # The refusal shown is the API's `detail`, carried on the error, and not a
    # sentence written in the bundle.
    assert '$("#org-error").textContent = err.message' in delete
    assert "you do not have permission" not in app_js.lower()
    assert "is locked by" not in app_js.lower()


def test_a_refused_delete_answers_with_its_own_detail(client):
    headers = {"X-User": "ana", "X-User-Name": "Ana"}
    workspace = client.post("/api/designer/workspaces", json={"name": "Design"},
                            headers=headers).json()["id"]
    system = client.post("/api/designer/systems",
                         json={"workspace_id": workspace, "name": "Acme"},
                         headers=headers).json()["id"]
    client.post(f"/api/designer/workspaces/{workspace}/members",
                json={"user_id": "vic", "role": "viewer"}, headers=headers)
    refused = client.delete(f"/api/designer/systems/{system}",
                            headers={"X-User": "vic", "X-User-Name": "Vic"})
    assert refused.status_code == 403
    # This is the text the UI puts on screen; it names the role and the reason.
    assert "viewer" in refused.json()["detail"]


# -- the empty state, and the line the milestone drew ----------------------

def _empty_state_copy(app_js: str) -> str:
    return re.search(r'const NO_SYSTEM = "([^"]+)"', app_js).group(1)


def test_the_empty_state_speaks_of_an_organisation_and_offers_to_create_one(app_js):
    assert "No organisation open" in _empty_state_copy(app_js)
    assert "Create an organisation" in app_js
    assert 'onclick: () => showOrgForm("create")' in app_js


def test_the_workspace_is_still_created_when_a_user_has_none(canvas_js):
    """The canvas creates a workspace for a first-time user; organisation
    management must not have taken that path away."""
    load = canvas_js.split("async function loadWorkspaces")[1].split("\n}")[0]
    assert "if (!canvas.workspaces.length)" in load
    assert '"My workspace"' in load


def test_organisation_management_adds_no_runtime_read(app_js):
    for path in ("/org/tree", "/org/units", "/agents", "/components"):
        assert f'api("{path}' not in app_js and f"api(`{path}" not in app_js


def test_the_ui_says_organisation_while_the_wire_says_system(index_html, app_js):
    assert "New organisation" in index_html
    assert "New system" not in index_html
    # The protocol is untouched: same paths, same payload fields.
    assert "/api/designer" in (BUNDLE / "canvas.js").read_text(encoding="utf-8")
    assert "workspace_id:" in app_js
    assert "/systems" in app_js


def test_the_org_view_stays_clear_of_the_command_centre(app_js, index_html):
    for source in (app_js, index_html):
        assert "/api/fabric" not in source and "/command/" not in source
