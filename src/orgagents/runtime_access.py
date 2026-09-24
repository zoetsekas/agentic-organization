"""Who may see and drive the running platform (ADR-0116).

The runtime routes (`/api/agents`, `/api/sessions`, `/api/ops`, `/api/org`,
`/api/components`) and both catalogs (`/api/catalog`, `/api/catalogs`) are
authorised here. No new role is invented: a permission is reached through a
role one of the two existing models already records.

* **Designer workspace roles (ADR-0032)** grant runtime permissions *in the
  workspace that owns the design* an agent was loaded from. A session belongs
  to its agent, an agent to its design (`Agent.system_id`), a design to its
  workspace. Designing an organization and watching it run are the same
  people's job, so the membership list that says who may edit the design is
  the list that says who may read its sessions.
* **Fabric operator roles (ADR-0049, ADR-0051)** grant installation-wide
  reach — every tenant, including agents that no design owns — for the
  operational acts only: read, acknowledge an alert, and (admins) govern the
  shared platform catalog. An operator may not run an agent or answer for a
  human in a session: that is acting *as* the organization, which ADR-0051
  keeps with the tenant's own people.
* **The local user** in `none` mode (single-user local, ADR-0114) holds every
  permission installation-wide, so the bundled UI works unchanged. It is the
  identity a request carries when it names nobody (`anonymous`), or
  `ORGAGENTS_LOCAL_USER`. A named `X-User` in `none` mode is resolved like any
  other principal — by membership — so an assistant configured as a viewer
  acts as a viewer (ADR-0115).

Deny by default: a permission not granted by one of the three is refused, and
an agent whose design is unknown is visible only at installation scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .designer.models import UserRole
from .designer.rbac import Principal, role_of
from .fabric.deployments import OperatorRole

RUNTIME_READ = "runtime.read"
RUNTIME_RUN = "runtime.run"
RUNTIME_RESUME = "runtime.resume"
#: Create/replace/delete agents and org units, publish sandbox templates,
#: install a marketplace item onto an agent.
RUNTIME_MANAGE = "runtime.manage"
OPS_ACK = "ops.ack"
CATALOG_READ = "catalog.read"
#: Publishing and editing one's own proposal; rating a marketplace item.
CATALOG_PUBLISH = "catalog.publish"
#: Approve, restrict, send back, retire.
CATALOG_REVIEW = "catalog.review"
CATALOG_DELETE = "catalog.delete"
CATALOG_ENTITLE = "catalog.entitle"

ALL_PERMISSIONS = frozenset({
    RUNTIME_READ, RUNTIME_RUN, RUNTIME_RESUME, RUNTIME_MANAGE, OPS_ACK,
    CATALOG_READ, CATALOG_PUBLISH, CATALOG_REVIEW, CATALOG_DELETE,
    CATALOG_ENTITLE,
})

#: What a workspace role grants over the runtime of that workspace's designs.
WORKSPACE_ROLE_PERMISSIONS: dict[UserRole, frozenset[str]] = {
    UserRole.VIEWER: frozenset({RUNTIME_READ}),
    # A reviewer answers for a human in a paused session: judgement, not
    # authorship, which is what the reviewer role already means in the design.
    UserRole.REVIEWER: frozenset({RUNTIME_READ, RUNTIME_RESUME}),
    UserRole.EDITOR: frozenset({RUNTIME_READ, RUNTIME_RUN, RUNTIME_RESUME,
                                CATALOG_PUBLISH}),
    UserRole.ADMIN: frozenset({RUNTIME_READ, RUNTIME_RUN, RUNTIME_RESUME,
                               RUNTIME_MANAGE, OPS_ACK, CATALOG_PUBLISH}),
    UserRole.OWNER: frozenset({RUNTIME_READ, RUNTIME_RUN, RUNTIME_RESUME,
                               RUNTIME_MANAGE, OPS_ACK, CATALOG_PUBLISH}),
}

#: What an operator role grants, installation-wide.
OPERATOR_ROLE_PERMISSIONS: dict[OperatorRole, frozenset[str]] = {
    OperatorRole.AUTOMATION: frozenset({RUNTIME_READ, OPS_ACK}),
    OperatorRole.OPERATOR: frozenset({RUNTIME_READ, OPS_ACK}),
    OperatorRole.ADMIN: frozenset({RUNTIME_READ, OPS_ACK, CATALOG_PUBLISH,
                                   CATALOG_REVIEW, CATALOG_DELETE,
                                   CATALOG_ENTITLE}),
}

#: Anyone who may read the runtime anywhere may read the catalogs: they are
#: the shared directory every design draws from.
_IMPLIED = {RUNTIME_READ: CATALOG_READ}


def _with_implied(perms: Iterable[str]) -> frozenset[str]:
    out = set(perms)
    for have, implied in _IMPLIED.items():
        if have in out:
            out.add(implied)
    return frozenset(out)


class RuntimeAccessDenied(PermissionError):
    """A runtime or catalog permission the caller does not hold."""


@dataclass(frozen=True)
class Grants:
    """Everything one principal may do, resolved once per request."""

    principal: Principal
    #: Held everywhere, including over agents no design owns.
    installation: frozenset[str] = frozenset()
    #: Held in one workspace only.
    workspaces: dict[str, frozenset[str]] = field(default_factory=dict)
    #: Why the installation grant exists, for the audit row.
    sources: tuple[str, ...] = ()

    def allows(self, permission: str, workspace_id: Optional[str]) -> bool:
        if permission in self.installation:
            return True
        return bool(workspace_id) and permission in self.workspaces.get(
            workspace_id or "", frozenset())

    def anywhere(self, permission: str) -> bool:
        return permission in self.installation or any(
            permission in p for p in self.workspaces.values())

    def reason(self, permission: str, workspace_id: Optional[str]) -> str:
        where = (f"workspace '{workspace_id}'" if workspace_id
                 else "installation scope (no design owns it)")
        return f"{self.principal.label} does not hold '{permission}' in {where}"


class RuntimeAccess:
    """Resolves grants and the scope of runtime objects."""

    def __init__(self, designer: Any, operators: Any, *,
                 local_user: str = "anonymous") -> None:
        self.designer = designer
        self.operators = operators
        self.local_user = local_user or "anonymous"

    def grants(self, principal: Principal) -> Grants:
        installation: set[str] = set()
        sources: list[str] = []
        # The mode is read off the principal -- how *this* identity was
        # established -- not off configuration: an OIDC subject that happens
        # to be called "anonymous" is not the local user.
        if (getattr(principal, "auth_mode", "") == "none"
                and principal.user_id == self.local_user):
            installation |= ALL_PERMISSIONS
            sources.append("local user")
        for role in self.operators.roles_for(principal.user_id):
            installation |= OPERATOR_ROLE_PERMISSIONS.get(role, frozenset())
            sources.append(role.value)
        # An installation-wide designer grant from the identity provider
        # ("*", ADR-0047) reaches every design, and so every design's runtime.
        star = principal.granted_roles.get("*") if principal.granted_roles else None
        if star is not None:
            installation |= WORKSPACE_ROLE_PERMISSIONS.get(star, frozenset())
            sources.append(f"designer:*:{star.value}")
        workspaces: dict[str, frozenset[str]] = {}
        for workspace in self.designer.repository.list_workspaces():
            role = role_of(workspace, principal, None)  # type: ignore[arg-type]
            if role is not None:
                workspaces[workspace.id] = _with_implied(
                    WORKSPACE_ROLE_PERMISSIONS.get(role, frozenset()))
        return Grants(principal, _with_implied(installation), workspaces,
                      tuple(sources))

    # -- scope -----------------------------------------------------------

    def workspace_of_system(self, system_id: Optional[str]) -> Optional[str]:
        if not system_id:
            return None
        record = self.designer.repository.get_system(system_id)
        return record.workspace_id if record else None

    def workspace_of_agent(self, agent: Any) -> Optional[str]:
        return self.workspace_of_system(getattr(agent, "system_id", None))
