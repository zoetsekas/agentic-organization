"""Workspaces are created, renamed and deleted; a design is imported from a
file on the author's disk; refusals name their fields (ADR-0106)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app

ROOT = Path(__file__).resolve().parents[1]
A = {"X-User": "ana"}
B = {"X-User": "bob"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "d.db")))


def _ws(client, name="Alpha", who=A):
    return client.post("/api/designer/workspaces", json={"name": name},
                       headers=who).json()


def test_a_workspace_needs_a_name_and_says_so_on_the_field(client):
    res = client.post("/api/designer/workspaces", json={"name": "  "}, headers=A)
    assert res.status_code == 422
    assert res.json()["detail"]["fields"] == {"name": "A workspace needs a name."}


def test_two_of_your_workspaces_cannot_share_a_name(client):
    _ws(client, "Alpha")
    res = client.post("/api/designer/workspaces", json={"name": "alpha"},
                      headers=A)
    assert res.status_code == 422 and "name" in res.json()["detail"]["fields"]


def test_a_workspace_is_renamed(client):
    ws = _ws(client)
    res = client.put(f"/api/designer/workspaces/{ws['id']}",
                     json={"name": "Beta", "description": "renamed"}, headers=A)
    assert res.json()["name"] == "Beta"
    listed = client.get("/api/designer/workspaces", headers=A).json()
    assert [w["name"] for w in listed] == ["Beta"]


def test_only_its_owner_deletes_a_workspace(client):
    ws = _ws(client)
    client.post(f"/api/designer/workspaces/{ws['id']}/members",
                json={"user_id": "bob", "role": "admin"}, headers=A)
    assert client.delete(f"/api/designer/workspaces/{ws['id']}",
                         headers=B).status_code == 403
    assert client.delete(f"/api/designer/workspaces/{ws['id']}",
                         headers=A).json()["deleted"]


def test_deleting_a_workspace_with_designs_needs_saying_so(client):
    ws = _ws(client)
    client.post("/api/designer/systems",
                json={"workspace_id": ws["id"], "name": "Acme"}, headers=A)
    refused = client.delete(f"/api/designer/workspaces/{ws['id']}", headers=A)
    assert refused.status_code == 422
    assert "workspace" in refused.json()["detail"]["fields"]
    done = client.delete(f"/api/designer/workspaces/{ws['id']}?cascade=true",
                         headers=A).json()
    assert done == {"deleted": True, "systems_deleted": 1}
    assert client.get("/api/designer/workspaces", headers=A).json() == []


def test_a_design_is_imported_from_a_file(client):
    ws = _ws(client)
    text = (ROOT / "examples" / "northwind" /
            "northwind.finance.system.yaml").read_text()
    made = client.post("/api/designer/import", headers=A, json={
        "workspace_id": ws["id"], "text": text,
        "filename": "northwind.finance.system.yaml"}).json()
    record = client.get(f"/api/designer/systems/{made['system_id']}",
                        headers=A).json()["record"]
    assert made["name"] == "Northwind Trading"
    assert record["spec"]["organization"]["members"]
    assert record["layout"]["diagrams"]["main"]["nodes"], "opens laid out"


def test_an_old_layout_file_is_stored_in_the_current_layout(client):
    """ADR-0101 moved the collections under `organization`; a file written
    before it is read, and stored the way the canvas reads it."""
    ws = _ws(client)
    text = ("metadata: {name: old}\n"
            "organization: {id: root, name: Old}\n"
            "capabilities: [{id: read_ledger}]\n")
    made = client.post("/api/designer/import", headers=A, json={
        "workspace_id": ws["id"], "text": text, "filename": "old.yaml"}).json()
    spec = client.get(f"/api/designer/systems/{made['system_id']}",
                      headers=A).json()["record"]["spec"]
    assert "capabilities" not in spec
    assert spec["organization"]["capabilities"] == [{"id": "read_ledger"}]


@pytest.mark.parametrize("text, says", [
    ("", "empty"),
    ("a: [", "Not valid YAML"),
    ("just: a mapping", "no `metadata`"),
    ("metadata: {name: x}\norganization: {id: r, name: r, members: 3}\n",
     "organization.members"),
])
def test_a_file_that_is_not_a_design_is_refused_on_the_file_field(client, text,
                                                                  says):
    ws = _ws(client)
    res = client.post("/api/designer/import", headers=A, json={
        "workspace_id": ws["id"], "text": text, "filename": "x.yaml"})
    assert res.status_code == 422
    assert says in res.json()["detail"]["fields"]["file"]


def test_an_import_names_its_workspace_or_is_refused(client):
    res = client.post("/api/designer/import", headers=A,
                      json={"text": "metadata: {name: x}"})
    assert res.json()["detail"]["fields"] == {
        "workspace": "Choose a workspace to import into."}


def test_the_cli_imports_a_file_from_anywhere_on_disk(tmp_path, capsys):
    from orgagents.cli import main
    path = tmp_path / "mine.system.yaml"
    path.write_text((ROOT / "examples" / "sentinel" /
                     "sentinel.secops.system.yaml").read_text())
    code = main(["--db", str(tmp_path / "d.db"), "examples", "import",
                 str(path), "--user", "ana"])
    assert code == 0 and "imported mine.system.yaml" in capsys.readouterr().out
