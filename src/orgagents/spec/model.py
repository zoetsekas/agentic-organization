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


class TriggerKind(str, Enum):
    """What starts a run (ADR-0020)."""

    SCHEDULE = "schedule"      # a cadence
    EVENT = "event"            # an abstract event class from a source system
    WEBHOOK = "webhook"        # an inbound call
    MESSAGE = "message"        # a human message on a channel
    MANUAL = "manual"          # a person or agent starts it


class OverlapPolicy(str, Enum):
    """What to do when a run is still going and the next one is due."""

    SKIP = "skip"
    QUEUE = "queue"
    CANCEL_PREVIOUS = "cancel_previous"
    ALLOW = "allow"


class CatchUpPolicy(str, Enum):
    """What to do about runs missed while the system was down."""

    SKIP_MISSED = "skip_missed"
    RUN_ONCE = "run_once"
    RUN_ALL = "run_all"


class ChannelPurpose(str, Enum):
    """Why an agent contacts a human on a channel (ADR-0021)."""

    NOTIFY = "notify"
    APPROVE = "approve"
    HANDOFF = "handoff"
    REPORT = "report"
    ASK = "ask"


class FlowKind(str, Enum):
    """A declared directional interaction between agents (ADR-0024)."""

    DELEGATE = "delegate"      # may hand work over and expect completion
    CONSULT = "consult"        # may ask, may not instruct
    NOTIFY = "notify"          # may inform, expects no reply
    ESCALATE = "escalate"      # may raise for decision


class KnowledgeKind(str, Enum):
    """Classes of grounding source, not products (ADR-0023)."""

    DOCUMENT_STORE = "document_store"
    WIKI = "wiki"
    TICKETING = "ticketing"
    CRM = "crm"
    MAILBOX = "mailbox"
    CODE_REPOSITORY = "code_repository"
    DATA_WAREHOUSE = "data_warehouse"
    WEB = "web"


class LifecycleStage(str, Enum):
    DRAFT = "draft"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    RETIRED = "retired"


class GateRequirement(str, Enum):
    """What must be true before an agent advances a stage (ADR-0022)."""

    EVALUATIONS_PASSED = "evaluations_passed"
    HUMAN_APPROVAL = "human_approval"
    SECURITY_REVIEW = "security_review"
    COST_WITHIN_BUDGET = "cost_within_budget"
    OWNER_ASSIGNED = "owner_assigned"
    PERMISSIONS_REVIEWED = "permissions_reviewed"


class BreachAction(str, Enum):
    WARN = "warn"
    THROTTLE = "throttle"
    HALT = "halt"


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
    knowledge: list[str] = Field(default_factory=list)
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


class Cadence(BaseModel):
    """When a schedule fires, in a form both humans and targets can read.

    `expression` is a five-field cron expression or a plain-English interval
    ("every 15 minutes", "every 2 hours"). Neither names a scheduler product.
    """

    expression: str
    timezone: str = "UTC"


class FailurePolicy(BaseModel):
    """What happens when a triggered run fails (ADR-0020)."""

    retries: int = 2
    backoff_seconds: int = 60
    escalate_after_failures: int = 2
    notify_channel: Optional[str] = None
    halt_after_consecutive_failures: int = 5


class TriggerSpec(BaseModel):
    """Something that starts a run without a person asking (ADR-0020).

    A triggered run carries exactly the same permission envelope as interactive
    work: it is the agent's own capabilities and identity, never a separate
    service account with broader reach.
    """

    id: str
    description: str = ""
    kind: TriggerKind = TriggerKind.SCHEDULE
    agent: str = ""                       # the agent that runs
    workflow: Optional[str] = None        # optionally a specific workflow
    cadence: Optional[Cadence] = None     # required for kind=schedule
    event_class: str = ""                 # abstract event, for kind=event
    filters: dict[str, Any] = Field(default_factory=dict)
    channel: Optional[str] = None         # for kind=message
    input: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    overlap: OverlapPolicy = OverlapPolicy.SKIP
    catch_up: CatchUpPolicy = CatchUpPolicy.SKIP_MISSED
    max_runtime_seconds: int = 900
    requires_approval: bool = False
    deliver_to: list[str] = Field(default_factory=list)   # channel ids
    failure: FailurePolicy = Field(default_factory=FailurePolicy)


class WorkflowSpec(BaseModel):
    """A declarative process graph (see `orgagents.workflows`)."""

    id: str
    name: str = ""
    description: str = ""
    graph: dict[str, Any] = Field(default_factory=dict)
    interrupt_before: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)


class WorkingHours(BaseModel):
    """When the humans on a channel are actually available."""

    timezone: str = "UTC"
    days: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])  # 1=Monday
    start_hour: int = 9
    end_hour: int = 17
    holidays: list[str] = Field(default_factory=list)   # ISO dates


class EscalationStep(BaseModel):
    """Who to try next when nobody answers, and when."""

    after_minutes: int
    notify: str                    # a human contact, agent id or team id
    channel: Optional[str] = None  # channel id; defaults to the originating one
    note: str = ""


class ChannelSpec(BaseModel):
    """A communication surface, abstract in the spec and bound per target.

    Human-facing channels carry a contract (ADR-0021): what they are for, when
    the people on them are available, how fast a reply is expected, and who is
    tried next when it does not come.
    """

    id: str
    channel_class: ChannelClass = ChannelClass.ASYNC_BUS
    description: str = ""
    address: str = ""
    members: list[str] = Field(default_factory=list)   # agent or team ids
    human_facing: bool = False
    purposes: list[ChannelPurpose] = Field(default_factory=list)
    working_hours: Optional[WorkingHours] = None
    response_sla_minutes: Optional[int] = None
    escalation: list[EscalationStep] = Field(default_factory=list)
    # Data classes that must never be written to this channel.
    forbid_data_classes: list[str] = Field(default_factory=list)
    out_of_hours: Literal["queue", "escalate", "notify_anyway"] = "queue"


class AlertCondition(BaseModel):
    id: str
    description: str = ""
    # A predicate over the standard metric set, e.g.
    # "sessions.failed / sessions.total > 0.2".
    expression: str = ""
    severity: Literal["info", "warning", "error", "critical"] = "warning"


class InteractionFlow(BaseModel):
    """A declared directional edge between agents (ADR-0024).

    The org tree already implies delegation down a hierarchy. Flows express
    what the tree cannot: that an analyst may *consult* compliance without
    being able to instruct it, and that the reverse does not hold.
    """

    source: str
    target: str
    kind: FlowKind = FlowKind.CONSULT
    description: str = ""
    requires_approval: bool = False


class KnowledgeSource(BaseModel):
    """Grounding material an agent may consult (ADR-0023)."""

    id: str
    description: str = ""
    kind: KnowledgeKind = KnowledgeKind.DOCUMENT_STORE
    data_classes: list[str] = Field(default_factory=list)
    # How stale an answer may be before it must be re-fetched.
    freshness_seconds: Optional[int] = None
    # Whether retrieved passages must be cited back to the reader.
    require_citation: bool = True
    secret_ref: Optional[str] = None


class EvaluationCase(BaseModel):
    """One check an agent must pass before it may be promoted (ADR-0022)."""

    id: str
    description: str = ""
    given: str = ""                 # the situation or prompt
    expect: str = ""                # what a correct response must contain or do
    must_not: list[str] = Field(default_factory=list)
    applies_to: list[str] = Field(default_factory=list)   # agent ids; empty = all
    weight: float = 1.0


class PromotionGate(BaseModel):
    """What must hold before an agent enters a stage (ADR-0022)."""

    to_stage: LifecycleStage
    requires: list[GateRequirement] = Field(default_factory=list)
    min_pass_rate: float = 1.0
    approvers: list[str] = Field(default_factory=list)


class Budget(BaseModel):
    """A spend ceiling with a mandatory action on breach (ADR-0022)."""

    id: str
    scope_kind: Literal["system", "team", "agent"] = "system"
    scope: str = "*"
    period: Literal["daily", "weekly", "monthly"] = "monthly"
    limit_usd: float = 0.0
    on_breach: BreachAction = BreachAction.WARN
    notify_channel: Optional[str] = None


class Compliance(BaseModel):
    """Obligations that constrain placement, retention and disclosure."""

    frameworks: list[str] = Field(default_factory=list)   # e.g. SOC2, GDPR
    data_residency: list[str] = Field(default_factory=list)
    audit_retention_days: int = 365
    redact_data_classes: list[str] = Field(default_factory=list)
    require_approval_for: list[Action] = Field(default_factory=list)
    # Review the permissions every agent holds at least this often.
    permission_review_days: int = 90


class Lifecycle(BaseModel):
    """Stages, gates and the review cadence for the whole system (ADR-0022)."""

    stage: LifecycleStage = LifecycleStage.DRAFT
    owner: str = ""
    gates: list[PromotionGate] = Field(default_factory=list)
    evaluations: list[EvaluationCase] = Field(default_factory=list)
    review_cadence_days: int = 90
    # Agents idle for longer than this are flagged for de-provisioning, which
    # is how fleets avoid accumulating stale identities and permissions.
    retire_after_idle_days: Optional[int] = 180


class Resilience(BaseModel):
    """Durability properties a target must provide (ADR-0025)."""

    checkpoint_each_step: bool = True
    resume_on_failure: bool = True
    idempotent_triggers: bool = True
    max_run_seconds: int = 3600
    dead_letter_channel: Optional[str] = None


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
    triggers: list[TriggerSpec] = Field(default_factory=list)
    interaction_flows: list[InteractionFlow] = Field(default_factory=list)
    knowledge: list[KnowledgeSource] = Field(default_factory=list)
    budgets: list[Budget] = Field(default_factory=list)
    compliance: Compliance = Field(default_factory=Compliance)
    lifecycle: Lifecycle = Field(default_factory=Lifecycle)
    resilience: Resilience = Field(default_factory=Resilience)
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

    def channel(self, channel_id: str) -> Optional[ChannelSpec]:
        return next((c for c in self.channels if c.id == channel_id), None)

    def knowledge_source(self, source_id: str) -> Optional[KnowledgeSource]:
        return next((k for k in self.knowledge if k.id == source_id), None)

    def triggers_for(self, agent_id: str) -> list[TriggerSpec]:
        return [t for t in self.triggers if t.agent == agent_id]

    def flows_from(self, agent_id: str) -> list[InteractionFlow]:
        return [f for f in self.interaction_flows if f.source == agent_id]
