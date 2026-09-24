"""The profile is split by concern, and the split changed nothing (ADR-0112).

`snapshots/metamodel_link_rules.json` was captured from the single-module
profile before the split (`snapshots/capture_metamodel.py`). The link table
the canvas reads must be the same rules; where the canvas picks the first
rule for a pair of kinds (`linkRules(a, b)[0]`), the order for that pair must
be the same too. Only the interleaving of unrelated pairs may move.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from orgagents.metamodel import (PROFILE, PROFILES, SPEC_PROFILES, RelKind,
                                 link_rules, profile_of)

SNAPSHOT = json.loads((Path(__file__).parent / "snapshots" /
                       "metamodel_link_rules.json").read_text("utf-8"))


def _canon(rows):
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


def _by_pair(rows):
    out = defaultdict(list)
    for r in rows:
        out[(r["source"], r["target"])].append(json.dumps(r, sort_keys=True))
    return dict(out)


def test_link_rules_are_the_rules_captured_before_the_split():
    now = json.loads(json.dumps(link_rules()))
    assert _canon(now) == _canon(SNAPSHOT["link_rules"])


def test_the_first_rule_for_each_pair_of_kinds_is_unchanged():
    now = json.loads(json.dumps(link_rules()))
    assert _by_pair(now) == _by_pair(SNAPSHOT["link_rules"])


def test_every_stereotype_and_relationship_before_the_split_is_still_declared():
    kinds = [s.kind for s in PROFILE.stereotypes]
    assert set(SNAPSHOT["stereotypes"]) <= set(kinds)
    rels = {json.dumps([r.source, r.target, r.kind.value, r.stereotype,
                        r.field]) for r in PROFILE.relationships}
    assert {json.dumps(r) for r in SNAPSHOT["relationships"]} <= rels


# -- profile integrity ---------------------------------------------------------

NAMES = [p.name for p in PROFILES]


def _visible(profile):
    """The profile and everything it imports, transitively."""
    by_name = {p.name: p for p in PROFILES}
    seen, todo = set(), [profile.name]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        todo.extend(by_name[n].imports)
    return seen


def test_there_is_one_profile_per_concern():
    assert NAMES == ["Core", "Organisation", "Authority", "Access", "Data",
                     "Knowledge", "Process", "Assurance", "Deployment"]


def test_every_import_names_a_profile():
    for p in PROFILES:
        assert set(p.imports) <= set(NAMES), p.name
        assert p.name not in p.imports


def test_imports_are_acyclic():
    by_name = {p.name: p for p in PROFILES}
    state: dict[str, int] = {}

    def visit(n, path):
        if state.get(n) == 1:
            raise AssertionError(f"import cycle: {' -> '.join(path + [n])}")
        if state.get(n) == 2:
            return
        state[n] = 1
        for m in by_name[n].imports:
            visit(m, path + [n])
        state[n] = 2

    for n in by_name:
        visit(n, [])


def test_no_spec_profile_imports_deployment():
    """The spec stays neutral (ADR-0004): nothing it declares can see the
    binding."""
    for p in SPEC_PROFILES:
        assert "Deployment" not in _visible(p), p.name
    assert all(p.name != "Deployment" for p in SPEC_PROFILES)


def test_nothing_imports_deployment():
    assert not [p.name for p in PROFILES if "Deployment" in p.imports]


def test_each_element_is_declared_in_exactly_one_profile():
    seen: dict[str, str] = {}
    for p in PROFILES:
        for name in p.element_names():
            assert name not in seen, \
                f"{name} is declared in {seen[name]} and {p.name}"
            seen[name] = p.name


def test_every_relationship_end_is_visible_from_its_profile():
    for p in PROFILES:
        visible = _visible(p)
        for r in p.relationships:
            for end in (r.source, r.target):
                home = profile_of(end)
                assert home is not None, f"{p.name}: {end} is not declared"
                assert home.name in visible, \
                    (f"{p.name} declares {r.source} {r.stereotype or r.kind.value}"
                     f" {r.target}, but {end} is in {home.name}, which it "
                     f"does not import")


def test_every_property_and_its_type_are_visible_from_its_profile():
    primitives = {"String", "Integer", "Real", "Boolean", "Any", "Map"}
    for p in PROFILES:
        visible = _visible(p)
        for prop in p.properties:
            for name in (prop.owner, prop.type):
                if name in primitives:
                    continue
                home = profile_of(name)
                assert home is not None, f"{p.name}: {name} is not declared"
                assert home.name in visible, (p.name, prop.owner, prop.name,
                                              name)


def test_a_generalisation_stays_within_what_the_specific_profile_sees():
    for p in PROFILES:
        for r in p.relationships:
            if r.kind is RelKind.GENERALIZATION:
                assert profile_of(r.target).name in _visible(p)
