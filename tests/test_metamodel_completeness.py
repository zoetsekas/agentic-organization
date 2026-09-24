"""Everything the platform models is declared in a UML profile (ADR-0112).

The profiles are authoritative for meaning and `spec/model.py` and
`spec/binding.py` realise them; this holds the two equal in both directions.
The gap still to close is `metamodel_gap.py`, an allow-list that may only
shrink.
"""
from __future__ import annotations

import typing

import pytest

from orgagents.metamodel import PROFILES, completeness
from orgagents.spec import binding as spec_binding
from orgagents.spec import model as spec_model

from metamodel_gap import GAP

ALLOWED = frozenset().union(*GAP.values())


def test_the_walk_reaches_both_models():
    names = {c.__name__ for c in completeness.classes()}
    assert {"SystemSpec", "AgentSpec", "Mandate", "DataRelation",
            "Binding", "ServerBinding", "SharingScope"} <= names


def test_nothing_outside_the_allow_list_is_undeclared():
    """A class, enumeration or field added to the models must be declared in
    a profile in the same change."""
    new = sorted(set(completeness.gap()) - ALLOWED)
    assert not new, ("declare these in a profile (ADR-0112):\n  "
                     + "\n  ".join(new))


def test_the_allow_list_only_shrinks():
    """An entry that is now declared must be removed from the allow-list,
    so the list is always the true remaining gap."""
    stale = sorted(ALLOWED - set(completeness.gap()))
    assert not stale, ("now declared; remove from tests/metamodel_gap.py:\n  "
                       + "\n  ".join(stale))


def test_the_allow_list_names_each_entry_once_under_its_profile():
    names = [p.name for p in PROFILES]
    assert set(GAP) <= set(names)
    seen: set[str] = set()
    for items in GAP.values():
        assert not (items & seen)
        seen |= items


def test_every_class_is_declared_in_exactly_one_profile():
    assert not completeness.duplicates()


def test_every_declaration_is_what_the_models_have():
    """The other direction: a declared property is a field of its owner, of
    the declared type and multiplicity; a declared enumeration has exactly
    the Python enum's literals; a relationship's field holds one reference
    or many as its multiplicity says."""
    wrong = completeness.mismatches()
    assert not wrong, "\n".join(wrong)


def test_a_declared_class_is_declared_in_the_profile_the_allow_list_expected():
    """The allow-list files each class under the profile it belongs to; once
    declared it must land there."""
    expected = {item.split(".")[0]: p for p, items in GAP.items()
                for item in items}
    decl = completeness.declarations()
    for cls, profile in expected.items():
        if cls in decl:
            assert {d.profile for d in decl[cls]} == {profile}, cls


ENUMS = [c for c in completeness.classes() if isinstance(c, type)
         and issubclass(c, __import__("enum").Enum)]


@pytest.mark.parametrize("enum_cls", ENUMS, ids=lambda c: c.__name__)
def test_each_declared_enumeration_has_the_python_literals(enum_cls):
    declared = [e for p in PROFILES for e in p.enumerations
                if e.model == enum_cls.__name__]
    if not declared:
        assert enum_cls.__name__ in ALLOWED
        return
    assert len(declared) == 1
    assert declared[0].literals == tuple(m.value for m in enum_cls)


def test_a_literal_typed_field_is_typed_by_an_enumeration_with_its_literals():
    """`Literal[...]` fields are anonymous enumerations in Python; in the
    profile they are named ones, with the same literals."""
    for p in PROFILES:
        for prop in p.properties:
            cls = completeness._owner_class(prop.owner)
            base = completeness.shape_of(
                completeness.annotation(cls, prop.name)).base
            if typing.get_origin(base) is typing.Literal:
                enum = next(e for q in PROFILES for e in q.enumerations
                            if e.name == prop.type)
                assert enum.literals == typing.get_args(base), prop


def test_the_models_are_the_ones_the_profile_realises():
    assert completeness.ROOTS == (spec_model.SystemSpec, spec_binding.Binding)
