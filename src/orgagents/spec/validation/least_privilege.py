"""Least privilege (ADR-0008)."""
from __future__ import annotations

from .context import ValidationContext
from .helpers import (
    _assignment_permissions,
    _perm_keys,
    _responsibility_mentions,
    team_permissions,
)
from .registry import rule

SECTION = "least privilege"


@rule(SECTION, {"wildcard_resource", "capability_without_responsibility"})
def roles_grant_what_they_explain(ctx: ValidationContext) -> None:
    spec, warn = ctx.spec, ctx.warn
    for role in spec.roles:
        for perm in role.permissions:
            if perm.resource == "*":
                warn(
                    "wildcard_resource",
                    f"role '{role.id}' grants {perm.action.value} on every "
                    f"{perm.resource_kind.value}",
                    role.id,
                    strict=True,
                )
        text = " ".join(role.responsibilities).lower()
        for cap_id in role.capabilities:
            cap = spec.capability(cap_id)
            if not _responsibility_mentions(text, cap_id, cap):
                warn(
                    "capability_without_responsibility",
                    f"role '{role.id}' grants capability '{cap_id}' that no "
                    "responsibility mentions",
                    role.id,
                )


@rule(SECTION, {"withhold_unknown_permission", "possible_escalation"})
def inheritance_narrows(ctx: ValidationContext) -> None:
    # An agent's effective permissions may not exceed its team's grant plus its
    # own roles — inheritance narrows, never widens (ADR-0008).
    spec, warn = ctx.spec, ctx.warn
    for team in ctx.teams:
        inherited = team_permissions(spec, team)
        for member in team.members:
            own: set[str] = set()
            for assignment in member.roles:
                own |= _assignment_permissions(spec, assignment)
            for assignment in member.roles:
                role = spec.role(assignment.role)
                if role and set(assignment.withhold) - _perm_keys(role.permissions):
                    warn(
                        "withhold_unknown_permission",
                        f"agent '{member.id}' withholds a permission role "
                        f"'{role.id}' does not grant",
                        member.id,
                    )
            escalated = {
                key
                for key in own
                if key.endswith(":administer") or key.startswith("administer:")
            }
            if escalated and not (escalated <= inherited):
                warn(
                    "possible_escalation",
                    f"agent '{member.id}' holds administrative permissions its team "
                    "does not",
                    member.id,
                    strict=True,
                )
