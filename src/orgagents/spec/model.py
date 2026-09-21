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

from pydantic import BaseModel, Field, field_validator, model_validator

SPEC_VERSION = "1.2.0"


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


class MissionStatus(str, Enum):
    """Where a short-lived team is in its life (ADR-0039)."""

    PROPOSED = "proposed"
    ACTIVE = "active"
    COMPLETED = "completed"
    DISBANDED = "disbanded"


class ModelClass(str, Enum):
    """What an agent needs from a model, not which model it gets (ADR-0040).

    The spec says `frontier_reasoning`; the catalog says which models qualify;
    the binding picks one and the compiler checks it is permitted.
    """

    FRONTIER_REASONING = "frontier_reasoning"
    BALANCED = "balanced"
    FAST_CHEAP = "fast_cheap"
    LONG_CONTEXT = "long_context"
    VISION = "vision"
    CODE = "code"
    EMBEDDING = "embedding"
    ON_PREMISES = "on_premises"


class GuardrailKind(str, Enum):
    """Where a guardrail sits in the loop (ADR-0035)."""

    INPUT = "input"              # what arrives, before the agent sees it
    OUTPUT = "output"            # what the agent produces, before it leaves
    TOOL_INPUT = "tool_input"    # arguments, before a tool runs
    TOOL_OUTPUT = "tool_output"  # results, before they re-enter context


class GuardrailCheck(str, Enum):
    """Named checks, so a guardrail is reviewable rather than a regex soup."""

    PII = "pii"                        # identifying data in free text
    SECRETS = "secrets"                # credentials, keys, connection strings
    PROMPT_INJECTION = "prompt_injection"
    DATA_CLASS = "data_class"          # a classified class leaving its boundary
    URL_ALLOWLIST = "url_allowlist"
    SCHEMA = "schema"                  # structured output conformance
    PATTERN = "pattern"                # declared regular expressions
    MAX_LENGTH = "max_length"


class GuardrailAction(str, Enum):
    """What happens when a check trips."""

    BLOCK = "block"        # refuse; the agent is told why
    REDACT = "redact"      # remove the offending content and continue
    FLAG = "flag"          # allow, record, and surface it
    ESCALATE = "escalate"  # stop and ask a human


class OutputViolationAction(str, Enum):
    RETRY = "retry"
    BLOCK = "block"
    FLAG = "flag"


class HumanRole(str, Enum):
    """Why a person is paired with an agent (ADR-0026).

    An agent answers to several people in different capacities: one is
    accountable for it, others approve specific actions, review its output, or
    are simply told what it did.
    """

    OWNER = "owner"              # accountable; exactly one per agent
    APPROVER = "approver"        # decides on gated actions
    REVIEWER = "reviewer"        # reviews output, does not gate it
    ESCALATION = "escalation"    # contacted when the owner does not answer
    OPERATOR = "operator"        # runs and maintains it day to day
    STAKEHOLDER = "stakeholder"  # informed, no decision rights


class SubAgentKind(str, Enum):
    """What a tool-shaped sub-agent is for (ADR-0027)."""

    RESEARCH = "research"
    REVIEW = "review"
    SUMMARIZE = "summarize"
    EXTRACT = "extract"
    CRITIQUE = "critique"
    PLAN = "plan"
    VERIFY = "verify"
    CUSTOM = "custom"


class EndpointTrust(str, Enum):
    """How far an external agent endpoint is trusted (ADR-0030)."""

    INTERNAL = "internal"    # another team's agent in this organization
    PARTNER = "partner"      # a contracted third party
    EXTERNAL = "external"    # anything else; treated as hostile input


class MemoryTier(str, Enum):
    """Where a memory lives (ADR-0028)."""

    SESSION = "session"        # short term, scoped to one session
    LONG_TERM = "long_term"    # survives sessions, recalled into them


class RecallMode(str, Enum):
    NONE = "none"
    ON_DEMAND = "on_demand"    # the agent asks
    AUTOMATIC = "automatic"    # relevant memories are pre-loaded


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


class DecisionClass(BaseModel):
    """A class of decision a unit may be authorized to take (ADR-0065).

    Declared in the spec, like a capability, so a mandate references a term
    somebody wrote down rather than inventing one per team. `ADR-0066` holds
    the open question of whether these should eventually be catalog-owned.
    """

    id: str
    title: str = ""
    description: str = ""


class Mandate(BaseModel):
    """A bounded scope of decision held by a unit (ADR-0065).

    Not a description of work — that is a responsibility. This is what the
    unit may settle without asking: permission decides whether the door
    opens, a mandate decides whether you were the one to open it.
    """

    decisions: list[str] = Field(default_factory=list)   # DecisionClass ids
    # Bounds that make a decision class finite: a value ceiling, a data class,
    # a reversibility requirement. Conditions accumulate down the tree and all
    # of them must hold, so a child can never loosen its parent's.
    conditions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _reject_prose(cls, v: Any) -> Any:
        """A list of sentences used to be a mandate. It never was one.

        Refusing loudly beats coercing: turning "Run the workforce safely"
        into a decision class would manufacture exactly the machine-readable
        wrong term ADR-0066 warns about.
        """
        if isinstance(v, list):
            raise ValueError(
                "mandate is a scope of decision, not a list of statements "
                "(ADR-0065). Declare `decisions:` with ids from the spec's "
                "`decisions:` vocabulary, and put the prose in the unit's "
                "description."
            )
        return v


class Capability(BaseModel):
    """An abstract access need, bound to an MCP server per target (ADR-0010)."""

    id: str
    description: str = ""
    action: Action = Action.QUERY
    # What class of thing this reaches, in the customer's own vocabulary.
    resource_class: str = ""
    data_classes: list[str] = Field(default_factory=list)
    constraints: CapabilityConstraint = Field(default_factory=CapabilityConstraint)
    # The decision class exercising this capability constitutes, if any
    # (ADR-0065). Most capabilities are ordinary work and decide nothing;
    # those leave this unset and never consult a mandate.
    decision: Optional[str] = None
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

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        """Describe both accepted shapes, because both are accepted.

        `roles: [analyst]` is the common form and the loader coerces it; the
        exported schema previously described only the object form, so a
        third-party editor would have rejected a spec this platform reads
        happily. A schema that disagrees with the loader is worse than no
        schema — it makes correct documents look wrong (WS-002 M5).
        """
        object_schema = handler(core_schema)
        object_schema.pop("$ref", None)
        return {
            "anyOf": [
                {"type": "string",
                 "description": "a role id, equivalent to {\"role\": <id>}"},
                object_schema,
            ],
            "title": cls.__name__,
        }


class WorkingHours(BaseModel):
    """When the humans on a channel are actually available."""

    timezone: str = "UTC"
    days: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])  # 1=Monday
    start_hour: int = 9
    end_hour: int = 17
    holidays: list[str] = Field(default_factory=list)   # ISO dates


class ModelPolicy(BaseModel):
    """Which models an agent is permitted to run on (ADR-0040).

    Stated as capability classes plus constraints, so the design stays neutral;
    the catalog resolves classes to concrete models and the compiler refuses a
    binding that picks one outside this policy.
    """

    classes: list[ModelClass] = Field(default_factory=lambda: [ModelClass.BALANCED])
    # Explicit catalog entry ids, when an organization names models directly.
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    # Constraints every permitted model must satisfy.
    max_cost_per_million_tokens: Optional[float] = None
    min_context_tokens: Optional[int] = None
    require_no_training_on_data: bool = True
    require_regions: list[str] = Field(default_factory=list)
    # A cheaper class for sub-agent calls, when the agent has sub-agents.
    subagent_classes: list[ModelClass] = Field(default_factory=list)
    # Fall back to a permitted model when the bound one is not allowed. Off by
    # default: a fallback is a quiet change of model, so it is opted into
    # rather than inherited (ADR-0040 v1.1.0).
    allow_fallback: bool = False


class Mission(BaseModel):
    """A short-lived team drawn from the standing organization (ADR-0039).

    The org chart changes slowly and describes accountability. A mission
    changes weekly and describes *work*: a named objective, deliverables, a
    leader, the people on it, and a date it ends. Members keep their home team
    and their own permissions; the mission is a temporary working arrangement,
    not a reorganization.
    """

    id: str
    name: str = ""
    objective: str = ""
    deliverables: list[str] = Field(default_factory=list)
    status: MissionStatus = MissionStatus.PROPOSED
    # Agent ids drawn from the standing organization. The leader must be one.
    leader: str = ""
    members: list[str] = Field(default_factory=list)
    # The human accountable for the mission's outcome.
    sponsor: Optional[HumanCounterpart] = None
    starts_on: Optional[str] = None      # ISO date
    ends_on: Optional[str] = None        # ISO date; a mission always ends
    # Members may delegate to each other for the mission's duration. This is
    # the point of a task force, and it is declared rather than assumed.
    internal_delegation: bool = True
    # Authority lent for the mission's duration, bounded by the sponsor's own
    # and expiring with the window (ADR-0065 rule 8). Without this a mission
    # is an authority hole: it already lends lateral reach, and a temporary
    # team with a permanent decision right is how standing authority gets
    # created by accident.
    mandate: Optional[Mandate] = None
    # A mission-scoped channel, if the members need one.
    channel: Optional[str] = None
    # Roles assigned for the duration. These may only narrow what a member
    # already holds — a mission never grants new access (ADR-0008).
    roles: list[RoleAssignment] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)

    model_config = {"validate_assignment": True}

    @field_validator("roles", mode="before")
    @classmethod
    def _coerce_roles(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [RoleAssignment.coerce(item) for item in v]
        return v

    @property
    def active(self) -> bool:
        return self.status is MissionStatus.ACTIVE


class Guardrail(BaseModel):
    """A boundary check on what enters or leaves an agent (ADR-0035).

    Permissions decide what an agent may *reach*; a guardrail decides what may
    *pass*. They are different questions, and a system that answers only the
    first will happily let a correctly-permissioned agent paste a customer's
    identifiers into a chat channel.
    """

    id: str
    description: str = ""
    applies_to: list[GuardrailKind] = Field(
        default_factory=lambda: [GuardrailKind.INPUT, GuardrailKind.OUTPUT]
    )
    checks: list[GuardrailCheck] = Field(default_factory=list)
    on_violation: GuardrailAction = GuardrailAction.BLOCK
    # Which classified data this guardrail is watching for, when checking
    # `data_class`.
    data_classes: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    allowed_urls: list[str] = Field(default_factory=list)
    max_length: Optional[int] = None
    escalate_channel: Optional[str] = None
    enabled: bool = True
    # What kind of model should form the verdict for judgement checks — pii,
    # secrets, prompt_injection. Unset means the deterministic
    # pattern classifier (ADR-0045). Says a class, never a vendor: the binding decides
    # which model satisfies it.
    classifier: Optional[ModelClass] = None


class ArtifactStore(BaseModel):
    """A workspace agents read and write files in (ADR-0036).

    Distinct from memory (what an agent *learned*) and from a sandbox (where it
    *executes*): this is where large intermediate material lives so it does not
    have to sit in the context window.
    """

    id: str
    description: str = ""
    scope: SharingScope = SharingScope.PRIVATE
    groups: list[str] = Field(default_factory=list)
    data_classes: list[str] = Field(default_factory=list)
    retention_days: Optional[int] = 30
    max_file_bytes: int = 10_000_000
    max_total_bytes: int = 1_000_000_000


class ContextPolicy(BaseModel):
    """How a long run keeps its context usable and affordable (ADR-0036)."""

    max_context_tokens: int = 150_000
    # Summarize the thread once it passes this, keeping the most recent turns
    # verbatim.
    summarize_after_tokens: int = 100_000
    keep_last_turns: int = 6
    # Tool results larger than this are written to the artifact store and
    # replaced by a reference the agent can read back on demand.
    offload_tool_output_bytes: int = 20_000
    offload_to: Optional[str] = None       # artifact store id
    # Keep each summary as a session memory, so nothing is silently lost.
    retain_summaries: bool = True
    # What kind of model should write the summary (ADR-0045). Unset means the
    # structural first/last fallback, which says that is what it did.
    summarizer: Optional[ModelClass] = None


class OutputContract(BaseModel):
    """The shape an agent, sub-agent or tool must return (ADR-0037).

    `returns: "a cited findings list"` tells a person what to expect and tells
    a caller nothing it can check. A contract is checkable.
    """

    id: str
    description: str = ""
    # A small JSON-Schema subset: type, properties, required, items, enum.
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    required: list[str] = Field(default_factory=list)
    on_violation: OutputViolationAction = OutputViolationAction.RETRY
    max_retries: int = 2

    model_config = {"populate_by_name": True}


class SkillSpec(BaseModel):
    """A packaged capability pack an agent can hold (ADR-0029).

    A skill is instructions plus optional resources — it changes how an agent
    works, not what it may reach. Access always comes from capabilities.
    """

    id: str
    description: str = ""
    version: str = "0.1.0"
    instructions: str = ""
    triggers: list[str] = Field(default_factory=list)   # phrases that invoke it
    # Capabilities the skill assumes; validated against the holder's grants.
    requires_capabilities: list[str] = Field(default_factory=list)
    resources: dict[str, str] = Field(default_factory=dict)


class PluginSpec(BaseModel):
    """An installable bundle of skills and tools (ADR-0029)."""

    id: str
    description: str = ""
    version: str = "0.1.0"
    provides_skills: list[str] = Field(default_factory=list)
    provides_tools: list[str] = Field(default_factory=list)
    requires_capabilities: list[str] = Field(default_factory=list)
    # Lifecycle hooks by event name, resolved by the target.
    hooks: dict[str, str] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    """A named callable an agent sees, wrapping something else (ADR-0029).

    A tool is a thin, reviewable wrapper: it narrows and names an underlying
    capability, sub-agent, workflow or external endpoint. It never grants
    anything its target does not already grant.
    """

    id: str
    description: str = ""
    wraps_kind: Literal["capability", "subagent", "workflow", "endpoint"] = "capability"
    wraps: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    # Constraints applied on top of the wrapped thing's own constraints.
    constraints: "CapabilityConstraint" = Field(default_factory=lambda: CapabilityConstraint())
    idempotent: bool = True
    # Check the result against `output_schema` before it re-enters context.
    validate_output: bool = False


class SubAgentSpec(BaseModel):
    """A task-scoped worker an agent calls like a tool (ADR-0027).

    A sub-agent is not an org member: it has no reporting line, no human
    counterpart of its own, no session URL and no memory beyond the call. It
    runs under its parent's identity with a **narrowed** set of that parent's
    capabilities, returns a result, and is gone.
    """

    id: str
    name: str = ""
    kind: SubAgentKind = SubAgentKind.CUSTOM
    purpose: str = ""
    instructions: str = ""
    # Must be a subset of the calling agent's capabilities; validated.
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    environment: Optional[str] = None       # environment class id, if it executes
    returns: str = ""                       # what the caller gets back
    output_contract: Optional[str] = None   # a checkable shape (ADR-0037)
    max_turns: int = 8
    max_runtime_seconds: int = 300
    parallel_safe: bool = True


class AgentEndpoint(BaseModel):
    """An agent outside this system that ours may call (ADR-0030).

    The agentic workforce reaches past one organization. An endpoint is a
    declared, trust-classified boundary: what it offers, what we may send it,
    and what its answers are worth.
    """

    id: str
    description: str = ""
    trust: EndpointTrust = EndpointTrust.EXTERNAL
    provides: list[str] = Field(default_factory=list)      # capability ids offered
    # Data classes we are permitted to send outward.
    send_data_classes: list[str] = Field(default_factory=list)
    # Answers from anything but `internal` are untrusted input, never instructions.
    treat_output_as_data: bool = True
    requires_approval: bool = False
    response_sla_seconds: Optional[int] = None
    secret_ref: Optional[str] = None


class MemoryPolicy(BaseModel):
    """How one memory tier behaves (ADR-0028)."""

    tier: MemoryTier = MemoryTier.SESSION
    enabled: bool = True
    # Classes of data this tier may hold; anything else is refused.
    data_classes: list[str] = Field(default_factory=list)
    retention_days: Optional[int] = None       # None = for the session only
    max_items: int = 500
    recall: RecallMode = RecallMode.ON_DEMAND
    # Long-term only: may a session promote a memory into this tier, and does
    # promotion need a human to agree?
    promotion_allowed: bool = False
    promotion_requires_approval: bool = False
    redact_data_classes: list[str] = Field(default_factory=list)


class Memory(BaseModel):
    """The two-tier memory contract for the system (ADR-0028)."""

    session: MemoryPolicy = Field(
        default_factory=lambda: MemoryPolicy(tier=MemoryTier.SESSION)
    )
    long_term: MemoryPolicy = Field(
        default_factory=lambda: MemoryPolicy(
            tier=MemoryTier.LONG_TERM, retention_days=365, promotion_allowed=True
        )
    )
    # Namespaces long-term memory is partitioned into, each with a scope.
    namespaces: list["MemoryNamespace"] = Field(default_factory=list)


class MemoryNamespace(BaseModel):
    """A partition of long-term memory, scoped like any other data."""

    id: str
    description: str = ""
    scope: SharingScope = SharingScope.PRIVATE
    groups: list[str] = Field(default_factory=list)
    data_classes: list[str] = Field(default_factory=list)
    retention_days: Optional[int] = None


class AgentMemoryOverride(BaseModel):
    """An agent's narrowing of the system memory contract."""

    session_retention_minutes: Optional[int] = None
    long_term_enabled: bool = True
    namespaces: list[str] = Field(default_factory=list)
    recall: Optional[RecallMode] = None
    may_promote: bool = True


class HumanCounterpart(BaseModel):
    """A person paired with an agent, in a named capacity (ADR-0026).

    An agent has one `owner` and may have any number of approvers, reviewers,
    escalation contacts, operators and stakeholders. Pairing is many-to-many:
    one person can be the owner of several agents and a reviewer on others.
    """

    name: str
    contact: str
    role_title: str = ""
    roles: list[HumanRole] = Field(default_factory=lambda: [HumanRole.OWNER])
    approves: list[str] = Field(default_factory=list)   # capability/action ids
    notify_on: list[ChannelClass] = Field(default_factory=lambda: [ChannelClass.MAIL])
    # The channel this person prefers to be reached on, if not the default.
    channel: Optional[str] = None
    working_hours: Optional["WorkingHours"] = None

    @property
    def is_owner(self) -> bool:
        return HumanRole.OWNER in self.roles


class AgentSpec(BaseModel):
    """One agent: who it answers to, what it is for, what it may reach."""

    id: str
    name: str = ""
    description: str = ""
    roles: list[RoleAssignment] = Field(default_factory=list)
    # One or more paired humans; exactly one carries the `owner` role.
    humans: list[HumanCounterpart] = Field(default_factory=list)
    environment: Optional[EnvironmentOverride] = None
    capabilities: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    plugins: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    # Tool-shaped workers this agent may call (ADR-0027).
    subagents: list[SubAgentSpec] = Field(default_factory=list)
    # External agents this agent may reach (ADR-0030).
    endpoints: list[str] = Field(default_factory=list)
    memory: Optional[AgentMemoryOverride] = None
    # Guardrails this agent adds on top of the system's (ADR-0035).
    guardrails: list[str] = Field(default_factory=list)
    # Where large intermediate material goes (ADR-0036).
    artifact_store: Optional[str] = None
    context: Optional[ContextPolicy] = None
    # What this agent must return, when it must return something checkable.
    output_contract: Optional[str] = None
    # Which models this agent may run on (ADR-0040).
    model_policy: Optional[ModelPolicy] = None
    workflows: list[str] = Field(default_factory=list)
    channels: list[ChannelClass] = Field(
        default_factory=lambda: [ChannelClass.DIRECT, ChannelClass.ASYNC_BUS]
    )
    # Lateral links; delegation otherwise follows the team tree.
    peers: list[str] = Field(default_factory=list)
    # What this agent may decide without asking; None inherits its team's
    # (ADR-0065). Effective authority is the intersection up the tree.
    mandate: Optional[Mandate] = None
    # Shared services are callable from anywhere in the organization.
    shared_service: bool = False
    runtime_requirements: list[RuntimeRequirement] = Field(default_factory=list)
    max_delegation_depth: int = 3
    labels: dict[str, str] = Field(default_factory=dict)

    model_config = {"validate_assignment": True}

    @field_validator("roles", mode="before")
    @classmethod
    def _coerce_roles(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [RoleAssignment.coerce(item) for item in v]
        return v

    @model_validator(mode="before")
    @classmethod
    def _lift_single_human(cls, data: Any) -> Any:
        """Accept the pre-1.1 `human:` field as a single owner pairing."""
        if isinstance(data, dict) and data.get("human") and not data.get("humans"):
            data = dict(data)
            data["humans"] = [data.pop("human")]
        return data

    # -- pairing lookups ---------------------------------------------------

    @property
    def owner(self) -> Optional[HumanCounterpart]:
        return next((h for h in self.humans if HumanRole.OWNER in h.roles), None)

    def humans_with(self, role: HumanRole) -> list[HumanCounterpart]:
        return [h for h in self.humans if role in h.roles]

    def approvers_for(self, action: str) -> list[HumanCounterpart]:
        """Who may approve this action; an approver with no list approves all."""
        approvers = self.humans_with(HumanRole.APPROVER)
        named = [h for h in approvers if action in h.approves]
        return named or [h for h in approvers if not h.approves]

    @property
    def approval_required_for(self) -> list[str]:
        return sorted({a for h in self.humans for a in h.approves})


class Team(BaseModel):
    """A team with exactly one leader, members, and nested child teams (ADR-0006)."""

    model_config = {"validate_assignment": True}

    id: str
    name: str = ""
    #: The unit's charter in prose. Narrative belongs here; what the unit may
    #: *decide* is the mandate below (ADR-0066).
    description: str = ""
    # None means "inherit the parent's" — never "unlimited" (ADR-0065 rule 5).
    mandate: Optional[Mandate] = None
    leader: str = ""                      # agent id; must also be a member
    members: list[AgentSpec] = Field(default_factory=list)
    teams: list["Team"] = Field(default_factory=list)
    roles: list[RoleAssignment] = Field(default_factory=list)   # team roles
    groups: list[str] = Field(default_factory=list)             # protected-data reach
    # Instructions every member of this team carries (ADR-0038).
    shared_instructions: list[str] = Field(default_factory=list)
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
HumanCounterpart.model_rebuild()
Memory.model_rebuild()
ToolSpec.model_rebuild()


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
    # The decision vocabulary a mandate draws from (ADR-0065).
    decisions: list[DecisionClass] = Field(default_factory=list)
    environments: list[EnvironmentClass] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    policies: list[PolicyRule] = Field(default_factory=list)
    organization: Team = Field(default_factory=lambda: Team(id="root", name="root"))
    # Principles every agent in the organization carries (ADR-0038).
    operating_principles: list[str] = Field(default_factory=list)
    guardrails: list[Guardrail] = Field(default_factory=list)
    artifact_stores: list[ArtifactStore] = Field(default_factory=list)
    context: ContextPolicy = Field(default_factory=ContextPolicy)
    output_contracts: list[OutputContract] = Field(default_factory=list)
    skills: list[SkillSpec] = Field(default_factory=list)
    plugins: list[PluginSpec] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    endpoints: list[AgentEndpoint] = Field(default_factory=list)
    memory: Memory = Field(default_factory=Memory)
    workflows: list[WorkflowSpec] = Field(default_factory=list)
    channels: list[ChannelSpec] = Field(default_factory=list)
    triggers: list[TriggerSpec] = Field(default_factory=list)
    # Short-lived teams drawn from the standing organization (ADR-0039).
    missions: list[Mission] = Field(default_factory=list)
    # The default model policy, narrowed per agent (ADR-0040).
    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
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

    def skill(self, skill_id: str) -> Optional[SkillSpec]:
        return next((s for s in self.skills if s.id == skill_id), None)

    def plugin(self, plugin_id: str) -> Optional[PluginSpec]:
        return next((p for p in self.plugins if p.id == plugin_id), None)

    def tool(self, tool_id: str) -> Optional[ToolSpec]:
        return next((t for t in self.tools if t.id == tool_id), None)

    def endpoint(self, endpoint_id: str) -> Optional[AgentEndpoint]:
        return next((e for e in self.endpoints if e.id == endpoint_id), None)

    def mission(self, mission_id: str) -> Optional[Mission]:
        return next((m for m in self.missions if m.id == mission_id), None)

    def missions_for(self, agent_id: str, active_only: bool = False) -> list[Mission]:
        return [
            m for m in self.missions
            if agent_id in m.members and (not active_only or m.active)
        ]

    def guardrail(self, guardrail_id: str) -> Optional[Guardrail]:
        return next((g for g in self.guardrails if g.id == guardrail_id), None)

    def artifact_store(self, store_id: str) -> Optional[ArtifactStore]:
        return next((a for a in self.artifact_stores if a.id == store_id), None)

    def output_contract(self, contract_id: str) -> Optional[OutputContract]:
        return next((c for c in self.output_contracts if c.id == contract_id), None)

    def shared_instructions_for(self, agent_id: str) -> list[tuple[str, str]]:
        """Organization principles and team instructions, with their source."""
        out = [("organization", text) for text in self.operating_principles]
        chain: list[Team] = []
        for team in self.teams():
            if any(m.id == agent_id for m in team.members):
                chain.append(team)
        # Walk up so an outer team's instructions come before an inner one's.
        for team in chain:
            parent_ids = [
                t.id for t in self.teams() if any(c.id == team.id for c in t.teams)
            ]
            for parent_id in parent_ids:
                parent = next((t for t in self.teams() if t.id == parent_id), None)
                if parent:
                    out += [(parent.id, text) for text in parent.shared_instructions]
            out += [(team.id, text) for text in team.shared_instructions]
        return out

    def namespace(self, namespace_id: str) -> Optional[MemoryNamespace]:
        return next((n for n in self.memory.namespaces if n.id == namespace_id), None)

    def humans(self) -> dict[str, list[str]]:
        """Every paired person, and the agents they are paired with."""
        pairs: dict[str, list[str]] = {}
        for agent in self.agents():
            for human in agent.humans:
                pairs.setdefault(human.contact, []).append(agent.id)
        return pairs
