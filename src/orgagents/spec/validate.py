"""Spec validation: structure, references and least privilege.

Findings are severity-tagged. `error` fails the build; `warning` is reported
and, for the rules ADR-0008 marks as mandatory in production, promoted to an
error when `metadata.environment == "production"`.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Literal, Optional

from .model import (
    NetworkPosture,
    Permission,
    RoleAssignment,
    SharingScope,
    SystemSpec,
    Team,
)

Severity = Literal["error", "warning"]


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str
    where: str = ""

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.severity}: {self.code}{loc}: {self.message}"


def _perm_keys(perms: list[Permission]) -> set[str]:
    return {p.key() for p in perms}


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


def validate_spec(spec: SystemSpec) -> list[Finding]:
    """Return every finding; an empty list means the spec is sound."""
    out: list[Finding] = []
    production = spec.metadata.environment == "production"

    def err(code: str, message: str, where: str = "") -> None:
        out.append(Finding("error", code, message, where))

    def warn(code: str, message: str, where: str = "", strict: bool = False) -> None:
        severity: Severity = "error" if (strict and production) else "warning"
        out.append(Finding(severity, code, message, where))

    teams = spec.teams()
    agents = spec.agents()
    agent_ids = [a.id for a in agents]
    team_ids = [t.id for t in teams]

    # -- uniqueness --------------------------------------------------------
    for label, ids in (
        ("agent", agent_ids),
        ("team", team_ids),
        ("role", [r.id for r in spec.roles]),
        ("capability", [c.id for c in spec.capabilities]),
        ("environment", [e.id for e in spec.environments]),
        ("data class", [d.id for d in spec.data_classes]),
        ("workflow", [w.id for w in spec.workflows]),
    ):
        seen: set[str] = set()
        for i in ids:
            if i in seen:
                err("duplicate_id", f"duplicate {label} id '{i}'")
            seen.add(i)

    # -- organization structure (ADR-0006) ---------------------------------
    for team in teams:
        member_ids = {m.id for m in team.members}
        if not team.leader:
            err("team_without_leader", f"team '{team.id}' has no leader", team.id)
        elif team.leader not in member_ids:
            err(
                "leader_not_member",
                f"leader '{team.leader}' is not a member of team '{team.id}'",
                team.id,
            )
        # A child team's leader participates in the parent *implicitly* through
        # leadership (ADR-0006 v1.1.0); listing it in the parent's members too
        # would give the agent two home teams.
        for child in team.teams:
            if child.leader and child.leader in member_ids:
                err(
                    "child_leader_double_listed",
                    f"leader '{child.leader}' of child team '{child.id}' is also "
                    f"listed as a member of parent team '{team.id}'; parent "
                    "membership is implicit",
                    child.id,
                )
        if not team.mandate:
            warn("team_without_mandate", f"team '{team.id}' declares no mandate", team.id)

    # An agent must have exactly one home team.
    homes: dict[str, list[str]] = {}
    for team in teams:
        for member in team.members:
            homes.setdefault(member.id, []).append(team.id)
    for agent_id, owners in homes.items():
        # A leader appears as leader of its own team and member of the parent;
        # membership is still recorded once, so >1 is a genuine error.
        if len(owners) > 1:
            err(
                "multiple_home_teams",
                f"agent '{agent_id}' is a member of {owners}; exactly one is allowed",
                agent_id,
            )

    # -- references --------------------------------------------------------
    for agent in agents:
        for assignment in agent.roles:
            role = spec.role(assignment.role)
            if role is None:
                err("unknown_role", f"agent '{agent.id}' references unknown role "
                    f"'{assignment.role}'", agent.id)
            elif role.kind != "agent":
                err("wrong_role_kind", f"agent '{agent.id}' is assigned team role "
                    f"'{role.id}'", agent.id)
        for cap_id in agent.capabilities:
            if spec.capability(cap_id) is None:
                err("unknown_capability", f"agent '{agent.id}' references unknown "
                    f"capability '{cap_id}'", agent.id)
        for wf_id in agent.workflows:
            if not any(w.id == wf_id for w in spec.workflows):
                err("unknown_workflow", f"agent '{agent.id}' references unknown "
                    f"workflow '{wf_id}'", agent.id)
        for peer in agent.peers:
            if peer not in agent_ids:
                err("unknown_peer", f"agent '{agent.id}' references unknown peer "
                    f"'{peer}'", agent.id)
        if agent.environment:
            env = spec.environment(agent.environment.environment)
            if env is None:
                err("unknown_environment", f"agent '{agent.id}' references unknown "
                    f"environment '{agent.environment.environment}'", agent.id)
            else:
                _check_narrowing(agent.id, env, agent.environment, err)
        if agent.human is None:
            warn("agent_without_human", f"agent '{agent.id}' has no human counterpart",
                 agent.id, strict=True)

    for team in teams:
        for assignment in team.roles:
            role = spec.role(assignment.role)
            if role is None:
                err("unknown_role", f"team '{team.id}' references unknown role "
                    f"'{assignment.role}'", team.id)
            elif role.kind != "team":
                err("wrong_role_kind", f"team '{team.id}' is assigned agent role "
                    f"'{role.id}'", team.id)

    for cap in spec.capabilities:
        for dc_id in cap.data_classes:
            if spec.data_class(dc_id) is None:
                err("unknown_data_class", f"capability '{cap.id}' references unknown "
                    f"data class '{dc_id}'", cap.id)

    for env in spec.environments:
        for dc_id in env.mounts:
            dc = spec.data_class(dc_id)
            if dc is None:
                err("unknown_data_class", f"environment '{env.id}' mounts unknown "
                    f"data class '{dc_id}'", env.id)
            elif dc.allowed_environments and env.id not in dc.allowed_environments:
                err(
                    "placement_violation",
                    f"data class '{dc_id}' may not be mounted in environment "
                    f"'{env.id}'",
                    env.id,
                )
        if env.network is NetworkPosture.NONE and env.egress_allowlist:
            err("egress_on_isolated_env", f"environment '{env.id}' has no network but "
                "declares an egress allowlist", env.id)

    for dc in spec.data_classes:
        if dc.scope is SharingScope.PROTECTED and not dc.groups:
            err("protected_without_groups", f"data class '{dc.id}' is protected but "
                "names no groups", dc.id)

    # -- least privilege (ADR-0008) ---------------------------------------
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

    # An agent's effective permissions may not exceed its team's grant plus its
    # own roles — inheritance narrows, never widens (ADR-0008).
    for team in teams:
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

    # -- capability coverage ----------------------------------------------
    used = {c for a in agents for c in a.capabilities}
    used |= {c for r in spec.roles for c in r.capabilities}
    for cap in spec.capabilities:
        if cap.id not in used:
            warn("unused_capability", f"capability '{cap.id}' is declared but unused",
                 cap.id)

    return out


def _responsibility_mentions(text: str, cap_id: str, cap) -> bool:
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


def _check_narrowing(agent_id: str, env, override, err) -> None:
    """An environment override may only make the class stricter (ADR-0009)."""
    order = [
        NetworkPosture.NONE,
        NetworkPosture.ALLOWLIST,
        NetworkPosture.INTERNAL,
        NetworkPosture.OPEN,
    ]
    if override.timeout_seconds is not None and override.timeout_seconds > env.timeout_seconds:
        err(
            "environment_widened",
            f"agent '{agent_id}' requests a longer timeout than environment "
            f"'{env.id}' permits",
            agent_id,
        )
    if override.network is not None and order.index(override.network) > order.index(
        env.network
    ):
        err(
            "environment_widened",
            f"agent '{agent_id}' requests network '{override.network.value}' but "
            f"environment '{env.id}' allows only '{env.network.value}'",
            agent_id,
        )
    if override.egress_allowlist:
        extra = set(override.egress_allowlist) - set(env.egress_allowlist)
        if extra or env.network is NetworkPosture.NONE:
            detail = sorted(extra) if extra else "on an isolated environment"
            err(
                "environment_widened",
                f"agent '{agent_id}' requests egress {detail} not permitted by "
                f"'{env.id}'",
                agent_id,
            )


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


def matches(pattern: str, value: str) -> bool:
    return fnmatch.fnmatch(value, pattern)
