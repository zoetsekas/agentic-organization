"""The Organisation profile (ADR-0112): the organisation, its teams, agents,
sub-agents, people, roles and missions, and how they relate to each other."""
from __future__ import annotations

from ..spec import model as _spec
from .uml import DataType, Draw, Enumeration
from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S
from .uml import props

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

    # -- ADR-0112: the rest of what an organisation's elements hold -----------
    R("agent", "agent", K.ASSOCIATION, "peer of", "peers", SH.REFS,
      linkable=False, draw=Draw.NONE,
      help="a lateral link; delegation otherwise follows the team tree"),
    R("mission", "agent", K.ASSOCIATION, "led by", "leader", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE),
    R("mission", "role", K.ASSOCIATION, "plays", "roles", SH.REF_OBJECTS,
      key="role", association_class="RoleAssignment", linkable=False,
      draw=Draw.NONE, help="roles the mission lends its members for its "
                           "duration"),
    R("person", "team", K.ASSOCIATION, "belongs to", "unit", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{empty = the root}",
      help="the unit whose mandate bounds theirs"),
]

ENUMERATIONS = [
    Enumeration("FlowKind", "FlowKind",
                ("delegate", "consult", "notify", "escalate"),
                "A declared directional interaction between agents "
                "(ADR-0024)."),
    Enumeration("UnitLinkKind", "UnitLinkKind",
                ("oversees", "escalates_to", "serves", "partners_with"),
                "How two units are related when one does not contain the "
                "other (ADR-0081)."),
    Enumeration("HumanRole", "HumanRole",
                ("owner", "approver", "reviewer", "escalation", "operator",
                 "stakeholder"),
                "Why a person is paired with an agent (ADR-0026)."),
    Enumeration("SubAgentKind", "SubAgentKind",
                ("research", "review", "summarize", "extract", "critique",
                 "plan", "verify", "custom"),
                "What a tool-shaped sub-agent is for (ADR-0027)."),
    Enumeration("MissionStatus", "MissionStatus",
                ("proposed", "active", "completed", "disbanded"),
                "Where a short-lived team is in its life (ADR-0039)."),
    Enumeration("RuntimeRequirement", "RuntimeRequirement",
                ("planning", "subagents", "handoff", "structured_output",
                 "long_context"),
                "What the agent loop must support — not which framework "
                "provides it."),
    Enumeration("RoleKind", "", ("agent", "team"),
                "Whether a role is played by an agent or by a whole unit."),
]

DATATYPES = [
    DataType("ScalingPolicy", "ScalingPolicy",
             "How many of an agent run, and how much each takes on "
             "(ADR-0095)."),
    DataType("WorkingHours", "WorkingHours",
             "When the humans on a channel or in a pairing are available."),
]

PROPERTIES = [
    *props("team",
           "id: String", "name: String", "description: String",
           "groups: String [0..*] -- directory groups",
           "placement: Boolean -- a placement boundary (ADR-0069)",
           "shared_instructions: String [0..*] -- instructions every "
           "member carries (ADR-0038)",
           "labels: Map"),
    *props("organization",
           "operating_principles: String [0..*] -- shared instructions, "
           "by text (ADR-0038)"),
    *props("worker", "id: String", "name: String", "instructions: String"),
    *props("agent",
           "description: String",
           "channels: ChannelClass [0..*] -- the surfaces it may use",
           "shared_service: Boolean",
           "runtime_requirements: RuntimeRequirement [0..*]",
           "max_delegation_depth: Integer",
           "scaling: ScalingPolicy", "planning: Boolean", "labels: Map"),
    *props("subagent",
           "kind: SubAgentKind", "purpose: String",
           "returns: String -- what it hands back, in words",
           "max_turns: Integer", "max_runtime_seconds: Integer",
           "parallel_safe: Boolean"),
    *props("person",
           "id: String", "name: String", "contact: String",
           "position: String -- a job title, as prose (ADR-0079)",
           "notify_on: ChannelClass [0..*]",
           "working_hours: WorkingHours [0..1]"),
    *props("role",
           "id: String", "title: String", "version: String",
           "kind: RoleKind", "responsibilities: String [0..*]",
           "constraints: Map"),
    *props("mission",
           "id: String", "name: String", "objective: String",
           "deliverables: String [0..*]", "status: MissionStatus",
           "sponsor: HumanCounterpart [0..1] -- the person whose authority "
           "bounds the mission",
           "starts_on: String [0..1] -- an ISO date",
           "ends_on: String [0..1] -- an ISO date",
           "internal_delegation: Boolean",
           "success_criteria: String [0..*]", "labels: Map"),
    *props("RoleAssignment",
           "withhold: String [0..*] -- permission keys of the role not "
           "granted at this site",
           "conditions: Map"),
    *props("HumanCounterpart",
           "name: String", "contact: String", "role_title: String",
           "roles: HumanRole [0..*]",
           "approves: String [0..*] -- capability or action ids",
           "notify_on: ChannelClass [0..*]",
           "working_hours: WorkingHours [0..1]"),
    *props("InteractionFlow",
           "kind: FlowKind", "description: String",
           "requires_approval: Boolean"),
    *props("UnitLink", "kind: UnitLinkKind", "reason: String"),
    *props("ScalingPolicy",
           "min_instances: Integer", "max_instances: Integer",
           "concurrent_sessions_per_instance: Integer"),
    *props("WorkingHours",
           "timezone: String", "days: Integer [0..*] -- 0 = Monday",
           "start_hour: Integer", "end_hour: Integer",
           "holidays: String [0..*] -- ISO dates"),
]

PROFILE = Profile(
    name="Organisation", imports=("Core",),
    doc="Who the organisation is: its units, agents, people, roles and "
        "missions.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
    enumerations=ENUMERATIONS, datatypes=DATATYPES, properties=PROPERTIES,
)
