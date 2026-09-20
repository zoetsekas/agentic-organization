"""Phase 1 of compilation: Spec → Intermediate Representation (ADR-0005).

Everything a target could otherwise get wrong is resolved exactly once here:
team inheritance, role expansion, effective permissions, environment narrowing,
data access, delegation edges, workload identities and the provider-neutral
resource set. Targets consume the IR and may only render it.

The IR is a published artifact — `orgagents spec ir` prints it, and it is the
thing a security reviewer should read.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from ..spec.binding import TargetBinding, default_binding
from ..spec.model import (
    Action,
    AgentSpec,
    Capability,
    ChannelClass,
    DataClass,
    EnvironmentClass,
    HumanCounterpart,
    Observability,
    Permission,
    PolicyRule,
    ResourceKind,
    SharingScope,
    SystemSpec,
    Team,
    WorkflowSpec,
)

IR_VERSION = "1.0.0"

# The provider-neutral resource set every cloud target maps from (ADR-0012).
NEUTRAL_RESOURCES = (
    "compute_service",
    "job_runner",
    "state_store",
    "object_store",
    "secret",
    "message_bus",
    "identity",
    "policy_binding",
    "network_boundary",
    "observability_sink",
)


class ResponsibilityIR(BaseModel):
    text: str
    source_role: str


class DataAccessIR(BaseModel):
    data_class: str
    scope: SharingScope
    groups: list[str] = Field(default_factory=list)
    read: bool = False
    write: bool = False


class IdentityIR(BaseModel):
    """One workload identity per agent (ADR-0015)."""

    id: str
    agent_id: str
    display_name: str
    permissions: list[str] = Field(default_factory=list)
    secret_refs: list[str] = Field(default_factory=list)


class ResourceIR(BaseModel):
    kind: str
    id: str
    owner: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class AgentIR(BaseModel):
    id: str
    name: str
    description: str = ""
    team_id: str
    team_path: list[str] = Field(default_factory=list)
    leader_of: Optional[str] = None
    reports_to: Optional[str] = None
    escalates_to: Optional[str] = None
    delegates_to: list[str] = Field(default_factory=list)
    shared_service: bool = False
    human: Optional[HumanCounterpart] = None
    responsibilities: list[ResponsibilityIR] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    permissions: list[Permission] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    data_access: list[DataAccessIR] = Field(default_factory=list)
    environment: Optional[EnvironmentClass] = None
    workflows: list[str] = Field(default_factory=list)
    channels: list[ChannelClass] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    identity: Optional[IdentityIR] = None
    max_delegation_depth: int = 3
    requires_approval_for: list[str] = Field(default_factory=list)
    runtime_adapter: str = "echo"
    model: dict[str, Any] = Field(default_factory=dict)

    def system_prompt(self) -> str:
        """Operating instructions composed from the org and the role contracts."""
        lines = [f"You are {self.name}."]
        if self.description:
            lines.append(self.description)
        lines += ["", "## Accountability"]
        for r in self.responsibilities:
            lines.append(f"- {r.text}  _(role: {r.source_role})_")
        lines += [
            "",
            "## Organization",
            f"- Team: {' / '.join(self.team_path) or self.team_id}",
            f"- Leader of: {self.leader_of or 'not a leader'}",
            f"- Reports to: {self.reports_to or 'no one'}",
            f"- May delegate to: {', '.join(self.delegates_to) or 'no one'}",
            f"- Escalates to: {self.escalates_to or 'the human counterpart'}",
        ]
        if self.human:
            lines += [
                f"- Human counterpart: {self.human.name} "
                f"({self.human.role_title or 'owner'}), {self.human.contact}",
                f"- Always seek approval before: "
                f"{', '.join(self.requires_approval_for) or 'nothing'}",
            ]
        lines += ["", "## Operating rules",
                  "- Prefer delegating to a team member whose role covers the task.",
                  "- Use an encoded workflow for any process that must be auditable.",
                  "- You may not widen your own access; ask your leader instead."]
        return "\n".join(lines)


class TeamIR(BaseModel):
    id: str
    name: str
    path: list[str] = Field(default_factory=list)
    parent_id: Optional[str] = None
    leader_agent_id: str = ""
    member_ids: list[str] = Field(default_factory=list)
    child_team_ids: list[str] = Field(default_factory=list)
    mandate: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    permissions: list[Permission] = Field(default_factory=list)


class SystemIR(BaseModel):
    ir_version: str = IR_VERSION
    target: str = "local"
    name: str
    spec_version: str
    environment: str = "development"
    teams: list[TeamIR] = Field(default_factory=list)
    agents: list[AgentIR] = Field(default_factory=list)
    data_classes: list[DataClass] = Field(default_factory=list)
    environments: list[EnvironmentClass] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    policies: list[PolicyRule] = Field(default_factory=list)
    workflows: list[WorkflowSpec] = Field(default_factory=list)
    observability: Observability = Field(default_factory=Observability)
    identities: list[IdentityIR] = Field(default_factory=list)
    resources: list[ResourceIR] = Field(default_factory=list)
    binding: TargetBinding = Field(default_factory=lambda: default_binding("local"))

    def agent(self, agent_id: str) -> Optional[AgentIR]:
        return next((a for a in self.agents if a.id == agent_id), None)

    def permission_map(self) -> dict[str, list[Permission]]:
        return {a.id: a.permissions for a in self.agents}


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def _dedupe(perms: list[Permission]) -> list[Permission]:
    seen: dict[str, Permission] = {}
    for p in perms:
        seen.setdefault(p.key(), p)
    return list(seen.values())


def _role_permissions(spec: SystemSpec, assignments, kind: str) -> tuple[
    list[Permission], list[str], list[str], list[ResponsibilityIR]
]:
    perms: list[Permission] = []
    caps: list[str] = []
    role_ids: list[str] = []
    responsibilities: list[ResponsibilityIR] = []
    for assignment in assignments:
        role = spec.role(assignment.role)
        if role is None or role.kind != kind:
            continue
        role_ids.append(role.id)
        withheld = set(assignment.withhold)
        for perm in role.permissions:
            if perm.key() in withheld:
                continue          # assignment-site constraints narrow only
            merged = perm.model_copy(deep=True)
            # Role- and assignment-level conditions both apply.
            merged.conditions = {**role.constraints, **perm.conditions,
                                 **assignment.conditions}
            perms.append(merged)
        caps.extend(role.capabilities)
        responsibilities.extend(
            ResponsibilityIR(text=t, source_role=role.id) for t in role.responsibilities
        )
    return perms, caps, role_ids, responsibilities


def _build_teams(spec: SystemSpec) -> tuple[list[TeamIR], dict[str, TeamIR]]:
    teams: list[TeamIR] = []
    index: dict[str, TeamIR] = {}

    def visit(team: Team, path: list[str], parent: Optional[str]) -> None:
        perms, _caps, _ids, _resp = _role_permissions(spec, team.roles, "team")
        inherited = index[parent].permissions if parent and parent in index else []
        ir = TeamIR(
            id=team.id,
            name=team.name or team.id,
            path=path + [team.name or team.id],
            parent_id=parent,
            leader_agent_id=team.leader,
            member_ids=[m.id for m in team.members],
            child_team_ids=[c.id for c in team.teams],
            mandate=team.mandate,
            groups=sorted({*team.groups, *(index[parent].groups if parent in index else [])})
            if parent
            else list(team.groups),
            permissions=_dedupe(list(inherited) + perms),
        )
        teams.append(ir)
        index[team.id] = ir
        for child in team.teams:
            visit(child, ir.path, team.id)

    visit(spec.organization, [], None)
    return teams, index


def _delegation_targets(
    spec: SystemSpec, agent: AgentSpec, team: Team, index: dict[str, TeamIR]
) -> list[str]:
    """Who this agent may hand work to (ADR-0006)."""
    targets: list[str] = []
    if team.leader == agent.id:
        targets += [m.id for m in team.members if m.id != agent.id]
        targets += [c.leader for c in team.teams if c.leader]
    targets += list(agent.peers)
    targets += [a.id for a in spec.agents() if a.shared_service and a.id != agent.id]
    return sorted(dict.fromkeys(targets))


def build_ir(
    spec: SystemSpec,
    *,
    target: str = "local",
    binding: Optional[TargetBinding] = None,
) -> SystemIR:
    """Resolve a validated spec into the IR every target consumes."""
    bound = binding or default_binding(target)
    teams, index = _build_teams(spec)
    team_by_agent: dict[str, Team] = {
        m.id: t for t in spec.teams() for m in t.members
    }
    leader_of: dict[str, str] = {t.leader: t.id for t in spec.teams() if t.leader}

    agents: list[AgentIR] = []
    identities: list[IdentityIR] = []

    for agent in spec.agents():
        team = team_by_agent[agent.id]
        team_ir = index[team.id]

        own_perms, own_caps, role_ids, responsibilities = _role_permissions(
            spec, agent.roles, "agent"
        )
        # Team grants flow down; the agent's own roles add to them. Neither path
        # can exceed what was granted upstream, which validation enforces.
        permissions = _dedupe(list(team_ir.permissions) + own_perms)

        capability_ids = sorted(dict.fromkeys([*own_caps, *agent.capabilities]))
        capabilities = [c for c in (spec.capability(i) for i in capability_ids) if c]

        # Data access derives from the capabilities the agent actually holds.
        access: dict[str, DataAccessIR] = {}
        for cap in capabilities:
            for dc_id in cap.data_classes:
                dc = spec.data_class(dc_id)
                if dc is None:
                    continue
                entry = access.setdefault(
                    dc_id,
                    DataAccessIR(data_class=dc_id, scope=dc.scope, groups=dc.groups),
                )
                if cap.action in (Action.READ, Action.QUERY):
                    entry.read = True
                if cap.action in (Action.WRITE, Action.PUBLISH):
                    entry.write = True

        environment = None
        if agent.environment:
            base = spec.environment(agent.environment.environment)
            if base:
                environment = base.narrow(agent.environment)

        reports_to = None
        if team.leader and team.leader != agent.id:
            reports_to = team.leader
        elif team_ir.parent_id:
            parent = next((t for t in spec.teams() if t.id == team_ir.parent_id), None)
            reports_to = parent.leader if parent else None

        overrides = bound.agent_overrides.get(agent.id, {})
        identity = IdentityIR(
            id=f"id-{agent.id}",
            agent_id=agent.id,
            display_name=f"{agent.name or agent.id} workload identity",
            permissions=[p.key() for p in permissions],
            secret_refs=sorted(
                {c.secret_ref for c in capabilities if c.secret_ref}
                | set(environment.secret_refs if environment else [])
            ),
        )
        identities.append(identity)

        agents.append(
            AgentIR(
                id=agent.id,
                name=agent.name or agent.id,
                description=agent.description,
                team_id=team.id,
                team_path=team_ir.path,
                leader_of=leader_of.get(agent.id),
                reports_to=reports_to,
                escalates_to=reports_to,
                delegates_to=_delegation_targets(spec, agent, team, index),
                shared_service=agent.shared_service,
                human=agent.human,
                responsibilities=responsibilities,
                role_ids=role_ids,
                permissions=permissions,
                capabilities=capabilities,
                data_access=list(access.values()),
                environment=environment,
                workflows=list(agent.workflows),
                channels=list(agent.channels),
                groups=list(team_ir.groups),
                identity=identity,
                max_delegation_depth=agent.max_delegation_depth,
                requires_approval_for=sorted(
                    {*(agent.human.approves if agent.human else []),
                     *[c.id for c in capabilities if c.constraints.requires_approval]}
                ),
                runtime_adapter=overrides.get("adapter", bound.runtime.adapter),
                model={**bound.model.model_dump(), **overrides.get("model", {})},
            )
        )

    ir = SystemIR(
        target=target,
        name=spec.metadata.name,
        spec_version=spec.metadata.spec_version,
        environment=spec.metadata.environment,
        teams=teams,
        agents=agents,
        data_classes=spec.data_classes,
        environments=spec.environments,
        capabilities=spec.capabilities,
        policies=spec.policies,
        workflows=spec.workflows,
        observability=spec.observability,
        identities=identities,
        binding=bound,
    )
    ir.resources = build_resources(ir)
    return ir


def build_resources(ir: SystemIR) -> list[ResourceIR]:
    """The provider-neutral resource set the cloud targets map from."""
    resources: list[ResourceIR] = [
        ResourceIR(kind="state_store", id=f"{ir.name}-state",
                   attributes={"engine": "relational", "purpose": "sessions, org, catalog"}),
        ResourceIR(kind="object_store", id=f"{ir.name}-artifacts",
                   attributes={"purpose": "session artifacts, sandbox outputs"}),
        ResourceIR(kind="message_bus", id=f"{ir.name}-bus",
                   attributes={"purpose": "agent-to-agent asynchronous messaging"}),
        ResourceIR(kind="observability_sink", id=f"{ir.name}-telemetry",
                   attributes={"traces": ir.observability.traces,
                               "metrics": ir.observability.metrics,
                               "retention_days": ir.observability.retention_days}),
    ]
    for agent in ir.agents:
        resources.append(
            ResourceIR(kind="compute_service", id=f"agent-{agent.id}", owner=agent.id,
                       attributes={"adapter": agent.runtime_adapter,
                                   "team": agent.team_id})
        )
        if agent.identity:
            resources.append(
                ResourceIR(kind="identity", id=agent.identity.id, owner=agent.id,
                           attributes={"permissions": agent.identity.permissions})
            )
            resources.append(
                ResourceIR(kind="policy_binding", id=f"pb-{agent.id}", owner=agent.id,
                           attributes={"identity": agent.identity.id,
                                       "permissions": agent.identity.permissions})
            )
            for ref in agent.identity.secret_refs:
                resources.append(
                    ResourceIR(kind="secret", id=ref, owner=agent.id,
                               attributes={"accessor": agent.identity.id})
                )
        if agent.environment:
            resources.append(
                ResourceIR(kind="job_runner", id=f"env-{agent.id}", owner=agent.id,
                           attributes={"environment": agent.environment.id,
                                       "tier": agent.environment.tier.value,
                                       "timeout_seconds": agent.environment.timeout_seconds})
            )
            resources.append(
                ResourceIR(kind="network_boundary", id=f"net-{agent.id}", owner=agent.id,
                           attributes={"posture": agent.environment.network.value,
                                       "allowlist": agent.environment.egress_allowlist})
            )
    # Deduplicate shared secrets.
    seen: set[tuple[str, str]] = set()
    unique: list[ResourceIR] = []
    for r in resources:
        key = (r.kind, r.id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique
