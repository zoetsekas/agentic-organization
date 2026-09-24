"""Small pure functions more than one rule, or a caller outside, needs."""
from __future__ import annotations

from typing import Any, Optional

from ..model import Permission, RoleAssignment, SystemSpec, Team


def _perm_keys(perms: list[Permission]) -> set[str]:
    return {p.key() for p in perms}


def _agent_capability_ids(spec: SystemSpec, agent: Any) -> set[str]:
    """Every capability an agent holds, bound directly or through a role."""
    held = set(agent.capabilities)
    for assignment in agent.roles:
        role = spec.role(assignment.role)
        if role is not None:
            held |= set(role.capabilities)
    return held


def _assignment_permissions(spec: SystemSpec, assignment: RoleAssignment) -> set[str]:
    role = spec.role(assignment.role)
    if role is None:
        return set()
    return _perm_keys(role.permissions) - set(assignment.withhold)


def team_permissions(spec: SystemSpec, team: Team) -> set[str]:
    """Permissions a team grants its members, including inherited ones."""
    granted: set[str] = set()
    for assignment in team.roles:
        granted |= _assignment_permissions(spec, assignment)
    parent = _parent_of(spec.organization, team.id)
    if parent is not None:
        granted |= team_permissions(spec, parent)
    return granted


def _parent_of(root: Team, team_id: str) -> Optional[Team]:
    for team in root.walk():
        if any(child.id == team_id for child in team.teams):
            return team
    return None


def _responsibility_mentions(text: str, cap_id: str, cap: Any) -> bool:
    """True when the prose responsibilities plausibly describe this capability.

    Deliberately loose: it matches word stems from the capability id and its
    resource class, because the point is to catch a permission nobody explained,
    not to police wording.
    """
    tokens = set(cap_id.lower().split("_"))
    if cap is not None:
        tokens |= set((cap.resource_class or "").lower().split("_"))
        tokens |= {cap.action.value.lower()}
    words = set(text.replace(",", " ").replace(".", " ").split())
    for token in tokens:
        if len(token) < 4:
            continue
        stem = token[:6]
        if any(w.startswith(stem) for w in words):
            return True
    return False
