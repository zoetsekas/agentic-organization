"""Containment and association are different relationships (ADR-0081).

`team.teams` says one unit is *inside* another: authority narrows through it,
placement inherits through it, and there is exactly one parent. Everything
else an organisation actually has — oversight, escalation, shared services —
had no way to be said, so it was said in comments.

Northwind's finance example carried this one for its whole life:

    Internal Audit deliberately does **not** report to the CFO — its
    independence is the point.

True, load-bearing, and unenforceable: independence was expressed by absence,
and an absence cannot be checked. These tests are about the rules that turn it
into a control.
"""
from __future__ import annotations

import copy
import pathlib

import pytest
import yaml

from orgagents.compiler.ir import build_ir
from orgagents.spec.loader import load_spec, load_spec_text
from orgagents.spec.validate import validate_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]
NORTHWIND = ROOT / "examples" / "northwind" / "northwind.finance.system.yaml"


def doc() -> dict:
    return yaml.safe_load(NORTHWIND.read_text())


def errors_of(document: dict) -> dict[str, str]:
    findings = validate_spec(load_spec_text(yaml.safe_dump(document)))
    return {f.code: f.message for f in findings if f.severity == "error"}


def test_the_example_declares_its_oversight_and_holds():
    spec = load_spec(NORTHWIND)
    kinds = {(l.source, l.target): l.kind.value for l in spec.unit_links}
    assert kinds[("internal_audit", "finance")] == "oversees"
    assert not [f for f in validate_spec(spec) if f.severity == "error"]


def test_an_overseer_may_not_sit_inside_what_it_oversees():
    """The rule that earns the feature.

    Move Internal Audit under Finance — the exact change the comment forbade
    and nothing prevented — and the spec now refuses.
    """
    d = doc()
    org = d["organization"]
    finance = next(t for t in org["teams"] if t["id"] == "finance")
    audit = next(t for t in org["teams"] if t["id"] == "internal_audit")
    org["teams"].remove(audit)
    finance["teams"].append(audit)

    codes = errors_of(d)
    assert "oversight_without_independence" in codes
    assert "independent" in codes["oversight_without_independence"]


def test_oversight_at_any_depth_is_still_not_independent():
    """One level down is obvious; three levels down is how it actually happens."""
    d = doc()
    org = d["organization"]
    finance = next(t for t in org["teams"] if t["id"] == "finance")
    controllership = next(t for t in finance["teams"] if t["id"] == "controllership")
    audit = next(t for t in org["teams"] if t["id"] == "internal_audit")
    org["teams"].remove(audit)
    controllership.setdefault("teams", []).append(audit)

    assert "oversight_without_independence" in errors_of(d)


def test_oversight_that_shares_a_leader_is_refused():
    """Out of the subtree and under the same person is the same problem."""
    d = doc()
    org = d["organization"]
    audit = next(t for t in org["teams"] if t["id"] == "internal_audit")
    finance = next(t for t in org["teams"] if t["id"] == "finance")
    audit["leader"] = finance["leader"]

    codes = errors_of(d)
    assert "oversight_shares_a_leader" in codes


def test_an_escalation_may_not_land_in_your_own_subtree():
    d = doc()
    d["organization"]["unit_links"].append({
        "source": "finance", "target": "controllership",
        "kind": "escalates_to", "reason": "nowhere",
    })
    codes = errors_of(d)
    assert "escalation_runs_downward" in codes
    assert "has not left the problem" in codes["escalation_runs_downward"]


def test_a_link_to_a_unit_that_does_not_exist_is_refused():
    d = doc()
    d["organization"]["unit_links"].append({
        "source": "internal_audit", "target": "no_such_team",
        "kind": "oversees", "reason": "invented",
    })
    assert "unknown_unit_link_endpoint" in errors_of(d)


def test_a_unit_linked_to_itself_is_refused():
    d = doc()
    d["organization"]["unit_links"].append({
        "source": "finance", "target": "finance",
        "kind": "oversees", "reason": "itself",
    })
    assert "self_unit_link" in errors_of(d)


def test_the_same_link_twice_is_one_fact_said_twice():
    d = doc()
    d["organization"]["unit_links"].append({
        "source": "internal_audit", "target": "finance",
        "kind": "oversees", "reason": "again",
    })
    assert "duplicate_unit_link" in errors_of(d)


def test_partnership_in_both_directions_is_a_duplicate():
    """Symmetric by definition, so the reverse is not a second fact."""
    d = doc()
    d["organization"]["unit_links"].append({
        "source": "corporate_development", "target": "fpa",
        "kind": "partners_with", "reason": "the other way round",
    })
    assert "duplicate_unit_link" in errors_of(d)


def test_a_link_without_a_reason_is_a_warning_not_an_error():
    """An association nobody can explain is the decoration this model refuses,
    but it is a legibility problem rather than a wrong one."""
    d = doc()
    d["organization"]["unit_links"].append({
        "source": "treasury", "target": "tax", "kind": "serves",
    })
    findings = validate_spec(load_spec_text(yaml.safe_dump(d)))
    assert not [f for f in findings if f.severity == "error"]
    assert "unit_link_without_a_reason" in {f.code for f in findings}


def test_an_association_grants_nothing():
    """The whole point of keeping it separate from containment.

    Overseeing a unit must not widen a single principal's reach: if it did, a
    documentation fact would be an access grant, and nobody would notice.
    """
    spec = load_spec(NORTHWIND)
    bare = copy.deepcopy(spec)
    bare.unit_links = []

    def resolved(s):
        ir = build_ir(s)
        return {aid: sorted(p.key() for p in perms)
                for aid, perms in ir.permission_map().items()}

    assert resolved(spec) == resolved(bare)

    # And the mandates too: an association is not authority either.
    def mandates(s):
        ir = build_ir(s)
        return {a.id: sorted(a.mandate.decisions) for a in ir.agents}

    assert mandates(spec) == mandates(bare)


@pytest.mark.parametrize(
    "path", sorted(ROOT.glob("examples/*/*.system.yaml")),
    ids=lambda p: p.stem)
def test_every_declared_link_names_two_real_units(path):
    spec = load_spec(path)
    ids = set()

    def walk(team):
        ids.add(team.id)
        for child in team.teams:
            walk(child)

    walk(spec.organization)
    for link in spec.unit_links:
        assert link.source in ids, f"{path.name}: {link.source}"
        assert link.target in ids, f"{path.name}: {link.target}"
