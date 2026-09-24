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
    #: A step of an Activity that does something: calls a tool, an agent, a
    #: workflow, evaluates an expression, or waits for a person.
    ACTION = "Action"
    #: An edge of an Activity: control passes from one node to the next.
    CONTROL_FLOW = "ControlFlow"
    #: A closed set of literals typing a property (ADR-0112).
    ENUMERATION = "Enumeration"


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
    #: `{isAbstract}`: never instantiated itself, only through a
    #: specialisation (Worker) or a realisation (Principal, Resource).
    abstract: bool = False


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


@dataclass(frozen=True)
class Property:
    """A UML Property holding a value, not a reference: an attribute of a
    stereotype or of a DataType, with its type and multiplicity (ADR-0112).
    A field that holds another element's id is a Relationship instead."""

    owner: str             # a stereotype's kind, or a DataType's name
    name: str              # the spec field
    type: str              # a UML primitive, an Enumeration or a DataType
    multiplicity: str = "1"
    doc: str = ""


@dataclass(frozen=True)
class Enumeration:
    """A UML Enumeration: a closed vocabulary with its literals, declared in
    the profile so a property typed by it is typed in the model and not only
    in Python (ADR-0112). `model` names the spec enum it must equal."""

    name: str
    model: str
    literals: tuple[str, ...]
    doc: str = ""
    #: What a literal is in UML, where it is one of UML's own concepts: a
    #: `fork` step is a ForkNode.
    uml: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DataType:
    """A UML DataType: a value with no identity of its own, owned by the
    element it describes (ADR-0112). Its value properties are `Property`
    declarations; its references are relationships from its owner through
    a dotted field (`interface.tools`)."""

    name: str
    model: str
    doc: str = ""


@dataclass
class Profile:
    name: str
    stereotypes: list[Stereotype] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    enumerations: list[Enumeration] = field(default_factory=list)
    datatypes: list[DataType] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)

    def enumeration(self, name: str) -> Optional[Enumeration]:
        return next((e for e in self.enumerations if e.name == name), None)

    def stereotype(self, kind: str) -> Optional[Stereotype]:
        return next((s for s in self.stereotypes if s.kind == kind), None)


# --------------------------------------------------------------------------
# The OrgAgents profile
# --------------------------------------------------------------------------

S = Stereotype
MC = MetaClass

STEREOTYPES = [
    S("Organization", "organization", MC.COMPONENT, "organization",
      "Organization",
      "The root unit of one organisation. A Team by generalisation; exactly "
      "one per System."),
    S("Team", "team", MC.COMPONENT, "", "Team",
      "A unit of the organisation; owns its members and sub-teams."),
    S("Agent", "agent", MC.ACTIVE_CLASS, "", "AgentSpec",
      "An active class: it has its own thread of control."),
    S("SubAgent", "subagent", MC.CLASS, "", "SubAgentSpec",
      "A tool-shaped worker owned by the agent that calls it (ADR-0027)."),
    S("Worker", "worker", MC.CLASS, "", "Worker",
      "What an agent and a sub-agent share. Neither is a kind of the other "
      "(ADR-0102).", abstract=True),
    S("Principal", "principal", MC.INTERFACE, "", "",
      "Anything a policy can be about: an agent, a team, a role.",
      abstract=True),
    S("Resource", "resource", MC.INTERFACE, "", "",
      "Anything a permission or policy can govern (ADR-0008).",
      abstract=True),
    S("Callable", "callable", MC.INTERFACE, "", "",
      "Anything a workflow step may call as a tool: a declared Tool, or a "
      "Capability (and its operations, `capability__operation`).",
      abstract=True),
    S("Wrappable", "wrappable", MC.INTERFACE, "", "",
      "What a Tool may wrap: a Capability, a SubAgent, a Workflow or an "
      "Endpoint (ADR-0029).", abstract=True),
    S("Action", "action", MC.ACTION, "", "ActivityNode",
      "A step of a workflow (ADR-0102)."),
    S("ControlFlow", "control_flow", MC.CONTROL_FLOW, "", "ControlFlow",
      "An edge between two steps of a workflow."),
    S("StepOwner", "step_owner", MC.INTERFACE, "", "",
      "Whoever may own a workflow step: an agent, a team or a person "
      "(ADR-0110).", abstract=True),
    S("Skill", "skill", MC.ARTIFACT, "skills", "SkillSpec"),
    S("Plugin", "plugin", MC.COMPONENT, "plugins", "PluginSpec"),
    S("Tool", "tool", MC.INTERFACE, "tools", "ToolSpec"),
    S("Person", "person", MC.ACTOR, "people", "Person"),
    S("Role", "role", MC.CLASS, "role_definitions", "Role"),
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
NON_PALETTE = {"system", "organization", "worker", "principal", "resource",
               "callable", "wrappable",
               "action", "control_flow", "step_owner"}

R = Relationship
K = RelKind
SH = Shape

RELATIONSHIPS = [
    # -- composition: the part lives in the whole --------------------------
    # -- generalisation: the organisation is the root team ------------------
    R("organization", "team", K.GENERALIZATION, "", "", SH.REF,
      linkable=False, draw=Draw.NONE,
      help="an Organization is a Team: it has a leader, members and "
           "sub-teams, and is the one unit with no parent"),
    R("agent", "worker", K.GENERALIZATION, "", "", SH.REF,
      linkable=False, draw=Draw.NONE),
    R("subagent", "worker", K.GENERALIZATION, "", "", SH.REF,
      linkable=False, draw=Draw.NONE),

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
    R("worker", "knowledge", K.ASSOCIATION, "consults", "knowledge", SH.REFS,
      help="grounding material this worker may consult"),
    R("worker", "capability", K.REALIZATION, "provides", "capabilities",
      SH.REFS, help="what this worker may do"),
    R("team", "role", K.ASSOCIATION, "plays", "roles", SH.REF_OBJECTS,
      key="role", association_class="RoleAssignment",
      help="a role the whole unit plays: it grants every member, and the "
           "members of its sub-teams (ADR-0007)"),
    R("agent", "role", K.ASSOCIATION, "plays", "roles", SH.REF_OBJECTS,
      key="role", association_class="RoleAssignment", help="a role and the capabilities it grants"),
    R("agent", "skill", K.ASSOCIATION, "holds", "skills", SH.REFS,
      draw=Draw.INLINE, legacy="holds",
      help="a named competence this agent may exercise. Shared freely: "
           "declaring it on a second agent does not take it from the first"),
    R("agent", "plugin", K.ASSOCIATION, "holds", "plugins", SH.REFS,
      draw=Draw.INLINE, legacy="holds",
      help="a packaged extension this agent loads"),
    R("worker", "tool", K.USAGE, "holds", "tools", SH.REFS,
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
    R("worker", "output_contract", K.ASSOCIATION, "returns", "output_contract",
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
    R("subagent", "environment", K.DEPLOYMENT, "runs in", "environments",
      SH.REFS, draw=Draw.NEST,
      constraint="{subsets parent.environments}",
      help="a sandbox this sub-agent runs in; one of its parent's"),
    # A sub-agent is called like a tool by being *wrapped* by one — not by
    # being one: `agent.tools` holds Tool ids, never sub-agents (ADR-0027).
    *[R(k, "wrappable", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("capability", "subagent", "workflow",
                                  "endpoint")],
    R("tool", "wrappable", K.ASSOCIATION, "wraps", "wraps", SH.REF,
      target_mult="1", linkable=False, draw=Draw.NONE,
      constraint="{kind given by wraps_kind}",
      help="what the tool names and narrows (ADR-0029)"),

    # -- what a policy is about (ADR-0008) ------------------------------------
    *[R(k, "principal", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("agent", "team", "role")],
    *[R(k, "resource", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("data_class", "capability", "agent", "team",
                                  "workflow", "environment", "channel")],
    R("policy", "principal", K.ASSOCIATION, "applies to", "subjects", SH.REFS,
      constraint="{'*' = every principal}",
      help="who the rule is about: agents, teams or roles, by id"),
    R("policy", "resource", K.ASSOCIATION, "governs", "resources", SH.REFS,
      constraint="{kind given by resource_kinds; '*' = every}",
      help="what the rule allows or denies acting on"),

    # -- a workflow is an Activity (ADR-0102) ---------------------------------
    R("workflow", "action", K.COMPOSITION, "step", "graph.nodes", SH.PART,
      source_mult="1", linkable=False, draw=Draw.NONE),
    R("workflow", "control_flow", K.COMPOSITION, "edge", "graph.edges",
      SH.PART, source_mult="1", linkable=False, draw=Draw.NONE),
    R("control_flow", "action", K.ASSOCIATION, "from", "source", SH.REF,
      target_mult="1", linkable=False, draw=Draw.NONE),
    R("control_flow", "action", K.ASSOCIATION, "to", "target", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{'END' = the ActivityFinalNode}"),
    *[R(k, "callable", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("tool", "capability")],
    R("action", "callable", K.USAGE, "calls", "tool", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = tool; 'c__op' = operation op of c}"),
    R("action", "agent", K.ASSOCIATION, "delegates to", "agent", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = agent}"),
    R("action", "workflow", K.ASSOCIATION, "invokes", "workflow", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = workflow}"),

    # -- the governed workflow and its seam to an engine (ADR-0110) ----------
    *[R(k, "step_owner", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("agent", "team", "person")],
    R("action", "step_owner", K.ASSOCIATION, "owned by", "owner", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{an agent step defaults to its agent}",
      help="who does the step: an agent, a team or a person"),
    R("action", "person", K.ASSOCIATION, "approved by", "person", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = human}"),
    R("action", "role", K.ASSOCIATION, "approved by role", "role", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = human}"),
    R("workflow", "callable", K.USAGE, "interface calls", "interface.tools",
      SH.REFS, linkable=False, draw=Draw.NONE,
      constraint="{'c__op' = operation op of c}",
      help="what an external body calls; the calling step's owner must hold it"),
    R("workflow", "endpoint", K.USAGE, "interface reaches",
      "interface.endpoints", SH.REFS, linkable=False, draw=Draw.NONE),
    R("workflow", "data_class", K.ASSOCIATION, "receives",
      "interface.receives_data_classes", SH.REFS, linkable=False,
      draw=Draw.NONE,
      help="data sent to the body; the calling step's owner must hold it"),
    R("workflow", "data_class", K.ASSOCIATION, "returns",
      "interface.returns_data_classes", SH.REFS, linkable=False,
      draw=Draw.NONE),
]

# -- ownership: the System owns one Organization, which owns its elements --
#: Collections that stay on the System: how the design is released, not what
#: the organisation is (ADR-0101).
SYSTEM_OWNED = {"organization", "lifecycle.evaluations"}

RELATIONSHIPS += [
    R("system" if st.collection in SYSTEM_OWNED else "organization",
      st.kind, K.COMPOSITION, "owns", st.collection, SH.PART,
      source_mult="1",
      target_mult="1" if st.kind == "organization" else "0..*",
      linkable=False, draw=Draw.NONE)
    for st in STEREOTYPES
    if st.collection and st.kind not in ("system",)
]

# -- values: enumerations, datatypes and properties (ADR-0112) --------------
# Declared for what ADR-0110 added to the process model. The rest of the
# spec's enumerations and value types are ADR-0112's migration, not this one.

ENUMERATIONS = [
    Enumeration(
        "ActivityNodeKind", "ActivityNodeKind",
        ("tool", "agent", "workflow", "transform", "branch", "human",
         "fork", "join"),
        "What a workflow step is: which UML ActivityNode it stands for.",
        uml=(("tool", "CallOperationAction"), ("agent", "CallBehaviorAction"),
             ("workflow", "CallBehaviorAction"), ("transform", "OpaqueAction"),
             ("branch", "DecisionNode"), ("human", "AcceptEventAction"),
             ("fork", "ForkNode"), ("join", "JoinNode"))),
    Enumeration(
        "WorkflowBody", "WorkflowBody", ("graph", "external"),
        "Where a workflow's insides live: drawn here as an Activity, or "
        "built in an engine the binding names (ADR-0110)."),
]

DATATYPES = [
    DataType("WorkflowInterface", "WorkflowInterface",
             "The seam between a governed workflow and a body built in an "
             "engine: what goes in and out, what it calls and which data it "
             "touches. Its references are the workflow's `interface calls`, "
             "`interface reaches`, `receives` and `returns` (ADR-0110)."),
]

PROPERTIES = [
    Property("action", "kind", "ActivityNodeKind", "1"),
    Property("workflow", "body", "WorkflowBody", "1",
             "graph unless the body is built in an engine"),
    Property("workflow", "interface", "WorkflowInterface", "1",
             "composite: the workflow owns its interface"),
    Property("WorkflowInterface", "inputs", "String", "0..*"),
    Property("WorkflowInterface", "outputs", "String", "0..*"),
]

PROFILE = Profile(name="OrgAgents", stereotypes=STEREOTYPES,
                  relationships=RELATIONSHIPS, enumerations=ENUMERATIONS,
                  datatypes=DATATYPES, properties=PROPERTIES)


# --------------------------------------------------------------------------
# Projections
# --------------------------------------------------------------------------

def specialisations(kind: str, profile: Profile = PROFILE) -> list[str]:
    """The concrete kinds that may stand where `kind` is asked for: itself
    unless abstract, its specialisations (Team → Organization; Worker →
    Agent, SubAgent) and, for an interface, its realisers (Principal →
    Agent, Team, Role). UML substitutability, computed once."""
    st = profile.stereotype(kind)
    out: list[str] = [] if (st is not None and st.abstract) else [kind]
    for r in profile.relationships:
        if r.target == kind and not r.field and r.kind in (
                RelKind.GENERALIZATION, RelKind.REALIZATION):
            out.extend(k for k in specialisations(r.source, profile)
                       if k not in out)
    return out


def _concrete(profile: Profile) -> list[Relationship]:
    """Every relationship with its abstract ends replaced by concrete ones:
    a Worker's `knowledge` is an Agent's and a SubAgent's. This is UML
    inheritance of association ends, applied once, here."""
    from dataclasses import replace
    out = []
    for r in profile.relationships:
        if r.kind in (RelKind.GENERALIZATION, RelKind.REALIZATION) \
                and not r.field:
            continue
        for src in specialisations(r.source, profile):
            for tgt in specialisations(r.target, profile):
                out.append(replace(r, source=src, target=tgt))
    return out


def link_rules(profile: Profile = PROFILE) -> list[dict[str, Any]]:
    """The designer's link table, derived from the profile.

    `relationship` keeps the word each existing canvas code path recognises
    (`contains`, `member`, `uses`, `holds`, `flow`, `association`, `fires`)
    and uses the stereotype for everything new, which the canvas handles by
    `shape`. `writes` keeps its old form, `<owner kind>.<field>`.
    """
    rules = []
    for r in _concrete(profile):
        if not r.linkable or r.source in NON_PALETTE or r.target in NON_PALETTE:
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
        "enumerations": [
            {"name": e.name, "literals": list(e.literals), "doc": e.doc,
             **({"uml": dict(e.uml)} if e.uml else {})}
            for e in profile.enumerations
        ],
        "datatypes": [{"name": d.name, "doc": d.doc} for d in profile.datatypes],
        "properties": [
            {"owner": p.owner, "name": p.name, "type": p.type,
             "multiplicity": p.multiplicity}
            for p in profile.properties
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
        if s.abstract and kw == "class":
            kw = "abstract class"
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
        # Ownership has its own view (`to_plantuml_ownership`): drawn here
        # it would be two dozen lines to one box, burying everything else.
        if r.stereotype == "owns":
            continue
        a, b = _cls(r.source), _cls(r.target)
        sm, tm = f'"{r.source_mult}"', f'"{r.target_mult}"'
        role = f"{r.field}"
        if r.kind is RelKind.GENERALIZATION:
            out.append(f"{a} --|> {b}")
            continue
        if r.kind is RelKind.COMPOSITION:
            line = f"{a} {sm} *-- {tm} {b} : {r.stereotype} >"
        elif r.kind is RelKind.REALIZATION:
            line = (f"{a} ..|> {b} : {r.stereotype} [{role}]" if r.field
                    else f"{a} ..|> {b}")
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


def to_plantuml_ownership(profile: Profile = None) -> str:  # type: ignore[assignment]
    """Who owns what: the System owns one Organization; the Organization is
    a Team and owns every element of its model. Each element has exactly one
    owner, which is what makes the containment tree a tree."""
    p = profile or PROFILE
    out = ["@startuml OrgAgentsOwnership", "!pragma layout smetana",
           "hide empty members", "left to right direction",
           "title OrgAgents ownership — composition from the root (ADR-0101)",
           ""]
    for s in p.stereotypes:
        if s.kind != "note":
            out.append(f"class {_cls(s.kind)} <<{s.name}>>")
    for r in p.relationships:
        a, b = _cls(r.source), _cls(r.target)
        if r.kind is RelKind.GENERALIZATION:
            out.append(f"{a} --|> {b}")
        elif r.stereotype == "owns" or r.kind is RelKind.COMPOSITION:
            out.append(f'{a} "{r.source_mult}" *-- "{r.target_mult}" {b} : '
                       f'{r.field}')
    out.append("@enduml")
    return "\n".join(out) + "\n"
