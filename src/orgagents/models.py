"""Domain model for the organizational agentic system.

The object graph, top down:

    OrgUnit ──< Agent ──< AgentSession
                 │  ├── Harness        (MCP servers, tools, DB grants, model)
                 │  ├── Skill[]        (prompt+code capability packs)
                 │  ├── Plugin[]       (installed extensions)
                 │  ├── SandboxSpec    (instantiated from a SandboxTemplate)
                 │  ├── DataGrant[]    (private / protected / public planes)
                 │  └── WorkflowRef[]  (LangGraph graphs the agent may invoke)
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from .ids import new_id, now_iso

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Visibility(str, Enum):
    """Data plane an artifact lives in.

    PRIVATE    - readable/writable only by the owning agent (and its human).
    PROTECTED  - shared with every agent in one or more groups.
    PUBLIC     - readable by every agent in the organization; any agent may
                 contribute, subject to the contribution policy.
    """

    PRIVATE = "private"
    PROTECTED = "protected"
    PUBLIC = "public"


class Runtime(str, Enum):
    """Which agent framework executes the agent loop."""

    DEEPAGENTS = "langchain_deepagents"
    OPENAI_AGENTS = "openai_agents_sdk"
    LANGGRAPH = "langgraph_native"
    ECHO = "echo"  # deterministic, dependency-free; used for tests/demos


class AgentKind(str, Enum):
    EXECUTIVE = "executive"  # top of a branch, mostly delegates
    MANAGER = "manager"  # delegates + reviews
    INDIVIDUAL = "individual"  # does the work
    SUBAGENT = "subagent"  # ephemeral worker spawned for one task
    SERVICE = "service"  # shared utility agent (e.g. "compliance-check")


class ChannelKind(str, Enum):
    DIRECT_TOOL = "direct_tool_call"  # synchronous agent-to-agent tool call
    SLACK = "slack"
    TEAMS = "teams"
    EMAIL = "email"
    WEBHOOK = "webhook"
    INTERNAL_BUS = "internal_bus"


class SessionState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# --------------------------------------------------------------------------
# Organization
# --------------------------------------------------------------------------


class HumanCounterpart(BaseModel):
    """The person an agent works for and escalates to.

    Every agent has exactly one accountable human. Approval policy decides
    which actions stop and wait for that person.
    """

    user_id: str
    display_name: str
    email: str
    role_title: str = ""
    timezone: str = "UTC"
    # Tool/action names that always require the human to approve first.
    approval_required_for: list[str] = Field(default_factory=list)
    notify_channels: list[ChannelKind] = Field(
        default_factory=lambda: [ChannelKind.EMAIL]
    )


class OrgUnit(BaseModel):
    """A node in the company org chart (division, department, team)."""

    id: str = Field(default_factory=lambda: new_id("org"))
    name: str
    parent_id: Optional[str] = None
    kind: Literal["company", "division", "department", "team"] = "team"
    # Groups used for PROTECTED data sharing and channel membership.
    groups: list[str] = Field(default_factory=list)
    cost_center: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Data planes
# --------------------------------------------------------------------------


class DataGrant(BaseModel):
    """An agent's access to one data plane."""

    visibility: Visibility
    # Required for PROTECTED: the group names this grant covers.
    groups: list[str] = Field(default_factory=list)
    can_read: bool = True
    can_write: bool = False
    # Optional namespace restriction within the plane, e.g. "finance/*".
    namespace_glob: str = "*"


class DataRecord(BaseModel):
    """A stored knowledge/memory record in one of the planes."""

    id: str = Field(default_factory=lambda: new_id("rec"))
    namespace: str
    key: str
    value: Any
    visibility: Visibility = Visibility.PRIVATE
    owner_agent_id: str = ""
    groups: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    tags: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Harness: how an agent reaches the outside world
# --------------------------------------------------------------------------


class MCPServerRef(BaseModel):
    """An MCP server the agent's harness mounts.

    `transport=stdio` runs a local command; `http`/`sse` connect to a URL.
    Secrets are referenced by name, never inlined.
    """

    name: str
    transport: Literal["stdio", "http", "sse"] = "stdio"
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    secret_refs: dict[str, str] = Field(default_factory=dict)
    # Tool allow-list; empty means "all tools this server exposes".
    allowed_tools: list[str] = Field(default_factory=list)
    read_only: bool = True


class RelationalGrant(BaseModel):
    """Scoped relational-database access exposed through an MCP server.

    The harness never hands an agent raw credentials: it hands it a named
    connection whose reachable schemas/tables and statement classes are fixed
    here, and the MCP server enforces them.
    """

    connection_name: str
    engine: Literal["postgres", "mysql", "sqlite", "snowflake", "bigquery"] = "postgres"
    dsn_secret_ref: str = ""
    schemas: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)  # empty = all in schemas
    allowed_statements: list[Literal["select", "insert", "update", "delete", "ddl"]] = (
        Field(default_factory=lambda: ["select"])
    )
    row_limit: int = 1000
    statement_timeout_ms: int = 30_000
    # Columns masked before rows reach the model.
    masked_columns: list[str] = Field(default_factory=list)


class ToolBinding(BaseModel):
    """A single callable exposed into the agent loop."""

    name: str
    description: str = ""
    source: Literal["mcp", "builtin", "workflow", "agent", "plugin", "skill"] = "builtin"
    # Reference into the source, e.g. mcp server name, workflow id, agent id.
    ref: str = ""
    requires_approval: bool = False
    # The decision class calling this constitutes, if any (ADR-0065). Most
    # tools are ordinary work and decide nothing.
    decision: Optional[str] = None


class ModelSpec(BaseModel):
    provider: Literal["anthropic", "openai", "azure_openai", "bedrock", "vertex"] = (
        "anthropic"
    )
    model: str = "claude-opus-5"
    temperature: float = 0.2
    max_tokens: int = 8192
    # Cheaper model used for sub-agents spawned by this agent.
    subagent_model: Optional[str] = None


class Harness(BaseModel):
    """Everything that wraps the model to make an agent operable.

    A harness is the unit the designer UI edits and the unit governance
    reviews: model, tools, MCP mounts, database grants, limits and policy.
    """

    id: str = Field(default_factory=lambda: new_id("hrn"))
    runtime: Runtime = Runtime.DEEPAGENTS
    model: ModelSpec = Field(default_factory=ModelSpec)
    system_prompt: str = ""
    mcp_servers: list[MCPServerRef] = Field(default_factory=list)
    relational_grants: list[RelationalGrant] = Field(default_factory=list)
    tools: list[ToolBinding] = Field(default_factory=list)
    data_grants: list[DataGrant] = Field(default_factory=list)
    # Guardrails.
    max_turns: int = 40
    max_subagent_depth: int = 3
    max_parallel_subagents: int = 4
    token_budget: int = 2_000_000
    wall_clock_budget_s: int = 3600
    # Human-in-the-loop policy.
    interrupt_on: list[str] = Field(default_factory=list)
    escalate_to_human_after_failures: int = 2


# --------------------------------------------------------------------------
# Skills, plugins, sandboxes
# --------------------------------------------------------------------------


class Skill(BaseModel):
    """A packaged capability: instructions plus optional scripts/resources."""

    id: str = Field(default_factory=lambda: new_id("skl"))
    name: str
    version: str = "0.1.0"
    description: str = ""
    instructions: str = ""
    # Files shipped with the skill, materialized into the sandbox workspace.
    files: dict[str, str] = Field(default_factory=dict)
    # Free-text triggers used by the catalog and by the runtime router.
    triggers: list[str] = Field(default_factory=list)
    visibility: Visibility = Visibility.PUBLIC
    owner_agent_id: str = ""


class Plugin(BaseModel):
    """An installable extension bundling tools, skills and hooks."""

    id: str = Field(default_factory=lambda: new_id("plg"))
    name: str
    version: str = "0.1.0"
    description: str = ""
    provides_tools: list[ToolBinding] = Field(default_factory=list)
    provides_skills: list[str] = Field(default_factory=list)
    # Lifecycle hooks: event name -> callable dotted path or webhook URL.
    hooks: dict[str, str] = Field(default_factory=dict)
    requires_scopes: list[str] = Field(default_factory=list)


class SandboxTemplate(BaseModel):
    """A reusable, reviewed execution environment definition.

    Templates are the governed boundary for agent code execution: an agent
    may only run inside a template an administrator has published.
    """

    id: str = Field(default_factory=lambda: new_id("sbx"))
    name: str
    description: str = ""
    base_image: str = "python:3.11-slim"
    # Toolchains preinstalled in the image.
    toolchain: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    cpu: str = "1"
    memory: str = "2Gi"
    disk: str = "10Gi"
    gpu: Optional[str] = None
    timeout_s: int = 900
    # Which provider actually executes this sandbox, and what that provider
    # really enforces (ADR-0054). Carried on the template because an operator
    # asking "what isolates this agent" must not have to read the generated
    # artifacts to find out — and because a provider that degraded to the
    # container floor has to be visible at runtime, not only at compile time.
    provider: str = "container"
    boundary_summary: str = ""
    boundary_verified: bool = False
    degraded_from: Optional[str] = None
    degradation_reason: str = ""
    network: Literal["none", "egress_allowlist", "internal", "full"] = (
        "egress_allowlist"
    )
    egress_allowlist: list[str] = Field(default_factory=list)
    # Data planes mountable inside the sandbox.
    mounts: list[Visibility] = Field(default_factory=lambda: [Visibility.PRIVATE])
    env: dict[str, str] = Field(default_factory=dict)
    secret_refs: list[str] = Field(default_factory=list)
    filesystem: Literal["ephemeral", "session_persistent", "agent_persistent"] = (
        "ephemeral"
    )
    privileged: bool = False


class SandboxSpec(BaseModel):
    """An agent's instantiation of a template, with narrow overrides."""

    template_id: str
    env: dict[str, str] = Field(default_factory=dict)
    egress_extra: list[str] = Field(default_factory=list)
    timeout_s: Optional[int] = None


# --------------------------------------------------------------------------
# Workflows
# --------------------------------------------------------------------------


class WorkflowRef(BaseModel):
    """A LangGraph workflow the agent may call as a tool."""

    id: str = Field(default_factory=lambda: new_id("wfl"))
    name: str
    description: str = ""
    # Dotted path to a callable returning a compiled LangGraph, or a
    # declarative graph definition understood by workflows.declarative.
    entrypoint: str = ""
    graph: Optional[dict[str, Any]] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    checkpointer: Literal["memory", "postgres", "sqlite"] = "sqlite"
    interrupt_before: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------


class Agent(BaseModel):
    """An organizational agent."""

    @model_validator(mode="before")
    @classmethod
    def _lift_single_sandbox(cls, data):
        """Accept the pre-ADR-0082 `sandbox=` kwarg as the first sandbox.

        `sandbox` is a read-only property now, so a construction still passing
        it would have the value silently dropped — leaving the agent with no
        place to run, which is the exact failure a sandbox exists to prevent.
        Lift it instead, and let an explicit `sandboxes` win if both are given.
        """
        if isinstance(data, dict) and data.get("sandbox") is not None:
            data = dict(data)
            single = data.pop("sandbox")
            data.setdefault("sandboxes", [single])
        return data

    id: str = Field(default_factory=lambda: new_id("agt"))
    name: str
    title: str = ""
    kind: AgentKind = AgentKind.INDIVIDUAL
    description: str = ""
    org_unit_id: Optional[str] = None
    # Org hierarchy: who this agent reports to, and who reports to it.
    manager_agent_id: Optional[str] = None
    report_agent_ids: list[str] = Field(default_factory=list)
    # Peers reachable by direct tool call without going through the manager.
    peer_agent_ids: list[str] = Field(default_factory=list)
    # Reach lent by a mission, each with the window it is good for. Kept apart
    # from `peer_agent_ids` because it expires (ADR-0039 v1.1.0).
    mission_grants: list[dict[str, Any]] = Field(default_factory=list)
    # Who stands in when this agent cannot run (ADR-0094). Unset means the
    # manager, who already holds a superset of this mandate under ADR-0065 and
    # is therefore granted nothing by standing in. Naming a *peer* here is a
    # real grant of authority the chart did not give, which is why it must be
    # said out loud rather than inferred.
    successor_agent_id: Optional[str] = None
    # Effective authority, resolved at the phase gate and never re-derived
    # here (ADR-0065): which decision classes this agent may take alone.
    mandate: list[str] = Field(default_factory=list)
    # Bounds that travel with it; every one in the line applies.
    mandate_conditions: list[dict[str, Any]] = Field(default_factory=list)
    # The accountable owner, kept for callers that want one person.
    human: Optional[HumanCounterpart] = None
    # Everyone paired with this agent, in their named capacities (ADR-0026).
    humans: list[HumanCounterpart] = Field(default_factory=list)
    # Sub-agents this agent may call as tools (ADR-0027), as resolved IR dicts.
    subagents: list[dict[str, Any]] = Field(default_factory=list)
    # The resolved two-tier memory contract (ADR-0028).
    memory: dict[str, Any] = Field(default_factory=dict)
    # Boundary checks on what enters and leaves (ADR-0035).
    guardrails: list[dict[str, Any]] = Field(default_factory=list)
    # Workspace and context policy (ADR-0036).
    artifact_store: dict[str, Any] = Field(default_factory=dict)
    context_policy: dict[str, Any] = Field(default_factory=dict)
    # The shape this agent must return, if any (ADR-0037).
    output_contract: dict[str, Any] = Field(default_factory=dict)
    harness: Harness = Field(default_factory=Harness)
    skill_ids: list[str] = Field(default_factory=list)
    plugin_ids: list[str] = Field(default_factory=list)
    workflow_ids: list[str] = Field(default_factory=list)
    #: Every sandbox this agent runs work in (ADR-0082). `sandbox` is the
    #: first of them and is what a caller that can only hold one uses; which
    #: one a given *call* belongs in is derived from the data classes it
    #: touches, and the derivation is enforced at the phase gate today rather
    #: than selected here.
    sandboxes: list[SandboxSpec] = Field(default_factory=list)

    @property
    def sandbox(self) -> Optional[SandboxSpec]:
        return self.sandboxes[0] if self.sandboxes else None
    channels: list[ChannelKind] = Field(
        default_factory=lambda: [ChannelKind.DIRECT_TOOL, ChannelKind.INTERNAL_BUS]
    )
    groups: list[str] = Field(default_factory=list)
    # Catalog/marketplace facing.
    tags: list[str] = Field(default_factory=list)
    published: bool = False
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


# --------------------------------------------------------------------------
# Sessions and messages
# --------------------------------------------------------------------------


class SessionEvent(BaseModel):
    """One entry in a session's append-only trace."""

    id: str = Field(default_factory=lambda: new_id("evt"))
    session_id: str
    ts: str = Field(default_factory=now_iso)
    type: str  # message | tool_call | delegation | workflow | approval | error
    actor: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    # Distributed-tracing correlation.
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""


class AgentSession(BaseModel):
    """One conversation/run of an agent, addressable by URL."""

    id: str = Field(default_factory=lambda: new_id("ses"))
    agent_id: str
    title: str = ""
    state: SessionState = SessionState.CREATED
    created_by: str = ""  # human user id or parent agent id
    parent_session_id: Optional[str] = None
    trace_id: str = Field(default_factory=lambda: new_id("trc"))
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    # Rollup counters surfaced in the catalog.
    turn_count: int = 0
    token_usage: int = 0
    cost_usd: float = 0.0

    def url(self, base_url: str = "") -> str:
        return f"{base_url.rstrip('/')}/sessions/{self.id}"


class Message(BaseModel):
    """An inter-agent message, direct or over an enterprise channel."""

    id: str = Field(default_factory=lambda: new_id("msg"))
    from_agent_id: str
    to_agent_id: Optional[str] = None
    channel: ChannelKind = ChannelKind.DIRECT_TOOL
    channel_address: str = ""  # e.g. "#finance-ops" or an email address
    subject: str = ""
    body: str = ""
    session_id: Optional[str] = None
    reply_to_id: Optional[str] = None
    requires_response: bool = False
    created_at: str = Field(default_factory=now_iso)
    payload: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Catalog / marketplace
# --------------------------------------------------------------------------


class CatalogEntry(BaseModel):
    """A marketplace listing for an agent, skill, plugin, workflow or template."""

    id: str = Field(default_factory=lambda: new_id("cat"))
    kind: Literal["agent", "skill", "plugin", "workflow", "sandbox_template", "session"]
    ref_id: str
    name: str
    summary: str = ""
    owner: str = ""
    org_unit_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    visibility: Visibility = Visibility.PUBLIC
    groups: list[str] = Field(default_factory=list)
    version: str = "0.1.0"
    installs: int = 0
    rating_sum: float = 0.0
    rating_count: int = 0
    published_at: str = Field(default_factory=now_iso)

    @property
    def rating(self) -> float:
        return self.rating_sum / self.rating_count if self.rating_count else 0.0


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: new_id("alr"))
    severity: Severity = Severity.WARNING
    title: str = ""
    detail: str = ""
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    acknowledged: bool = False
