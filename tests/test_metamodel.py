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
    stereo = PROFILE.stereotype(kind)
    assert stereo is not None, f"no stereotype for {kind}"
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
    if rel.shape is Shape.RECORD:
        assert rel.field in spec_model.SystemSpec.model_fields
        return
    owner = _model(rel.owner_kind())
    assert owner is not None, f"{rel.owner_kind()} has no model"
    assert rel.field in owner.model_fields, \
        f"{owner.__name__} has no field '{rel.field}'"


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
