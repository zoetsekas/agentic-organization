"""Bindings — where implementation choices are allowed to live.

The System Spec says *what* a system needs; a binding says *how* that is
realized for one target (ADR-0004). Vendor names, images, SDKs, providers and
resource types belong here and nowhere else, which is what keeps a spec
deployable to a laptop and to three clouds without being rewritten.

A binding may only produce something **at least as restrictive** as the spec it
binds: `orgagents.compiler` re-checks network posture, mounts and permissions
after applying one.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class ModelBinding(BaseModel):
    """Which model serves an agent loop."""

    provider: str = "anthropic"
    model: str = "claude-opus-5"
    subagent_model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 8192


class RuntimeBinding(BaseModel):
    """Which agent framework executes the loop (ADR-0013)."""

    adapter: str = "echo"        # langchain_deepagents | openai_agents_sdk | ...
    options: dict[str, Any] = Field(default_factory=dict)


class EnvironmentBinding(BaseModel):
    """How an abstract environment class becomes a concrete workspace."""

    environment: str                      # EnvironmentClass id
    image: str = "python:3.11-slim"
    packages: list[str] = Field(default_factory=list)
    cpu: str = "1"
    memory: str = "2Gi"
    disk: str = "10Gi"
    accelerator: Optional[str] = None
    node_selector: dict[str, str] = Field(default_factory=dict)


class WorkflowBinding(BaseModel):
    """Which engine runs a declared workflow, and how it is reached (ADR-0056).

    The spec says what a workflow is for; this says what executes it. Engine
    names are deliberately *not* enumerated here — the runtime registry owns
    that vocabulary, the same way no model vendor is named in a spec model
    (ADR-0002). `mode` is the binding author's statement of intent; the
    registry decides the real invocation mode, because a service engine
    declared in-process would skip every check that makes the call safe.

    `endpoint`, `secret_ref`, `tenant` and `send_data_classes` only mean
    anything for an out-of-process engine, where invoking the engine is egress
    and is governed exactly like any other external endpoint (ADR-0030).
    """

    workflow: str = ""                    # WorkflowSpec id; "" binds all of them
    engine: str = "native"
    mode: str = "in_process"              # in_process | out_of_process
    endpoint: Optional[str] = None        # base URL of a service engine
    flow: str = ""                        # the engine's own id for the flow
    # Where a person opens the flow in the engine's own builder (ADR-0110).
    # The designer links out to it; it never embeds the engine's editor.
    editor_url: Optional[str] = None
    secret_ref: Optional[str] = None      # the engine's credential, never ours
    tenant: str = ""                      # the tenant this engine instance is for
    trust: str = "partner"                # EndpointTrust value
    # Data classes the caller may send outward to this engine.
    send_data_classes: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class ProtocolBinding(BaseModel):
    """Which wire protocol an external agent endpoint is reached over.

    The spec says an endpoint exists, what it is trusted as and what may be
    sent to it; *which protocol* reaches it is an implementation choice and
    lives here, like a model provider or a channel (ADR-0002, ADR-0058).
    Protocol names are the runtime registry's vocabulary and are deliberately
    not enumerated in this module — the same stance `WorkflowBinding` takes
    towards engine names.
    """

    endpoint: str = ""                    # AgentEndpoint id; "" binds all
    protocol: str = ""                    # runtime-registry protocol name
    transport: str = ""                   # the protocol's transport variant
    protocol_version: str = ""
    base_url: str = ""                    # the peer's base URL
    options: dict[str, Any] = Field(default_factory=dict)


class ServerBinding(BaseModel):
    """A backing enterprise system, declared once and shared by capabilities.

    An enterprise has a handful of systems — an ERP, a data warehouse, an
    order-management system, a document store — and dozens of capabilities that
    reach into them. Declaring the connection on every capability repeats the
    system's URL, credential and trust posture N times and hides the fact that
    twenty capabilities all land on one server. A server catalog names each
    system once; a capability then references it by `server` and inherits the
    connection (ADR-0085). It is also the deployment's systems inventory — the
    thing an auditor asks for.
    """

    id: str
    #: What kind of system this is, for the reader and the systems inventory.
    kind: str = "mcp"          # mcp | database | http_api | object_store | process | reporting
    description: str = ""
    transport: str = "stdio"   # stdio | http | sse
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    engine: Optional[str] = None
    dsn_secret_ref: Optional[str] = None   # a reference, never a value
    secret_ref: Optional[str] = None
    #: How much to trust what this system returns (mirrors EndpointTrust).
    trust: str = "internal"    # internal | partner | external
    #: Hosts this server itself may reach, for the egress story.
    egress_allowlist: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class CapabilityBinding(BaseModel):
    """How an abstract capability becomes a concrete MCP server mount.

    Two forms: inline (name the connection here) or by reference (`server`
    names a `ServerBinding` in the target's catalog, and the connection is
    inherited; any field set here still overrides it). `server_name` is
    optional when `server` is given — the catalog supplies it (ADR-0085).
    """

    capability: str                       # Capability id
    server: Optional[str] = None          # a ServerBinding id, or None for inline
    server_name: str = ""
    transport: str = "stdio"              # stdio | http | sse
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    engine: Optional[str] = None          # for relational capabilities
    dsn_secret_ref: Optional[str] = None  # a reference, never a value
    options: dict[str, Any] = Field(default_factory=dict)


class ChannelBinding(BaseModel):
    """How an abstract channel becomes a real surface people watch.

    `provider` is where implementation names are allowed: slack, msteams, smtp,
    a ticketing system. `bot_identity_ref` is a reference to the workspace
    identity the agent posts as — a name, never a token (ADR-0015).
    """

    channel: str                          # ChannelSpec id or ChannelClass value
    provider: str = "internal"
    address: str = ""
    workspace: str = ""
    bot_identity_ref: Optional[str] = None
    thread_replies: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class ScheduleBinding(BaseModel):
    """Which scheduler actually fires the triggers (ADR-0020)."""

    provider: str = "internal"     # internal | cloud_scheduler | eventbridge | ...
    queue: str = ""
    dead_letter: str = ""
    max_concurrency: int = 4
    options: dict[str, Any] = Field(default_factory=dict)


class MemoryBinding(BaseModel):
    """Where the two memory tiers actually live (ADR-0028)."""

    session_store: str = "in_process"      # in_process | redis | ...
    long_term_store: str = "relational"    # relational | document | vector | ...
    index: str = ""
    embedding_model: str = ""
    secret_ref: Optional[str] = None
    options: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBinding(BaseModel):
    """Which concrete system backs a declared grounding source (ADR-0023)."""

    knowledge: str                        # KnowledgeSource id
    provider: str = "internal"
    location: str = ""
    index: str = ""
    secret_ref: Optional[str] = None
    options: dict[str, Any] = Field(default_factory=dict)


class InfrastructureBinding(BaseModel):
    """Provider-side choices for the neutral resource set (ADR-0012)."""

    provider: str = "local"               # local | gcp | aws | azure
    region: str = ""
    project: str = ""
    state_backend: str = ""
    network: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class TargetBinding(BaseModel):
    """Everything one target needs beyond the spec."""

    target: str
    model: ModelBinding = Field(default_factory=ModelBinding)
    runtime: RuntimeBinding = Field(default_factory=RuntimeBinding)
    infrastructure: InfrastructureBinding = Field(default_factory=InfrastructureBinding)
    environments: list[EnvironmentBinding] = Field(default_factory=list)
    servers: list[ServerBinding] = Field(default_factory=list)
    capabilities: list[CapabilityBinding] = Field(default_factory=list)
    channels: list[ChannelBinding] = Field(default_factory=list)
    knowledge: list[KnowledgeBinding] = Field(default_factory=list)
    workflows: list[WorkflowBinding] = Field(default_factory=list)
    protocols: list[ProtocolBinding] = Field(default_factory=list)
    scheduler: Optional[ScheduleBinding] = None
    memory: Optional[MemoryBinding] = None
    secrets_backend: str = "environment"
    observability_sink: str = "otel_collector"
    # Per-agent overrides of the runtime/model binding.
    agent_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _server_refs_resolve(self) -> "TargetBinding":
        catalog = {sv.id for sv in self.servers}
        for cb in self.capabilities:
            if cb.server and cb.server not in catalog:
                raise ValueError(
                    f"capability '{cb.capability}' references server "
                    f"'{cb.server}', which the target's `servers:` catalog does "
                    "not declare"
                )
            if not cb.server and not cb.server_name:
                raise ValueError(
                    f"capability '{cb.capability}' names neither a `server` "
                    "from the catalog nor an inline `server_name`"
                )
        return self

    def environment_binding(self, env_id: str) -> Optional[EnvironmentBinding]:
        return next((e for e in self.environments if e.environment == env_id), None)

    def server(self, server_id: str) -> Optional[ServerBinding]:
        return next((sv for sv in self.servers if sv.id == server_id), None)

    def _resolve_capability(self, cb: CapabilityBinding) -> CapabilityBinding:
        """Merge a `server` reference into the capability binding (ADR-0085).

        The catalog supplies the connection; anything set inline still wins, so
        a capability can point at a shared server and override just its DSN.
        The effective `server_name` is the catalog id, which is what the phase
        gate reads to tell whether a separation survives the binding — two
        capabilities on one catalog server collide exactly as two inline
        capabilities on one `server_name` do.
        """
        if not cb.server:
            return cb
        sv = self.server(cb.server)
        if sv is None:
            return cb                       # a dangling ref; validation flags it
        return cb.model_copy(update={
            "server_name": cb.server_name or sv.id,
            "transport": cb.transport if cb.transport != "stdio" else sv.transport,
            "command": cb.command or sv.command,
            "args": cb.args or list(sv.args),
            "url": cb.url or sv.url,
            "engine": cb.engine or sv.engine,
            "dsn_secret_ref": cb.dsn_secret_ref or sv.dsn_secret_ref,
        })

    def capability_binding(self, cap_id: str) -> Optional[CapabilityBinding]:
        raw = next((c for c in self.capabilities if c.capability == cap_id), None)
        return self._resolve_capability(raw) if raw is not None else None

    def channel_binding(self, channel_id: str) -> Optional[ChannelBinding]:
        return next((c for c in self.channels if c.channel == channel_id), None)

    def workflow_binding(self, workflow_id: str) -> Optional[WorkflowBinding]:
        exact = next((w for w in self.workflows if w.workflow == workflow_id), None)
        return exact or next((w for w in self.workflows if not w.workflow), None)

    def protocol_binding(self, endpoint_id: str) -> Optional[ProtocolBinding]:
        exact = next((p for p in self.protocols if p.endpoint == endpoint_id), None)
        return exact or next((p for p in self.protocols if not p.endpoint), None)

    def knowledge_binding(self, source_id: str) -> Optional[KnowledgeBinding]:
        return next((k for k in self.knowledge if k.knowledge == source_id), None)


class Binding(BaseModel):
    """A binding document: one or more target bindings for a spec."""

    spec: str = ""                        # spec name this binding applies to
    version: str = "0.1.0"
    targets: list[TargetBinding] = Field(default_factory=list)

    def for_target(self, target: str) -> Optional[TargetBinding]:
        exact = next((t for t in self.targets if t.target == target), None)
        if exact:
            return exact
        # `terraform:gcp` falls back to a `terraform` binding.
        family = target.split(":")[0]
        return next((t for t in self.targets if t.target == family), None)


def default_binding(target: str) -> TargetBinding:
    """A usable binding for a spec that ships without one."""
    provider = target.split(":")[1] if ":" in target else "local"
    return TargetBinding(
        target=target,
        runtime=RuntimeBinding(adapter="echo"),
        infrastructure=InfrastructureBinding(provider=provider),
    )
