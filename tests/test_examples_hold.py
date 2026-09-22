"""Every shipped example is a claim about what this platform can express.

An example that does not validate is worse than no example: it is a worked
answer that is wrong, and it is the first thing somebody copies. So each one
in `examples/` is loaded, validated and compiled here, and a broken one
breaks the suite rather than waiting to be noticed.

The examples are deliberately unlike each other. Northwind is segregation of
duties over a payment; Meridian is a three-lines-of-defence lender where a
decline belongs to a human by statute; Lumière is a consumer business where
the control is on what an agent may *say*; Northbeam is a marketing function
where consent is a legal basis and measurement must not grade itself; Sentinel
is a SOC where the analyst runs in two sandboxes and every agent is authored
with its own instructions (ADR-0082, ADR-0083).
"""
from __future__ import annotations

import pathlib

import pytest

from orgagents.compiler.engine import compile_system
from orgagents.spec.loader import load_spec
from orgagents.spec.validate import validate_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]
SYSTEMS = sorted(ROOT.glob("examples/*.system.yaml"))


def test_the_examples_we_expect_are_all_here():
    """A rename or a deletion should be a decision, not an accident."""
    assert {p.name for p in SYSTEMS} == {
        "acme.system.yaml",
        "northwind.finance.system.yaml",
        "meridian.lending.system.yaml",
        "lumiere.beauty.system.yaml",
        "northbeam.marketing.system.yaml",
        "sentinel.secops.system.yaml",
        "helios.pharma.system.yaml",
        "atlas.bank.system.yaml",
    }


@pytest.mark.parametrize("path", SYSTEMS, ids=lambda p: p.stem)
def test_an_example_validates_without_an_error(path):
    findings = validate_spec(load_spec(path))
    errors = [str(f) for f in findings if f.severity == "error"]
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("path", SYSTEMS, ids=lambda p: p.stem)
def test_an_example_compiles(path, tmp_path):
    results = compile_system(load_spec(path), targets=["local", "maf"],
                             out_dir=str(tmp_path / path.stem))
    for result in results:
        assert result.files, f"{path.name} compiled to nothing for a target"


#: `acme` is the breadth example — every field the spec has, exercised once.
#: The rest are organisations, and an organisation without a separation of
#: duties is a hierarchy, which is what every other framework already gives
#: you. That distinction is the platform's claim, so it is held here.
ORGANISATIONS = [p for p in SYSTEMS if p.name != "acme.system.yaml"]


@pytest.mark.parametrize("path", ORGANISATIONS, ids=lambda p: p.stem)
def test_an_example_declares_a_control_worth_having(path):
    spec = load_spec(path)
    assert spec.separations, f"{path.name} declares no separation of duties"


@pytest.mark.parametrize("path", ORGANISATIONS, ids=lambda p: p.stem)
def test_a_separation_actually_bites_on_a_principal(path):
    """A rule nobody could break is decoration.

    Every separation must name decisions the declared principals actually
    hold between them — otherwise it is a sentence about a control rather
    than a control, and the spec would validate with it deleted. Principals
    are people *and* agents (ADR-0070): `assess_and_decide` keeps two agents
    apart, `alert_and_report` keeps two people apart, and a check that looked
    at only one kind would call the other decoration.
    """
    spec = load_spec(path)
    held = {d for person in spec.people for d in person.mandate.decisions}

    def walk(team):
        for agent in team.members:
            held.update(agent.mandate.decisions if agent.mandate else [])
        for child in team.teams:
            walk(child)

    walk(spec.organization)
    for separation in spec.separations:
        covered = [d for d in separation.decisions if d in held]
        assert len(covered) >= 2, (
            f"{path.name}: separation '{separation.id}' keeps "
            f"{separation.decisions} apart, but the declared people hold only "
            f"{covered} between them, so nothing is being kept apart")


@pytest.mark.parametrize("path", ORGANISATIONS, ids=lambda p: p.stem)
def test_no_two_components_share_an_id(path):
    """One id, one component — including across kinds.

    References are looked up per kind, so a data class and a team sharing an
    id resolves. Nothing that *draws* a spec can resolve it: a picture has one
    box per id, and the designer's layout is keyed by id, so one of the two is
    invisible. Two of these examples had it, and both were accidents.
    """
    findings = validate_spec(load_spec(path))
    clashes = [str(f) for f in findings if f.code == "id_used_by_two_kinds"]
    assert not clashes, "\n".join(clashes)
