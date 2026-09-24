"""UML is the metamodel, and the platform's kinds are a profile (ADR-0101).

The profile is checked against the spec model, not the other way round: a
relationship that names a field the spec does not have fails here, so the
metamodel cannot describe a spec that does not exist.
"""
from __future__ import annotations

import pytest

from orgagents.api import LINK_RULES, palette_kinds, palette_tree
from orgagents.metamodel import (
    PROFILE,
    MetaClass,
    RelKind,
    Shape,
    describe,
    link_rules,
)
from orgagents.spec import model as spec_model


def _model(kind):
    """A stereotype's spec class — or, for a relationship owned by a DataType
    or an association class (a Mandate's decisions, ADR-0112), that class."""
    stereo = PROFILE.stereotype(kind)
    if stereo is None:
        dt = PROFILE.datatype(kind)
        cls = getattr(spec_model, dt.model if dt else kind, None)
        assert cls is not None, f"no stereotype, DataType or class {kind}"
        return cls
    return getattr(spec_model, stereo.model) if stereo.model else None


def test_every_palette_kind_is_a_stereotype_of_exactly_one_metaclass():
    kinds = {k["kind"] for k in palette_kinds(palette_tree())}
    stereotyped = {s.kind for s in PROFILE.stereotypes}
    assert kinds <= stereotyped, f"unprofiled kinds: {sorted(kinds - stereotyped)}"
    for s in PROFILE.stereotypes:
        assert isinstance(s.extends, MetaClass)


def test_every_stereotype_names_a_real_spec_class():
    for s in PROFILE.stereotypes:
        if s.model:
            assert hasattr(spec_model, s.model), f"«{s.name}» → {s.model}"


@pytest.mark.parametrize("rel", PROFILE.relationships,
                         ids=lambda r: f"{r.source}-{r.stereotype}-{r.target}")
def test_every_relationship_names_a_field_the_spec_has(rel):
    """The check that keeps the metamodel honest."""
    from orgagents.metamodel import RelKind
    if rel.kind is RelKind.GENERALIZATION:
        # A specialisation is a subclass in the spec, not a field.
        assert issubclass(_model(rel.source), _model(rel.target))
        return
    if rel.kind is RelKind.REALIZATION and not rel.field:
        # Realising an interface (Principal, Resource, Tool) is structural:
        # nothing is stored, the kind simply may stand where it is asked for.
        return
    if rel.shape is Shape.RECORD:
        assert rel.field in spec_model.Organization.model_fields
        return
    owner = _model(rel.owner_kind())
    assert owner is not None, f"{rel.owner_kind()} has no model"
    # A dotted field walks nested models: `memory.namespaces`.
    *path, last = rel.field.split(".")
    for step in path:
        assert step in owner.model_fields, f"{owner.__name__} has no '{step}'"
        owner = owner.model_fields[step].annotation
    assert last in owner.model_fields, \
        f"{owner.__name__} has no field '{last}'"


@pytest.mark.parametrize("rel", [r for r in PROFILE.relationships
                                 if r.shape is Shape.REF_OBJECTS],
                         ids=lambda r: r.field)
def test_a_keyed_reference_names_a_key_the_objects_have(rel):
    owner = _model(rel.owner_kind())
    annotation = owner.model_fields[rel.field].annotation
    item = annotation.__args__[0]
    assert rel.key in item.model_fields, f"{item.__name__} has no '{rel.key}'"


def test_every_rule_the_canvas_relied_on_is_still_derived():
    """The nine hand-written rules, by (source, target, word)."""
    before = {
        ("team", "team", "contains"), ("team", "team", "association"),
        ("team", "agent", "member"), ("agent", "subagent", "uses"),
        ("agent", "agent", "flow"), ("agent", "skill", "holds"),
        ("agent", "plugin", "holds"), ("agent", "tool", "holds"),
        ("trigger", "agent", "fires"),
    }
    now = {(r["source"], r["target"], r["relationship"]) for r in LINK_RULES}
    assert before <= now, f"lost: {before - now}"


def test_link_rules_are_the_profile():
    assert LINK_RULES == link_rules()


def test_the_relationship_a_user_asked_for_exists():
    """Associate knowledge with one or more agents."""
    rule = next(r for r in LINK_RULES
                if (r["source"], r["target"]) == ("agent", "knowledge"))
    assert rule["uml"] == "association" and rule["bidirectional"]
    assert rule["writes"] == "agent.knowledge"


def test_an_agent_in_an_environment_is_a_deployment_drawn_as_nesting():
    rule = next(r for r in LINK_RULES
                if (r["source"], r["target"]) == ("agent", "environment"))
    assert rule["uml"] == RelKind.DEPLOYMENT.value
    assert rule["draw"] == "nest" and rule["key"] == "environment"


def test_the_profile_describes_itself():
    d = describe()
    assert d["profile"] == "OrgAgents"
    agent = next(s for s in d["stereotypes"] if s["kind"] == "agent")
    assert agent["extends"] == "Class {isActive}"


LINK_WITH_ATTRIBUTES = [r for r in PROFILE.relationships
                        if r.shape is Shape.RECORD or r.shape is Shape.REF_OBJECTS]


@pytest.mark.parametrize("rel", LINK_WITH_ATTRIBUTES, ids=lambda r: r.field)
def test_a_link_with_attributes_is_an_association_class(rel):
    """Strict UML: a link that carries data of its own is an AssociationClass
    (a DeploymentSpecification for a deployment), typed by the spec model the
    YAML object actually is."""
    assert rel.association_class, f"{rel.field} carries attributes"
    assert hasattr(spec_model, rel.association_class)
    if rel.shape is Shape.REF_OBJECTS:
        owner = _model(rel.owner_kind())
        item = owner.model_fields[rel.field].annotation.__args__[0]
        assert item.__name__ == rel.association_class
    else:
        item = spec_model.Organization.model_fields[rel.field].annotation.__args__[0]
        assert item.__name__ == rel.association_class


def test_no_instance_link_is_a_bare_dependency():
    """A reference an instance stores is an association (or usage,
    realization, deployment) — never an untyped dependency, which UML reserves
    for model-level reliance."""
    from orgagents.metamodel import RelKind
    assert not [r for r in PROFILE.relationships
                if r.kind is RelKind.DEPENDENCY]


def test_every_element_has_exactly_one_owner():
    """The System owns the Organization (and the release-time evaluations);
    the Organization owns every other element of the model."""
    owners = {}
    for r in PROFILE.relationships:
        if r.stereotype == "owns":
            owners.setdefault(r.target, []).append(r.source)
    top = {s.kind for s in PROFILE.stereotypes
           if s.collection and s.kind != "system"}
    assert set(owners) == top
    assert all(len(v) == 1 for v in owners.values())
    assert owners["organization"] == ["system"]
    assert owners["skill"] == ["organization"]


def test_leadership_subsets_membership():
    lead = next(r for r in PROFILE.relationships if r.field == "leader")
    assert lead.constraint == "{subsets members}"


def test_the_committed_diagrams_are_current():
    """docs/metamodel is generated; regenerate with
    `orgagents metamodel diagram`."""
    from pathlib import Path
    from orgagents.metamodel import to_plantuml, to_plantuml_profile
    root = Path(__file__).resolve().parents[1] / "docs" / "metamodel"
    assert (root / "orgagents-model.puml").read_text() == to_plantuml()
    assert (root / "orgagents-profile.puml").read_text() == to_plantuml_profile()
    from orgagents.metamodel import to_plantuml_ownership
    assert (root / "orgagents-ownership.puml").read_text() == \
        to_plantuml_ownership()


def test_each_system_has_exactly_one_organization_as_its_root():
    from orgagents.spec.model import Organization, SystemSpec, Team
    own = next(r for r in PROFILE.relationships
               if r.source == "system" and r.target == "organization")
    assert own.target_mult == "1" and own.field == "organization"
    assert issubclass(Organization, Team)
    assert SystemSpec.model_fields["organization"].annotation is Organization
    assert not [r for r in PROFILE.relationships
                if r.target == "organization" and r.source != "system"], \
        "nothing but the System owns or contains the organisation"


def test_a_subagent_is_not_an_agent_and_both_are_workers():
    """ADR-0102: neither specialises the other; both specialise Worker."""
    from orgagents.spec.model import AgentSpec, SubAgentSpec, Worker
    assert not issubclass(SubAgentSpec, AgentSpec)
    assert not issubclass(AgentSpec, SubAgentSpec)
    assert issubclass(AgentSpec, Worker) and issubclass(SubAgentSpec, Worker)
    assert PROFILE.stereotype("worker").abstract


def test_what_a_worker_has_both_kinds_have():
    """A Worker relationship is inherited by each concrete kind."""
    rules = {(r["source"], r["target"], r["field"]) for r in link_rules()}
    for kind in ("agent", "subagent"):
        assert (kind, "knowledge", "knowledge") in rules
        assert (kind, "capability", "capabilities") in rules
        assert (kind, "tool", "tools") in rules
    assert not [r for r in link_rules() if r["source"] == "worker"]


def test_a_policy_is_about_principals_and_governs_resources():
    from orgagents.metamodel import specialisations
    # An Organization is a Team, so it may stand wherever a Team may.
    assert set(specialisations("principal")) == {"agent", "team",
                                                 "organization", "role"}
    assert set(specialisations("resource")) == {
        "data_class", "capability", "agent", "team", "organization",
        "workflow", "environment", "channel"}


def test_a_workflow_is_an_activity_whose_actions_reference_the_model():
    from orgagents.spec.model import ActivityNodeKind
    fields = {r.field for r in PROFILE.relationships if r.source == "action"}
    # What a step calls, and who owns or approves it (ADR-0110).
    assert fields == {"tool", "agent", "workflow", "owner", "person", "role"}
    assert {k.value for k in ActivityNodeKind} >= {"tool", "agent", "workflow"}


# -- ADR-0110's additions are in the profile (ADR-0112) -----------------------

def test_every_declared_enumeration_is_the_spec_enum_literal_for_literal():
    for enum in PROFILE.enumerations:
        if not enum.model:
            continue        # a named `Literal[...]`; see test_metamodel_completeness
        py = getattr(spec_model, enum.model)
        assert tuple(m.value for m in py) == enum.literals, enum.name
        for literal, _uml in enum.uml:
            assert literal in enum.literals


def test_fork_and_join_are_uml_control_nodes_in_the_profile():
    kinds = PROFILE.enumeration("ActivityNodeKind")
    assert dict(kinds.uml)["fork"] == "ForkNode"
    assert dict(kinds.uml)["join"] == "JoinNode"


def test_every_declared_property_is_a_field_of_its_owner_and_is_typed():
    datatypes = {d.name: getattr(spec_model, d.model) for d in PROFILE.datatypes}
    from orgagents.metamodel.completeness import PRIMITIVES
    types = ({e.name for e in PROFILE.enumerations} | set(datatypes)
             | set(PRIMITIVES)
             | {r.association_class for r in PROFILE.relationships})
    for prop in PROFILE.properties:
        owner = datatypes.get(prop.owner) or _model(prop.owner)
        assert prop.name in owner.model_fields, (prop.owner, prop.name)
        assert prop.type in types, prop


def test_every_field_adr_0110_added_is_declared():
    """The workflow's body and interface, and a step's owner and approver,
    are each a declared property or relationship end (ADR-0112)."""
    declared = {(p.owner, p.name) for p in PROFILE.properties}
    declared |= {(r.owner_kind(), r.field) for r in PROFILE.relationships}
    for owner, name in [("workflow", "body"), ("workflow", "interface"),
                        ("action", "kind"), ("action", "owner"),
                        ("action", "person"), ("action", "role"),
                        ("workflow", "interface.tools"),
                        ("workflow", "interface.endpoints"),
                        ("workflow", "interface.receives_data_classes"),
                        ("workflow", "interface.returns_data_classes"),
                        ("WorkflowInterface", "inputs"),
                        ("WorkflowInterface", "outputs")]:
        assert (owner, name) in declared, (owner, name)
    interface = spec_model.WorkflowInterface.model_fields
    covered = {n for o, n in declared if o == "WorkflowInterface"} | {
        n.split(".", 1)[1] for o, n in declared
        if o == "workflow" and n.startswith("interface.")}
    assert set(interface) == covered
