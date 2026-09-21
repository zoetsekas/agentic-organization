"""The designer's view of authority and autonomy.

Two design facts the UI could not previously show: what an agent may *decide*,
and how much of each activity it does without a person. Neither is a
permission — permission is whether the door opens, a mandate is whether you
were the one to open it.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient

from orgagents.api import create_app

ALICE = {"X-User": "alice"}


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "authority.db")))


@pytest.fixture()
def northwind(client) -> str:
    """The worked finance function, opened as a design."""
    spec = yaml.safe_load(
        pathlib.Path("examples/northwind.finance.system.yaml").read_text()
    )
    ws = client.post("/api/designer/workspaces", json={"name": "ws"},
                     headers=ALICE).json()
    created = client.post("/api/designer/systems",
                          json={"workspace_id": ws["id"], "name": "nw",
                                "spec": spec}, headers=ALICE).json()
    return created["id"]


def _authority(client, system_id):
    res = client.get(f"/api/designer/systems/{system_id}/authority",
                     headers=ALICE)
    assert res.status_code == 200
    return res.json()


def test_the_mandate_shown_is_effective_and_not_declared(client, northwind):
    """Showing the declaration would let a reader believe an agent holds
    something its line excludes."""
    data = _authority(client, northwind)
    treasurer = data["agents"]["treasurer"]
    assert treasurer["declared"] is None, "it declares nothing of its own"
    assert "release_payment" in treasurer["decisions"], "and still holds it"


def test_the_line_names_the_units_that_produced_the_mandate(client, northwind):
    """So a refusal can be explained to somebody who did not write the spec."""
    data = _authority(client, northwind)
    assert data["agents"]["payables"]["line"] == [
        "northwind", "finance", "controllership", "payables"
    ]


def test_an_advisory_unit_holds_only_what_its_work_needs(client, northwind):
    """Corporate development models — which writes to the planning tool, so it
    holds `revise_plan` — and takes no decision that commits money."""
    data = _authority(client, northwind)
    assert data["agents"]["corpdev_lead"]["decisions"] == ["revise_plan"]


def test_activities_carry_their_posture(client, northwind):
    data = _authority(client, northwind)
    by_cap = {a["capability"]: a
              for a in data["agents"]["payables"]["activities"]}
    assert by_cap["invoice_entry"]["posture"] == "autonomous"
    assert by_cap["invoice_entry"]["decision"] == "raise_payment"
    assert by_cap["ledger_query"]["posture"] == "advisory"


def test_a_supervised_activity_reports_that_a_person_confirms(client, northwind):
    data = _authority(client, northwind)
    by_cap = {a["capability"]: a
              for a in data["agents"]["treasurer"]["activities"]}
    assert by_cap["payment_release"]["posture"] == "supervised"
    assert by_cap["payment_release"]["requires_approval"] is True


def test_bounds_travel_with_the_mandate(client, northwind):
    """A condition is part of the authority, not a footnote to it."""
    data = _authority(client, northwind)
    conditions = data["agents"]["cash_manager"]["conditions"]
    assert any("max_facility_gbp" in c for c in conditions)


def test_separations_say_who_enforces_them(client, northwind):
    data = _authority(client, northwind)
    payment = next(r for r in data["separations"] if r["id"] == "payment_control")
    assert payment["enforced_by"] == "both"
    assert "ERP" in payment["enforced_in"]
    assert payment["reason"], "a rule without one is a rule nobody defends"


def test_findings_reach_the_view_where_the_thing_is_edited(client, northwind):
    """The authority view carries the authority findings, and no others.

    Northwind used to fail here on `human_decides_with_no_holder`: capital
    expenditure had no holder because a board is people and people carried no
    mandate. ADR-0079 gave it one, so the view is clean of errors — which is
    the state a reviewer should be able to trust.
    """
    data = _authority(client, northwind)
    assert not [f for f in data["findings"] if f["severity"] == "error"]


def test_only_authority_findings_are_carried(client, northwind):
    """The view is about authority; unrelated warnings belong elsewhere."""
    data = _authority(client, northwind)
    assert not [f for f in data["findings"]
                if f["code"] in ("no_guardrails", "wildcard_resource")]


def test_a_design_that_does_not_compile_is_absent_not_broken(client):
    """A design mid-edit legitimately has nothing to say."""
    ws = client.post("/api/designer/workspaces", json={"name": "ws"},
                     headers=ALICE).json()
    created = client.post(
        "/api/designer/systems",
        json={"workspace_id": ws["id"], "name": "half",
              "spec": {"metadata": {"name": "h", "spec_version": "1.1.0",
                                    "version": "0.1.0"},
                       "organization": {"id": "root", "name": "R",
                                        "members": [{"name": "no id yet"}]}}},
        headers=ALICE).json()
    res = client.get(f"/api/designer/systems/{created['id']}/authority",
                     headers=ALICE)
    assert res.status_code != 200


def test_the_palette_offers_the_authority_fields(client):
    """A field the UI cannot edit is a field nobody will set."""
    palette = client.get("/api/designer/palette", headers=ALICE).json()
    kinds = {k["kind"]: k
             for group in palette["groups"] for k in group["kinds"]}
    agent_fields = {f["name"]: f for f in kinds["agent"]["fields"]}
    assert agent_fields["mandate"]["type"] == "decisions"
    assert agent_fields["autonomy"]["type"] == "autonomy"
    assert "mandate" in {f["name"] for f in kinds["team"]["fields"]}
    assert "separation" in kinds and "decision" in kinds


def test_the_view_carries_people_and_nothing_they_may_reach(client, northwind):
    """People are principals for authority and never for access (ADR-0079).

    A reviewer needs to see where an escalation lands. They must not be shown
    a person's access, because there is none to show: the person signs into
    those systems under their employer's identity, not ours.
    """
    data = _authority(client, northwind)
    cfo = data["people"]["p_cfo"]
    assert cfo["position"] == "Chief Financial Officer"
    assert cfo["unit"] == "finance"
    assert "approve_invoice" in cfo["decisions"]
    assert len(cfo["pairings"]) == 6, "one principal, six pairings"
    assert not any(
        k in person for person in data["people"].values()
        for k in ("capabilities", "permissions")
    )


def test_capital_allocation_is_visible_as_a_persons(client, northwind):
    data = _authority(client, northwind)
    assert "approve_capex" in data["people"]["p_ceo"]["decisions"]
    assert not [a for a in data["agents"].values()
                if "approve_capex" in a["decisions"]]
