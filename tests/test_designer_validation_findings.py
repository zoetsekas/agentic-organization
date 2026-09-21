"""A validation failure has to name what is wrong with it.

The designer's right panel showed one bullet holding the whole of pydantic's
report: four errors, four documentation URLs and every input value, run
together into a paragraph nobody could read and nothing could be clicked. The
information was all there; none of it was usable.

These hold the shape that fixed it: one finding per error, the message
without the apparatus, and the id of the nearest component that has one — the
thing the reader is actually looking at on the canvas.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app

ANA = {"X-User": "ana", "X-User-Name": "Ana"}


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "d.db")))


def open_draft(client, spec):
    ws = client.post("/api/designer/workspaces", json={"name": "W"},
                     headers=ANA).json()
    made = client.post("/api/designer/systems",
                       json={"workspace_id": ws["id"], "name": "S",
                             "spec": spec}, headers=ANA).json()
    body = client.get(f"/api/designer/systems/{made['id']}", headers=ANA).json()
    return body["validation"]


def a_draft_with_a_bad_mandate() -> dict:
    """`mandate` is a scope of decision, and a list is not one (ADR-0065)."""
    return {
        "metadata": {"name": "S", "spec_version": "1.1.0", "version": "0.1.0",
                     "environment": "development"},
        "organization": {"id": "root", "name": "S", "leader": "",
                         "mandate": [], "members": [],
                         "teams": [{"id": "team_3", "name": "Three",
                                    "leader": "", "mandate": [],
                                    "members": [], "teams": []}]},
    }


def test_each_error_is_its_own_finding(client):
    validation = open_draft(client, a_draft_with_a_bad_mandate())
    assert validation["ok"] is False
    findings = validation["findings"]
    # Two bad mandates, two findings — not one paragraph holding both.
    assert len(findings) == 2
    assert {f["where"] for f in findings} == {
        "organization.mandate", "organization.teams.0.mandate"}


def test_a_finding_names_the_component_it_is_about(client):
    findings = open_draft(client, a_draft_with_a_bad_mandate())["findings"]
    by_where = {f["where"]: f for f in findings}
    # Nearest, not outermost: the sub-team's error is the sub-team's.
    assert by_where["organization.teams.0.mandate"]["component"] == "team_3"
    assert by_where["organization.mandate"]["component"] == "root"


def test_a_finding_carries_the_sentence_and_not_the_apparatus(client):
    findings = open_draft(client, a_draft_with_a_bad_mandate())["findings"]
    for finding in findings:
        assert "mandate is a scope of decision" in finding["message"]
        # The documentation URL, the input value and the error count are
        # pydantic's framing, and none of them told the reader anything.
        assert "errors.pydantic.dev" not in finding["message"]
        assert "input_value" not in finding["message"]
        assert "validation error" not in finding["message"].lower()


def test_the_strings_still_carry_every_finding(client):
    """`errors` is what non-UI callers report; it must not lose anything."""
    validation = open_draft(client, a_draft_with_a_bad_mandate())
    assert len(validation["errors"]) == len(validation["findings"])


def test_a_finding_about_no_component_says_so_rather_than_guessing(client):
    draft = a_draft_with_a_bad_mandate()
    draft["metadata"]["environment"] = "not-an-environment"
    findings = open_draft(client, draft)["findings"]
    metadata = [f for f in findings if f["where"].startswith("metadata")]
    assert metadata, "the bad environment should be reported"
    # `metadata` is not a component; claiming it was the organisation would
    # send the reader to the wrong box.
    assert all(f["component"] == "" for f in metadata)


def test_a_validator_finding_that_names_a_component_stays_traceable(client,
                                                                    tmp_path):
    """The two validators name locations differently, and both must land.

    `validate_spec` says "payables_clerk"; pydantic says
    "organization.teams.0.mandate". If only the second were understood, every
    finding from the rule set — which is most of them — would arrive with
    nothing to click.
    """
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[1]
    spec = yaml.safe_load(
        (root / "examples" / "northwind.finance.system.yaml").read_text())
    validation = open_draft(client, spec)
    attributed = [f for f in validation["findings"] if f["component"]]
    assert attributed, "findings that name a component should carry its id"
    for finding in attributed:
        assert finding["component"] == finding["where"] or \
            finding["where"].endswith(finding["component"]) or \
            "." in finding["where"]
