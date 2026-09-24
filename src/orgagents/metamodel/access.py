"""The Access profile (ADR-0112): what an agent may reach and where it runs —
capabilities, external endpoints and sandbox environments."""
from __future__ import annotations

from .uml import Draw
from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S

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
]

PROFILE = Profile(
    name="Access", imports=("Core", "Organisation"),
    doc="What an agent may reach, and the sandboxes it runs in.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
)
