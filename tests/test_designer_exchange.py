"""The designer exports a design typed and imports typed or untyped files
(ADR-0113): `GET /api/designer/systems/{id}/export`, and the existing
import accepting YAML or JSON with or without types."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.spec import exchange
from orgagents.spec.loader import load_spec

ROOT = Path(__file__).resolve().parents[1]
A = {"X-User": "ana"}
NORTHWIND = ROOT / "examples" / "northwind" / "northwind.finance.system.yaml"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "d.db")))


def _ws(client):
    return client.post("/api/designer/workspaces", json={"name": "Alpha"},
                       headers=A).json()["id"]


def _import(client, text, filename="x.json"):
    return client.post("/api/designer/import", headers=A, json={
        "workspace_id": _ws(client), "text": text, "filename": filename})


@pytest.mark.parametrize("fmt", exchange.FORMATS)
def test_a_typed_file_imports_and_exports_to_the_same_file(client, fmt):
    typed = exchange.dump(load_spec(NORTHWIND), fmt)
    made = _import(client, typed, f"nw.{fmt}").json()
    res = client.get(f"/api/designer/systems/{made['system_id']}/export",
                     params={"format": fmt}, headers=A)
    assert res.status_code == 200
    assert res.text == typed
    assert res.headers["content-type"].startswith(
        "application/json" if fmt == "json" else "application/yaml")
    assert 'filename="' in res.headers["content-disposition"]


def test_an_untyped_file_exports_typed(client):
    made = _import(client, NORTHWIND.read_text(encoding="utf-8"),
                   "nw.system.yaml").json()
    doc = yaml.safe_load(client.get(
        f"/api/designer/systems/{made['system_id']}/export",
        headers=A).text)
    assert doc["type"] == "Core::Model"
    assert doc["organization"]["type"] == "Organisation::Organization"
    untyped = client.get(f"/api/designer/systems/{made['system_id']}/export",
                         params={"typed": "false"}, headers=A).text
    assert "Organisation::" not in untyped


def test_a_mistyped_file_is_refused_on_the_file_field_with_its_code(client):
    doc = json.loads(exchange.dump(load_spec(NORTHWIND), "json"))
    doc["organization"]["members"][0]["type"] = "Organisation::Team"
    res = _import(client, json.dumps(doc))
    assert res.status_code == 422
    says = res.json()["detail"]["fields"]["file"]
    assert "OA-3002" in says and "organization.members[0]" in says


def test_the_binding_a_design_carries_exports_typed(client):
    from orgagents.designer.examples import list_examples
    ayc = next(e for e in list_examples() if e.id == "ayc")
    made = client.post("/api/designer/examples/load", headers=A, json={
        "example": ayc.id, "workspace_id": _ws(client)}).json()
    sid = made.get("system_id") or made.get("id") or made["record"]["id"]
    res = client.get(f"/api/designer/systems/{sid}/export",
                     params={"part": "binding"}, headers=A)
    assert res.status_code == 200, res.text
    doc = yaml.safe_load(res.text)
    assert doc["type"] == "Deployment::Binding"
    assert doc["targets"][0]["type"] == "Deployment::Target"


def test_export_refuses_what_it_cannot_do(client):
    made = _import(client, NORTHWIND.read_text(encoding="utf-8")).json()
    sid = made["system_id"]
    assert client.get(f"/api/designer/systems/{sid}/export",
                      params={"format": "xml"}, headers=A).status_code == 422
    assert client.get(f"/api/designer/systems/{sid}/export",
                      params={"part": "binding"}, headers=A).status_code == 404
    assert client.get(f"/api/designer/systems/{sid}/export",
                      headers={"X-User": "mallory"}).status_code == 403
