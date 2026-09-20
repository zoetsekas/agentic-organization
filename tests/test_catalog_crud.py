"""Editing a catalog entry: what may change in place, and what makes a version.

ADR-0062. Editorial fields are housekeeping and may be corrected at any status;
substantive fields — kind, version, `attributes` — decide what a design bound
to the entry resolves to, so on an approved or restricted entry they are
refused with both ways forward named. Delete is narrow, retirement is how an
entry leaves, and every mutation is attributed.

These cover the ADR's Verification clauses, the CLI round trip, and the
designer bundle's catalog calls against the routes that actually exist.
"""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.catalogs import (
    ApprovalStatus,
    CatalogEntry,
    CatalogError,
    CatalogKind,
    CatalogService,
    CatalogUsage,
    attribute_schema,
)
from orgagents.cli import main
from orgagents.platform import Platform

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def service(tmp_path) -> CatalogService:
    return CatalogService(
        Platform(str(tmp_path / "cat.db"), configure_logs=False).store)


@pytest.fixture()
def entry(service) -> CatalogEntry:
    return service.publish(CatalogEntry(
        kind=CatalogKind.MCP_SERVER, name="jira", summary="Jira issues.",
        owner="Platform", attributes={"transport": "http", "tools": ["search"]},
    ), actor="alice")


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "api.db")))


# -- editorial edits -------------------------------------------------------

@pytest.mark.parametrize("status", list(ApprovalStatus))
def test_an_editorial_edit_succeeds_at_any_status(service, entry, status):
    service.review(entry.id, status, reviewer="reviewer")
    edited = service.update(entry.id, {"summary": "Jira issue tracker.",
                                       "owner": "Developer Experience"},
                            actor="bob")
    assert edited.summary == "Jira issue tracker."
    assert edited.owner == "Developer Experience"
    assert edited.status is status


def test_an_editorial_edit_refuses_a_substantive_field(service, entry):
    with pytest.raises(CatalogError) as e:
        service.update(entry.id, {"attributes": {"transport": "stdio"}})
    assert "substantive" in str(e.value)


def test_an_editorial_edit_refuses_an_unknown_field(service, entry):
    with pytest.raises(CatalogError):
        service.update(entry.id, {"installs": 9000})


# -- substantive edits -----------------------------------------------------

@pytest.mark.parametrize("status", [ApprovalStatus.APPROVED,
                                    ApprovalStatus.RESTRICTED])
def test_a_substantive_edit_is_refused_on_a_reviewed_entry(service, entry, status):
    service.review(entry.id, status, reviewer="reviewer")
    with pytest.raises(CatalogError) as e:
        service.amend(entry.id, {"attributes": {"transport": "stdio"}},
                      actor="bob")
    message = str(e.value)
    assert status.value in message
    # Both remedies, by name: a refusal that does not say what to do instead
    # simply gets worked around.
    assert "new entry" in message and "supersede" in message
    assert "send this one back for review" in message.lower()
    assert service.get(entry.id).attributes["transport"] == "http"


def test_a_substantive_edit_succeeds_on_a_proposed_entry(service, entry):
    amended = service.amend(entry.id, {"attributes": {"transport": "stdio"},
                                       "version": "1.1.0"}, actor="bob")
    assert amended.attributes == {"transport": "stdio"}
    assert amended.version == "1.1.0"


def test_amend_refuses_an_editorial_field(service, entry):
    with pytest.raises(CatalogError) as e:
        service.amend(entry.id, {"summary": "no"})
    assert "use update" in str(e.value)


# -- send-back -------------------------------------------------------------

def test_send_back_makes_an_entry_unselectable(service, entry):
    approved = service.review(entry.id, ApprovalStatus.APPROVED,
                              reviewer="reviewer")
    assert approved.selectable
    sent = service.send_back(entry.id, actor="bob", note="tool list changed")
    assert sent.status is ApprovalStatus.PROPOSED
    assert not sent.selectable
    assert "tool list changed" in sent.review_note
    # And the substantive edit it was sent back for is now possible.
    service.amend(entry.id, {"attributes": {"transport": "stdio"}}, actor="bob")


def test_send_back_names_the_designs_it_breaks(service, entry):
    service.review(entry.id, ApprovalStatus.APPROVED, reviewer="reviewer")
    service.record_usage(CatalogUsage(entry_id=entry.id, system="payments"))
    sent = service.send_back(entry.id, actor="bob")
    assert "payments" in sent.review_note


# -- delete ----------------------------------------------------------------

def test_delete_is_permitted_for_an_unapproved_unreferenced_draft(service, entry):
    assert service.delete(entry.id, actor="bob") == entry.id
    assert service.get(entry.id) is None


def test_delete_is_refused_for_an_approved_entry(service, entry):
    service.review(entry.id, ApprovalStatus.APPROVED, reviewer="reviewer")
    with pytest.raises(CatalogError) as e:
        service.delete(entry.id)
    assert "retire it instead" in str(e.value)
    assert service.get(entry.id) is not None


def test_delete_is_refused_for_a_referenced_entry(service, entry):
    service.record_usage(CatalogUsage(entry_id=entry.id, system="payments"))
    with pytest.raises(CatalogError) as e:
        service.delete(entry.id)
    assert "payments" in str(e.value)


# -- retirement and versions ----------------------------------------------

def test_retirement_still_refuses_while_designs_reference_the_entry(service, entry):
    service.review(entry.id, ApprovalStatus.APPROVED, reviewer="reviewer")
    service.record_usage(CatalogUsage(entry_id=entry.id, system="payments"))
    with pytest.raises(CatalogError):
        service.retire(entry.id, reviewer="bob")
    retired = service.retire(entry.id, reviewer="bob", force=True)
    assert retired.status is ApprovalStatus.RETIRED


def test_a_superseded_version_remains_readable_and_resolvable(service, entry):
    service.review(entry.id, ApprovalStatus.APPROVED, reviewer="reviewer")
    successor = service.publish(CatalogEntry(
        kind=CatalogKind.MCP_SERVER, name="jira", version="2.0.0",
        attributes={"transport": "stdio"}), actor="bob")
    service.retire(entry.id, reviewer="bob", superseded_by=successor.id)
    old = service.get(entry.id)
    assert old is not None
    assert old.version == "1.0.0"
    assert old.attributes["transport"] == "http"   # what was reviewed
    assert old.superseded_by == successor.id


# -- attribution -----------------------------------------------------------

def test_every_mutation_records_its_actor(service, entry):
    service.update(entry.id, {"summary": "s"}, actor="bob")
    service.amend(entry.id, {"version": "1.2.0"}, actor="carol")
    service.review(entry.id, ApprovalStatus.APPROVED, reviewer="dave")
    service.send_back(entry.id, actor="erin")
    history = service.get(entry.id).history
    actions = {(h.action, h.actor) for h in history}
    assert ("created", "alice") in actions
    assert ("updated", "bob") in actions
    assert ("amended", "carol") in actions
    assert ("reviewed", "dave") in actions
    assert ("reviewed", "erin") in actions
    assert all(h.at for h in history)
    assert any("summary" in h.changes for h in history)


# -- the schema the UI form is generated from ------------------------------

def test_every_kind_declares_its_attributes(service):
    for kind in CatalogKind:
        fields = attribute_schema(kind)
        assert fields, f"{kind.value} declares no attributes to build a form from"
        for field in fields:
            assert field["type"] in ("string", "integer", "number", "boolean",
                                     "choice", "list", "objects")
            if field["type"] == "choice":
                assert field["choices"]


def test_the_kinds_route_carries_the_attribute_schema(client):
    kinds = client.get("/api/catalogs/kinds").json()
    assert len(kinds) == len(CatalogKind)
    by_id = {k["id"]: k for k in kinds}
    transport = [f for f in by_id["mcp_server"]["attributes"]
                 if f["name"] == "transport"][0]
    assert transport["type"] == "choice" and "stdio" in transport["choices"]


# -- the API ---------------------------------------------------------------

def _publish(client, **over):
    body = {"kind": "mcp_server", "name": "jira", "version": "1.0.0",
            "summary": "Jira issues.", "owner": "Platform",
            "attributes": {"transport": "http"}, **over}
    res = client.post("/api/catalogs", json=body, headers={"x-user": "alice"})
    assert res.status_code == 200, res.text
    return res.json()


def test_the_patch_route_takes_a_body_not_a_query_parameter(client):
    """The bug this file must never reintroduce: a body model imported inside
    `create_app` degrades to a query field under `from __future__ annotations`."""
    entry = _publish(client)
    res = client.patch(f"/api/catalogs/{entry['id']}",
                       json={"summary": "Jira issue tracker.", "owner": "DX"},
                       headers={"x-user": "bob"})
    assert res.status_code == 200, res.text
    assert res.json()["summary"] == "Jira issue tracker."


def test_the_amend_route_returns_the_services_own_refusal(client):
    entry = _publish(client)
    client.post(f"/api/catalogs/{entry['id']}/review?status=approved")
    res = client.post(f"/api/catalogs/{entry['id']}/amend",
                      json={"attributes": {"transport": "stdio"}})
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "supersede" in detail and "send this one back for review" in detail.lower()


def test_the_send_back_and_delete_routes(client):
    entry = _publish(client)
    client.post(f"/api/catalogs/{entry['id']}/review?status=approved")
    res = client.post(f"/api/catalogs/{entry['id']}/send_back",
                      json={"note": "tools changed"}, headers={"x-user": "bob"})
    assert res.status_code == 200 and res.json()["status"] == "proposed"
    # Reviewed once, so it retires rather than deletes.
    assert client.delete(f"/api/catalogs/{entry['id']}").status_code == 400
    draft = _publish(client, name="draft-server")
    assert client.delete(f"/api/catalogs/{draft['id']}").status_code == 200
    assert client.get(f"/api/catalogs/{draft['id']}").status_code == 404


def test_the_retire_route_refuses_while_a_design_uses_the_entry(client):
    entry = _publish(client)
    app_catalog = client.app.state.catalog
    app_catalog.record_usage(CatalogUsage(entry_id=entry["id"], system="payments"))
    res = client.post(f"/api/catalogs/{entry['id']}/retire")
    assert res.status_code == 400 and "payments" in res.json()["detail"]
    assert client.post(
        f"/api/catalogs/{entry['id']}/retire?force=true").status_code == 200


def test_the_detail_route_explains_the_lock_before_a_form_is_opened(client):
    entry = _publish(client)
    client.post(f"/api/catalogs/{entry['id']}/review?status=approved")
    detail = client.get(f"/api/catalogs/{entry['id']}").json()
    assert detail["substantively_locked"] is True
    assert detail["amend_refusal"]
    assert detail["deletable"] is False
    assert detail["schema"] and detail["history"]


# -- the CLI ---------------------------------------------------------------

def test_the_cli_round_trip(tmp_path, capsys):
    db = str(tmp_path / "cli.db")

    def run(*argv) -> tuple[int, str]:
        code = main(["--db", db, "catalogs", *argv])
        return code, capsys.readouterr().out

    code, out = run("add", "mcp_server", "jira", "--summary", "Jira issues.",
                    "--owner", "Platform", "--attr", "transport=http",
                    "--attr", "tools[]=search", "--attr", "tools[]=create",
                    "--actor", "alice")
    assert code == 0
    # Honest at the point of creation: proposed, and not yet selectable.
    assert "proposed" in out and "not selectable" in out
    entry_id = out.split()[0]

    service = CatalogService(Platform(db, configure_logs=False).store)
    assert service.get(entry_id).attributes["tools"] == ["search", "create"]

    assert run("edit", entry_id, "--summary", "Jira issue tracker.")[0] == 0
    assert service.get(entry_id).summary == "Jira issue tracker."

    assert run("approve", entry_id)[0] == 0
    code, out = run("edit", entry_id, "--attr", "transport=stdio")
    assert code == 1 and "supersede" in out

    assert run("send-back", entry_id, "--note", "tools changed")[0] == 0
    assert service.get(entry_id).status is ApprovalStatus.PROPOSED
    assert run("edit", entry_id, "--attr", "transport=stdio")[0] == 0
    assert service.get(entry_id).attributes["transport"] == "stdio"

    # Reviewed once: retirement, not deletion.
    code, out = run("delete", entry_id)
    assert code == 1 and "retire it instead" in out
    assert run("retire", entry_id)[0] == 0
    assert service.get(entry_id).status is ApprovalStatus.RETIRED


def test_the_cli_adds_from_a_json_file(tmp_path, capsys):
    path = tmp_path / "entries.json"
    path.write_text(json.dumps([
        {"kind": "guardrail", "name": "no-secrets", "attributes":
         {"checks": ["secrets"], "on_violation": "block"}}]))
    db = str(tmp_path / "file.db")
    assert main(["--db", db, "catalogs", "add", "--file", str(path)]) == 0
    assert "no-secrets" in capsys.readouterr().out
    service = CatalogService(Platform(db, configure_logs=False).store)
    assert service.by_name(CatalogKind.GUARDRAIL, "no-secrets") is not None


# -- the designer bundle ---------------------------------------------------

def _called_catalog_routes(source: str) -> set[tuple[str, str]]:
    """(method, path) for every `/catalogs` call the bundle makes."""
    found = set()
    pattern = re.compile(r"(?<!\w)api\(\s*[`\"](/catalogs[^`\"]*)[`\"]"
                         r"((?:[^;]|\n){0,400}?)\)")
    for match in pattern.finditer(source):
        raw, tail = match.group(1), match.group(2)
        verb = re.search(r'method:\s*"(\w+)"', tail)
        path = "/api" + raw.split("?")[0]
        path = re.sub(r"\$\{[^}]*\}", "{}", path)
        found.add((verb.group(1) if verb else "GET", path.rstrip("/")))
    return found


def test_the_catalog_routes_the_bundle_calls_exist(client):
    source = (ROOT / "web" / "app.js").read_text()
    backend = {(method, re.sub(r"\{[^}]+\}", "{}", route.path))
               for route in client.app.routes
               for method in getattr(route, "methods", ())}
    called = _called_catalog_routes(source)
    assert called, "no /catalogs calls found in the bundle"
    assert not called - backend, f"the UI calls routes that do not exist: {called - backend}"


def test_the_bundle_offers_add_edit_retire_send_back_and_delete():
    source = (ROOT / "web" / "app.js").read_text()
    html = (ROOT / "web" / "index.html").read_text()
    assert 'id="pc-add"' in html
    for affordance in ("openEntryForm", "sendBackEntry", "retireEntry",
                       "deleteEntry"):
        assert affordance in source


def test_the_bundle_builds_its_form_from_the_kind_attributes():
    """Twelve kinds, one form: the fields come from `/catalogs/kinds`."""
    source = (ROOT / "web" / "app.js").read_text()
    assert "attributeInput" in source and "readAttributes" in source
    assert "k.attributes" in source or ".attributes || []" in source
    # A substantive input is disabled while the entry is locked, and the
    # reason is shown from the detail route rather than discovered on submit.
    assert "substantively_locked" in source
    assert "amend_refusal" in source
    assert "disabled: true" in source


def test_the_ui_says_a_new_entry_is_not_selectable():
    html = (ROOT / "web" / "index.html").read_text()
    source = (ROOT / "web" / "app.js").read_text()
    assert "proposed" in html and "approve" in html.lower()
    assert "not selectable" in source or "selectable" in html
