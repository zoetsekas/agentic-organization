"""The Authority profile (ADR-0112): what may be decided and by whom —
decisions, mandates, separations of duty, policies and permissions, who
enforces each control, and how autonomously an activity may be done."""
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
    S("Decision", "decision", MC.DATA_TYPE, "decisions", "DecisionClass"),
    S("Separation", "separation", MC.CONSTRAINT, "separations",
      "SeparationRule"),
    S("Policy", "policy", MC.CONSTRAINT, "policies", "PolicyRule"),
]

RELATIONSHIPS = [
    R("separation", "decision", K.ASSOCIATION, "keeps apart", "decisions",
      SH.REFS, target_mult="2..*"),
    R("policy", "principal", K.ASSOCIATION, "applies to", "subjects", SH.REFS,
      constraint="{'*' = every principal}",
      help="who the rule is about: agents, teams or roles, by id"),
    R("policy", "resource", K.ASSOCIATION, "governs", "resources", SH.REFS,
      constraint="{kind given by resource_kinds; '*' = every}",
      help="what the rule allows or denies acting on"),
    # -- a mandate's decisions (ADR-0065) and a permission's resource --------
    R("Mandate", "decision", K.ASSOCIATION, "may decide", "decisions",
      SH.REFS, linkable=False, draw=Draw.NONE,
      help="the decision classes the holder may take, within the conditions"),
    R("Permission", "resource", K.ASSOCIATION, "on", "resource", SH.REF,
      target_mult="1", linkable=False, draw=Draw.NONE,
      constraint="{kind given by resource_kind; an id or a glob; "
                 "'*' = every}"),
]

ENUMERATIONS = [
    Enumeration("ControlEnforcer", "ControlEnforcer",
                ("platform", "application", "both"),
                "Who actually enforces a control (ADR-0073)."),
    Enumeration("AutonomyPosture", "AutonomyPosture",
                ("advisory", "human_decides", "supervised", "autonomous"),
                "How much of an activity an agent does without a person "
                "(ADR-0072)."),
    Enumeration("ResourceKind", "ResourceKind",
                ("data_class", "capability", "agent", "team", "workflow",
                 "environment", "channel"),
                "The kinds of «Resource» a permission or policy can "
                "address: the stereotypes that realise Resource."),
]

DATATYPES = [
    DataType("Mandate", "Mandate",
             "A bounded scope of decision held by a unit, an agent, a person "
             "or a mission (ADR-0065). Acting outside it escalates."),
    DataType("Permission", "Permission",
             "`(action, resource)` with optional conditions (ADR-0008); "
             "granted by a role, or held by a person for authority "
             "(ADR-0079)."),
    DataType("ControlEnforcement", "ControlEnforcement",
             "Who enforces a control, and what is being trusted to them "
             "(ADR-0073)."),
]

PROPERTIES = [
    *props("decision",
           "id: String", "title: String", "description: String"),
    *props("separation",
           "id: String",
           "enforcement: ControlEnforcement -- who enforces the separation",
           "reason: String"),
    *props("policy",
           "id: String", "effect: Effect -- deny always wins",
           "description: String", "actions: Action [0..*]",
           "resource_kinds: ResourceKind [0..*]",
           "conditions: Map -- evaluated by security.rbac (ADR-0008)",
           "unless: Map"),
    *props("Mandate",
           "enforcement: ControlEnforcement -- who enforces the conditions",
           "conditions: Map -- bounds that make a decision class finite; "
           "they accumulate down the tree"),
    *props("Permission",
           "action: Action", "resource_kind: ResourceKind",
           "conditions: Map"),
    *props("ControlEnforcement",
           "enforced_by: ControlEnforcer",
           "enforced_in: String -- what kind of system, in words; the "
           "binding names the product",
           "authoritative: ControlEnforcer [0..1] -- which side wins when "
           "enforced_by is both",
           "application_bounds: Map -- what the application is claimed to "
           "enforce"),
    # Who holds a mandate or permissions, owned here because Organisation
    # does not import Authority.
    *props("team", "mandate: Mandate [0..1]"),
    *props("agent", "mandate: Mandate [0..1]",
           "autonomy: Map -- activity → AutonomyPosture (ADR-0072)"),
    *props("person", "mandate: Mandate [0..1]",
           "permissions: Permission [0..0] -- refused on sight: a person "
           "is a principal for authority, never for access (ADR-0079)"),
    *props("mission", "mandate: Mandate [0..1] -- lent for the mission's "
           "duration, bounded by the sponsor's own"),
    *props("role", "permissions: Permission [0..*]"),
]

PROFILE = Profile(
    name="Authority", imports=("Core", "Organisation"),
    doc="What may be decided, by whom, and what a rule allows or denies.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
    enumerations=ENUMERATIONS, datatypes=DATATYPES, properties=PROPERTIES,
)
