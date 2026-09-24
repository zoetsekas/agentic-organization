"""The runtime and the catalogs resolve their caller and authorise it (ADR-0116).

Every route takes identity from the one authenticator; the runtime routes and
both catalogs then ask what the caller may do, scoped by the workspace that
owns the design an agent came from. Deny by default: 401 without an identity
(in the modes that have one), 403 without a grant or outside one's workspaces
-- the designer's own choice for a system in another workspace -- and every
mutation, allowed or refused, is in the audit log.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.designer.audit import AuditAction, AuditOutcome
from orgagents.designer.auth import Authenticator
from orgagents.observability import Severity

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "src" / "orgagents" / "api.py"

ALICE = {"X-User": "alice"}      # owner of workspace A
VICTOR = {"X-User": "victor"}    # viewer in A
EDDIE = {"X-User": "eddie"}      # editor in A
RITA = {"X-User": "rita"}        # reviewer in A
STRANGER = {"X-User": "sam"}     # owner of workspace B only
OPS = {"X-User": "olga"}         # fabric operator
NOBODY = {"X-User": "mallory"}   # no grant anywhere


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORGAGENTS_DESIGNER_PATH", str(tmp_path / "designer"))
    monkeypatch.delenv("ORGAGENTS_DESIGNER_AUTH", raising=False)
    monkeypatch.setenv("ORGAGENTS_FABRIC_OPERATORS",
                       "olga=fabric_operator,ada=fabric_admin")
    app = create_app(str(tmp_path / "rt.db"))
    http = TestClient(app)
    ws_a = http.post("/api/designer/workspaces", json={"name": "A"},
                     headers=ALICE).json()
    for user, role in (("victor", "viewer"), ("eddie", "editor"),
                       ("rita", "reviewer")):
        http.post(f"/api/designer/workspaces/{ws_a['id']}/members",
                  json={"user_id": user, "role": role}, headers=ALICE)
    ws_b = http.post("/api/designer/workspaces", json={"name": "B"},
                     headers=STRANGER).json()
    sys_a = http.post("/api/designer/systems",
                      json={"workspace_id": ws_a["id"], "name": "a"},
                      headers=ALICE).json()
    sys_b = http.post("/api/designer/systems",
                      json={"workspace_id": ws_b["id"], "name": "b"},
                      headers=STRANGER).json()
    agent = {"name": "clerk", "harness": {"runtime": "echo",
                                          "system_prompt": "You file things."}}
    a = http.post("/api/agents", json={**agent, "system_id": sys_a["id"]},
                  headers=ALICE)
    assert a.status_code == 200, a.text
    b = http.post("/api/agents", json={**agent, "name": "b-clerk",
                                       "system_id": sys_b["id"]},
                  headers=STRANGER)
    assert b.status_code == 200, b.text
    run = http.post(f"/api/agents/{a.json()['id']}/run",
                    json={"prompt": "file it"}, headers=EDDIE)
    assert run.status_code == 200, run.text
    return {"app": app, "http": http, "ws_a": ws_a["id"], "ws_b": ws_b["id"],
            "agent_a": a.json()["id"], "agent_b": b.json()["id"],
            "session_a": run.json()["session_id"]}


def _events(app, action):
    return app.state.designer.audit.query(action=action, limit=500)


# -- identity comes from one place -------------------------------------------

IDENTITY_HEADERS = {"x_user", "x_user_name", "x_user_email", "authorization",
                    "x_auth_request_user", "x_auth_request_email",
                    "x_forwarded_user", "x_orgagents_proxy_secret"}


def _own_nodes(fn):
    """A function's nodes, not descending into the functions it defines
    (each of those is checked on its own)."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def test_no_route_reads_an_identity_header_itself():
    """Only `principal` (which hands the request to the authenticator) may
    declare a header parameter or read `request.headers`."""
    tree = ast.parse(API.read_text(encoding="utf-8"))
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name == "principal":
            continue
        args = fn.args.args + fn.args.kwonlyargs
        defaults = ([None] * (len(fn.args.args) - len(fn.args.defaults))
                    + list(fn.args.defaults) + list(fn.args.kw_defaults))
        for arg, default in zip(args, defaults):
            is_header = (isinstance(default, ast.Call)
                         and getattr(default.func, "id", "") == "Header")
            if is_header or arg.arg in IDENTITY_HEADERS:
                offenders.append(f"{fn.name}({arg.arg})")
        for node in _own_nodes(fn):
            if (isinstance(node, ast.Attribute) and node.attr == "headers"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "request"):
                offenders.append(f"{fn.name}: request.headers")
    assert offenders == [], offenders


# -- the local user ------------------------------------------------------------

def test_the_local_user_keeps_everything_in_none_mode(world):
    http = world["http"]
    # No X-User: the bundled UI's calls, unchanged.
    assert http.get("/api/sessions").status_code == 200
    assert http.get("/api/ops/metrics").status_code == 200
    assert http.post(f"/api/agents/{world['agent_b']}/run",
                     json={"prompt": "x"}).status_code == 200
    entry = http.post("/api/catalogs", json={
        "kind": "mcp_server", "name": "jira", "version": "1.0.0",
        "summary": "Jira.", "owner": "P", "attributes": {"transport": "http"}})
    assert entry.status_code == 200, entry.text
    assert http.post(f"/api/catalogs/{entry.json()['id']}/review?status=approved"
                     ).status_code == 200


# -- 401 in the authenticated modes --------------------------------------------

@pytest.mark.parametrize("route", [
    ("GET", "/api/sessions"), ("GET", "/api/v1/agents"),
    ("GET", "/api/ops/alerts"), ("GET", "/api/org/tree"),
    ("GET", "/api/components"), ("GET", "/api/catalogs"),
    ("GET", "/api/catalog"), ("POST", "/api/catalogs/x/review?status=approved"),
    ("POST", "/api/agents/x/run"),
])
def test_no_identity_is_401_in_trusted_proxy_mode(tmp_path, monkeypatch, route):
    monkeypatch.setenv("ORGAGENTS_DESIGNER_PATH", str(tmp_path / "designer"))
    monkeypatch.setenv("ORGAGENTS_DESIGNER_AUTH", "trusted_proxy")
    monkeypatch.setenv("ORGAGENTS_PROXY_SECRET", "p-secret")
    http = TestClient(create_app(str(tmp_path / "p.db")))
    method, path = route
    body = {"prompt": "x"} if path.endswith("/run") else None
    # A client-set X-User without the proxy's secret is nobody.
    res = http.request(method, path, json=body, headers={"X-User": "alice"})
    assert res.status_code == 401, (path, res.text)
    ok = http.get("/api/sessions", headers={"X-User": "alice",
                                             "X-Orgagents-Proxy-Secret": "p-secret"})
    assert ok.status_code == 403   # authenticated, but alice holds nothing


def test_no_bearer_is_401_in_oidc_mode(world):
    from orgagents.designer.models import DesignerSettings
    app, http = world["app"], world["http"]
    app.state.designer_auth = Authenticator(
        DesignerSettings(persistence="memory", auth_mode="oidc",
                         oidc_issuer="https://idp.example.test",
                         oidc_audiences=["orgagents"]),
        verifier=None, audit=app.state.designer.audit)
    for path in ("/api/sessions", f"/api/sessions/{world['session_a']}",
                 "/api/catalogs", "/api/ops/metrics"):
        assert http.get(path, headers=ALICE).status_code == 401, path
    assert http.post("/api/ops/alerts/x/ack").status_code == 401


# -- 403: wrong role, and another workspace -----------------------------------

def test_a_viewer_reads_but_does_not_run_or_resume(world):
    http, agent, session = world["http"], world["agent_a"], world["session_a"]
    assert http.get(f"/api/sessions/{session}", headers=VICTOR).status_code == 200
    assert http.get(f"/api/sessions/{session}/trace", headers=VICTOR).status_code == 200
    assert http.get(f"/api/agents/{agent}", headers=VICTOR).status_code == 200
    assert http.post(f"/api/agents/{agent}/run", json={"prompt": "x"},
                     headers=VICTOR).status_code == 403
    assert http.post(f"/api/sessions/{session}/resume", json={"response": "ok"},
                     headers=VICTOR).status_code == 403
    assert http.delete(f"/api/agents/{agent}", headers=EDDIE).status_code == 403
    assert http.post("/api/catalogs/x/review?status=approved",
                     headers=EDDIE).status_code == 403


def test_another_workspaces_sessions_are_refused_and_filtered(world):
    http, session = world["http"], world["session_a"]
    # The designer answers 403 for a system in a workspace one is not in; so
    # does the runtime for that system's sessions and agents.
    for path in (f"/api/sessions/{session}", f"/api/sessions/{session}/events",
                 f"/api/sessions/{session}/trace", f"/api/agents/{world['agent_a']}",
                 f"/api/ops/health/{world['agent_a']}",
                 f"/api/v1/sessions/{session}"):
        assert http.get(path, headers=STRANGER).status_code == 403, path
    assert http.get("/api/sessions", headers=STRANGER).json() == []
    assert {a["id"] for a in http.get("/api/agents", headers=STRANGER).json()} == {
        world["agent_b"]}
    assert http.get(f"/api/sessions?agent_id={world['agent_a']}",
                    headers=STRANGER).status_code == 403
    # A caller with no grant at all is refused the list outright.
    assert http.get("/api/sessions", headers=NOBODY).status_code == 403
    assert http.get("/api/catalogs", headers=NOBODY).status_code == 403


def test_an_operator_sees_every_tenant_but_does_not_act_as_the_organization(world):
    http = world["http"]
    ids = {s["id"] for s in http.get("/api/sessions", headers=OPS).json()}
    assert world["session_a"] in ids
    assert http.post(f"/api/agents/{world['agent_a']}/run", json={"prompt": "x"},
                     headers=OPS).status_code == 403
    alert = world["app"].state.platform.obs.raise_alert(
        "slow", "latency", severity=Severity.WARNING, agent_id=world["agent_a"])
    assert http.post(f"/api/ops/alerts/{alert.id}/ack",
                     headers=EDDIE).status_code == 403
    assert http.post(f"/api/ops/alerts/{alert.id}/ack",
                     headers=OPS).status_code == 200


def test_metrics_are_scoped_to_the_callers_workspaces(world):
    http = world["http"]
    assert http.get("/api/ops/metrics", headers=STRANGER).json()["sessions"]["total"] == 0
    assert http.get("/api/ops/metrics", headers=VICTOR).json()["sessions"]["total"] == 1


# -- allowed paths, and who they are recorded against ---------------------------

def test_the_caller_is_who_ran_and_resumed_not_the_body(world):
    http, agent = world["http"], world["agent_a"]
    run = http.post(f"/api/agents/{agent}/run",
                    json={"prompt": "x", "created_by": "ceo"}, headers=EDDIE).json()
    session = http.get(f"/api/sessions/{run['session_id']}", headers=EDDIE).json()
    assert session["created_by"] == "eddie"
    mcp = http.post(f"/api/agents/{agent}/run",
                    json={"prompt": "x", "created_by": "mcp:eddie"}, headers=EDDIE).json()
    assert http.get(f"/api/sessions/{mcp['session_id']}",
                    headers=EDDIE).json()["created_by"] == "mcp:eddie"


def test_catalog_actors_come_from_the_authenticator(world):
    http = world["http"]
    ada = {"X-User": "ada"}
    entry = http.post("/api/catalogs", json={
        "kind": "mcp_server", "name": "jira", "version": "1.0.0",
        "summary": "Jira.", "owner": "P", "attributes": {"transport": "http"}},
        headers=EDDIE)   # an editor may propose
    assert entry.status_code == 200, entry.text
    eid = entry.json()["id"]
    assert http.post(f"/api/catalogs/{eid}/review?status=approved",
                     headers=EDDIE).status_code == 403
    reviewed = http.post(f"/api/catalogs/{eid}/review?status=approved", headers=ada)
    assert reviewed.status_code == 200, reviewed.text
    history = str(http.get(f"/api/catalogs/{eid}", headers=ada).json())
    assert "ada" in history and "anonymous" not in history


# -- the audit log -------------------------------------------------------------

def test_allowed_and_denied_mutations_are_audited(world):
    app, http, agent = world["app"], world["http"], world["agent_a"]
    http.post(f"/api/agents/{agent}/run", json={"prompt": "x"}, headers=VICTOR)
    runs = _events(app, AuditAction.RUNTIME_RUN)
    denied = [e for e in runs if e.outcome is AuditOutcome.DENIED]
    allowed = [e for e in runs if e.outcome is AuditOutcome.SUCCESS]
    assert denied and denied[0].actor == "victor"
    assert denied[0].workspace_id == world["ws_a"]
    assert denied[0].permission == "runtime.run"
    assert allowed and allowed[-1].actor == "eddie"
    assert allowed[-1].detail["session"] == world["session_a"]
    http.get(f"/api/sessions/{world['session_a']}", headers=STRANGER)
    reads = _events(app, AuditAction.RUNTIME_READ)
    assert any(e.actor == "sam" and e.outcome is AuditOutcome.DENIED for e in reads)
    http.post("/api/catalogs/x/review?status=approved", headers=EDDIE)
    assert any(e.actor == "eddie" and e.outcome is AuditOutcome.DENIED
               for e in _events(app, AuditAction.CATALOG_REVIEW))
    # The workspace's admins read it through the designer's own audit route.
    rows = http.get("/api/designer/audit?action=runtime.run", headers=ALICE).json()
    assert {r["outcome"] for r in rows} == {"success", "denied"}
