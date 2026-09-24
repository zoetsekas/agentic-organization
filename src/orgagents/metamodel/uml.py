"""The UML subset the profiles are written in (ADR-0101, ADR-0112).

The metaclasses the platform extends, UML's relationship kinds, and the
declaration types every profile module uses: `Stereotype`, `Relationship`,
`Property`, `Enumeration`, `DataType` and `Profile`. Nothing here is the
platform's own vocabulary; that is in the profile modules (`core`,
`organisation`, `authority`, ...), which `metamodel/__init__.py` assembles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

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
    #: Does the designer's palette offer it? False for the root, the abstract
    #: kinds, the parts of a workflow and everything set from Properties.
    palette: bool = True


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
    """A UML Profile: one concern's stereotypes, DataTypes, Enumerations,
    relationships and properties, and the profiles it imports (ADR-0112).

    A relationship is owned by the profile it is declared in, and both of its
    ends must be visible from there: declared in the profile or in one it
    imports, directly or through an import (UML package import is public, so
    transitive). The assembled `metamodel.PROFILE` is also a Profile — the
    union of the spec profiles — so every caller reads one shape."""

    name: str
    stereotypes: list[Stereotype] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    enumerations: list[Enumeration] = field(default_factory=list)
    datatypes: list[DataType] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)
    #: The profiles this one imports, by name.
    imports: tuple[str, ...] = ()
    doc: str = ""
    version: str = "1.0.0"

    def enumeration(self, name: str) -> Optional[Enumeration]:
        return next((e for e in self.enumerations if e.name == name), None)

    def stereotype(self, kind: str) -> Optional[Stereotype]:
        return next((s for s in self.stereotypes if s.kind == kind), None)

    def datatype(self, name: str) -> Optional[DataType]:
        return next((d for d in self.datatypes if d.name == name), None)

    def element_names(self) -> set[str]:
        """Every classifier it declares: stereotype kinds, DataType and
        Enumeration names, and the association classes its relationships
        name."""
        return ({s.kind for s in self.stereotypes}
                | {d.name for d in self.datatypes}
                | {e.name for e in self.enumerations}
                | {r.association_class for r in self.relationships
                   if r.association_class})

