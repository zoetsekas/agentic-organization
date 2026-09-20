"""Tenant registration and lifecycle: CLI, API and the machine underneath.

The question these tests keep asking is whether a refusal *refused* — that a
tenant that could not be moved was also not quietly changed — because a
control plane that repairs its own inputs stops being a record (WS-028 M5).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.cli import main
from orgagents.fabric.deployments import (
    DeploymentService,
    DeploymentState,
    OperatorRole,
)
from orgagents.fabric.rbac import OperatorRegistry
from orgagents.fabric.tenants import (
    PrefixError,
    TenantIllegalTransition,
    TenantRegistry,
    TenantRetirementBlocked,
    TenantStatus,
    TenantTransitionDenied,
)
from orgagents.platform import Platform

ADMIN = {"X-User": "adam", "X-User-Name": "Adam Admin"}
OPERATOR = {"X-User": "olive", "X-User-Name": "Olive Operator"}
DESIGNER = {"X-User": "dana", "X-User-Name": "Dana Designer"}


@pytest.fixture()
def registry(tmp_path):
    platform = Platform(str(tmp_path / "fabric.db"), configure_logs=False)
    return TenantRegistry(platform.store), DeploymentService(platform.store)


@pytest.fixture()
def app(tmp_path):
    application = create_app(str(tmp_path / "api.db"))
    operators = application.state.fabric["operators"]
    operators.grant("adam", [OperatorRole.ADMIN], granted_by="test")
    operators.grant("olive", [OperatorRole.OPERATOR], granted_by="test")
    return application


@pytest.fixture()
def client(app):
    return TestClient(app)


# -- registration ---------------------------------------------------------


def test_registration_assigns_a_derived_isolation_domain(registry):
    tenants, _ = registry
    tenant = tenants.register(id="acme", name="Acme")
    assert tenant.namespace_prefix == "acme"
    assert tenant.isolation_domain.network == "acme-net"
    assert tenant.isolation_domain.secret_scope == "acme-secrets"


# Note "Acme!" is absent on purpose: `normalize_prefix` tidies it to "acme",
# which is safe. These are the ones no tidy-up can rescue.
@pytest.mark.parametrize("prefix", ["ab", "fabric", "x!", "a--b", "9acme"])
def test_registration_refuses_an_unsafe_prefix(registry, prefix):
    tenants, _ = registry
    with pytest.raises(PrefixError):
        tenants.register(id="t1", name="T", namespace_prefix=prefix)
    assert tenants.list() == []


def test_registration_refuses_a_prefix_that_prefixes_another(registry):
    tenants, _ = registry
    tenants.register(id="acme", name="Acme")
    with pytest.raises(PrefixError):
        tenants.register(id="acme-eu", name="Acme EU")
    with pytest.raises(PrefixError):
        tenants.register(id="other", name="Other", namespace_prefix="acme")
    assert [t.id for t in tenants.list()] == ["acme"]


def test_a_retired_tenant_keeps_its_prefix_forever(registry):
    tenants, _ = registry
    tenants.register(id="acme", name="Acme")
    tenants.retire("acme", actor="adam", role=OperatorRole.ADMIN)
    with pytest.raises(PrefixError):
        tenants.register(id="acme2", name="Acme again", namespace_prefix="acme")


# -- the lifecycle machine ------------------------------------------------


def test_an_illegal_transition_is_refused_without_mutating(registry):
    tenants, _ = registry
    tenants.register(id="acme", name="Acme")
    tenants.retire("acme", actor="adam", role=OperatorRole.ADMIN)
    before = tenants.require("acme").model_dump(mode="json")
    with pytest.raises(TenantIllegalTransition):
        tenants.transition("acme", TenantStatus.ACTIVE, actor="adam",
                           role=OperatorRole.ADMIN)
    assert tenants.require("acme").model_dump(mode="json") == before


def test_retiring_is_admin_only_and_a_denial_changes_nothing(registry):
    tenants, _ = registry
    tenants.register(id="acme", name="Acme")
    with pytest.raises(TenantTransitionDenied):
        tenants.transition("acme", TenantStatus.RETIRED, actor="olive",
                           role=OperatorRole.OPERATOR)
    assert tenants.require("acme").status is TenantStatus.ACTIVE
    assert tenants.require("acme").history == []


def test_suspend_and_resume_are_recorded_in_the_history(registry):
    tenants, _ = registry
    tenants.register(id="acme", name="Acme")
    tenants.transition("acme", TenantStatus.SUSPENDED, actor="olive",
                       role=OperatorRole.OPERATOR, reason="non-payment")
    resumed = tenants.transition("acme", TenantStatus.ACTIVE, actor="olive",
                                 role=OperatorRole.OPERATOR, reason="paid")
    assert resumed.status is TenantStatus.ACTIVE
    assert [(e.source.value, e.target.value) for e in resumed.history] == [
        ("active", "suspended"), ("suspended", "active"),
    ]
    assert resumed.history[0].reason == "non-payment"


def test_retirement_is_refused_while_a_deployment_is_live(registry):
    tenants, deployments = registry
    tenants.register(id="acme", name="Acme")
    live = deployments.request("acme", name="orders")
    with pytest.raises(TenantRetirementBlocked) as raised:
        tenants.retire("acme", actor="adam", role=OperatorRole.ADMIN,
                       deployments=deployments.list("acme"))
    assert live.id in raised.value.deployments
    assert tenants.require("acme").status is TenantStatus.ACTIVE

    # Retired deployments no longer occupy the isolation domain.
    deployments.transition(live.id, DeploymentState.RETIRED, actor="adam",
                           role=OperatorRole.ADMIN)
    retired = tenants.retire("acme", actor="adam", role=OperatorRole.ADMIN,
                             deployments=deployments.list("acme"))
    assert retired.status is TenantStatus.RETIRED


# -- the API --------------------------------------------------------------


def _register(client, tenant_id="acme", **body):
    payload = {"id": tenant_id, "name": tenant_id.title(), **body}
    return client.post("/api/fabric/tenants", json=payload, headers=ADMIN)


def test_the_api_registers_and_then_moves_a_tenant(client):
    created = _register(client)
    assert created.status_code == 200, created.text
    assert created.json()["isolation_domain"]["id"] == "acme-domain"
    assert "suspended" in created.json()["allowed_transitions"]

    suspended = client.post("/api/fabric/tenants/acme/actions/suspend",
                            json={"reason": "investigating"}, headers=OPERATOR)
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    resumed = client.post("/api/fabric/tenants/acme/actions/resume",
                          json={"reason": "cleared"}, headers=OPERATOR)
    assert resumed.json()["status"] == "active"


def test_the_api_refuses_a_colliding_prefix_with_409(client):
    _register(client)
    clash = _register(client, "acme-eu")
    assert clash.status_code == 409
    assert "collides" in clash.json()["detail"]


def test_the_api_refuses_an_operator_retiring_a_tenant(client):
    _register(client)
    denied = client.post("/api/fabric/tenants/acme/actions/retire",
                         json={"reason": "done"}, headers=OPERATOR)
    assert denied.status_code == 403
    assert client.get("/api/fabric/tenants/acme",
                      headers=ADMIN).json()["tenant"]["status"] == "active"


def test_the_api_refuses_retirement_while_a_deployment_is_live(app, client):
    _register(client)
    app.state.fabric["deployments"].request("acme", name="orders")
    blocked = client.post("/api/fabric/tenants/acme/actions/retire",
                          json={"reason": "done"}, headers=ADMIN)
    assert blocked.status_code == 409
    assert "live deployment" in blocked.json()["detail"]


def test_an_unknown_tenant_action_is_404(client):
    _register(client)
    assert client.post("/api/fabric/tenants/acme/actions/delete", json={},
                       headers=ADMIN).status_code == 404


def test_a_designer_with_no_fabric_grant_is_refused_everywhere(client):
    _register(client)
    refused = [
        client.post("/api/fabric/tenants", json={"id": "globex"}, headers=DESIGNER),
        client.post("/api/fabric/tenants/acme/actions/suspend", json={},
                    headers=DESIGNER),
        client.post("/api/fabric/tenants/acme/actions/resume", json={},
                    headers=DESIGNER),
        client.post("/api/fabric/tenants/acme/actions/retire", json={},
                    headers=DESIGNER),
    ]
    assert [r.status_code for r in refused] == [403, 403, 403, 403]
    assert client.get("/api/fabric/tenants/acme",
                      headers=ADMIN).json()["tenant"]["status"] == "active"


def test_a_designer_role_never_becomes_a_fabric_role(app, client):
    """The refusal above is about the *absence of a grant*, not the route."""
    assert client.get("/api/fabric/whoami",
                      headers=DESIGNER).json()["operator_roles"] == []
    assert OperatorRegistry(app.state.fabric["operators"].store).get("dana") is None


def test_every_tenant_action_lands_in_the_audit_log(client):
    _register(client)
    client.post("/api/fabric/tenants/acme/actions/suspend",
                json={"reason": "investigating"}, headers=OPERATOR)
    client.post("/api/fabric/tenants/acme/actions/retire", json={"reason": "no"},
                headers=OPERATOR)  # denied
    client.post("/api/fabric/tenants/acme/actions/retire", json={"reason": "yes"},
                headers=ADMIN)

    rows = client.get("/api/fabric/audit?tenant_id=acme", headers=ADMIN).json()
    by_action = [(r["action"], r["outcome"]) for r in rows]
    assert ("fabric.tenant.register", "success") in by_action
    assert ("fabric.tenant.lifecycle", "success") in by_action
    assert ("fabric.tenant.lifecycle", "denied") in by_action
    retire_row = next(r for r in rows
                      if r["detail"].get("to") == "retired")
    assert retire_row["detail"]["acted_as"] == "fabric_admin"
    assert retire_row["reason"] == "yes"


def test_a_refused_registration_is_audited_too(client):
    _register(client)
    _register(client, "acme-eu")
    rows = client.get("/api/fabric/audit", headers=ADMIN).json()
    assert any(r["action"] == "fabric.tenant.register" and r["outcome"] == "conflict"
               for r in rows)


# -- the CLI --------------------------------------------------------------


def test_the_cli_round_trips_register_list_show_retire(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    assert main(["--db", db, "tenants", "register", "acme", "--name", "Acme"]) == 0
    assert "prefix=acme" in capsys.readouterr().out

    assert main(["--db", db, "tenants", "list"]) == 0
    assert "acme-domain" in capsys.readouterr().out

    assert main(["--db", db, "tenants", "show", "acme"]) == 0
    assert '"status": "active"' in capsys.readouterr().out

    assert main(["--db", db, "tenants", "suspend", "acme", "--reason", "late"]) == 0
    assert main(["--db", db, "tenants", "resume", "acme"]) == 0
    assert main(["--db", db, "tenants", "retire", "acme", "--reason", "closed"]) == 0
    assert "retired" in capsys.readouterr().out

    # Terminal, and the prefix stays spent.
    assert main(["--db", db, "tenants", "resume", "acme"]) == 1
    assert main(["--db", db, "tenants", "register", "acme2",
                 "--prefix", "acme"]) == 1
    assert "collides" in capsys.readouterr().out


def test_the_cli_refuses_retiring_a_tenant_with_a_live_deployment(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    assert main(["--db", db, "tenants", "register", "acme"]) == 0
    capsys.readouterr()
    platform = Platform(db, configure_logs=False)
    DeploymentService(platform.store).request("acme", name="orders")

    assert main(["--db", db, "tenants", "retire", "acme"]) == 1
    assert "live deployment" in capsys.readouterr().out
    assert TenantRegistry(platform.store).require("acme").status is TenantStatus.ACTIVE


def test_the_cli_audits_what_it_did(tmp_path, capsys):
    from orgagents.fabric.audit import FabricAuditLog

    db = str(tmp_path / "cli.db")
    main(["--db", db, "tenants", "register", "acme", "--actor", "olive"])
    main(["--db", db, "tenants", "suspend", "acme", "--actor", "olive",
          "--reason", "late"])
    capsys.readouterr()

    events = FabricAuditLog(Platform(db, configure_logs=False).store).query(
        tenant_id="acme"
    )
    assert {e.action.value for e in events} == {
        "fabric.tenant.register", "fabric.tenant.lifecycle",
    }
    assert all(e.actor == "olive" for e in events)
