"""The model, played out (ADR-0102).

Model-driven development: the metamodel's constraints and operations are the
behaviour, and these scenarios are its specification. The designer and the
compiler come after, and are checked against this.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orgagents.metamodel.constraints import CONSTRAINTS, check
from orgagents.metamodel.instances import collect
from orgagents.metamodel.operations import OperationError, link, relationship
from orgagents.metamodel.scenarios import (SCENARIOS, base, catalogue, play,
                                           to_object_diagram)
from orgagents.spec.loader import load_spec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*/*.system.yaml"))


def test_the_base_organisation_is_a_valid_model():
    assert check(base()) == []


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_the_scenario_holds(scenario):
    played = play(scenario)
    assert not played.failures, "\n".join(played.failures)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_every_shipped_example_is_a_valid_model(path):
    violations = check(load_spec(path))
    assert not violations, "\n".join(map(str, violations))


def test_every_constraint_is_shown_refusing_something():
    """A constraint no scenario ever trips is a rule nobody has seen work."""
    refused = {step.refused_by or step.incomplete
               for sc in SCENARIOS for step in sc.steps}
    assert {c.name for c in CONSTRAINTS} <= refused, \
        sorted({c.name for c in CONSTRAINTS} - refused)


def test_a_draft_may_be_incomplete_but_never_broken():
    """ADR-0103: a new element may lack a required end; an existing one may
    not be made to, and integrity is never broken."""
    from orgagents.metamodel.operations import create, delete
    made = create(base(), "trigger", "hourly")
    assert made.accepted and {v.constraint for v in made.incomplete} == \
        {"multiplicities_hold"}
    refused = delete(base(), "tool", "ledger_lookup")
    assert not refused.accepted


def test_every_constraint_states_its_rule_in_ocl_and_has_a_context():
    for c in CONSTRAINTS:
        assert c.ocl and c.context and c.doc, c.name


def test_a_refused_operation_leaves_the_model_untouched():
    before = base()
    result = link(before, ("team", "ap"), ("team", "ops"), "unit link",
                  kind="oversees")
    assert not result.accepted
    assert result.spec is before
    assert before.model_dump() == base().model_dump()


def test_an_ambiguous_link_must_say_which_relationship():
    with pytest.raises(OperationError, match="leads, member"):
        relationship("team", "agent")
    assert relationship("team", "agent", "member").field == "members"


def test_an_organisation_stands_wherever_a_team_may():
    model = collect(base())
    assert "acme" in model.ids("team")
    assert model.get("team", "acme").kind == "organization"


def test_the_catalogue_and_diagrams_are_current():
    """docs/metamodel/scenarios* are generated; regenerate with
    `orgagents metamodel scenarios`."""
    docs = ROOT / "docs" / "metamodel"
    assert (docs / "scenarios.md").read_text() == catalogue()
    for sc in SCENARIOS:
        assert (docs / "scenarios" / f"{sc.id}.puml").read_text() == \
            to_object_diagram(play(sc)), sc.id
