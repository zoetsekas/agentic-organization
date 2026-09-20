"""The System Spec — the implementation-neutral definition of an agentic system.

ADR-0004 makes this document the single source of truth. It is written entirely
in capability terms: what an agent is accountable for, what class of data it may
touch, what kind of isolation its work needs, who it may reach. It names no
vendor, SDK, cloud provider or resource type — every such choice lives in a
`Binding` (see `binding.py`), which is target-scoped by design.

Block layout::

    metadata          name, spec_version, owner
    data_classes      classification driving access, placement and egress
    capabilities      abstract access needs, later bound to MCP servers
    environments      abstract execution classes (the isolation boundary)
    roles             responsibilities + capabilities + permissions
    policies          allow/deny rules with attribute conditions
    organization      the recursive team tree with one leader each
    workflows         declarative process graphs
    channels          abstract communication surfaces
    observability     required signals, retention and alert conditions
    deployment        which targets this system is compiled for
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

SPEC_VERSION = "1.0.0"


# --------------------------------------------------------------------------
# Vocabulary — all abstract, none of it vendor-shaped
# --------------------------------------------------------------------------


class SharingScope(str, Enum):
    """How widely a data class may travel."""

    PRIVATE = "private"        # the owning agent and its human counterpart
    PROTECTED = "protected"    # the groups named on the record
    PUBLIC = "public"          # organization-wide; any agent may contribute


class ResourceTier(str, Enum):
    """Abstract size of an execution environment."""

    MINIMAL = "minimal"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    ACCELERATED = "accelerated"   # hardware acceleration required


class NetworkPosture(str, Enum):
    NONE = "none"
    ALLOWLIST = "allowlist"
    INTERNAL = "internal"
    OPEN = "open"


class Persistence(str, Enum):
    EPHEMERAL = "ephemeral"
    SESSION = "session"
    AGENT = "agent"


class ToolchainClass(str, Enum):
    """Categories of tooling an environment may need, not concrete images."""

    NONE = "none"
    SCRIPTING = "scripting"
    DATA_ANALYSIS = "data_analysis"
    SOFTWARE_BUILD = "software_build"
    BROWSER = "browser"
    DOCUMENT = "document"
    MODEL_TRAINING = "model_training"
    NETWORK_CLIENT = "network_client"


class ResourceKind(str, Enum):
    """Kinds of resource a permission or capability can address."""

    DATA_CLASS = "data_class"
    CAPABILITY = "capability"
    AGENT = "agent"
    TEAM = "team"
    WORKFLOW = "workflow"
    ENVIRONMENT = "environment"
    CHANNEL = "channel"


class Action(str, Enum):
    READ = "read"
    WRITE = "write"
    QUERY = "query"
    INVOKE = "invoke"
    DELEGATE = "delegate"
    PUBLISH = "publish"
    APPROVE = "approve"
    ADMINISTER = "administer"


class Effect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class ChannelClass(str, Enum):
    """Abstract communication surfaces; the binding picks the product."""

    DIRECT = "direct"                    # synchronous agent-to-agent call
    ASYNC_BUS = "async_bus"              # internal message bus
    TEAM_CHAT = "team_chat"              # human-facing chat surface
    MAIL = "mail"
    WEBHOOK = "webhook"


class RuntimeRequirement(str, Enum):
    """What the agent loop must support — not which framework provides it."""

    PLANNING = "planning"
    SUBAGENTS = "subagents"
    HANDOFF = "handoff"
    STRUCTURED_OUTPUT = "structured_output"
    LONG_CONTEXT = "long_context"


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------


class Metadata(BaseModel):
    name: str
    spec_version: str = SPEC_VERSION
    version: str = "0.1.0"
    description: str = ""
    owner: str = ""
    environment: Literal["development", "staging", "production"] = "development"
    labels: dict[str, str] = Field(default_factory=dict)


class DataClass(BaseModel):
    """A classification that drives access, placement and egress (ADR-0017)."""

    id: str
    description: str = ""
    scope: SharingScope = SharingScope.PRIVATE
    groups: list[str] = Field(default_factory=list)
    # Placement constraints checked against the chosen environment class.
    allowed_environments: list[str] = Field(default_factory=list)
    may_leave_region: bool = True
    may_appear_in_traces: bool = True
    retention_days: Optional[int] = None


class CapabilityConstraint(BaseModel):
    """Limits enforced at the capability boundary, never by the model."""

    max_rows: Optional[int] = None
    masked_fields: list[str] = Field(default_factory=list)
    allowed_operations: list[str] = Field(default_factory=list)
    resource_scope: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    rate_per_minute: Optional[int] = None


class Capability(BaseModel):
    """An abstract access need, bound to an MCP server per target (ADR-0010)."""

    id: str
    description: str = ""
    action: Action = Action.QUERY
    # What class of thing this reaches, in the customer's own vocabulary.
    resource_class: str = ""
    data_classes: list[str] = Field(default_factory=list)
    constraints: CapabilityConstraint = Field(default_factory=CapabilityConstraint)
    # Named reference resolved by the platform at bind time; never a value.
    secret_ref: Optional[str] = None


class EnvironmentClass(BaseModel):
    """A reviewed, abstract execution profile (ADR-0009)."""

    id: str
    description: str = ""
    toolchains: list[ToolchainClass] = Field(default_factory=lambda: [ToolchainClass.NONE])
    tier: ResourceTier = ResourceTier.MINIMAL
    network: NetworkPosture = NetworkPosture.NONE
    egress_allowlist: list[str] = Field(default_factory=list)
    mounts: list[str] = Field(default_factory=list)   # data class ids
    persistence: Persistence = Persistence.EPHEMERAL
    timeout_seconds: int = 300
    secret_refs: list[str] = Field(default_factory=list)

    def narrow(self, override: "EnvironmentOverride") -> "EnvironmentClass":
        """Apply an agent's override, which may only make the class stricter."""
        out = self.model_copy(deep=True)
        if override.timeout_seconds is not None:
            out.timeout_seconds = min(out.timeout_seconds, override.timeout_seconds)
        if override.drop_mounts:
            out.mounts = [m for m in out.mounts if m not in override.drop_mounts]
        if override.network is not None:
            order = [
                NetworkPosture.NONE,
                NetworkPosture.ALLOWLIST,
                NetworkPosture.INTERNAL,
                NetworkPosture.OPEN,
            ]
            if order.index(override.network) < order.index(out.network):
                out.network = override.network
                if override.network is NetworkPosture.NONE:
                    out.egress_allowlist = []
        # Egress may only be reduced, never extended.
        if override.egress_allowlist is not None:
            out.egress_allowlist = [
                e for e in out.egress_allowlist if e in override.egress_allowlist
            ]
        if out.network is NetworkPosture.NONE:
            out.egress_allowlist = []
        return out


class EnvironmentOverride(BaseModel):
    """An agent's narrowing of its environment class. Widening is rejected."""

    environment: str
    timeout_seconds: Optional[int] = None
    network: Optional[NetworkPosture] = None
    egress_allowlist: Optional[list[str]] = None
    drop_mounts: list[str] = Field(default_factory=list)


class Permission(BaseModel):
    """`(action, resource)` with optional conditions (ADR-0008)."""

    action: Action
    resource_kind: ResourceKind
    resource: str = "*"          # id or glob within the kind
    conditions: dict[str, Any] = Field(default_factory=dict)

    def key(self) -> str:
        return f"{self.action.value}:{self.resource_kind.value}:{self.resource}"


class PolicyRule(BaseModel):
    """An explicit allow or deny; deny always wins and cannot be overridden."""

    id: str
    effect: Effect
    description: str = ""
    actions: list[Action] = Field(default_factory=list)
    resource_kinds: list[ResourceKind] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=lambda: ["*"])
    # Attribute conditions evaluated against the request context.
    subjects: list[str] = Field(default_factory=lambda: ["*"])   # agent/team/role ids
    # `conditions` must hold for the rule to apply; `unless` disapplies it.
    # A deny with `unless` is the "deny except here" shape, written explicitly
    # rather than by negating conditions, so a reviewer cannot misread it.
    conditions: dict[str, Any] = Field(default_factory=dict)
    unless: dict[str, Any] = Field(default_factory=dict)


class Role(BaseModel):
    """Responsibilities, capabilities and permissions as one contract (ADR-0007)."""

    id: str
    title: str = ""
    version: str = "0.1.0"
    kind: Literal["agent", "team"] = "agent"
    responsibilities: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    permissions: list[Permission] = Field(default_factory=list)
    # Conditions attached to everything this role grants.
    constraints: dict[str, Any] = Field(default_factory=dict)


class RoleAssignment(BaseModel):
    """A role bound to an agent or team; may narrow, never widen."""

    role: str
    # Permission keys from the role to withhold at this assignment site.
    withhold: list[str] = Field(default_factory=list)
    conditions: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def coerce(cls, value: "str | RoleAssignment") -> "RoleAssignment":
        return cls(role=value) if isinstance(value, str) else value


class HumanCounterpart(BaseModel):
    """The accountable person behind an agent."""

    name: str
    contact: str
    role_title: str = ""
    approves: list[str] = Field(default_factory=list)   # capability/action ids
    notify_on: list[ChannelClass] = Field(default_factory=lambda: [ChannelClass.MAIL])


class AgentSpec(BaseModel):
    """One agent: who it answers to, what it is for, what it may reach."""

    id: str
    name: str = ""
    description: str = ""
    roles: list[RoleAssignment] = Field(default_factory=list)
    human: Optional[HumanCounterpart] = None
    environment: Optional[EnvironmentOverride] = None
    capabilities: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    channels: list[ChannelClass] = Field(
        default_factory=lambda: [ChannelClass.DIRECT, ChannelClass.ASYNC_BUS]
    )
    # Lateral links; delegation otherwise follows the team tree.
    peers: list[str] = Field(default_factory=list)
    # Shared services are callable from anywhere in the organization.
    shared_service: bool = False
    runtime_requirements: list[RuntimeRequirement] = Field(default_factory=list)
    max_delegation_depth: int = 3
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("roles", mode="before")
    @classmethod
    def _coerce_roles(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [RoleAssignment.coerce(item) for item in v]
        return v


class Team(BaseModel):
    """A team with exactly one leader, members, and nested child teams (ADR-0006)."""

    id: str
    name: str = ""
    mandate: list[str] = Field(default_factory=list)
    leader: str = ""                      # agent id; must also be a member
    members: list[AgentSpec] = Field(default_factory=list)
    teams: list["Team"] = Field(default_factory=list)
    roles: list[RoleAssignment] = Field(default_factory=list)   # team roles
    groups: list[str] = Field(default_factory=list)             # protected-data reach
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("roles", mode="before")
    @classmethod
    def _coerce_roles(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [RoleAssignment.coerce(item) for item in v]
        return v

    def walk(self) -> "list[Team]":
        out = [self]
        for child in self.teams:
            out.extend(child.walk())
        return out

    def all_agents(self) -> list[AgentSpec]:
        return [a for t in self.walk() for a in t.members]


Team.model_rebuild()


class WorkflowSpec(BaseModel):
    """A declarative process graph (see `orgagents.workflows`)."""

    id: str
    name: str = ""
    description: str = ""
    graph: dict[str, Any] = Field(default_factory=dict)
    interrupt_before: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)


class ChannelSpec(BaseModel):
    id: str
    channel_class: ChannelClass = ChannelClass.ASYNC_BUS
    description: str = ""
    address: str = ""
    members: list[str] = Field(default_factory=list)   # agent or team ids


class AlertCondition(BaseModel):
    id: str
    description: str = ""
    # A predicate over the standard metric set, e.g.
    # "sessions.failed / sessions.total > 0.2".
    expression: str = ""
    severity: Literal["info", "warning", "error", "critical"] = "warning"


class Observability(BaseModel):
    """The contract every target must satisfy (ADR-0016)."""

    traces: bool = True
    metrics: bool = True
    logs: bool = True
    propagate_across_delegation: bool = True
    retention_days: int = 30
    sample_rate: float = 1.0
    redact_data_classes: list[str] = Field(default_factory=list)
    alerts: list[AlertCondition] = Field(default_factory=list)


class DeploymentSpec(BaseModel):
    """Which targets this system compiles for; bindings live separately."""

    targets: list[str] = Field(default_factory=lambda: ["local"])
    regions: list[str] = Field(default_factory=list)
    high_availability: bool = False
    binding: Optional[str] = None        # path or id of the binding document


class SystemSpec(BaseModel):
    """The whole implementation-neutral definition."""

    metadata: Metadata
    data_classes: list[DataClass] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    environments: list[EnvironmentClass] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    policies: list[PolicyRule] = Field(default_factory=list)
    organization: Team = Field(default_factory=lambda: Team(id="root", name="root"))
    workflows: list[WorkflowSpec] = Field(default_factory=list)
    channels: list[ChannelSpec] = Field(default_factory=list)
    observability: Observability = Field(default_factory=Observability)
    deployment: DeploymentSpec = Field(default_factory=DeploymentSpec)

    # -- lookups -----------------------------------------------------------

    def teams(self) -> list[Team]:
        return self.organization.walk()

    def agents(self) -> list[AgentSpec]:
        return self.organization.all_agents()

    def agent(self, agent_id: str) -> Optional[AgentSpec]:
        return next((a for a in self.agents() if a.id == agent_id), None)

    def team_of(self, agent_id: str) -> Optional[Team]:
        return next(
            (t for t in self.teams() if any(m.id == agent_id for m in t.members)), None
        )

    def role(self, role_id: str) -> Optional[Role]:
        return next((r for r in self.roles if r.id == role_id), None)

    def environment(self, env_id: str) -> Optional[EnvironmentClass]:
        return next((e for e in self.environments if e.id == env_id), None)

    def capability(self, cap_id: str) -> Optional[Capability]:
        return next((c for c in self.capabilities if c.id == cap_id), None)

    def data_class(self, dc_id: str) -> Optional[DataClass]:
        return next((d for d in self.data_classes if d.id == dc_id), None)
