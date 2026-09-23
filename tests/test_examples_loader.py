"""Load a shipped example into the designer, from the UI's route or the CLI.

There was no way to open an example in the designer at all: New… took a name
and a description, the CLI had no import, and people were handed a paste-in
script that posted a spec to the API by hand.
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.cli import main as cli
from orgagents.designer.examples import (
    UnknownExample,
    get_example,
    initial_layout,
    list_examples,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
A = {"X-User": "ana"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "d.db")))


def test_every_shipped_example_is_offered():
    offered = {e.id for e in list_examples()}
    on_disk = {p.parent.name for p in ROOT.glob("examples/*/*.system.yaml")}
    assert offered == on_disk


def test_an_example_is_named_for_the_organisation_not_its_slug():
    """A picker of slugs asks the reader to already know what each one is."""
    assert get_example("northwind").name == "Northwind Trading"


def test_an_unknown_example_names_the_ones_that_exist():
    with pytest.raises(UnknownExample) as caught:
        get_example("nope")
    assert "northwind" in caught.value.args[0]


def test_a_loaded_example_opens_laid_out(client):
    """Arriving as a spec alone, it would open on an empty canvas with the
    whole organisation in the explorer — which reads as nothing loaded."""
    loaded = client.post("/api/designer/examples/load",
                         json={"example": "northwind"}, headers=A).json()
    record = client.get(f"/api/designer/systems/{loaded['system_id']}",
                        headers=A).json()["record"]
    nodes = record["layout"]["diagrams"]["main"]["nodes"]
    assert len(nodes) == 21
    assert record["name"] == "Northwind Trading"


def test_the_layout_is_the_products_own_tree():
    import yaml

    spec = yaml.safe_load(get_example("northwind").path.read_text())
    nodes = initial_layout(spec).diagrams["main"].nodes
    root = spec["organization"]["id"]
    child = spec["organization"]["teams"][0]["id"]
    assert nodes[child].y > nodes[root].y


def test_loading_into_the_open_workspace_adds_rather_than_replaces(client):
    first = client.post("/api/designer/examples/load",
                        json={"example": "ayc"}, headers=A).json()
    second = client.post("/api/designer/examples/load",
                         json={"example": "northwind",
                               "workspace_id": first["workspace_id"]},
                         headers=A).json()
    assert second["created_workspace"] is False
    systems = client.get(
        f"/api/designer/systems?workspace_id={first['workspace_id']}",
        headers=A).json()
    assert {s["name"] for s in systems} == {"AYC", "Northwind Trading"}


def test_somebody_outside_the_workspace_is_refused(client):
    """Through the designer service, so the same role checks as New…"""
    ws = client.post("/api/designer/examples/load", json={"example": "ayc"},
                     headers=A).json()["workspace_id"]
    refused = client.post("/api/designer/examples/load",
                          json={"example": "ayc", "workspace_id": ws},
                          headers={"X-User": "eve"})
    assert refused.status_code == 403


def test_an_unknown_example_is_a_404(client):
    assert client.post("/api/designer/examples/load", json={"example": "nope"},
                       headers=A).status_code == 404


def test_the_cli_loads_into_the_store_the_ui_reads(tmp_path, capsys):
    """Same module, same store: an example loaded in a shell is the one the
    reader opens in the browser."""
    db = str(tmp_path / "d.db")
    assert cli(["--db", db, "examples", "load", "sentinel", "--user", "ana"]) == 0
    assert "Sentinel Security" in capsys.readouterr().out
    client = TestClient(create_app(db))
    names = [w["name"] for w in
             client.get("/api/designer/workspaces", headers=A).json()]
    assert "Sentinel Security" in names


def test_the_cli_refuses_to_load_for_nobody(tmp_path, capsys):
    """A workspace is visible only to its members, so a load with no owner
    would look exactly like a load that failed."""
    assert cli(["--db", str(tmp_path / "d.db"), "examples", "load", "ayc"]) == 2
    assert "--user" in capsys.readouterr().out


def test_the_cli_lists_them(capsys):
    assert cli(["examples", "list"]) == 0
    assert "Northwind Trading" in capsys.readouterr().out
