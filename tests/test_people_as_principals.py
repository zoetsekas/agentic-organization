"""A person is a principal for authority and never for access (ADR-0079).

The goal is an organization of agents under the controls a human organization
runs on. Agents had the whole apparatus — roles, permissions resolved once,
mandates, separations, autonomy postures — and people had a five-value pairing
enum. A person's *relationship to an agent* was modelled and a person's
*position in the organization* was not, which is why separation of duties did
not cover the principal in most real frauds.

What these tests hold down is the line: authority yes, access never. A
permission this platform cannot enforce is worse than none.
"""
from __future__ import annotations

import pytest

from orgagents.compiler.ir import build_ir
from orgagents.mandates import resolve
from orgagents.spec.loader import load_spec
from orgagents.spec.model import SystemSpec
from orgagents.spec.validate import errors, validate_spec

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


BASE = {
    "metadata": {"name": "t", "spec_version": "1.1.0", "version": "0.1.0"},
    "decisions": [
        {"id": "raise_payment"}, {"id": "approve_payment"},
        {"id": "allocate_capital"},
    ],
    "organization": {
        "id": "root",
        "name": "Root",
        "leader": "ceo",
        "members": [
            {"id": "ceo", "name": "CEO",
             "mandate": {"decisions": ["approve_payment"]},
             "humans": [{"person": "p_chair", "roles": ["owner"]}]},
        ],
        "teams": [
            {
                "id": "fin",
                "name": "Finance",
                "leader": "cfo",
                "mandate": {"decisions": ["raise_payment", "approve_payment"]},
                "members": [
                    {"id": "cfo", "name": "CFO",
                     "mandate": {"decisions": ["approve_payment"]},
                     "humans": [{"person": "p_director", "roles": ["owner"]}]},
                    {"id": "clerk", "name": "Clerk",
                     "mandate": {"decisions": ["raise_payment"]},
                     "humans": [
                         {"person": "p_clerk", "roles": ["owner"]},
                         {"person": "p_director", "roles": ["approver"],
                          "approves": ["pay"]},
                     ]},
                ],
            }
        ],
    },
    "people": [
        {"id": "p_chair", "name": "Chair", "contact": "chair@t.example",
         "mandate": {"decisions": ["allocate_capital"]}},
        {"id": "p_director", "name": "Director", "contact": "dir@t.example",
         "unit": "fin", "mandate": {"decisions": ["approve_payment"]}},
        {"id": "p_clerk", "name": "Clerk", "contact": "clerk@t.example",
         "unit": "fin", "mandate": {"decisions": ["raise_payment"]}},
    ],
}


def _spec(**overrides) -> SystemSpec:
    import copy

    d = copy.deepcopy(BASE)
    d.update(copy.deepcopy(overrides))
    return SystemSpec.model_validate(d)


def _resolved(spec: SystemSpec):
    return resolve(
        spec.organization, [d.id for d in spec.decisions], spec.people
    )


# --------------------------------------------------------------------------
# One human, one principal
# --------------------------------------------------------------------------


def test_a_person_referenced_by_several_agents_is_one_principal():
    """The precondition for every other check here.

    The director owns the CFO agent and approves on the clerk's. Declared
    inline, that was two principals with two invented ids, and no separation
    check could see past it.
    """
    spec = _spec()
    paired = [
        a.id for a in spec.agents()
        for h in a.humans if h.principal() == "p_director"
    ]
    assert paired == ["cfo", "clerk"]
    assert len(_resolved(spec).people) == 3


def test_two_people_sharing_one_mailbox_are_refused():
    """The defect that made this decision necessary, caught directly."""
    spec = _spec(people=BASE["people"] + [
        {"id": "p_director_again", "name": "Director",
         "contact": "dir@t.example"},
    ])
    codes = [f.code for f in validate_spec(spec)]
    assert "duplicate_person" in codes


def test_a_pairing_naming_nobody_declared_is_refused():
    spec = _spec()
    spec.organization.teams[0].members[0].humans[0].person = "p_ghost"
    assert any(
        f.code == "unknown_reference" and "p_ghost" in f.message
        for f in validate_spec(spec)
    )


# --------------------------------------------------------------------------
# Authority yes, access never
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim",
    [
        {"capabilities": ["ledger_write"]},
        {"permissions": [{"action": "write", "resource_kind": "data_class",
                          "resource": "ledger"}]},
    ],
)
def test_a_person_holding_access_is_refused_with_the_reason(claim):
    """We do not mediate a person's access, so we do not model it.

    They sign into the ERP under their employer's IAM. A permission declared
    here would read as enforced and enforce nothing, which is the defect
    ADR-0073 refuses for controls, applied to principals.
    """
    people = [dict(p) for p in BASE["people"]]
    people[1].update(claim)
    found = [f for f in validate_spec(_spec(people=people))
             if f.code == "person_holds_access"]
    assert found, f"a person declaring {list(claim)} must be refused"
    assert "mandate" in found[0].message
    assert "IAM" in found[0].message


def test_a_person_holding_only_a_mandate_is_accepted():
    assert not errors(validate_spec(_spec()))


# --------------------------------------------------------------------------
# A person's authority narrows against their unit
# --------------------------------------------------------------------------


def test_a_persons_mandate_is_bounded_by_the_unit_they_sit_in():
    """A finance director's authority stops at Finance."""
    people = [dict(p) for p in BASE["people"]]
    people[1]["mandate"] = {
        "decisions": ["approve_payment", "allocate_capital"]
    }
    spec = _spec(people=people)
    resolved = _resolved(spec)
    assert resolved.for_person("p_director").decisions == {"approve_payment"}
    assert resolved.overreach["p_director"] == ["allocate_capital"]
    assert any(f.code == "mandate_overreach" and f.where == "p_director"
               for f in validate_spec(spec))


def test_a_person_attached_to_nothing_sits_under_the_root():
    """The widest bound the organization has, and still a bound."""
    resolved = _resolved(_spec())
    assert resolved.person_unit["p_chair"] == "root"
    assert resolved.for_person("p_chair").line == ("root", "p_chair")


def test_a_person_attached_to_a_unit_that_does_not_exist_is_refused():
    people = [dict(p) for p in BASE["people"]]
    people[1]["unit"] = "nowhere"
    assert any(
        f.code == "unknown_reference" and "nowhere" in f.message
        for f in validate_spec(_spec(people=people))
    )


# --------------------------------------------------------------------------
# Escalation reaches a person
# --------------------------------------------------------------------------


def test_the_holder_search_reaches_a_person_when_no_agent_holds_it():
    """Agents first, then the people on that unit (ADR-0079).

    Capital allocation is a board's. No agent holds it, and before this the
    search returned `None` — a refusal — so the work could not complete inside
    the organization.
    """
    resolved = _resolved(_spec())
    assert resolved.holder("clerk", "allocate_capital") == "p_chair"
    assert resolved.is_person("p_chair")


def test_an_agent_is_preferred_over_a_person_on_the_same_line():
    """An escalation prefers something that can act here."""
    resolved = _resolved(_spec())
    assert resolved.holder("clerk", "approve_payment") == "cfo"


def test_a_decision_only_a_person_holds_is_not_reported_as_unheld():
    resolved = _resolved(_spec())
    assert resolved.holders("allocate_capital") == ["p_chair"]
    assert resolved.agent_holders("allocate_capital") == []


# --------------------------------------------------------------------------
# Separation of duties covers people
# --------------------------------------------------------------------------


SEPARATION = [{
    "id": "payment_control",
    "decisions": ["raise_payment", "approve_payment"],
    "reason": "Raising and approving the same payment is one signature.",
}]


def test_a_person_holding_both_sides_of_a_control_is_refused():
    people = [dict(p) for p in BASE["people"]]
    people[1]["mandate"] = {
        "decisions": ["raise_payment", "approve_payment"]
    }
    found = [f for f in validate_spec(_spec(people=people,
                                            separations=SEPARATION))
             if f.code == "separation_violated" and f.where == "p_director"]
    assert found
    assert "frauds" in found[0].message


def test_people_who_split_a_control_between_them_are_accepted():
    assert not errors(validate_spec(_spec(separations=SEPARATION)))


def test_a_person_may_not_approve_what_an_agent_they_own_raises():
    """Four-eyes, expressed where it can be checked (ADR-0079 rule 5).

    ADR-0072 rule 3 could only approximate this, because a person was not an
    identity and the owner and the approver were two inline declarations that
    happened to name the same human.
    """
    spec = _spec()
    clerk = spec.organization.teams[0].members[1]
    clerk.humans[1].person = "p_clerk"     # the owner, now also the approver
    found = [f for f in validate_spec(spec)
             if f.code == "owner_approves_own_agent"]
    assert found and found[0].where == "clerk"
    assert "p_clerk" in found[0].message


def test_owning_one_agent_and_approving_on_another_is_fine():
    """Which is the whole point: the director does exactly this."""
    assert not [f for f in validate_spec(_spec())
                if f.code == "owner_approves_own_agent"]


# --------------------------------------------------------------------------
# The compiled artifact carries it
# --------------------------------------------------------------------------


def test_the_ir_carries_a_persons_authority_and_no_access():
    ir = build_ir(_spec())
    director = next(p for p in ir.people if p.id == "p_director")
    assert director.mandate.decisions == ["approve_payment"]
    assert director.unit == "fin"
    assert director.pairings == ["cfo:owner", "clerk:approver"]
    # There is nowhere in this structure to put access, and that is deliberate.
    assert not hasattr(director, "capabilities")
    assert not hasattr(director, "permissions")


# --------------------------------------------------------------------------
# The worked example
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def northwind() -> SystemSpec:
    return load_spec(ROOT / "examples" / "northwind.finance.system.yaml")


def test_northwind_declares_one_finance_director_not_six(northwind):
    """The defect, measured.

    One human appeared six times under six invented ids, so the same person
    was six principals and no check over people could have worked.
    """
    directors = [p for p in northwind.people
                 if p.contact == "marcus.oyelaran@northwind.example"]
    assert len(directors) == 1
    assert directors[0].id == "p_cfo"

    paired = [
        (a.id, tuple(r.value for r in h.roles))
        for a in northwind.agents() for h in a.humans
        if h.principal() == "p_cfo"
    ]
    assert len(paired) == 6, "one principal, still six pairings"


def test_northwind_pairings_carry_a_reference_and_not_a_copy(northwind):
    for agent in northwind.agents():
        for human in agent.humans:
            assert human.person, (
                f"agent '{agent.id}' still declares a person inline"
            )
            assert northwind.person(human.person) is not None


def test_northwinds_capital_allocation_lands_on_the_chief_executive(northwind):
    resolved = resolve(
        northwind.organization,
        [d.id for d in northwind.decisions],
        northwind.people,
    )
    assert resolved.holders("approve_capex") == ["p_ceo"]
    corpdev = resolved.holder("corpdev_lead", "approve_capex")
    assert corpdev == "p_ceo", "the case reaches somebody who can decide it"


def test_northwind_survives_the_checks_it_now_gets(northwind):
    assert not errors(validate_spec(northwind))
