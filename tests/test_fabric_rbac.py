"""The platform-operator role set, and its disjointness from designer RBAC."""
import pytest

from orgagents.designer import rbac as designer_rbac
from orgagents.designer.models import UserRole
from orgagents.fabric import rbac as fabric_rbac
from orgagents.fabric.deployments import OperatorRole
from orgagents.fabric.rbac import (
    FabricPermissionDenied,
    OperatorRegistry,
    decide,
    permissions_for,
    require,
)
from orgagents.store import Store


@pytest.fixture()
def registry(tmp_path) -> OperatorRegistry:
    return OperatorRegistry(Store(str(tmp_path / "fabric.db")))


def test_permission_vocabularies_do_not_overlap():
    designer_permissions = {p for perms in designer_rbac.ROLE_PERMISSIONS.values()
                            for p in perms}
    assert fabric_rbac.disjoint_from(sorted(designer_permissions))
    assert not (fabric_rbac.ALL_PERMISSIONS & designer_permissions)
    assert all(p.startswith("fabric.") for p in fabric_rbac.ALL_PERMISSIONS)


def test_no_designer_role_grants_a_fabric_permission():
    for role in UserRole:
        granted = set(designer_rbac.permissions_for(role))
        assert not (granted & fabric_rbac.ALL_PERMISSIONS)


def test_no_operator_role_grants_anything_inside_a_design():
    designer_permissions = {p for perms in designer_rbac.ROLE_PERMISSIONS.values()
                            for p in perms}
    for role in OperatorRole:
        assert not (permissions_for([role]) & designer_permissions)
    # There is no spec-editing permission in the fabric vocabulary at all.
    assert not any("spec" in p or "system." in p for p in fabric_rbac.ALL_PERMISSIONS)


def test_deny_by_default_without_a_grant():
    decision = decide([], fabric_rbac.TENANT_READ)
    assert not decision
    assert "no platform-operator role" in decision.reason
    with pytest.raises(FabricPermissionDenied):
        require([], fabric_rbac.TENANT_READ)


def test_automation_may_quarantine_but_not_deploy_or_requota():
    roles = [OperatorRole.AUTOMATION]
    assert decide(roles, fabric_rbac.QUARANTINE)
    assert not decide(roles, fabric_rbac.DEPLOY)
    assert not decide(roles, fabric_rbac.REQUOTA)
    assert decide(roles, fabric_rbac.TENANT_READ)


def test_operator_may_act_but_not_read_the_operator_log_or_grant():
    roles = [OperatorRole.OPERATOR]
    for permission in (fabric_rbac.DEPLOY, fabric_rbac.STOP,
                       fabric_rbac.QUARANTINE, fabric_rbac.REDEPLOY,
                       fabric_rbac.REQUOTA):
        assert decide(roles, permission)
    assert not decide(roles, fabric_rbac.AUDIT_READ)
    assert not decide(roles, fabric_rbac.GRANT)


def test_a_grant_is_a_record_not_an_inference(registry):
    assert registry.roles_for("dana") == []
    registry.grant("dana", [OperatorRole.OPERATOR], granted_by="root",
                   reason="on call")
    assert registry.roles_for("dana") == [OperatorRole.OPERATOR]
    # Holding both role sets is two grants: the designer role is untouched and
    # unreadable from here.
    assert registry.get("dana").granted_by == "root"
    assert registry.revoke("dana")
    assert registry.roles_for("dana") == []


def test_grants_survive_a_second_registry_over_the_same_store(tmp_path):
    store = Store(str(tmp_path / "shared.db"))
    OperatorRegistry(store).grant("iris", [OperatorRole.ADMIN])
    assert OperatorRegistry(store).roles_for("iris") == [OperatorRole.ADMIN]


def test_bootstrap_parsing_drops_unknown_roles():
    parsed = fabric_rbac.parse_bootstrap(
        "alice=fabric_admin,bob=fabric_operator,carol=wizard,broken"
    )
    assert parsed == {
        "alice": [OperatorRole.ADMIN],
        "bob": [OperatorRole.OPERATOR],
    }


def test_bootstrap_seeds_a_registry_once(tmp_path):
    store = Store(str(tmp_path / "boot.db"))
    OperatorRegistry(store, bootstrap={"alice": [OperatorRole.ADMIN]})
    OperatorRegistry(store).grant("alice", [OperatorRole.OPERATOR])
    # A later bootstrap must not quietly restore a revoked or changed grant.
    again = OperatorRegistry(store, bootstrap={"alice": [OperatorRole.ADMIN]})
    assert again.roles_for("alice") == [OperatorRole.OPERATOR]
