"""Who may operate the fabric (ADR-0049, ADR-0051).

This is a *second* access-control model, deliberately disjoint from the
designer's (`designer/rbac.py`). Designing an organization and running the
fabric it lands on are different jobs, and ADR-0049 asks for the separation to
be structural rather than remembered: a designer role never confers a fabric
permission, and an operator role grants nothing inside a design.

Two things make that real here rather than decorative:

* The permission vocabularies do not overlap — every name below is
  `fabric.*`, and none of them appears in the designer's table. `disjoint()`
  is exported so a test can assert it rather than a reviewer eyeballing it.
* A grant is a *record*, not an inference. An operator grant is written into
  the fabric's own collection by somebody who already holds
  ``fabric.operator.grant``; nothing reads a designer role, a workspace
  membership or an OIDC group mapping to decide it. Holding both role sets is
  possible and is then two rows in two places, which is the point.

The role enum itself lives in `deployments.py`, next to the transition table
that is its main consumer: one definition of "what an operator is", used both
to admit a request and to decide a lifecycle move.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from pydantic import BaseModel, Field

from ..ids import now_iso
from ..store import Store
from .deployments import OperatorRole

OPERATOR_GRANTS = "fabric_operator_grants"

# -- permission vocabulary -------------------------------------------------
# Small, and `fabric.`-prefixed so that a permission string is self-describing
# in an audit row and cannot be confused with a designer one.

TENANT_READ = "fabric.tenant.read"
DEPLOYMENT_READ = "fabric.deployment.read"
HEALTH_READ = "fabric.health.read"
QUOTA_READ = "fabric.quota.read"
SERVICE_READ = "fabric.service.read"
AUDIT_READ = "fabric.audit.read"

#: Creating a tenant mints an isolation domain and spends a namespace prefix
#: for good, so it sits with the administrative permissions rather than the
#: operational ones. Moving one along its lifecycle is operational.
TENANT_REGISTER = "fabric.tenant.register"
TENANT_LIFECYCLE = "fabric.tenant.lifecycle"

DEPLOY = "fabric.deployment.deploy"
STOP = "fabric.deployment.stop"
QUARANTINE = "fabric.deployment.quarantine"
REDEPLOY = "fabric.deployment.redeploy"
REQUOTA = "fabric.quota.write"
GRANT = "fabric.operator.grant"

READ_PERMISSIONS = frozenset(
    {TENANT_READ, DEPLOYMENT_READ, HEALTH_READ, QUOTA_READ, SERVICE_READ}
)

#: There is no `fabric.spec.*` permission and there never should be: changing
#: what an organization *is* stays with the tenant's own designers (ADR-0051).
ALL_PERMISSIONS = frozenset(
    READ_PERMISSIONS
    | {AUDIT_READ, DEPLOY, STOP, QUARANTINE, REDEPLOY, REQUOTA, GRANT,
       TENANT_REGISTER, TENANT_LIFECYCLE}
)

ROLE_PERMISSIONS: dict[OperatorRole, frozenset[str]] = {
    # The fabric acting on its own observation: it may see, and it may pull a
    # cord in one direction (quarantine). It may not deploy or re-quota,
    # because an automated re-quota is how a runaway bill is made invisible.
    OperatorRole.AUTOMATION: frozenset(READ_PERMISSIONS | {QUARANTINE}),
    OperatorRole.OPERATOR: frozenset(
        READ_PERMISSIONS
        | {DEPLOY, STOP, QUARANTINE, REDEPLOY, REQUOTA, TENANT_LIFECYCLE}
    ),
    # Admin is not "operator plus": it adds the administrative permissions
    # (reading the operator log, granting roles) and the lifecycle table keeps
    # its own opinion about which of the two may make a given move.
    OperatorRole.ADMIN: frozenset(
        READ_PERMISSIONS
        | {AUDIT_READ, DEPLOY, STOP, QUARANTINE, REDEPLOY, REQUOTA, GRANT,
           TENANT_REGISTER, TENANT_LIFECYCLE}
    ),
}

#: Weakest to strongest, for picking a role to act as and for a readable
#: refusal message. It is not an inheritance chain.
ROLE_ORDER: tuple[OperatorRole, ...] = (
    OperatorRole.AUTOMATION,
    OperatorRole.OPERATOR,
    OperatorRole.ADMIN,
)


class FabricPermissionDenied(PermissionError):
    """Raised when a principal lacks a fabric permission."""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    role: Optional[OperatorRole] = None

    def __bool__(self) -> bool:
        return self.allowed


class OperatorGrant(BaseModel):
    """One recorded grant of one operator role to one person."""

    #: One row per person: the id is the user id, so a re-grant replaces.
    id: str = ""
    user_id: str
    roles: list[OperatorRole] = Field(default_factory=list)
    granted_by: str = ""
    reason: str = ""
    granted_at: str = Field(default_factory=now_iso)

    def model_post_init(self, _context: object) -> None:
        if not self.id:
            self.id = self.user_id


def permissions_for(roles: Sequence[OperatorRole]) -> frozenset[str]:
    granted: set[str] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(granted)


def decide(roles: Sequence[OperatorRole], permission: str) -> Decision:
    """Deny by default: no grant, no access, and the reason says which."""
    if not roles:
        return Decision(False, "no platform-operator role is granted to this principal")
    for role in reversed(ROLE_ORDER):
        if role in roles and permission in ROLE_PERMISSIONS.get(role, frozenset()):
            return Decision(True, f"granted by operator role '{role.value}'", role)
    held = ", ".join(sorted(r.value for r in roles))
    return Decision(False, f"operator roles [{held}] do not grant '{permission}'")


def require(roles: Sequence[OperatorRole], permission: str) -> Decision:
    decision = decide(roles, permission)
    if not decision.allowed:
        raise FabricPermissionDenied(decision.reason)
    return decision


def disjoint_from(designer_permissions: Sequence[str]) -> bool:
    """True when no permission name is shared with the designer's model."""
    return not (ALL_PERMISSIONS & set(designer_permissions))


class OperatorRegistry:
    """The fabric's record of who operates it.

    Nothing here consults the designer: a person is an operator because a row
    says so, never because of a workspace role or a group they are in.
    """

    def __init__(self, store: Store, *, bootstrap: Optional[dict[str, list[OperatorRole]]] = None) -> None:
        self.store = store
        for user_id, roles in (bootstrap or {}).items():
            if self.get(user_id) is None:
                self.grant(user_id, roles, granted_by="bootstrap",
                           reason="installation bootstrap")

    def get(self, user_id: str) -> Optional[OperatorGrant]:
        return self.store.get(OPERATOR_GRANTS, user_id, OperatorGrant)

    def list(self) -> list[OperatorGrant]:
        return sorted(
            self.store.list(OPERATOR_GRANTS, OperatorGrant, limit=10_000),
            key=lambda g: g.user_id,
        )

    def roles_for(self, user_id: str) -> list[OperatorRole]:
        grant = self.get(user_id)
        return list(grant.roles) if grant else []

    def grant(self, user_id: str, roles: Sequence[OperatorRole], *,
              granted_by: str = "", reason: str = "") -> OperatorGrant:
        record = OperatorGrant(
            user_id=user_id,
            roles=sorted({OperatorRole(r) for r in roles}, key=ROLE_ORDER.index),
            granted_by=granted_by,
            reason=reason,
        )
        self.store.put(OPERATOR_GRANTS, record, name=user_id)
        return record

    def revoke(self, user_id: str) -> bool:
        return self.store.delete(OPERATOR_GRANTS, user_id)


def parse_bootstrap(config: str) -> dict[str, list[OperatorRole]]:
    """Read ``alice=fabric_admin,bob=fabric_operator`` from configuration.

    Unknown role names are dropped rather than raising: a typo in an
    environment variable should not hand out a role, and it should not stop
    the API starting either.
    """
    grants: dict[str, list[OperatorRole]] = {}
    for item in config.split(","):
        user, _, role = item.strip().partition("=")
        if not user or not role:
            continue
        try:
            parsed = OperatorRole(role.strip())
        except ValueError:
            continue
        grants.setdefault(user, []).append(parsed)
    return grants
