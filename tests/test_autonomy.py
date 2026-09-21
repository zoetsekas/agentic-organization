"""Autonomy posture: what an agent does alone, and what it does for a person.

The posture used to be emergent — it fell out of the action, whether the
capability named a decision class, whether the agent held it, and whether
approval was required. Four fields, no stated intent, so an activity could
change from supervised to autonomous by deleting one line and nothing could
say that it had (ADR-0072).
"""
from __future__ import annotations

import pathlib

import pytest

from orgagents.spec.loader import load_spec
from orgagents.spec.model import AutonomyPosture, SystemSpec, autonomy_rank
from orgagents.spec.validate import errors, validate_spec


def _spec(**over) -> SystemSpec:
    base = {
        "metadata": {"name": "t", "spec_version": "1.1.0", "version": "0.1.0"},
        "decisions": [{"id": "spend"}],
        "capabilities": [
            {"id": "write_thing", "action": "write", "resource_class": "x"},
        ],
        "roles": [{"id": "doer", "title": "Doer", "capabilities": ["write_thing"]}],
        "organization": {
            "id": "root", "name": "Root", "leader": "a",
            "members": [{"id": "a", "name": "A", "roles": ["doer"],
                         "mandate": {"decisions": []}}],
            "teams": [],
        },
    }
    for k, v in over.items():
        base[k] = v
    return SystemSpec.model_validate(base)


def _codes(spec):
    return {f.code for f in validate_spec(spec)}


def test_the_default_posture_is_the_most_restrictive():
    """A capability nobody has thought about must not be the one running
    unattended."""
    spec = _spec()
    assert spec.capabilities[0].autonomy is AutonomyPosture.ADVISORY
    assert autonomy_rank(AutonomyPosture.ADVISORY) > autonomy_rank(
        AutonomyPosture.AUTONOMOUS
    )


def test_an_advisory_activity_may_not_change_a_system_of_record():
    """Four writes in the finance example were governed by nothing at all —
    no decision class, so no mandate, no bound, and nothing to escalate."""
    assert "advisory_mutates" in _codes(_spec())


def test_a_read_only_advisory_activity_is_fine():
    spec = _spec(capabilities=[{"id": "write_thing", "action": "query",
                                "resource_class": "x"}])
    assert "advisory_mutates" not in _codes(spec)


def test_autonomy_without_a_decision_class_is_refused():
    spec = _spec(capabilities=[{"id": "write_thing", "action": "write",
                                "resource_class": "x",
                                "autonomy": "autonomous"}])
    assert "autonomous_without_decision" in _codes(spec)


def test_autonomy_without_the_mandate_to_back_it_is_refused():
    spec = _spec(capabilities=[{"id": "write_thing", "action": "write",
                                "resource_class": "x", "decision": "spend",
                                "autonomy": "autonomous"}])
    assert "autonomous_without_mandate" in _codes(spec)


def test_autonomy_with_the_mandate_passes():
    spec = _spec(
        capabilities=[{"id": "write_thing", "action": "write",
                       "resource_class": "x", "decision": "spend",
                       "autonomy": "autonomous"}],
        organization={
            "id": "root", "name": "Root", "leader": "a",
            "members": [{"id": "a", "name": "A", "roles": ["doer"],
                         "mandate": {"decisions": ["spend"]}}],
            "teams": [],
        },
    )
    assert not [f for f in errors(validate_spec(spec))
                if f.code.startswith("autonom")]


def test_supervised_without_approval_is_refused():
    """Declaring that a person confirms means nothing if nothing stops."""
    spec = _spec(
        capabilities=[{"id": "write_thing", "action": "write",
                       "resource_class": "x", "decision": "spend",
                       "autonomy": "supervised"}],
        organization={
            "id": "root", "name": "Root", "leader": "a",
            "members": [{"id": "a", "name": "A", "roles": ["doer"],
                         "mandate": {"decisions": ["spend"]}}],
            "teams": [],
        },
    )
    assert "supervised_without_approval" in _codes(spec)


def test_being_confirmed_by_your_own_owner_is_not_a_second_pair_of_eyes():
    spec = _spec(
        capabilities=[{"id": "write_thing", "action": "write",
                       "resource_class": "x", "decision": "spend",
                       "autonomy": "supervised",
                       "constraints": {"requires_approval": True}}],
        organization={
            "id": "root", "name": "Root", "leader": "a",
            "members": [{"id": "a", "name": "A", "roles": ["doer"],
                         "mandate": {"decisions": ["spend"]},
                         "humans": [{"user_id": "u", "name": "O",
                                     "display_name": "O", "contact": "o@x.example",
                                     "roles": ["owner"]}]}],
            "teams": [],
        },
    )
    assert "supervised_by_its_own_owner" in _codes(spec)


def test_human_decides_refuses_when_the_agent_holds_the_decision():
    """Otherwise it never reaches a person at all."""
    spec = _spec(
        capabilities=[{"id": "write_thing", "action": "write",
                       "resource_class": "x", "decision": "spend",
                       "autonomy": "human_decides"}],
        organization={
            "id": "root", "name": "Root", "leader": "a",
            "members": [{"id": "a", "name": "A", "roles": ["doer"],
                         "mandate": {"decisions": ["spend"]}}],
            "teams": [],
        },
    )
    assert "human_decides_but_agent_holds" in _codes(spec)


def test_an_assignment_may_tighten_the_posture():
    spec = _spec(
        capabilities=[{"id": "write_thing", "action": "write",
                       "resource_class": "x", "decision": "spend",
                       "autonomy": "supervised",
                       "constraints": {"requires_approval": True}}],
        organization={
            "id": "root", "name": "Root", "leader": "a",
            "members": [{"id": "a", "name": "A", "roles": ["doer"],
                         "mandate": {"decisions": []},
                         "autonomy": {"write_thing": "human_decides"}}],
            "teams": [],
        },
    )
    assert "autonomy_widened" not in _codes(spec)


def test_an_assignment_may_not_loosen_it():
    """The same narrowing discipline roles, permissions and mandates follow."""
    spec = _spec(
        capabilities=[{"id": "write_thing", "action": "write",
                       "resource_class": "x", "decision": "spend",
                       "autonomy": "supervised",
                       "constraints": {"requires_approval": True}}],
        organization={
            "id": "root", "name": "Root", "leader": "a",
            "members": [{"id": "a", "name": "A", "roles": ["doer"],
                         "mandate": {"decisions": ["spend"]},
                         "autonomy": {"write_thing": "autonomous"}}],
            "teams": [],
        },
    )
    assert "autonomy_widened" in _codes(spec)


# --------------------------------------------------------------------------
# The worked finance example
# --------------------------------------------------------------------------


@pytest.fixture()
def northwind() -> SystemSpec:
    return load_spec(pathlib.Path("examples/northwind.finance.system.yaml"))


def test_the_finance_example_declares_a_posture_for_every_mutation(northwind):
    """No capability may change a system of record by default."""
    for cap in northwind.capabilities:
        if cap.action.value not in ("read", "query"):
            assert cap.autonomy is not AutonomyPosture.ADVISORY, cap.id


def test_the_finance_example_compiles_with_its_postures(northwind):
    assert not errors(validate_spec(northwind))


def test_invoice_approval_is_no_longer_autonomous_by_omission(northwind):
    """It is the AP control, and it ran with no human confirmation purely
    because nobody had set a flag."""
    cap = next(c for c in northwind.capabilities if c.id == "invoice_approval")
    assert cap.autonomy is AutonomyPosture.SUPERVISED
    assert cap.constraints.requires_approval


def test_clerical_volume_stays_autonomous(northwind):
    """The point is not to supervise everything — entry and cash application
    are exactly what should run alone."""
    for cid in ("invoice_entry", "cash_application"):
        cap = next(c for c in northwind.capabilities if c.id == cid)
        assert cap.autonomy is AutonomyPosture.AUTONOMOUS
        assert cap.decision, "autonomy needs something to govern it"


def test_every_autonomous_agent_carries_evaluation_evidence(northwind):
    """Never-evaluated is not safe-unattended (ADR-0060)."""
    assert not [f for f in validate_spec(northwind)
                if f.code == "autonomy_without_evidence"]


def test_capital_expenditure_still_has_nowhere_to_land(northwind):
    """Honest residue: a board is people, and people carry no mandate.

    ADR-0064 holds that question open, and this is what it costs — the agent
    prepares the case and the decision cannot complete inside the system.
    """
    found = [f for f in validate_spec(northwind)
             if f.code == "human_decides_with_no_holder"]
    assert found and "capex_register" in found[0].where
