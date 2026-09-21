"""Who may do what *in the designer* (ADR-0032).

This is platform access control for people, and it is deliberately separate
from the agent-facing RBAC in `security/rbac.py`. Conflating them is a common
and expensive mistake: being an agent's accountable owner (ADR-0026) says
nothing about whether you may edit the design, and being a workspace admin
says nothing about what any agent may reach.

Same three properties as the agent model: deny by default, a role is the only
way to obtain a permission, and every decision names the reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from .models import UserRole, Workspace

# Permission vocabulary. Small on purpose: a long list nobody can hold in their
# head is a list nobody reviews.
VIEW = "system.view"
EDIT = "system.edit"
CREATE = "system.create"
DELETE = "system.delete"
PUBLISH = "system.publish"
REVIEW = "system.review"
RESTORE = "revision.restore"
LOCK = "lock.acquire"
BREAK_LOCK = "lock.break"
MANAGE_MEMBERS = "workspace.members"
MANAGE_SETTINGS = "designer.settings"
# Reading the audit log is an administrative act: it names people and what they
# were refused, so it sits with membership and settings, not with VIEW.
READ_AUDIT = "audit.read"
DELETE_WORKSPACE = "workspace.delete"

ROLE_PERMISSIONS: dict[UserRole, frozenset[str]] = {
    UserRole.VIEWER: frozenset({VIEW}),
    UserRole.REVIEWER: frozenset({VIEW, REVIEW}),
    UserRole.EDITOR: frozenset({VIEW, EDIT, CREATE, LOCK, RESTORE}),
    UserRole.ADMIN: frozenset({
        VIEW, EDIT, CREATE, DELETE, PUBLISH, REVIEW, RESTORE, LOCK, BREAK_LOCK,
        MANAGE_MEMBERS, MANAGE_SETTINGS, READ_AUDIT,
    }),
    UserRole.OWNER: frozenset({
        VIEW, EDIT, CREATE, DELETE, PUBLISH, REVIEW, RESTORE, LOCK, BREAK_LOCK,
        MANAGE_MEMBERS, MANAGE_SETTINGS, READ_AUDIT, DELETE_WORKSPACE,
    }),
}


class PermissionDenied(PermissionError):
    """Raised when a user lacks the permission for an action."""


@dataclass(frozen=True)
class Principal:
    """The person making the request."""

    user_id: str
    display_name: str = ""
    email: str = ""
    # Roles vouched for by the identity provider (ADR-0047), keyed by workspace
    # id with "*" for installation-wide. Empty for a header identity, and
    # empty is the whole point: no grant, no access.
    granted_roles: Mapping[str, UserRole] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.display_name or self.user_id

    def granted_role(self, workspace_id: str) -> Optional[UserRole]:
        """The strongest role the provider granted for this workspace."""
        candidates = [r for r in (self.granted_roles.get(workspace_id),
                                  self.granted_roles.get("*")) if r is not None]
        if not candidates:
            return None
        order = [UserRole.VIEWER, UserRole.REVIEWER, UserRole.EDITOR,
                 UserRole.ADMIN, UserRole.OWNER]
        return max(candidates, key=order.index)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    role: Optional[UserRole] = None

    def __bool__(self) -> bool:
        return self.allowed


def role_of(workspace: Optional[Workspace], principal: Principal,
            default: UserRole = UserRole.VIEWER) -> Optional[UserRole]:
    """The principal's role in a workspace, or None if not a member."""
    if workspace is None:
        return default
    member = workspace.member(principal.user_id)
    if member:
        # An explicit membership wins over the directory: a role an admin
        # chose is not quietly rewritten by a group change (ADR-0047).
        return member.role
    return principal.granted_role(workspace.id)


#: Roles in order of reach, for the one permission an installation may move.
ROLE_ORDER: tuple[UserRole, ...] = (
    UserRole.VIEWER, UserRole.REVIEWER, UserRole.EDITOR,
    UserRole.ADMIN, UserRole.OWNER,
)


def decide(workspace: Optional[Workspace], principal: Principal, permission: str,
           *, default_role: UserRole = UserRole.VIEWER,
           break_lock_requires: Optional[UserRole] = None) -> Decision:
    role = role_of(workspace, principal, default_role)
    if role is None:
        return Decision(False, f"{principal.label} is not a member of this workspace")

    # `lock_break_requires` was a setting an installation could change and
    # nothing read: an operator could move it to `editor` or to `owner` and
    # breaking a lock kept answering from the table below. A knob on a
    # security surface that quietly configures nothing is worse than one that
    # is not offered, so it is consulted here — for this permission only,
    # because a general per-permission override would replace the table with a
    # second one that can disagree with it.
    if permission == BREAK_LOCK and break_lock_requires is not None:
        try:
            needed = ROLE_ORDER.index(break_lock_requires)
            held = ROLE_ORDER.index(role)
        except ValueError:
            needed = held = None          # an unknown role falls back to the table
        if needed is not None:
            if held >= needed:
                return Decision(
                    True,
                    f"granted by role '{role.value}': this workspace requires "
                    f"'{break_lock_requires.value}' or above to break a lock",
                    role,
                )
            return Decision(
                False,
                f"role '{role.value}' may not break a lock here: this "
                f"workspace requires '{break_lock_requires.value}' or above",
                role,
            )

    if permission in ROLE_PERMISSIONS.get(role, frozenset()):
        return Decision(True, f"granted by role '{role.value}'", role)
    return Decision(
        False,
        f"role '{role.value}' does not grant '{permission}'",
        role,
    )


def require(workspace: Optional[Workspace], principal: Principal, permission: str,
            *, default_role: UserRole = UserRole.VIEWER,
            break_lock_requires: Optional[UserRole] = None) -> Decision:
    decision = decide(workspace, principal, permission, default_role=default_role,
                      break_lock_requires=break_lock_requires)
    if not decision.allowed:
        raise PermissionDenied(decision.reason)
    return decision


def permissions_for(role: Optional[UserRole],
                    break_lock_requires: Optional[UserRole] = None) -> list[str]:
    """What this role may do, as the server would actually answer.

    The setting is applied here as well as in `decide`, because this list is
    what the UI is told and a UI that offers a button the server will refuse —
    or hides one it would allow — is a worse lie than either alone.
    """
    if role is None:
        return []
    held = set(ROLE_PERMISSIONS.get(role, frozenset()))
    if break_lock_requires is not None:
        try:
            allowed = ROLE_ORDER.index(role) >= ROLE_ORDER.index(break_lock_requires)
        except ValueError:
            allowed = BREAK_LOCK in held          # unknown role: keep the table
        held.discard(BREAK_LOCK)
        if allowed:
            held.add(BREAK_LOCK)
    return sorted(held)
