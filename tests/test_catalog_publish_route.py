"""Publishing a catalog entry over HTTP actually works.

This route existed and was uncallable: `api.py` uses
`from __future__ import annotations`, so FastAPI resolves a route's
annotations as strings against the *module* globals — and the entry model was
imported inside `create_app`, where module-level resolution cannot see it.
FastAPI does not fail on that; it silently degrades the parameter to a query
field, so every POST returned 422 asking for a query parameter called `entry`.

Nothing caught it because nothing called the route. That is the gap these
tests close: an operator adding their own MCP server is the first person who
would have found it.
"""
import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "catalog.db")))


def _entry(**over):
    body = {
        "kind": "mcp_server",
        "name": "jira",
        "version": "1.0.0",
        "summary": "Read and comment on Jira issues.",
        "owner": "Platform Engineering",
        "attributes": {"transport": "http", "tools": ["search_issues"], "read_only": False},
        "tags": ["ticketing"],
    }
    body.update(over)
    return body


def test_an_entry_can_be_published_over_http(client):
    response = client.post("/api/catalogs", json=_entry())
    assert response.status_code == 200, response.text
    entry = response.json()
    assert entry["id"]
    assert entry["name"] == "jira"


def test_the_body_is_a_body_not_a_query_parameter(client):
    # The regression in its own words: a JSON body must be accepted, and the
    # failure mode was a 422 naming `entry` in the query string.
    response = client.post("/api/catalogs", json=_entry(name="confluence"))
    assert response.status_code != 422
    detail = response.json() if response.status_code >= 400 else {}
    assert "query" not in str(detail)


def test_a_new_entry_starts_unapproved_and_unselectable(client):
    # Publishing is not approving: an entry nobody reviewed must not be
    # choosable in a design (ADR-0041).
    entry = client.post("/api/catalogs", json=_entry(name="unreviewed")).json()
    assert entry["status"] == "proposed"
    assert entry.get("selectable") in (False, None)


def test_review_promotes_a_published_entry(client):
    entry = client.post("/api/catalogs", json=_entry(name="reviewed")).json()
    response = client.post(
        f"/api/catalogs/{entry['id']}/review",
        params={"status": "approved", "reviewer": "ana@acme.example",
                "note": "read-only, scoped to one project"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_a_published_entry_is_findable_by_kind(client):
    client.post("/api/catalogs", json=_entry(name="findable"))
    found = client.get("/api/catalogs", params={"kind": "mcp_server"}).json()
    assert any(e["name"] == "findable" for e in found)
