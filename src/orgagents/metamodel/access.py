"""The Access profile (ADR-0112): what an agent may reach and where it runs —
capabilities and the limits enforced at their boundary, external endpoints
and sandbox environments."""
from __future__ import annotations

from .uml import DataType, Draw, Enumeration
from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S
from .uml import props

STEREOTYPES = [
    S("Callable", "callable", MC.INTERFACE, "", "",
      "Anything a workflow step may call as a tool: a declared Tool, or a "
      "Capability (and its operations, `capability__operation`).",
      abstract=True, palette=False),
    S("Wrappable", "wrappable", MC.INTERFACE, "", "",
      "What a Tool may wrap: a Capability, a SubAgent, a Workflow or an "
      "Endpoint (ADR-0029).", abstract=True, palette=False),
    S("Capability", "capability", MC.INTERFACE, "capabilities", "Capability",
      "What may be done; an agent that has it provides the interface."),
    S("Environment", "environment", MC.EXECUTION_ENVIRONMENT, "environments",
      "EnvironmentClass",
      "A sandbox class. Agents are deployed into it (ADR-0082)."),
    S("Endpoint", "endpoint", MC.INTERFACE, "endpoints", "AgentEndpoint"),
]

RELATIONSHIPS = [
    # -- deployment: an agent runs in a sandbox -----------------------------
    R("agent", "environment", K.DEPLOYMENT, "runs in", "environments",
      SH.REF_OBJECTS, key="environment", draw=Draw.NEST,
      association_class="EnvironmentOverride",
      help="the sandbox class this agent runs in; one of several "
           "(ADR-0082). Drop an agent into an environment's box to deploy it"),
    R("worker", "capability", K.REALIZATION, "provides", "capabilities",
      SH.REFS, help="what this worker may do"),
    R("agent", "endpoint", K.USAGE, "calls", "endpoints", SH.REFS,
      help="an external agent this one may call"),
    R("role", "capability", K.ASSOCIATION, "grants", "capabilities", SH.REFS),
    R("subagent", "environment", K.DEPLOYMENT, "runs in", "environments",
      SH.REFS, draw=Draw.NEST,
      constraint="{subsets parent.environments}",
      help="a sandbox this sub-agent runs in; one of its parent's"),
    # A sub-agent is called like a tool by being *wrapped* by one — not by
    # being one: `agent.tools` holds Tool ids, never sub-agents (ADR-0027).
    *[R(k, "wrappable", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("capability", "subagent", "endpoint")],
    *[R(k, "resource", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("capability", "environment")],
    R("capability", "callable", K.REALIZATION, "", "", SH.REF,
      linkable=False, draw=Draw.NONE),
    R("capability", "decision", K.ASSOCIATION, "exercises", "decision",
      SH.REF, target_mult="0..1", linkable=False, draw=Draw.NONE,
      help="the decision class using it takes (ADR-0065)"),
]

ENUMERATIONS = [
    Enumeration("NetworkPosture", "NetworkPosture",
                ("none", "allowlist", "internal", "open"),
                "How far an environment may reach over the network."),
    Enumeration("ToolchainClass", "ToolchainClass",
                ("none", "scripting", "data_analysis", "software_build",
                 "browser", "document", "model_training", "network_client"),
                "Categories of tooling an environment may need, not concrete "
                "images (ADR-0055)."),
    Enumeration("ResourceTier", "ResourceTier",
                ("minimal", "small", "medium", "large", "accelerated"),
                "Abstract size of an execution environment."),
    Enumeration("Persistence", "Persistence",
                ("ephemeral", "session", "agent"),
                "How long an environment's disk outlives a run."),
    Enumeration("EndpointTrust", "EndpointTrust",
                ("internal", "partner", "external"),
                "How far an external agent endpoint is trusted (ADR-0030)."),
]

DATATYPES = [
    DataType("CapabilityConstraint", "CapabilityConstraint",
             "Limits enforced at the capability boundary, never by the "
             "model; a tool may narrow them, never widen them."),
]

PROPERTIES = [
    *props("capability",
           "id: String", "description: String", "action: Action",
           "resource_class: String -- the kind of system reached, in words",
           "constraints: CapabilityConstraint",
           "autonomy: AutonomyPosture",
           "secret_ref: String [0..1] -- a reference, never a secret "
           "(ADR-0015)"),
    *props("CapabilityConstraint",
           "max_rows: Integer [0..1]", "masked_fields: String [0..*]",
           "allowed_operations: String [0..*]",
           "resource_scope: String [0..*]", "requires_approval: Boolean",
           "rate_per_minute: Integer [0..1]",
           "enforcement: ControlEnforcement -- who enforces these limits "
           "(ADR-0073)"),
    *props("endpoint",
           "id: String", "description: String", "trust: EndpointTrust",
           "provides: String [0..*] -- the operations the external agent "
           "offers, by its own names",
           "treat_output_as_data: Boolean", "requires_approval: Boolean",
           "response_sla_seconds: Integer [0..1]",
           "secret_ref: String [0..1]"),
    *props("environment",
           "id: String", "description: String",
           "toolchains: ToolchainClass [0..*]", "tier: ResourceTier",
           "network: NetworkPosture", "egress_allowlist: String [0..*]",
           "mounts: String [0..*]", "persistence: Persistence",
           "timeout_seconds: Integer", "secret_refs: String [0..*]"),
    # A person is a principal for authority, never for access (ADR-0079):
    # the field exists only so the validator can refuse it, so it may hold
    # nothing.
    *props("person", "capabilities: String [0..0] -- refused on sight"),
    *props("EnvironmentOverride",
           "timeout_seconds: Integer [0..1]",
           "network: NetworkPosture [0..1]",
           "egress_allowlist: String [0..*] -- absent = the class's own",
           "drop_mounts: String [0..*]"),
]

PROFILE = Profile(
    name="Access", imports=("Core", "Organisation", "Authority"),
    doc="What an agent may reach, and the sandboxes it runs in.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
    enumerations=ENUMERATIONS, datatypes=DATATYPES, properties=PROPERTIES,
)
