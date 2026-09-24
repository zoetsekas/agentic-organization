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

The profile is split by concern (ADR-0112): `core`, `organisation`,
`authority`, `access`, `data`, `knowledge`, `process`, `assurance` and
`deployment`, each declaring its stereotypes, DataTypes, Enumerations,
relationships and imports, written in the UML subset of `uml`. This module
assembles them; `PROFILE` is the union of the spec profiles, so no caller
moves. The profiles are authoritative for what the model means; `spec.model`
is its Python realisation, held equal to it by
`tests/test_metamodel_completeness.py`.
"""
from __future__ import annotations

from typing import Any, Optional

from . import access, assurance, authority, core, data, deployment, knowledge, organisation, process
from .core import SYSTEM_OWNED
from .uml import (
    DataType,
    Draw,
    Enumeration,
    MetaClass,
    Profile,
    Property,
    Relationship,
    RelKind,
    Shape,
    Stereotype,
)

__all__ = [
    "MetaClass", "RelKind", "Shape", "Draw", "Stereotype", "Relationship",
    "Property", "Enumeration", "DataType", "Profile",
    "PROFILE", "PROFILES", "SPEC_PROFILES", "STEREOTYPES", "RELATIONSHIPS",
    "ENUMERATIONS", "DATATYPES", "PROPERTIES", "NON_PALETTE", "SYSTEM_OWNED",
    "profile_of", "specialisations", "link_rules", "describe",
    "palette_profiles",
    "to_plantuml", "to_plantuml_profile", "to_plantuml_ownership",
]


# --------------------------------------------------------------------------
# The profiles, one per concern (ADR-0112)
# --------------------------------------------------------------------------

def _owns(profile: Profile) -> None:
    """Ownership: the System owns one Organization, which owns every
    top-level element. Each `owns` composition belongs to the profile of the
    element owned, which imports Core and Organisation and so sees both
    ends."""
    profile.relationships += [
        Relationship(
            "system" if st.collection in SYSTEM_OWNED else "organization",
            st.kind, RelKind.COMPOSITION, "owns", st.collection, Shape.PART,
            source_mult="1",
            target_mult="1" if st.kind == "organization" else "0..*",
            linkable=False, draw=Draw.NONE)
        for st in profile.stereotypes
        if st.collection and st.kind != "system"
    ]


#: The spec profiles, in import order. Deployment is not among them: the
#: spec profiles never see the binding (ADR-0004).
SPEC_PROFILES: list[Profile] = [
    m.PROFILE for m in (core, organisation, authority, access, data,
                        knowledge, process, assurance)
]
for _p in SPEC_PROFILES:
    _owns(_p)

#: Every profile, the Deployment profile last.
PROFILES: list[Profile] = [*SPEC_PROFILES, deployment.PROFILE]

STEREOTYPES = [s for p in SPEC_PROFILES for s in p.stereotypes]
RELATIONSHIPS = [r for p in SPEC_PROFILES for r in p.relationships]
ENUMERATIONS = [e for p in SPEC_PROFILES for e in p.enumerations]
DATATYPES = [d for p in SPEC_PROFILES for d in p.datatypes]
PROPERTIES = [pr for p in SPEC_PROFILES for pr in p.properties]

#: Stereotypes that are not palette kinds.
NON_PALETTE = {s.kind for s in STEREOTYPES if not s.palette}

#: The assembled spec profile: every spec profile's declarations, as one
#: Profile, which is what the link rules, constraints, operations and the
#: transformation read.
PROFILE = Profile(name="OrgAgents", stereotypes=STEREOTYPES,
                  relationships=RELATIONSHIPS, enumerations=ENUMERATIONS,
                  datatypes=DATATYPES, properties=PROPERTIES)


def profile_of(name: str, profiles: Optional[list[Profile]] = None
               ) -> Optional[Profile]:
    """The profile that declares an element — a stereotype by kind, a
    DataType, an Enumeration or an association class by name."""
    for p in profiles or PROFILES:
        if name in p.element_names():
            return p
    return None




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
            # One field holding several relationships (ADR-0111): which of
            # its objects are this rule's links, as [attribute, value]. The
            # canvas draws and filters by it, and names the link by its
            # stereotype because the field alone is ambiguous.
            "selector": list(r.selector) if r.selector else None,
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

def _declaring(item: Any, attr: str) -> str:
    """The name of the profile whose `attr` list holds `item`."""
    for p in PROFILES:
        if any(x is item for x in getattr(p, attr)):
            return p.name
    return ""


def palette_profiles() -> dict[str, str]:
    """Each palette kind's profile, so the designer can group the palette
    by profile and show the profiles a diagram draws (ADR-0112 §6)."""
    return {s.kind: _declaring(s, "stereotypes") for s in PROFILE.stereotypes
            if s.palette}


def describe(profile: Profile = PROFILE) -> dict[str, Any]:
    """The profile as data, for the API and for anybody reading the model.

    Every element names the profile that declares it (ADR-0112), and
    `profiles` lists all of them — the Deployment profile included — with
    their imports and what each declares."""
    return {
        "profile": profile.name,
        "metaclasses": [m.value for m in MetaClass],
        "relationship_kinds": [k.value for k in RelKind],
        "profiles": [
            {"name": p.name, "version": p.version, "doc": p.doc,
             "imports": list(p.imports),
             "stereotypes": [s.kind for s in p.stereotypes],
             "palette": [s.kind for s in p.stereotypes if s.palette],
             "datatypes": [d.name for d in p.datatypes],
             "enumerations": [e.name for e in p.enumerations],
             "spec": p.name != "Deployment"}
            for p in PROFILES
        ],
        "stereotypes": [
            {"name": f"«{s.name}»", "kind": s.kind, "extends": s.extends.value,
             "collection": s.collection, "doc": s.doc,
             "profile": _declaring(s, "stereotypes"),
             "abstract": s.abstract, "palette": s.palette}
            for s in profile.stereotypes
        ],
        "relationships": [
            {"source": r.source, "target": r.target, "kind": r.kind.value,
             "stereotype": f"«{r.stereotype}»",
             "multiplicity": [r.source_mult, r.target_mult],
             "field": f"{r.owner_kind()}.{r.field}", "shape": r.shape.value,
             "draw": r.draw.value, "linkable": r.linkable,
             "association_class": r.association_class,
             "constraint": r.constraint,
             "profile": _declaring(r, "relationships")}
            for r in profile.relationships
        ],
        "enumerations": [
            {"name": e.name, "literals": list(e.literals), "doc": e.doc,
             "profile": _declaring(e, "enumerations"),
             **({"uml": dict(e.uml)} if e.uml else {})}
            for e in profile.enumerations
        ],
        "datatypes": [{"name": d.name, "doc": d.doc,
                       "profile": _declaring(d, "datatypes")}
                      for d in profile.datatypes],
        "properties": [
            {"owner": p.owner, "name": p.name, "type": p.type,
             "multiplicity": p.multiplicity,
             "profile": _declaring(p, "properties")}
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
    RelKind.ABSTRACTION: "..>",
}


def _cls(kind: str) -> str:
    """A diagram name: a stereotype kind in CamelCase (`data_class` →
    `DataClass`); a DataType or association class name as it is."""
    return "".join(p[:1].upper() + p[1:] for p in kind.split("_"))


def _attributes(owner: str, profile: Profile) -> list[str]:
    """`name : Type [mult]` lines for an owner's declared properties — read
    from the profile, never from the Python model (ADR-0112 §8)."""
    return [f"{p.name} : {p.type}"
            + ("" if p.multiplicity == "1" else f" [{p.multiplicity}]")
            for p in profile.properties if p.owner == owner]


def to_plantuml_profile(profile: Optional[Profile] = None) -> str:
    """The profile diagram: each stereotype extends a UML metaclass."""
    p = profile or PROFILE
    out = ["@startuml OrgAgentsProfile", "!pragma layout smetana",
           "hide empty members", "left to right direction",
           'title OrgAgents profile — stereotypes extending UML metaclasses', ""]
    for mc in sorted({s.extends for s in p.stereotypes}, key=lambda m: m.value):
        out.append(f'class "{mc.value}" as MC_{mc.name} <<metaclass>>')
    out.append('class "AssociationClass" as MC_ASSOCIATION_CLASS <<metaclass>>')
    out.append('class "DeploymentSpecification" as MC_DEPLOYMENT_SPECIFICATION'
               ' <<metaclass>>')
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


def to_plantuml(profile: Optional[Profile] = None) -> str:
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
    def box(head: str, attrs: list[str]) -> None:
        if not attrs:
            out.append(f"  {head}")
            return
        out.append(f"  {head} {{")
        out.extend(f"    {a}" for a in attrs)
        out.append("  }")

    for s in p.stereotypes:
        if s.kind == "system":
            continue
        stereo = f"<<{s.name}>>" + (" <<active>>" if s.extends is
                                     MetaClass.ACTIVE_CLASS else "")
        kw = {MetaClass.INTERFACE: "interface", MetaClass.ACTOR: "class",
              MetaClass.DATA_TYPE: "class"}.get(s.extends, "class")
        if s.abstract and kw == "class":
            kw = "abstract class"
        box(f"{kw} {_cls(s.kind)} {stereo}", _attributes(s.kind, p))
    seen: set[str] = set()
    for r in p.relationships:
        if r.association_class and r.association_class not in seen:
            seen.add(r.association_class)
            kind = ("DeploymentSpecification" if r.kind is RelKind.DEPLOYMENT
                    else "AssociationClass")
            box(f"class {r.association_class} <<{kind}>>",
                _attributes(r.association_class, p))
    for d in p.datatypes:
        box(f"class {d.name} <<dataType>>", _attributes(d.name, p))
    for e in p.enumerations:
        box(f"enum {e.name}", list(e.literals))
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
        elif r.kind is RelKind.ABSTRACTION:
            line = f"{a} ..> {b} : <<{r.stereotype}>> [{role}]"
        else:
            line = f"{a} {sm} --> {tm} {b} : {r.stereotype} [{role}]"
        if r.constraint:
            line += f" {r.constraint}"
        out.append(line)
        if r.association_class:
            out.append(f"({a}, {b}) .. {r.association_class}")
    out.append("@enduml")
    return "\n".join(out) + "\n"


def to_plantuml_ownership(profile: Optional[Profile] = None) -> str:
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
