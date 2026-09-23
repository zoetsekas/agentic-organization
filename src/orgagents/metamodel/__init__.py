"""The metamodel: a subset of UML, and the platform's kinds as a profile over it
(ADR-0101).

Three layers, as UML itself has them:

* **Metaclasses** — the UML concepts the platform uses: Class (and active
  Class), Component, Interface, Actor, Artifact, Node, ExecutionEnvironment,
  DataType, Activity, Event, Constraint. A subset on purpose: declaring the
  rest of UML would be declaring things nothing reads.
* **Stereotypes** — the platform's own kinds. Every palette kind extends
  exactly one metaclass: «Agent» is an active Class, «Capability» an Interface,
  «Environment» an ExecutionEnvironment, and so on. Together they are the
  *OrgAgents profile*.
* **Relationships** — every link the spec can hold, declared once, as a UML
  relationship kind between two stereotypes, with multiplicities, the end that
  owns it, and the spec field it lives in.

The relationship kind is not a label. It decides behaviour in the designer:
a composition moves the part when it is linked or dropped, a deployment is
drawn as nesting and set by dropping an agent into an environment, an
association is an edge that can be drawn from either end, and a dependency is
a dashed, stereotyped edge. That is the reason to adopt UML rather than to
rename the rules that grew before it.

`link_rules()` derives the designer's link table from this module, so the
canvas, the API and the tests read one declaration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from ..spec import model as _spec


# --------------------------------------------------------------------------
# UML — the subset in use
# --------------------------------------------------------------------------


class MetaClass(str, Enum):
    """The UML metaclasses the profile extends."""

    CLASS = "Class"
    #: A Class with `isActive = true`: it has its own thread of control. The
    #: precise UML reading of an agent.
    ACTIVE_CLASS = "Class {isActive}"
    COMPONENT = "Component"
    INTERFACE = "Interface"
    ACTOR = "Actor"
    ARTIFACT = "Artifact"
    NODE = "Node"
    EXECUTION_ENVIRONMENT = "ExecutionEnvironment"
    DATA_TYPE = "DataType"
    ACTIVITY = "Activity"
    EVENT = "Event"
    CONSTRAINT = "Constraint"
    COMMENT = "Comment"
    #: The root: a Model is a Package that owns everything in one system.
    MODEL = "Model"
    #: An Association that is also a Class: a link with attributes of its own
    #: (a flow's kind, a role assignment's withheld capabilities).
    ASSOCIATION_CLASS = "AssociationClass"
    #: The configuration of one Deployment (UML 19.3): a sandbox override.
    DEPLOYMENT_SPECIFICATION = "DeploymentSpecification"


class RelKind(str, Enum):
    """UML's relationship kinds, as the platform uses them."""

    #: The part's lifetime is the whole's; it lives inside the whole in the
    #: spec. Linking or dropping *moves* it.
    COMPOSITION = "composition"
    #: Shared aggregation. Declared for completeness of the kinds; the profile
    #: uses association instead, because UML leaves shared aggregation's
    #: semantics to the modeller and a kind with no agreed meaning decides
    #: nothing.
    AGGREGATION = "aggregation"
    #: A reference: the target stands on its own and many may point at it.
    ASSOCIATION = "association"
    #: One element relies on another.
    DEPENDENCY = "dependency"
    #: A dependency where one uses the other to do its work.
    USAGE = "usage"
    #: An artifact runs on a node or execution environment — an agent in a
    #: sandbox. Drawn as nesting.
    DEPLOYMENT = "deployment"
    #: One element provides what another specifies.
    REALIZATION = "realization"
    GENERALIZATION = "generalization"


class Shape(str, Enum):
    """How a relationship is stored in the spec. Decides how a link writes it."""

    #: A list of owned objects: `team.members`. Linking moves the part.
    PART = "part"
    #: A single id: `trigger.agent`.
    REF = "ref"
    #: A list of ids: `agent.knowledge`.
    REFS = "refs"
    #: A list of objects keyed by an id: `agent.environments` holds
    #: `{environment: id, ...}`. `key` names the id field.
    REF_OBJECTS = "ref_objects"
    #: A top-level list of `{source, target, kind}` records:
    #: `interaction_flows`, `unit_links`.
    RECORD = "record"


class Draw(str, Enum):
    """How the canvas presents a relationship."""

    EDGE = "edge"
    #: The target is drawn *inside* the source's box.
    NEST = "nest"
    #: Drawn inside the owner's box as a chip while only one owner holds it,
    #: and as an edge once shared — the existing held-component behaviour.
    INLINE = "inline"
    #: Not drawn as its own mark; shown in Properties.
    NONE = "none"


@dataclass(frozen=True)
class Stereotype:
    """One of the platform's kinds, extending one UML metaclass."""

    name: str              # «Agent»
    kind: str              # the palette kind, "agent"
    extends: MetaClass
    #: Where instances live in the spec: a SystemSpec collection, or a path
    #: for nested ones. Empty for kinds that live inside another (agents live
    #: in teams).
    collection: str = ""
    #: The spec model class, by name, whose fields relationships may name.
    model: str = ""
    doc: str = ""


@dataclass(frozen=True)
class Relationship:
    """A UML relationship between two stereotypes, and where it lives.

    `owner` is the end whose spec object holds the field. For most, the source;
    an association can be drawn from either end, and writes the same field.
    """

    source: str            # palette kind
    target: str            # palette kind
    kind: RelKind
    #: The relationship's stereotype, shown on the edge: «consults», «member».
    stereotype: str
    field: str
    shape: Shape
    owner: str = "source"
    key: str = ""          # for REF_OBJECTS: the id field in each object
    #: Multiplicity at the source and target ends, UML notation.
    source_mult: str = "0..*"
    target_mult: str = "0..*"
    draw: Draw = Draw.EDGE
    #: May a person draw it on the canvas? False for ones set another way —
    #: team leadership is set from the agent's Properties.
    linkable: bool = True
    #: The word the designer used before this module, kept so every existing
    #: canvas code path still recognises its rule.
    legacy: str = ""
    help: str = ""
    #: A closed vocabulary the link must choose from when drawn — a flow's
    #: kind, a unit link's kind. Read from the spec's own enums.
    choices: tuple[str, ...] = ()
    #: The verb shown on the canvas when it reads better than the stereotype.
    display: str = ""
    #: When the link carries attributes of its own, the UML AssociationClass
    #: (or, for a Deployment, the DeploymentSpecification) that types it, by
    #: spec model name. The YAML object in the list *is* the link instance.
    association_class: str = ""
    #: A UML constraint on the relationship, in OCL-ish text: `{subsets
    #: members}`, `{ordered}`.
    constraint: str = ""

    @property
    def label(self) -> str:
        return self.display or self.stereotype

    def owner_kind(self) -> str:
        return self.source if self.owner == "source" else self.target


@dataclass
class Profile:
    name: str
    stereotypes: list[Stereotype] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)

    def stereotype(self, kind: str) -> Optional[Stereotype]:
        return next((s for s in self.stereotypes if s.kind == kind), None)


# --------------------------------------------------------------------------
# The OrgAgents profile
# --------------------------------------------------------------------------

S = Stereotype
MC = MetaClass

STEREOTYPES = [
    S("Team", "team", MC.COMPONENT, "organization", "Team",
      "A unit of the organisation; owns its members and sub-teams."),
    S("Agent", "agent", MC.ACTIVE_CLASS, "", "AgentSpec",
      "An active class: it has its own thread of control."),
    S("SubAgent", "subagent", MC.CLASS, "", "SubAgentSpec",
      "A tool-shaped worker owned by the agent that calls it (ADR-0027)."),
    S("Skill", "skill", MC.ARTIFACT, "skills", "SkillSpec"),
    S("Plugin", "plugin", MC.COMPONENT, "plugins", "PluginSpec"),
    S("Tool", "tool", MC.INTERFACE, "tools", "ToolSpec"),
    S("Person", "person", MC.ACTOR, "people", "Person"),
    S("Role", "role", MC.CLASS, "roles", "Role"),
    S("Decision", "decision", MC.DATA_TYPE, "decisions", "DecisionClass"),
    S("Separation", "separation", MC.CONSTRAINT, "separations",
      "SeparationRule"),
    S("Policy", "policy", MC.CONSTRAINT, "policies", "PolicyRule"),
    S("Capability", "capability", MC.INTERFACE, "capabilities", "Capability",
      "What may be done; an agent that has it provides the interface."),
    S("DataClass", "data_class", MC.DATA_TYPE, "data_classes", "DataClass"),
    S("Environment", "environment", MC.EXECUTION_ENVIRONMENT, "environments",
      "EnvironmentClass",
      "A sandbox class. Agents are deployed into it (ADR-0082)."),
    S("Endpoint", "endpoint", MC.INTERFACE, "endpoints", "AgentEndpoint"),
    S("Mission", "mission", MC.COMPONENT, "missions", "Mission"),
    S("Workflow", "workflow", MC.ACTIVITY, "workflows", "WorkflowSpec"),
    S("Trigger", "trigger", MC.EVENT, "triggers", "TriggerSpec"),
    S("Channel", "channel", MC.CLASS, "channels", "ChannelSpec"),
    S("Knowledge", "knowledge", MC.ARTIFACT, "knowledge", "KnowledgeSource"),
    S("MemoryNamespace", "memory_namespace", MC.ARTIFACT, "memory.namespaces",
      "MemoryNamespace"),
    S("Guardrail", "guardrail", MC.CONSTRAINT, "guardrails", "Guardrail"),
    S("OutputContract", "output_contract", MC.CONSTRAINT, "output_contracts",
      "OutputContract"),
    S("Evaluation", "evaluation", MC.CONSTRAINT, "lifecycle.evaluations",
      "EvaluationCase"),
    S("Note", "note", MC.COMMENT, "", ""),
    S("System", "system", MC.MODEL, "", "SystemSpec",
      "The Model: one organisation design, owning every element in it."),
]

#: Stereotypes that are not palette kinds.
NON_PALETTE = {"system"}

R = Relationship
K = RelKind
SH = Shape

RELATIONSHIPS = [
    # -- composition: the part lives in the whole --------------------------
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

    # -- deployment: an agent runs in a sandbox -----------------------------
    R("agent", "environment", K.DEPLOYMENT, "runs in", "environments",
      SH.REF_OBJECTS, key="environment", draw=Draw.NEST,
      association_class="EnvironmentOverride",
      help="the sandbox class this agent runs in; one of several "
           "(ADR-0082). Drop an agent into an environment's box to deploy it"),

    # -- associations an agent owns -----------------------------------------
    R("agent", "knowledge", K.ASSOCIATION, "consults", "knowledge", SH.REFS,
      help="grounding material this agent may consult"),
    R("agent", "capability", K.REALIZATION, "provides", "capabilities",
      SH.REFS, help="what this agent may do"),
    R("agent", "role", K.ASSOCIATION, "plays", "roles", SH.REF_OBJECTS,
      key="role", association_class="RoleAssignment", help="a role and the capabilities it grants"),
    R("agent", "skill", K.ASSOCIATION, "holds", "skills", SH.REFS,
      draw=Draw.INLINE, legacy="holds",
      help="a named competence this agent may exercise. Shared freely: "
           "declaring it on a second agent does not take it from the first"),
    R("agent", "plugin", K.ASSOCIATION, "holds", "plugins", SH.REFS,
      draw=Draw.INLINE, legacy="holds",
      help="a packaged extension this agent loads"),
    R("agent", "tool", K.USAGE, "holds", "tools", SH.REFS,
      draw=Draw.INLINE, legacy="holds",
      help="a tool this agent may call. A tool held by one agent is drawn "
           "inside it; one held by several is drawn shared"),
    R("agent", "endpoint", K.USAGE, "calls", "endpoints", SH.REFS,
      help="an external agent this one may call"),
    R("agent", "workflow", K.USAGE, "runs", "workflows", SH.REFS,
      help="an encoded process this agent may invoke"),
    R("agent", "data_class", K.ASSOCIATION, "produces", "produces_data",
      SH.REFS, help="data this agent produces (ADR-0099)"),
    R("agent", "data_class", K.ASSOCIATION, "relies on", "data_dependencies",
      SH.REF_OBJECTS, key="data_class", association_class="DataDependency",
      help="data this agent relies on, with freshness (ADR-0099)"),
    R("agent", "person", K.ASSOCIATION, "paired with", "humans",
      SH.REF_OBJECTS, key="person", association_class="HumanCounterpart",
      help="a human counterpart (ADR-0026)"),
    R("agent", "guardrail", K.ASSOCIATION, "guarded by", "guardrails", SH.REFS),
    R("agent", "output_contract", K.ASSOCIATION, "returns", "output_contract",
      SH.REF, target_mult="0..1"),
    R("agent", "agent", K.ASSOCIATION, "successor", "successor", SH.REF,
      target_mult="0..1", linkable=False,
      help="who stands in when this agent cannot run (ADR-0094); set in "
           "Properties"),
    R("team", "agent", K.ASSOCIATION, "leads", "leader", SH.REF,
      source_mult="0..1", target_mult="0..1", linkable=False,
      constraint="{subsets members}",
      help="set from the agent's Properties"),

    # -- dependencies between agents and work -------------------------------
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
    R("trigger", "agent", K.ASSOCIATION, "fires", "agent", SH.REF,
      owner="source", target_mult="1", legacy="fires",
      help="unattended work: the trigger wakes this agent (ADR-0018)"),
    R("trigger", "workflow", K.ASSOCIATION, "starts", "workflow", SH.REF,
      target_mult="0..1"),

    # -- what the building blocks reach -------------------------------------
    R("role", "capability", K.ASSOCIATION, "grants", "capabilities", SH.REFS),
    R("capability", "data_class", K.ASSOCIATION, "reaches", "data_classes",
      SH.REFS),
    R("knowledge", "data_class", K.ASSOCIATION, "contains", "data_classes",
      SH.REFS),
    R("memory_namespace", "data_class", K.ASSOCIATION, "stores",
      "data_classes", SH.REFS),
    R("guardrail", "data_class", K.ASSOCIATION, "protects", "data_classes",
      SH.REFS),
    R("endpoint", "data_class", K.ASSOCIATION, "may receive",
      "send_data_classes", SH.REFS),
    R("skill", "capability", K.USAGE, "requires",
      "requires_capabilities", SH.REFS),
    R("plugin", "capability", K.USAGE, "requires",
      "requires_capabilities", SH.REFS),
    R("plugin", "skill", K.ASSOCIATION, "provides", "provides_skills", SH.REFS),
    R("plugin", "tool", K.ASSOCIATION, "provides", "provides_tools", SH.REFS),
    R("separation", "decision", K.ASSOCIATION, "keeps apart", "decisions",
      SH.REFS, target_mult="2..*"),
    R("evaluation", "agent", K.ASSOCIATION, "evaluates", "applies_to", SH.REFS),
    R("channel", "agent", K.ASSOCIATION, "includes", "members", SH.REFS),
    R("mission", "agent", K.ASSOCIATION, "includes", "members", SH.REFS),
    R("subagent", "capability", K.REALIZATION, "provides", "capabilities",
      SH.REFS),
    R("subagent", "knowledge", K.ASSOCIATION, "consults", "knowledge", SH.REFS),
]

# -- the Model owns every top-level element (composition from «System») -----
RELATIONSHIPS += [
    R("system", st.kind, K.COMPOSITION, "owns", st.collection, SH.PART,
      source_mult="1", target_mult="0..1" if st.kind == "team" else "0..*",
      linkable=False, draw=Draw.NONE)
    for st in STEREOTYPES
    if st.collection and st.kind != "system"
]

PROFILE = Profile(name="OrgAgents", stereotypes=STEREOTYPES,
                  relationships=RELATIONSHIPS)


# --------------------------------------------------------------------------
# Projections
# --------------------------------------------------------------------------

def link_rules(profile: Profile = PROFILE) -> list[dict[str, Any]]:
    """The designer's link table, derived from the profile.

    `relationship` keeps the word each existing canvas code path recognises
    (`contains`, `member`, `uses`, `holds`, `flow`, `association`, `fires`)
    and uses the stereotype for everything new, which the canvas handles by
    `shape`. `writes` keeps its old form, `<owner kind>.<field>`.
    """
    rules = []
    for r in profile.relationships:
        if not r.linkable:
            continue
        rules.append({
            "source": r.source,
            "target": r.target,
            "relationship": r.legacy or r.stereotype,
            "writes": (r.field if r.shape is Shape.RECORD
                       else f"{r.owner_kind()}.{r.field}"),
            "label": r.label,
            "help": r.help,
            **({"kinds": list(r.choices)} if r.choices else {}),
            "uml": r.kind.value,
            "shape": r.shape.value,
            "field": r.field,
            "owner": r.owner,
            "key": r.key,
            "draw": r.draw.value,
            "multiplicity": [r.source_mult, r.target_mult],
            # An association can be drawn from either end; ownership and
            # deployment have a direction a person should not have to guess.
            "bidirectional": r.kind in (RelKind.ASSOCIATION, RelKind.USAGE,
                                        RelKind.REALIZATION,
                                        RelKind.DEPENDENCY)
                             and r.shape is not Shape.RECORD,
        })
    return rules


def describe(profile: Profile = PROFILE) -> dict[str, Any]:
    """The profile as data, for the API and for anybody reading the model."""
    return {
        "profile": profile.name,
        "metaclasses": [m.value for m in MetaClass],
        "relationship_kinds": [k.value for k in RelKind],
        "stereotypes": [
            {"name": f"«{s.name}»", "kind": s.kind, "extends": s.extends.value,
             "collection": s.collection, "doc": s.doc}
            for s in profile.stereotypes
        ],
        "relationships": [
            {"source": r.source, "target": r.target, "kind": r.kind.value,
             "stereotype": f"«{r.stereotype}»",
             "multiplicity": [r.source_mult, r.target_mult],
             "field": f"{r.owner_kind()}.{r.field}", "shape": r.shape.value,
             "draw": r.draw.value, "linkable": r.linkable,
             "association_class": r.association_class,
             "constraint": r.constraint}
            for r in profile.relationships
        ],
    }


# --------------------------------------------------------------------------
# Diagrams of the profile — for review, generated so they cannot drift
# --------------------------------------------------------------------------

_PUML_ARROW = {
    RelKind.COMPOSITION: "*--",
    RelKind.AGGREGATION: "o--",
    RelKind.ASSOCIATION: "-->",
    RelKind.DEPENDENCY: "..>",
    RelKind.USAGE: "..>",
    RelKind.DEPLOYMENT: "..>",
    RelKind.REALIZATION: "..|>",
    RelKind.GENERALIZATION: "--|>",
}


def _cls(kind: str) -> str:
    return "".join(p.title() for p in kind.split("_"))


def _attributes(model_name: str, skip: set[str]) -> list[str]:
    """`name : Type` lines for a spec model's own fields, for a class box."""
    cls = getattr(_spec, model_name, None)
    if cls is None:
        return []
    out = []
    for name, f in cls.model_fields.items():
        if name in skip:
            continue
        ann = f.annotation
        t = (ann.__name__ if isinstance(ann, type) else str(ann))
        for junk in ("typing.", "orgagents.spec.model.", "<class '", "'>"):
            t = t.replace(junk, "")
        t = t.replace("Optional[", "").rstrip("]") + " [0..1]" \
            if t.startswith("Optional[") else t
        out.append(f"{name} : {t}")
    return out


def to_plantuml_profile(profile: Profile = None) -> str:  # type: ignore[assignment]
    """The profile diagram: each stereotype extends a UML metaclass."""
    p = profile or PROFILE
    out = ["@startuml OrgAgentsProfile", "!pragma layout smetana",
           "hide empty members", "left to right direction",
           'title OrgAgents profile — stereotypes extending UML metaclasses', ""]
    for mc in sorted({s.extends for s in p.stereotypes}, key=lambda m: m.value):
        out.append(f'class "{mc.value}" as MC_{mc.name} <<metaclass>>')
    out.append(f'class "AssociationClass" as MC_ASSOCIATION_CLASS <<metaclass>>')
    out.append(f'class "DeploymentSpecification" as MC_DEPLOYMENT_SPECIFICATION'
               f' <<metaclass>>')
    for s in p.stereotypes:
        out.append(f'class "{s.name}" as ST_{s.kind} <<stereotype>>')
        # UML extension: a line with a filled arrowhead to the metaclass.
        out.append(f"ST_{s.kind} --|> MC_{s.extends.name} : <<extends>>")
    for r in p.relationships:
        if r.association_class:
            mc = ("DEPLOYMENT_SPECIFICATION" if r.kind is RelKind.DEPLOYMENT
                  else "ASSOCIATION_CLASS")
            out.append(f'class "{r.association_class}" as ST_{r.association_class}'
                       f' <<stereotype>>')
            out.append(f"ST_{r.association_class} --|> MC_{mc} : <<extends>>")
    out.append("@enduml")
    return "\n".join(out) + "\n"


def to_plantuml(profile: Profile = None) -> str:  # type: ignore[assignment]
    """The model as a strict UML class diagram.

    Everything the «System» Model owns sits in its package (ownership by
    containment, which is UML's own notation for a Model's packagedElements).
    Associations are navigable from the end that stores them; composition has
    the diamond on the whole; a link with attributes is an AssociationClass
    attached by a dashed line; a deployment carries its DeploymentSpecification.
    """
    p = profile or PROFILE
    out = ["@startuml OrgAgentsModel", "!pragma layout smetana",
           "hide empty methods", "skinparam classAttributeIconSize 0",
           'title OrgAgents model — strict UML (ADR-0101)', ""]
    sys_st = p.stereotype("system")
    out.append(f'package "<<{sys_st.name}>> Model" as SystemModel {{' if sys_st
               else "package Model {")
    keys = {r.key for r in p.relationships if r.key}
    for s in p.stereotypes:
        if s.kind == "system":
            continue
        stereo = f"<<{s.name}>>" + (" <<active>>" if s.extends is
                                     MetaClass.ACTIVE_CLASS else "")
        kw = {MetaClass.INTERFACE: "interface", MetaClass.ACTOR: "class",
              MetaClass.DATA_TYPE: "class"}.get(s.extends, "class")
        out.append(f"  {kw} {_cls(s.kind)} {stereo}")
    for r in p.relationships:
        if r.association_class:
            kind = ("DeploymentSpecification" if r.kind is RelKind.DEPLOYMENT
                    else "AssociationClass")
            attrs = _attributes(r.association_class,
                                {"source", "target", r.key})
            out.append(f"  class {r.association_class} <<{kind}>> {{")
            out.extend(f"    {a}" for a in attrs)
            out.append("  }")
    out.append("}")
    out.append("")
    for r in p.relationships:
        if r.source == "system":
            continue
        a, b = _cls(r.source), _cls(r.target)
        sm, tm = f'"{r.source_mult}"', f'"{r.target_mult}"'
        role = f"{r.field}"
        if r.kind is RelKind.COMPOSITION:
            line = f"{a} {sm} *-- {tm} {b} : {r.stereotype} >"
        elif r.kind is RelKind.REALIZATION:
            line = f"{a} ..|> {b} : {r.stereotype} [{role}]"
        elif r.kind is RelKind.USAGE:
            line = f"{a} ..> {b} : <<use>> {r.stereotype} [{role}]"
        elif r.kind is RelKind.DEPLOYMENT:
            line = f"{a} ..> {b} : <<deploy>> [{role}]"
        else:
            line = f"{a} {sm} --> {tm} {b} : {r.stereotype} [{role}]"
        if r.constraint:
            line += f" {r.constraint}"
        out.append(line)
        if r.association_class:
            out.append(f"({a}, {b}) .. {r.association_class}")
    out.append("@enduml")
    return "\n".join(out) + "\n"
