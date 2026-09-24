"""The publish path: validate, compile, request (WS-032 M9).

The one thing the platform exists to do, and the one thing the UI could not
do. A designer could edit, lock, validate, read the authority view, run the
evaluation gate and diff two revisions — and then had to leave for a terminal.

The shape that matters is the plane split (ADR-0049). The designer
**requests**; it does not deploy. A deployment is created in `requested` and
the fabric compiles it for the tenant under its own authority, because the
tenant is assigned by the fabric and never named by a design (ADR-0050) and
the platform policy is the fabric's to apply (ADR-0076).
"""
from __future__ import annotations

import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.designer.models import UserRole
from orgagents.fabric.deployments import DeploymentState

ROOT = pathlib.Path(__file__).resolve().parents[1]
ALICE = {"X-User": "alice"}          # workspace owner: holds system.publish
EDITH = {"X-User": "edith"}          # editor: may change a design, not run it


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = create_app(str(tmp_path / "publish.db"))
    application.state.fabric["tenants"].register(
        id="northwind", name="Northwind", namespace_prefix="northwind"
    )
    return application


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def system(client) -> str:
    spec = yaml.safe_load(
        (ROOT / "examples" / "northwind" / "northwind.finance.system.yaml")
        .read_text(encoding="utf-8")
    )
    ws = client.post("/api/designer/workspaces", json={"name": "ws"},
                     headers=ALICE).json()
    client.post(f"/api/designer/workspaces/{ws['id']}/members",
                json={"user_id": "edith", "role": UserRole.EDITOR.value},
                headers=ALICE)
    created = client.post("/api/designer/systems",
                          json={"workspace_id": ws["id"], "name": "nw",
                                "spec": spec}, headers=ALICE).json()
    return created["id"]


# --------------------------------------------------------------------------
# Preflight: would this be refused?
# --------------------------------------------------------------------------


def test_preflight_validates_and_compiles_without_deploying_anything(client,
                                                                     system):
    res = client.post(f"/api/designer/systems/{system}/preflight",
                      json={"target": "local"}, headers=ALICE)
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["stage"] == "compiled"
    assert len(body["files"]) > 20, "a compile that produced nothing is not one"
    assert "docker-compose.yaml" in body["files"]


def test_preflight_carries_the_warnings_it_did_not_refuse_on(client, system):
    """Northwind's accepted residuals are the point of showing warnings."""
    body = client.post(f"/api/designer/systems/{system}/preflight",
                       headers=ALICE).json()
    codes = {w["code"] for w in body["warnings"]}
    assert "separated_agents_co_resident" in codes


def test_a_design_that_does_not_validate_is_refused_with_the_gates_reason(
    client, system
):
    """The refusal is the deliverable, not an error page.

    A CFO agent that holds both sides of a payment control is the kind of
    thing this platform exists to refuse, and a designer should read that
    sentence rather than a stack trace.
    """
    opened = client.get(f"/api/designer/systems/{system}",
                        headers=ALICE).json()["record"]
    spec = opened["spec"]
    # Give the CFO the one decision that breaks `payment_control`. It has to
    # be somebody whose *line* already holds both sides: the Controller sits
    # under a unit that excludes `release_payment`, so the claim would be
    # narrowed away as overreach before separation ever saw it. Finance
    # declares no mandate and inherits the whole vocabulary, so the CFO can
    # genuinely hold raising and approving at once.
    for member in spec["organization"]["teams"][0]["members"]:
        if member["id"] == "cfo":
            member["mandate"]["decisions"].append("raise_payment")
    version = opened["version"]
    client.put(f"/api/designer/systems/{system}",
               json={"spec": spec, "version": version}, headers=ALICE)

    body = client.post(f"/api/designer/systems/{system}/preflight",
                       headers=ALICE).json()
    assert body["ok"] is False
    assert body["stage"] == "validate"
    codes = {r["code"] for r in body["refusals"]}
    assert "separation_violated" in codes
    assert not body["files"], "a refused design produces no artifacts"


def test_a_draft_that_is_not_a_spec_yet_is_a_stage_not_a_crash(client):
    ws = client.post("/api/designer/workspaces", json={"name": "w2"},
                     headers=ALICE).json()
    made = client.post("/api/designer/systems",
                       json={"workspace_id": ws["id"], "name": "empty",
                             "spec": {"metadata": {"name": "x"}}},
                       headers=ALICE).json()
    body = client.post(f"/api/designer/systems/{made['id']}/preflight",
                       headers=ALICE).json()
    assert body["ok"] is False
    assert body["stage"] in ("spec", "validate")


# --------------------------------------------------------------------------
# Publish: the designer requests, the fabric deploys
# --------------------------------------------------------------------------


def test_publishing_requests_a_deployment_and_does_not_deploy_it(client, system):
    res = client.post(f"/api/designer/systems/{system}/publish",
                      json={"tenant_id": "northwind", "target": "local"},
                      headers=ALICE)
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["deployment"]["state"] == DeploymentState.REQUESTED.value
    assert body["deployment"]["tenant_id"] == "northwind"
    assert body["deployment"]["system_id"] == system
    assert body["deployment"]["revision"] == str(body["version"])
    assert "not deployed" in body["note"]


def test_the_deployment_names_the_revision_so_the_artifact_is_checkable(client,
                                                                        system):
    """A publish names a version, so an unsaved draft cannot be published."""
    before = client.get(f"/api/designer/systems/{system}",
                        headers=ALICE).json()["record"]["version"]
    body = client.post(f"/api/designer/systems/{system}/publish",
                       json={"tenant_id": "northwind"}, headers=ALICE).json()
    assert body["version"] == before
    assert body["deployment"]["revision"] == str(before)


def test_a_refused_design_is_never_requested(client, system):
    opened = client.get(f"/api/designer/systems/{system}",
                        headers=ALICE).json()["record"]
    spec = opened["spec"]
    spec["organization"]["leader"] = "nobody_at_all"
    version = opened["version"]
    client.put(f"/api/designer/systems/{system}",
               json={"spec": spec, "version": version}, headers=ALICE)

    res = client.post(f"/api/designer/systems/{system}/publish",
                      json={"tenant_id": "northwind"}, headers=ALICE)
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["verdict"]["ok"] is False
    assert detail["verdict"]["refusals"], "a refusal with no reason is not one"

    deployments = client.app.state.fabric["deployments"].list("northwind")
    assert not deployments, "a refused design must reach the fabric as nothing"


def test_a_publish_must_name_a_tenant_because_a_design_may_not(client, system):
    """The tenant is the fabric's to assign (ADR-0050)."""
    res = client.post(f"/api/designer/systems/{system}/publish",
                      json={"target": "local"}, headers=ALICE)
    assert res.status_code == 422
    assert "tenant" in res.json()["detail"]["reason"].lower()


def test_an_unknown_tenant_is_refused(client, system):
    res = client.post(f"/api/designer/systems/{system}/publish",
                      json={"tenant_id": "ghost"}, headers=ALICE)
    assert res.status_code == 404


# --------------------------------------------------------------------------
# Who may
# --------------------------------------------------------------------------


def test_an_editor_may_change_a_design_and_may_not_run_it(client, system):
    """Deciding what an organization should be and switching it on differ."""
    opened = client.get(f"/api/designer/systems/{system}",
                        headers=EDITH).json()["record"]
    saved = client.put(f"/api/designer/systems/{system}",
                       json={"spec": opened["spec"],
                             "version": opened["version"]},
                       headers=EDITH)
    assert saved.status_code == 200, "an editor may edit"

    res = client.post(f"/api/designer/systems/{system}/publish",
                      json={"tenant_id": "northwind"}, headers=EDITH)
    assert res.status_code == 403


def test_a_refused_publish_is_written_down(client, system):
    """Nothing else in the system would keep it.

    A denial leaves no revision, no lock and no deployment, so an audit entry
    is the only trace that somebody asked and the gate said no.
    """
    client.post(f"/api/designer/systems/{system}/publish",
                json={"tenant_id": "northwind"}, headers=EDITH)
    events = client.get("/api/designer/audit", headers=ALICE).json()
    publishes = [e for e in events if e["action"] == "system.publish"]
    assert publishes, "a refused publish left no trace"
    assert any(e["outcome"] == "denied" for e in publishes)


def test_a_successful_publish_is_written_down_with_the_deployment(client,
                                                                  system):
    body = client.post(f"/api/designer/systems/{system}/publish",
                       json={"tenant_id": "northwind"}, headers=ALICE).json()
    events = client.get("/api/designer/audit", headers=ALICE).json()
    allowed = [e for e in events
               if e["action"] == "system.publish" and e["outcome"] == "success"]
    assert allowed
    assert body["deployment"]["id"] in str(allowed)


# --------------------------------------------------------------------------
# The UI reaches it
# --------------------------------------------------------------------------


def test_the_bundle_reaches_both_halves_of_the_path():
    """Two new routes, and neither is allowed to be another orphan.

    `test_every_designer_route_has_something_that_calls_it` enforces this
    generally; these assert the specific shape, because a publish path whose
    refusal never reaches a person is the failure mode that matters.
    """
    canvas_js = (ROOT / "web" / "canvas.js").read_text(encoding="utf-8")
    assert "/preflight`" in canvas_js
    assert "/publish`" in canvas_js
    assert "function renderPublishVerdict" in canvas_js


def test_the_refusal_is_rendered_rather_than_a_status_code():
    canvas_js = (ROOT / "web" / "canvas.js").read_text(encoding="utf-8")
    # A 422 carries the gate's verdict; the handler must show it.
    assert "detail?.verdict" in canvas_js
    assert "renderPublishVerdict(detail.verdict)" in canvas_js


def test_the_request_button_needs_the_permission_and_a_clean_verdict():
    """A clean preflight is not authority to publish."""
    canvas_js = (ROOT / "web" / "canvas.js").read_text(encoding="utf-8")
    assert 'canvas.permissions.includes("system.publish")' in canvas_js


def test_the_bar_says_who_deploys():
    """The plane split, on the surface rather than only in an ADR."""
    index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "designer requests" in index_html
    assert "requested" in index_html
