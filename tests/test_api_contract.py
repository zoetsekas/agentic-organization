"""The public API contract (ADR-0115): tags, /api/v1, the committed schema,
and the typed client acting through the same RBAC as the UI."""
from __future__ import annotations

import json

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.api_contract import PUBLIC_PREFIX, SCHEMA_PATH, schema_text, tag_for

httpx = pytest.importorskip("httpx")
from orgagents.client import ApiError, DesignerClient, RuntimeClient  # noqa: E402

ALICE = {"X-User": "alice"}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGAGENTS_DESIGNER_PATH", str(tmp_path / "designer"))
    monkeypatch.delenv("ORGAGENTS_DESIGNER_AUTH", raising=False)
    return create_app(str(tmp_path / "contract.db"))


@pytest.fixture()
def http(app):
    return TestClient(app)


def test_every_route_is_in_a_named_group(app):
    """A new route lands in a group by its prefix; one that fits none is a
    row missing from `_GROUPS`, caught here rather than in `/docs`."""
    untagged = [r.path for r in app.routes
                if isinstance(r, APIRoute) and not r.tags]
    assert untagged == []
    assert tag_for("/api/v1/designer/systems/x/publish") == "designer: review and publish"
    assert tag_for("/api/designer/operations") == "designer: model"


def test_every_api_route_is_also_served_under_v1(app):
    routes = {(r.path, frozenset(r.methods)) for r in app.routes
              if isinstance(r, APIRoute)}
    for path, methods in routes:
        if path.startswith("/api/") and not path.startswith(PUBLIC_PREFIX):
            assert (PUBLIC_PREFIX + path[4:], methods) in routes, path


def test_the_schema_documents_v1_only(app):
    paths = app.openapi()["paths"]
    assert all(p.startswith(PUBLIC_PREFIX) or p == "/healthz" for p in paths)
    assert "/api/v1/designer/systems/{system_id}/publish" in paths
    tags = {t["name"] for t in app.openapi()["tags"]}
    assert {"designer: designs", "fabric", "sessions"} <= tags


def test_v1_and_unversioned_are_the_same_endpoint(http):
    """Same function, same dependencies: identity and RBAC cannot differ."""
    ws = http.post("/api/v1/designer/workspaces", json={"name": "W"},
                   headers=ALICE).json()
    design = http.post("/api/designer/systems",
                       json={"workspace_id": ws["id"], "name": "d"},
                       headers=ALICE).json()
    assert http.get(f"/api/v1/designer/systems/{design['id']}",
                    headers=ALICE).status_code == 200
    for prefix in ("/api", "/api/v1"):
        assert http.get(f"{prefix}/designer/systems/{design['id']}",
                        headers={"X-User": "mallory"}).status_code == 403


def test_committed_schema_is_current():
    """docs/api/openapi.json is what a client is generated from. Regenerate
    with `orgagents api schema` when this fails."""
    assert SCHEMA_PATH.is_file(), "run `orgagents api schema`"
    committed = SCHEMA_PATH.read_text(encoding="utf-8")
    assert committed == schema_text(), (
        "docs/api/openapi.json is stale: run `orgagents api schema`")
    assert json.loads(committed)["info"]["title"]


# -- the typed client ----------------------------------------------------------

def test_client_sends_identity_in_the_form_each_mode_expects():
    none = DesignerClient(user="alice", http=httpx.Client())
    assert none._headers == {"X-User": "alice"}
    proxy = DesignerClient(user="alice", auth_mode="trusted_proxy",
                           proxy_secret="s3cret", http=httpx.Client())
    assert proxy._headers["X-Orgagents-Proxy-Secret"] == "s3cret"
    oidc = DesignerClient(auth_mode="oidc", token="tok", http=httpx.Client())
    assert oidc._headers == {"Authorization": "Bearer tok"}
    with pytest.raises(ValueError):
        DesignerClient(auth_mode="oidc", http=httpx.Client())
    with pytest.raises(ValueError):
        DesignerClient(user="a", auth_mode="trusted_proxy", http=httpx.Client())


def test_client_round_trip_through_rbac(http):
    alice = DesignerClient(user="alice", http=http)
    ws = alice.create_workspace("Team")
    alice.add_member(ws["id"], "edith", "editor")
    design = alice.create_design(ws["id"], "demo")
    assert [d["id"] for d in alice.list_designs(ws["id"])] == [design["id"]]

    findings = alice.validate(design["id"])["findings"]
    assert any(f["issue_id"] == "OA-1201" for f in findings)
    assert alice.issue_code("OA-1201")["code"] == "team_without_leader"

    spec = alice.get_design(design["id"])["record"]["spec"]
    created = alice.apply_operation(spec, {
        "op": "create", "kind": "agent", "id": "lead", "owner": "root",
        "attrs": {"name": "Lead"}})
    assert created["accepted"]
    led = alice.apply_operation(created["spec"], {
        "op": "set_leader", "team": "root", "agent": "lead"})
    saved = alice.save_design(design["id"], spec=led["spec"], base_version=1,
                              message="lead")
    assert saved["status"] == "saved" and saved["record"]["version"] == 2
    assert alice.diff(design["id"])["changes"]
    assert alice.preflight(design["id"])["target"] == "local"

    # An editor may change a design and may not ask for it to be run.
    edith = DesignerClient(user="edith", http=http)
    with pytest.raises(ApiError) as refused:
        edith.request_publish(design["id"], tenant_id="t1")
    assert refused.value.forbidden
    # A stranger may not even read it.
    with pytest.raises(ApiError) as hidden:
        DesignerClient(user="mallory", http=http).get_design(design["id"])
    assert hidden.value.status == 403


def test_runtime_client_reads(http):
    # Deny by default (ADR-0116): a named caller reads the runtime through a
    # membership, so alice has a workspace; mallory has none and is refused.
    http.post("/api/v1/designer/workspaces", json={"name": "W"}, headers=ALICE)
    with pytest.raises(ApiError) as refused:
        RuntimeClient(user="mallory", http=http).list_sessions()
    assert refused.value.forbidden
    rc = RuntimeClient(user="alice", http=http)
    assert isinstance(rc.list_sessions(), list)
    assert isinstance(rc.metrics(), dict)
    assert isinstance(rc.search_catalogs(), list)
