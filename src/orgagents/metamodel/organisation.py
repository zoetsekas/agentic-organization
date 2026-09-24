"""The Organisation profile (ADR-0112): the organisation, its teams, agents,
sub-agents, people, roles and missions, and how they relate to each other."""
from __future__ import annotations

from ..spec import model as _spec
from .uml import Draw
from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S

STEREOTYPES = [
    S("Organization", "organization", MC.COMPONENT, "organization",
      "Organization",
      "The root unit of one organisation. A Team by generalisation; exactly "
      "one per System.", palette=False),
    S("Team", "team", MC.COMPONENT, "", "Team",
      "A unit of the organisation; owns its members and sub-teams."),
    S("Agent", "agent", MC.ACTIVE_CLASS, "", "AgentSpec",
      "An active class: it has its own thread of control."),
    S("SubAgent", "subagent", MC.CLASS, "", "SubAgentSpec",
      "A tool-shaped worker owned by the agent that calls it (ADR-0027)."),
    S("Worker", "worker", MC.CLASS, "", "Worker",
      "What an agent and a sub-agent share. Neither is a kind of the other "
      "(ADR-0102).", abstract=True, palette=False),
    S("Person", "person", MC.ACTOR, "people", "Person"),
    S("Role", "role", MC.CLASS, "role_definitions", "Role"),
    S("Mission", "mission", MC.COMPONENT, "missions", "Mission"),
]

RELATIONSHIPS = [
    # -- generalisation: the organisation is the root team ------------------
    R("organization", "team", K.GENERALIZATION, "", "", SH.REF,
      linkable=False, draw=Draw.NONE,
      help="an Organization is a Team: it has a leader, members and "
           "sub-teams, and is the one unit with no parent"),
    R("agent", "worker", K.GENERALIZATION, "", "", SH.REF,
      linkable=False, draw=Draw.NONE),
    R("subagent", "worker", K.GENERALIZATION, "", "", SH.REF,
      linkable=False, draw=Draw.NONE),

    # -- composition: the part lives in the whole ---------------------------
    R("team", "team", K.COMPOSITION, "contains", "teams", SH.PART,
      source_mult="0..1", legacy="contains",
      help="the target becomes a sub-team of the source. Authority and "
           "permissions narrow downward from here (ADR-0008, ADR-0065)"),
    R("team", "agent", K.COMPOSITION, "member", "members", SH.PART,
      source_mult="1", legacy="member", display="has member",
      help="an agent belongs to exactly one team, which is what bounds what "
           "it may hold"),
    R("agent", "subagent", K.COMPOSITION, "uses", "subagents", SH.PART,
      source_mult="1", legacy="uses",
      help="a tool-shaped worker this agent may call (ADR-0027)"),

    # -- associations between the organisation's own elements ---------------
    R("team", "role", K.ASSOCIATION, "plays", "roles", SH.REF_OBJECTS,
      key="role", association_class="RoleAssignment",
      help="a role the whole unit plays: it grants every member, and the "
           "members of its sub-teams (ADR-0007)"),
    R("agent", "role", K.ASSOCIATION, "plays", "roles", SH.REF_OBJECTS,
      key="role", association_class="RoleAssignment",
      help="a role and the capabilities it grants"),
    R("agent", "person", K.ASSOCIATION, "paired with", "humans",
      SH.REF_OBJECTS, key="person", association_class="HumanCounterpart",
      help="a human counterpart (ADR-0026)"),
    R("agent", "agent", K.ASSOCIATION, "successor", "successor", SH.REF,
      target_mult="0..1", linkable=False,
      help="who stands in when this agent cannot run (ADR-0094); set in "
           "Properties"),
    R("team", "agent", K.ASSOCIATION, "leads", "leader", SH.REF,
      source_mult="0..1", target_mult="0..1", linkable=False,
      constraint="{subsets members}",
      help="set from the agent's Properties"),
    R("agent", "agent", K.ASSOCIATION, "flow", "interaction_flows", SH.RECORD,
      legacy="flow", display="may…", association_class="InteractionFlow",
      choices=tuple(k.value for k in _spec.FlowKind),
      help="a declared, directional interaction (ADR-0024). Two agents are "
           "not otherwise connected: membership is what puts them in an "
           "organisation, not a line between them"),
    R("team", "team", K.ASSOCIATION, "unit link", "unit_links", SH.RECORD,
      legacy="association", display="is associated with",
      association_class="UnitLink",
      choices=tuple(k.value for k in _spec.UnitLinkKind),
      help="a relationship that is not containment: oversight, escalation or "
           "a shared service. It grants nothing, narrows nothing and inherits "
           "nothing — an overseer that sits inside what it oversees is "
           "refused (ADR-0081)"),
    R("mission", "agent", K.ASSOCIATION, "includes", "members", SH.REFS),

    # -- what a policy is about (ADR-0008) -----------------------------------
    *[R(k, "principal", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("agent", "team", "role")],
    *[R(k, "resource", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("agent", "team")],
]

PROFILE = Profile(
    name="Organisation", imports=("Core",),
    doc="Who the organisation is: its units, agents, people, roles and "
        "missions.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
)
