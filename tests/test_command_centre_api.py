"""The command centre's backend: route separation, actions and audit.

Exercises the exit criteria of WS-029 and the verification clause of ADR-0051:
designer-only credentials are refused on every fabric route, no operator role
can mutate a spec, no designer role can read another tenant's operational
state, every cross-tenant read lands in the audit log, and operator actions are
decided by the lifecycle table rather than by this namespace.
"""
import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.fabric.deployments import DeploymentState, OperatorRole
from orgagents.fabric.health import HealthStatus, TargetObservation

OPERATOR = {"X-User": "olive", "X-User-Name": "Olive Operator"}
ADMIN = {"X-User": "adam", "X-User-Name": "Adam Admin"}
DESIGNER = {"X-User": "dana", "X-User-Name": "Dana Designer"}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = create_app(str(tmp_path / "command.db"))
    fabric = application.state.fabric
    fabric["tenants"].register(id="acme", name="Acme", namespace_prefix="acme")
    fabric["tenants"].register(id="globex", name="Globex", namespace_prefix="globex")
    fabric["deployments"].request("acme", name="acme-core", system_id="sys_a",
                                  revision="r1")
    fabric["deployments"].request("globex", name="globex-core", system_id="sys_b",
                                  revision="r9")
    fabric["operators"].grant("olive", [OperatorRole.OPERATOR], granted_by="boot")
    fabric["operators"].grant("adam", [OperatorRole.ADMIN], granted_by="boot")
    # Dana is a designer and nothing else: she holds a workspace role in the
    # designer and no fabric grant at all.
    return application


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


def _deployment(app, tenant_id="acme"):
    return app.state.fabric["deployments"].list(tenant_id)[0]


def _audit(client):
    response = client.get("/api/fabric/audit", headers=ADMIN)
    assert response.status_code == 200
    return response.json()


# -- route separation ------------------------------------------------------

READ_ROUTES = [
    "/api/fabric/tenants",
    "/api/fabric/tenants/acme",
    "/api/fabric/deployments",
    "/api/fabric/health",
    "/api/fabric/drift",
    "/api/fabric/quotas",
    "/api/fabric/services",
    "/api/fabric/services/boundary",
    "/api/fabric/audit",
    "/api/fabric/operators",
]


@pytest.mark.parametrize("route", READ_ROUTES)
def test_designer_only_credentials_are_refused_on_every_read_route(client, route):
    assert client.get(route, headers=DESIGNER).status_code == 403


def test_designer_only_credentials_are_refused_on_every_action_route(client, app):
    deployment = _deployment(app)
    for action in ("generate", "deploy", "stop", "quarantine", "redeploy"):
        response = client.post(
            f"/api/fabric/deployments/{deployment.id}/actions/{action}",
            json={"reason": "no"}, headers=DESIGNER,
        )
        assert response.status_code == 403
    assert client.put("/api/fabric/tenants/acme/quotas", json={"quotas": {}},
                      headers=DESIGNER).status_code == 403
    assert client.post("/api/fabric/operators",
                       json={"user_id": "dana", "roles": ["fabric_admin"]},
                       headers=DESIGNER).status_code == 403
    assert client.delete("/api/fabric/operators/olive",
                         headers=DESIGNER).status_code == 403


def test_deployment_detail_routes_refuse_designer_credentials(client, app):
    deployment = _deployment(app)
    for route in (f"/api/fabric/deployments/{deployment.id}",
                  f"/api/fabric/deployments/{deployment.id}/history"):
        assert client.get(route, headers=DESIGNER).status_code == 403


def test_whoami_never_reports_a_designer_role_as_an_operator_role(client):
    body = client.get("/api/fabric/whoami", headers=DESIGNER).json()
    assert body["operator_roles"] == []
    assert body["permissions"] == []
    assert body["is_operator"] is False
    assert client.get("/api/fabric/whoami", headers=OPERATOR).json()[
        "operator_roles"] == ["fabric_operator"]


def test_no_operator_role_can_mutate_a_spec(client, app):
    """An operator has no designer grant, so the designer refuses them."""
    created = client.post("/api/designer/workspaces",
                          json={"name": "Acme design"}, headers=DESIGNER).json()
    system = client.post("/api/designer/systems",
                         json={"workspace_id": created["id"], "name": "Org"},
                         headers=DESIGNER)
    assert system.status_code == 200
    system_id = system.json()["id"]

    for headers in (OPERATOR, ADMIN):
        assert client.put(f"/api/designer/systems/{system_id}",
                          json={"spec": {"version": "0.9.0"}},
                          headers=headers).status_code == 403
        assert client.delete(f"/api/designer/systems/{system_id}",
                             headers=headers).status_code == 403

    # And the fabric namespace offers no spec route to reach for.
    fabric_paths = [r.path for r in app.routes if r.path.startswith("/api/fabric")]
    assert fabric_paths
    assert not any("system" in p or "spec" in p for p in fabric_paths)


def test_the_fabric_namespace_has_no_write_route_outside_operations(app):
    writes = {
        (r.path, method)
        for r in app.routes if r.path.startswith("/api/fabric")
        for method in getattr(r, "methods", set())
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert writes == {
        ("/api/fabric/deployments/{deployment_id}/actions/{action}", "POST"),
        ("/api/fabric/tenants/{tenant_id}/quotas", "PUT"),
        ("/api/fabric/operators", "POST"),
        ("/api/fabric/operators/{user_id}", "DELETE"),
    }


def test_no_designer_role_can_read_another_tenants_operational_state(client):
    # The designer's own namespace answers her; the fabric's does not, for any
    # tenant, whether or not she names one.
    assert client.get("/api/designer/whoami", headers=DESIGNER).status_code == 200
    for route in ("/api/fabric/tenants/globex", "/api/fabric/health?tenant_id=globex",
                  "/api/fabric/quotas?tenant_id=globex",
                  "/api/fabric/deployments?tenant_id=globex"):
        assert client.get(route, headers=DESIGNER).status_code == 403


# -- reads -----------------------------------------------------------------

def test_tenant_list_and_detail_shape(client, app):
    tenants = client.get("/api/fabric/tenants", headers=OPERATOR).json()
    assert {t["id"] for t in tenants} == {"acme", "globex"}
    assert tenants[0]["deployment_count"] == 1
    assert tenants[0]["isolation_domain"]["network"].endswith("-net")

    detail = client.get("/api/fabric/tenants/acme", headers=OPERATOR).json()
    assert detail["tenant"]["namespace_prefix"] == "acme"
    assert detail["deployments"][0]["state"] == "requested"
    assert "generated" in detail["deployments"][0]["allowed_transitions"]
    assert detail["quotas"]["recorded"] is False
    assert detail["health"][0]["confidence"] == "unobserved"


def test_unknown_tenant_is_a_404_for_an_operator(client):
    assert client.get("/api/fabric/tenants/nope", headers=OPERATOR).status_code == 404


def test_health_and_drift_report_belief_versus_observation(client, app):
    fabric = app.state.fabric
    deployment = _deployment(app)
    fabric["deployments"].transition(deployment.id, DeploymentState.GENERATED,
                                     actor="olive", role=OperatorRole.OPERATOR)
    fabric["deployments"].transition(deployment.id, DeploymentState.DEPLOYED,
                                     actor="olive", role=OperatorRole.OPERATOR)
    fabric["health_backend"].report(TargetObservation(
        deployment_id=deployment.id, tenant_id="acme",
        state=DeploymentState.RUNNING, revision="r1",
        status=HealthStatus.HEALTHY,
    ))
    checks = client.get("/api/fabric/health?tenant_id=acme", headers=OPERATOR).json()
    assert checks[0]["confidence"] == "fresh"
    drift = client.get("/api/fabric/drift", headers=OPERATOR).json()
    kinds = {s["kind"] for s in drift}
    assert "state_mismatch" in kinds  # deployed here, running there
    # Globex has never been observed, and unobserved is reported as unknown
    # rather than as health.
    globex = client.get("/api/fabric/health?tenant_id=globex",
                        headers=OPERATOR).json()
    assert globex[0]["status"] == "unknown"
    assert globex[0]["confidence"] == "unobserved"


def test_common_service_boundary_report_is_readable(client):
    services = client.get("/api/fabric/services", headers=OPERATOR).json()
    assert {s["kind"] for s in services} >= {"catalog", "observability", "identity"}
    rows = client.get("/api/fabric/services/boundary", headers=OPERATOR).json()
    assert any(r["visible_to_other_tenants"] == "true" for r in rows)
    assert all(r["why"] for r in rows)


def test_quota_state_and_entitlements(client, app):
    response = client.put("/api/fabric/tenants/acme/quotas", headers=OPERATOR,
                          json={"quotas": {"agents": {"soft_limit": 5,
                                                      "hard_ceiling": 10}},
                                "catalog_entries": ["cat_reviewer"],
                                "reason": "growth"})
    assert response.status_code == 200
    state = client.get("/api/fabric/quotas?tenant_id=acme", headers=OPERATOR).json()
    assert state[0]["quotas"]["agents"]["hard_ceiling"] == 10
    assert state[0]["catalog_entries"] == ["cat_reviewer"]
    assert state[0]["recorded"] is True


def test_requota_of_an_unknown_tenant_is_a_404(client):
    assert client.put("/api/fabric/tenants/nope/quotas", json={"quotas": {}},
                      headers=OPERATOR).status_code == 404


# -- operator actions ------------------------------------------------------

def test_operator_actions_follow_the_lifecycle_rules(client, app):
    deployment = _deployment(app)
    url = f"/api/fabric/deployments/{deployment.id}/actions"

    # deploy before generate is off the machine, and is refused not coerced.
    refused = client.post(f"{url}/deploy", json={"reason": "early"}, headers=OPERATOR)
    assert refused.status_code == 409
    assert "not a legal deployment transition" in refused.json()["detail"]

    assert client.post(f"{url}/generate", json={"reason": "compiled"},
                       headers=OPERATOR).json()["state"] == "generated"
    assert client.post(f"{url}/deploy", json={"reason": "go"},
                       headers=OPERATOR).json()["state"] == "deployed"
    assert client.post(f"{url}/stop", json={"reason": "noisy"},
                       headers=OPERATOR).json()["state"] == "stopped"
    body = client.post(f"{url}/redeploy", json={"reason": "fixed"},
                       headers=OPERATOR).json()
    assert body["state"] == "running"
    assert [e["target"] for e in body["history"]] == [
        "generated", "deployed", "stopped", "running"]
    assert body["history"][-1]["actor"] == "olive"
    assert body["history"][-1]["reason"] == "fixed"


def test_an_unauthorized_transition_is_refused_by_the_table(client, app):
    """Out of quarantine is admin-only, and only downwards."""
    fabric = app.state.fabric
    deployment = _deployment(app)
    url = f"/api/fabric/deployments/{deployment.id}/actions"
    client.post(f"{url}/generate", json={"reason": "compiled"}, headers=OPERATOR)
    client.post(f"{url}/deploy", json={"reason": "go"}, headers=OPERATOR)
    client.post(f"{url}/quarantine", json={"reason": "suspicion"}, headers=OPERATOR)

    # The operator holds `fabric.deployment.stop`, so the namespace admits the
    # request; the lifecycle table is what refuses it.
    denied = client.post(f"{url}/stop", json={"reason": "clean up"}, headers=OPERATOR)
    assert denied.status_code == 403
    assert "fabric_operator" in denied.json()["detail"]
    assert fabric["deployments"].get(deployment.id).state is DeploymentState.QUARANTINED

    allowed = client.post(f"{url}/stop", json={"reason": "investigating"},
                          headers=ADMIN)
    assert allowed.status_code == 200
    assert allowed.json()["state"] == "stopped"

    # Quarantine never leads straight back to running, for anybody.
    client.post(f"{url}/quarantine", json={"reason": "again"}, headers=ADMIN)
    assert client.post(f"{url}/redeploy", json={"reason": "please"},
                       headers=ADMIN).status_code == 409


def test_automation_may_quarantine_but_not_deploy(client, app):
    app.state.fabric["operators"].grant("watchdog", [OperatorRole.AUTOMATION])
    headers = {"X-User": "watchdog"}
    deployment = _deployment(app)
    url = f"/api/fabric/deployments/{deployment.id}/actions"
    assert client.post(f"{url}/generate", json={"reason": "x"},
                       headers=headers).status_code == 403
    client.post(f"{url}/generate", json={"reason": "compiled"}, headers=OPERATOR)
    client.post(f"{url}/deploy", json={"reason": "go"}, headers=OPERATOR)
    assert client.post(f"{url}/quarantine", json={"reason": "breach"},
                       headers=headers).json()["state"] == "quarantined"


def test_unknown_action_and_unknown_deployment_are_404(client, app):
    deployment = _deployment(app)
    assert client.post(f"/api/fabric/deployments/{deployment.id}/actions/nuke",
                       json={}, headers=OPERATOR).status_code == 404
    assert client.post("/api/fabric/deployments/dep_nope/actions/stop",
                       json={}, headers=OPERATOR).status_code == 404


def test_granting_an_operator_role_needs_an_admin(client):
    assert client.post("/api/fabric/operators",
                       json={"user_id": "nina", "roles": ["fabric_operator"]},
                       headers=OPERATOR).status_code == 403
    granted = client.post("/api/fabric/operators",
                          json={"user_id": "nina", "roles": ["fabric_operator"],
                                "reason": "joining the rota"}, headers=ADMIN)
    assert granted.status_code == 200
    assert granted.json()["granted_by"] == "adam"
    assert client.get("/api/fabric/whoami",
                      headers={"X-User": "nina"}).json()["is_operator"] is True
    assert client.delete("/api/fabric/operators/nina", headers=ADMIN).json()["revoked"]
    assert client.get("/api/fabric/whoami",
                      headers={"X-User": "nina"}).json()["is_operator"] is False


# -- audit -----------------------------------------------------------------

def test_every_cross_tenant_read_lands_in_the_audit_log(client):
    client.get("/api/fabric/tenants", headers=OPERATOR)
    client.get("/api/fabric/health", headers=OPERATOR)
    client.get("/api/fabric/quotas", headers=OPERATOR)

    cross = [e for e in _audit(client) if e["cross_tenant"]]
    assert {e["action"] for e in cross} == {
        "fabric.tenant.read", "fabric.health.read", "fabric.quota.read"}
    for event in cross:
        assert event["actor"] == "olive"
        assert event["operator_roles"] == ["fabric_operator"]
        assert event["detail"]["tenant_ids"] == ["acme", "globex"]
        assert event["detail"]["tenant_count"] == 2


def test_a_single_tenant_read_is_audited_but_not_marked_cross_tenant(client):
    client.get("/api/fabric/tenants/acme", headers=OPERATOR)
    event = next(e for e in _audit(client) if e["route"].endswith("{tenant_id}"))
    assert event["tenant_id"] == "acme"
    assert event["cross_tenant"] is False


def test_a_list_read_is_one_row_not_one_per_tenant(client):
    before = len(_audit(client))
    client.get("/api/fabric/tenants", headers=OPERATOR)
    after = _audit(client)
    # One row for the list read, plus the two audit reads bracketing it.
    added = [e for e in after[: len(after) - before]
             if e["action"] == "fabric.tenant.read"]
    assert len(added) == 1
    assert added[0]["detail"]["tenant_count"] == 2


def test_refusals_are_audited_too(client):
    client.get("/api/fabric/tenants", headers=DESIGNER)
    denied = [e for e in _audit(client) if e["outcome"] == "denied"]
    assert denied[0]["actor"] == "dana"
    assert denied[0]["permission"] == "fabric.tenant.read"
    assert denied[0]["operator_roles"] == []


def test_every_operator_action_is_audited_with_its_transition(client, app):
    deployment = _deployment(app)
    url = f"/api/fabric/deployments/{deployment.id}/actions"
    client.post(f"{url}/generate", json={"reason": "compiled"}, headers=OPERATOR)
    client.post(f"{url}/deploy", json={"reason": "go live"}, headers=OPERATOR)
    client.put("/api/fabric/tenants/acme/quotas", headers=OPERATOR,
               json={"quotas": {"agents": {"soft_limit": 3}}, "reason": "trim"})

    events = _audit(client)
    deploys = [e for e in events if e["action"] == "fabric.deployment.deploy"]
    assert [(e["detail"]["from"], e["detail"]["to"]) for e in deploys] == [
        ("generated", "deployed"), ("requested", "generated")]
    assert all(e["deployment_id"] == deployment.id for e in deploys)
    assert all(e["reason"] for e in deploys)

    requota = next(e for e in events if e["action"] == "fabric.quota.write")
    assert requota["detail"]["before"] == {}
    assert requota["detail"]["after"]["agents"]["soft_limit"] == 3


def test_an_illegal_transition_is_audited_as_a_conflict(client, app):
    deployment = _deployment(app)
    client.post(f"/api/fabric/deployments/{deployment.id}/actions/deploy",
                json={"reason": "early"}, headers=OPERATOR)
    conflict = next(e for e in _audit(client) if e["outcome"] == "conflict")
    assert conflict["action"] == "fabric.deployment.deploy"
    assert "not a legal deployment transition" in conflict["reason"]


def test_the_operator_log_is_administrative_and_is_itself_audited(client):
    assert client.get("/api/fabric/audit", headers=OPERATOR).status_code == 403
    _audit(client)
    reads = [e for e in _audit(client) if e["action"] == "fabric.audit.read"]
    assert {e["outcome"] for e in reads} == {"success", "denied"}


def test_the_operator_log_is_append_only(app):
    log = app.state.fabric["audit"]
    assert not hasattr(log, "update")
    assert not hasattr(log, "delete")
