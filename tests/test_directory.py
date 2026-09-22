"""Directory reconciliation: departed and unknown pairings (ADR-0044, WS-016 M4)."""
import json
from pathlib import Path

import pytest

from orgagents.directory import (
    Directory,
    DirectoryUnavailable,
    FetchDirectory,
    NullDirectory,
    PersonStatus,
    StaticDirectory,
    reconcile,
)
from orgagents.spec import load_spec, validate_spec
from orgagents.spec.model import HumanRole
from orgagents.spec.validate import directory_findings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme" / "acme.system.yaml"


@pytest.fixture(scope="module")
def spec():
    return load_spec(EXAMPLE)


def _contacts(spec):
    return {h.contact for a in spec.agents() for h in a.humans}


def _everyone_active(spec, **overrides):
    people = {c: {"active": True, "display_name": c.split("@")[0]}
              for c in _contacts(spec)}
    people.update(overrides)
    return StaticDirectory(people, name="acme-ldap")


def _owner_of(spec, agent_id):
    return spec.agent(agent_id).owner


# -- the protocol and its implementations ---------------------------------


def test_null_directory_knows_nothing_and_says_so():
    null = NullDirectory()
    assert isinstance(null, Directory)
    person = null.lookup("anyone@acme.example")
    assert person.status is PersonStatus.UNKNOWN
    assert not person.is_active          # never reports absent people as present
    assert null.knows_anyone() is False


def test_static_directory_reads_a_dict_and_distinguishes_three_answers():
    d = StaticDirectory({
        "Ana@Acme.example": {"active": True, "display_name": "Ana Silva",
                             "groups": ["finance"], "manager": "priya@acme.example"},
        "tom@acme.example": {"active": False},
        "lena@acme.example": "departed",
    })
    ana = d.lookup("ana@acme.example")          # contacts match case-insensitively
    assert ana.is_active and ana.display_name == "Ana Silva"
    assert ana.groups == ("finance",) and ana.manager == "priya@acme.example"
    assert d.lookup("tom@acme.example").status is PersonStatus.DEPARTED
    assert d.lookup("lena@acme.example").is_departed
    assert d.lookup("ghost@acme.example").status is PersonStatus.UNKNOWN
    assert d.knows_anyone()


def test_static_directory_from_file(tmp_path):
    path = tmp_path / "people.json"
    path.write_text(json.dumps({"people": [
        {"contact": "ana@acme.example", "active": True, "displayName": "Ana"},
        {"userName": "tom@acme.example", "active": False},
    ]}))
    d = StaticDirectory.from_file(path)
    assert len(d) == 2
    assert d.lookup("ana@acme.example").display_name == "Ana"
    assert d.lookup("tom@acme.example").is_departed


def test_empty_static_directory_knows_nobody():
    assert StaticDirectory({}).knows_anyone() is False


def test_fetch_directory_maps_a_scim_shaped_stub():
    payloads = {
        "ana@acme.example": {"active": True, "displayName": "Ana Silva",
                             "groups": ["finance"]},
        "tom@acme.example": {"accountEnabled": False, "displayName": "Tom Becker"},
    }
    calls = []

    def fetch(contact):
        calls.append(contact)
        return payloads.get(contact)

    d = FetchDirectory(fetch, name="graph-stub")
    assert d.lookup("ana@acme.example").is_active
    assert d.lookup("tom@acme.example").status is PersonStatus.DEPARTED
    assert d.lookup("nobody@acme.example").status is PersonStatus.UNKNOWN
    d.lookup("ana@acme.example")
    assert calls.count("ana@acme.example") == 1     # cached


def test_fetch_directory_turns_provider_errors_into_unavailable():
    def fetch(contact):
        raise ConnectionError("no route to host")

    with pytest.raises(DirectoryUnavailable):
        FetchDirectory(fetch, name="ldap-stub").lookup("ana@acme.example")


# -- reconciliation --------------------------------------------------------


def test_reconciliation_is_silent_when_everyone_is_active(spec):
    report = reconcile(spec, _everyone_active(spec))
    assert report.consulted and report.issues == []


def test_reconciliation_reports_a_departed_owner(spec):
    owner = _owner_of(spec, "analyst")
    report = reconcile(spec, _everyone_active(
        spec, **{owner.contact: {"active": False, "display_name": owner.name}}))
    mine = [i for i in report.issues if i.where == "analyst"]
    assert [i.contact for i in mine] == [owner.contact]
    assert mine[0].departed and mine[0].is_owner
    assert HumanRole.OWNER.value in mine[0].roles


def test_unknown_is_not_reported_as_departed(spec):
    owner = _owner_of(spec, "analyst")
    people = {c: {"active": True} for c in _contacts(spec) if c != owner.contact}
    report = reconcile(spec, StaticDirectory(people, name="acme-ldap"))
    issue = next(i for i in report.issues if i.contact == owner.contact)
    assert issue.unknown and not issue.departed
    assert report.unknown and not report.departed


def test_reconciliation_covers_approvers_and_every_other_paired_role(spec):
    # One person per distinct role across the whole fleet, all departed.
    by_role = {}
    for agent in spec.agents():
        for human in agent.humans:
            for role in human.roles:
                by_role.setdefault(role, (agent.id, human.contact))
    assert {HumanRole.OWNER, HumanRole.APPROVER, HumanRole.REVIEWER} <= set(by_role)
    departed = {contact: {"active": False} for _, contact in by_role.values()}
    report = reconcile(spec, _everyone_active(spec, **departed))
    seen = {(i.where, i.contact) for i in report.issues}
    for role, pair in by_role.items():
        assert pair in seen, role
    assert all(i.departed for i in report.issues)


def test_a_null_directory_reconciles_to_nothing(spec):
    report = reconcile(spec, NullDirectory())
    assert report.issues == [] and report.consulted is False
    assert reconcile(spec).issues == []          # and no directory at all


def test_a_directory_outage_is_no_opinion_not_a_wall_of_findings(spec):
    def fetch(contact):
        raise DirectoryUnavailable("ldap timeout")

    report = reconcile(spec, FetchDirectory(fetch, name="ldap-stub"))
    assert report.issues == [] and report.consulted is False
    assert "ldap timeout" in report.unavailable_reason
    with pytest.raises(DirectoryUnavailable):
        reconcile(spec, FetchDirectory(fetch, name="ldap-stub"), strict=True)


# -- findings --------------------------------------------------------------


def test_no_directory_and_a_null_directory_produce_no_findings(spec):
    assert directory_findings(spec, None) == []
    assert directory_findings(spec, NullDirectory()) == []
    baseline = {(f.code, f.where) for f in validate_spec(spec)}
    assert {(f.code, f.where) for f in validate_spec(spec, NullDirectory())} == baseline
    assert not any(f.code.startswith("departed") for f in validate_spec(spec))


def test_a_departed_owner_is_a_finding_of_its_own_code(spec):
    owner = _owner_of(spec, "analyst")
    findings = validate_spec(spec, _everyone_active(
        spec, **{owner.contact: {"active": False}}))
    dep = [f for f in findings if f.code == "departed_owner"]
    assert [f.where for f in dep] == ["analyst"]
    assert dep[0].severity == "warning"          # non-production
    assert "departed" in dep[0].message and "acme-ldap" in dep[0].message


def _never_an_owner(spec):
    """A contact paired only in non-owner roles, anywhere in the fleet."""
    owners = {h.contact for a in spec.agents() for h in a.humans if h.is_owner}
    return next(h for a in spec.agents() for h in a.humans
                if h.contact not in owners)


def test_a_departed_non_owner_is_a_separate_code(spec):
    reviewer = _never_an_owner(spec)
    findings = directory_findings(spec, _everyone_active(
        spec, **{reviewer.contact: {"active": False}}))
    assert {f.code for f in findings} == {"departed_human"}
    assert all(f.severity == "warning" for f in findings)


def test_an_unknown_contact_never_reports_as_departed(spec):
    owner = _owner_of(spec, "analyst")
    people = {c: {"active": True} for c in _contacts(spec) if c != owner.contact}
    findings = directory_findings(spec, StaticDirectory(people, name="acme-ldap"))
    assert {f.code for f in findings} == {"human_unknown_to_directory"}
    assert "no record" in findings[0].message


def test_production_promotes_a_departed_owner_but_not_an_unknown_one(spec):
    production = spec.model_copy(deep=True)
    production.metadata.environment = "production"
    owner = _owner_of(production, "analyst")
    other = _never_an_owner(production)

    promoted = directory_findings(production, _everyone_active(
        production, **{owner.contact: {"active": False}}))
    assert [(f.code, f.severity) for f in promoted] == [("departed_owner", "error")]

    stale = directory_findings(production, _everyone_active(
        production, **{other.contact: {"active": False}}))
    assert all(f.severity == "warning" for f in stale)

    people = {c: {"active": True} for c in _contacts(production)
              if c != owner.contact}
    unknown = directory_findings(production, StaticDirectory(people, name="ldap"))
    assert {(f.code, f.severity) for f in unknown} == {
        ("human_unknown_to_directory", "warning")}
