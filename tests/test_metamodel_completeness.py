"""Everything the platform models is declared in a UML profile (ADR-0112).

The profiles are authoritative for meaning and `spec/model.py` and
`spec/binding.py` realise them; this holds the two equal in both directions.

The gap was closed profile by profile against an allow-list that could only
shrink; it is empty now and gone (ADR-0112 M6). A class, enumeration or field
added to the models without a declaration fails here.
"""
from __future__ import annotations

import enum
import typing

import pytest

from orgagents.metamodel import PROFILES, completeness
from orgagents.spec import binding as spec_binding
from orgagents.spec import model as spec_model


def test_the_walk_reaches_both_models():
    names = {c.__name__ for c in completeness.classes()}
    assert {"SystemSpec", "AgentSpec", "Mandate", "DataRelation",
            "Binding", "ServerBinding", "SharingScope"} <= names


def test_everything_the_models_hold_is_declared_in_a_profile():
    """Every class and enumeration reachable from SystemSpec and Binding is
    declared, and every field is a declared property or relationship end."""
    gap = completeness.gap()
    assert not gap, ("declare these in a profile (ADR-0112):\n  "
                     + "\n  ".join(gap))


def test_every_class_is_declared_in_exactly_one_profile():
    assert not completeness.duplicates()


def test_every_declaration_is_what_the_models_have():
    """The other direction: a declared property is a field of its owner, of
    the declared type and multiplicity; a declared enumeration has exactly
    the Python enum's literals; a relationship's field holds one reference
    or many as its multiplicity says."""
    wrong = completeness.mismatches()
    assert not wrong, "\n".join(wrong)


ENUMS = [c for c in completeness.classes()
         if isinstance(c, type) and issubclass(c, enum.Enum)]


@pytest.mark.parametrize("enum_cls", ENUMS, ids=lambda c: c.__name__)
def test_every_enumeration_is_declared_with_the_python_literals(enum_cls):
    declared = [e for p in PROFILES for e in p.enumerations
                if e.model == enum_cls.__name__]
    assert len(declared) == 1, enum_cls.__name__
    assert declared[0].literals == tuple(m.value for m in enum_cls)


def test_a_literal_typed_field_is_typed_by_an_enumeration_with_its_literals():
    """`Literal[...]` fields are anonymous enumerations in Python; in the
    profile they are named ones, with the same literals."""
    seen = 0
    for p in PROFILES:
        for prop in p.properties:
            cls = completeness._owner_class(prop.owner)
            base = completeness.shape_of(
                completeness.annotation(cls, prop.name)).base
            if typing.get_origin(base) is typing.Literal:
                seen += 1
                enum_ = next(e for q in PROFILES for e in q.enumerations
                             if e.name == prop.type)
                assert enum_.literals == typing.get_args(base), prop
    assert seen >= 5


def test_the_binding_is_declared_only_in_the_deployment_profile():
    decl = completeness.declarations()
    binding_classes = [c for c in completeness.reachable(spec_binding.Binding)
                       if c.__module__.endswith("binding")]
    for c in binding_classes:
        assert {d.profile for d in decl[c.__name__]} == {"Deployment"}, c


def test_an_undeclared_field_or_class_is_reported(monkeypatch):
    """The check has teeth: take a declaration away and it is a gap."""
    from orgagents.metamodel import authority
    props = [p for p in authority.PROFILE.properties
             if (p.owner, p.name) != ("Mandate", "conditions")]
    monkeypatch.setattr(authority.PROFILE, "properties", props)
    assert "Mandate.conditions" in completeness.gap()
    monkeypatch.setattr(authority.PROFILE, "enumerations", [
        e for e in authority.PROFILE.enumerations if e.name != "Effect"
        and e.name != "ControlEnforcer"])
    assert "ControlEnforcer" in completeness.gap()


def test_the_models_are_the_ones_the_profile_realises():
    assert completeness.ROOTS == (spec_model.SystemSpec, spec_binding.Binding)
