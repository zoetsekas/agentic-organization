"""The Deployment profile (ADR-0112 §4): the binding — how a design is
realised on a target — modelled as UML deployment.

A binding document is an Artifact holding one DeploymentSpecification per
target; a server is a Node; each spec element a target binds (a capability,
an environment, a channel, a knowledge source, a workflow, an endpoint) is
bound by a Deployment whose attributes are the binding record, an
AssociationClass, and a capability binding is deployed on the server that
realises it.

It imports every spec profile and none imports it, which is how the spec's
neutrality (ADR-0004) is kept: nothing in the spec profiles can name a
server, a runtime or a cloud. It is not part of the assembled spec
`PROFILE`; `metamodel.PROFILES` lists it last.
"""
from __future__ import annotations

from .uml import DataType, Draw
from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S
from .uml import props

STEREOTYPES = [
    S("Binding", "binding", MC.ARTIFACT, "", "Binding",
      "A binding document: how one spec is realised on one or more "
      "targets.", palette=False),
    S("Target", "target", MC.DEPLOYMENT_SPECIFICATION, "", "TargetBinding",
      "Everything one target needs beyond the spec.", palette=False),
    S("Server", "server", MC.NODE, "", "ServerBinding",
      "A backing enterprise system, declared once and shared by the "
      "capabilities deployed on it (ADR-0085).", palette=False),
]


def _binds(target: str, field: str, key: str, ac: str, help_: str = "") -> R:
    """A target binds one spec element: a Deployment whose record is `ac`."""
    return R("target", target, K.DEPLOYMENT, "binds", field, SH.REF_OBJECTS,
             key=key, association_class=ac, linkable=False, draw=Draw.NONE,
             source_mult="0..*", help=help_)


RELATIONSHIPS = [
    R("binding", "system", K.ASSOCIATION, "binds", "spec", SH.REF,
      target_mult="1", linkable=False, draw=Draw.NONE,
      help="the spec this binding realises, by name"),
    R("binding", "target", K.COMPOSITION, "target", "targets", SH.PART,
      source_mult="1", linkable=False, draw=Draw.NONE),
    R("target", "server", K.COMPOSITION, "server", "servers", SH.PART,
      source_mult="1", linkable=False, draw=Draw.NONE),
    _binds("capability", "capabilities", "capability", "CapabilityBinding",
           "how a capability becomes a concrete MCP server mount"),
    _binds("environment", "environments", "environment",
           "EnvironmentBinding",
           "how an environment class becomes a concrete workspace"),
    _binds("channel", "channels", "channel", "ChannelBinding"),
    _binds("knowledge", "knowledge", "knowledge", "KnowledgeBinding"),
    _binds("workflow", "workflows", "workflow", "WorkflowBinding",
           "which engine runs a workflow (ADR-0056, ADR-0110)"),
    _binds("endpoint", "protocols", "endpoint", "ProtocolBinding",
           "which wire protocol an external endpoint is reached over"),
    R("CapabilityBinding", "server", K.DEPLOYMENT, "deployed on", "server",
      SH.REF, target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{empty = the binding mounts its own server}"),
    R("WorkflowBinding", "data_class", K.ASSOCIATION, "may send",
      "send_data_classes", SH.REFS, linkable=False, draw=Draw.NONE),
]

DATATYPES = [
    DataType("ModelBinding", "ModelBinding",
             "Which model serves an agent loop."),
    DataType("RuntimeBinding", "RuntimeBinding",
             "Which agent framework executes the loop (ADR-0013)."),
    DataType("InfrastructureBinding", "InfrastructureBinding",
             "Provider-side choices for the neutral resource set "
             "(ADR-0012)."),
    DataType("ScheduleBinding", "ScheduleBinding",
             "Which scheduler fires the triggers (ADR-0020)."),
    DataType("MemoryBinding", "MemoryBinding",
             "Where the two memory tiers live (ADR-0028)."),
]

_MOUNT = ("transport: String -- stdio or http", "command: String [0..1]",
          "args: String [0..*]", "url: String [0..1]",
          "engine: String [0..1]", "dsn_secret_ref: String [0..1]",
          "options: Map")

PROPERTIES = [
    *props("binding", "version: String"),
    *props("target",
           "target: String -- the target's name",
           "model: ModelBinding", "runtime: RuntimeBinding",
           "infrastructure: InfrastructureBinding",
           "scheduler: ScheduleBinding [0..1]",
           "memory: MemoryBinding [0..1]",
           "secrets_backend: String", "observability_sink: String",
           "agent_overrides: Map -- agent id → overrides"),
    *props("server",
           "id: String", "kind: String", "description: String", *_MOUNT,
           "secret_ref: String [0..1]", "trust: String",
           "egress_allowlist: String [0..*]"),
    *props("CapabilityBinding", "server_name: String", *_MOUNT),
    *props("EnvironmentBinding",
           "image: String", "packages: String [0..*]", "cpu: String",
           "memory: String", "disk: String", "accelerator: String [0..1]",
           "node_selector: Map"),
    *props("ChannelBinding",
           "provider: String", "address: String", "workspace: String",
           "bot_identity_ref: String [0..1]", "thread_replies: Boolean",
           "options: Map"),
    *props("KnowledgeBinding",
           "provider: String", "location: String", "index: String",
           "secret_ref: String [0..1]", "options: Map"),
    *props("WorkflowBinding",
           "engine: String", "mode: String",
           "endpoint: String [0..1] -- where the engine is reached",
           "flow: String -- the engine's own id for the body",
           "editor_url: String [0..1] -- for Open in (ADR-0110)",
           "secret_ref: String [0..1]", "tenant: String", "trust: String",
           "requires_approval: Boolean", "options: Map"),
    *props("ProtocolBinding",
           "protocol: String", "transport: String",
           "protocol_version: String", "base_url: String", "options: Map"),
    *props("ModelBinding",
           "provider: String", "model: String",
           "subagent_model: String [0..1]", "temperature: Real",
           "max_tokens: Integer"),
    *props("RuntimeBinding", "adapter: String", "options: Map"),
    *props("InfrastructureBinding",
           "provider: String", "region: String", "project: String",
           "state_backend: String", "network: String", "labels: Map",
           "options: Map"),
    *props("ScheduleBinding",
           "provider: String", "queue: String", "dead_letter: String",
           "max_concurrency: Integer", "options: Map"),
    *props("MemoryBinding",
           "session_store: String", "long_term_store: String",
           "index: String", "embedding_model: String",
           "secret_ref: String [0..1]", "options: Map"),
]

PROFILE = Profile(
    name="Deployment",
    imports=("Core", "Organisation", "Authority", "Access", "Data",
             "Knowledge", "Process", "Assurance"),
    doc="The binding: targets, servers, runtimes and one binding per spec "
        "concern.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
    datatypes=DATATYPES, properties=PROPERTIES,
)
