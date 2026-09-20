"""The designer's two review surfaces over HTTP (WS-009).

`GET /api/designer/systems/{id}/gate` reports the evaluation gate and
`GET .../diff` reports what moved between two revisions. Both are read-only:
the gate answers "what does the evidence say today", so a request must never
leave a run behind, and the diff reads revisions that already exist.

Every test here calls the route, because a route is where the wiring bugs live
(a model imported inside `create_app` silently degrades to a query parameter).
"""
import copy

import pytest
import yaml
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.evaluations import CaseResponse, EvaluationService
from orgagents.spec.loader import load_spec_text

ALICE = {"X-User": "alice"}
MALLORY = {"X-User": "mallory"}

SPEC = {
    "metadata": {"name": "review-demo", "spec_version": "1.1.0",
                 "version": "0.1.0", "environment": "development"},
    "data_classes": [
        {"id": "public_knowledge", "name": "Public", "sensitivity": "public"},
        {"id": "customer_pii", "name": "Customer PII", "sensitivity": "restricted"},
    ],
    "roles": [
        {"id": "analyst_role", "title": "Analyst", "kind": "agent",
         "responsibilities": ["Answer questions."],
         "permissions": [{"action": "read", "resource_kind": "data_class",
                          "resource": "public_knowledge"}]},
    ],
    "organization": {
        "id": "root", "name": "Root", "leader": "analyst", "mandate": [],
        "members": [{"id": "analyst", "name": "Analyst", "roles": ["analyst_role"],
                     "description": "reads things"}],
        "teams": [],
    },
    "capabilities": [], "environments": [], "policies": [], "channels": [],
    "triggers": [], "knowledge": [], "skills": [], "plugins": [], "tools": [],
    "endpoints": [],
    "lifecycle": {
        "gates": [{"to_stage": "production", "requires": ["evaluations_passed"],
                   "min_pass_rate": 1.0}],
        "evaluations": [{"id": "must_cite", "description": "cite the source",
                         "given": "what was revenue?",
                         "expect": "contains: warehouse.revenue",
                         "applies_to": ["analyst"]}],
    },
}


#: A design mid-edit: the member has no id yet, so the spec does not load.
UNCOMPILABLE = {
    "metadata": SPEC["metadata"],
    "organization": {"id": "root", "name": "Root",
                     "members": [{"name": "half-typed agent"}], "teams": []},
}


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "review.db")))


@pytest.fixture()
def system(client) -> str:
    """One design, owned by alice, with a permission widened in version 2."""
    workspace = client.post("/api/designer/workspaces", json={"name": "ws"},
                            headers=ALICE).json()
    created = client.post("/api/designer/systems",
                          json={"workspace_id": workspace["id"], "name": "demo",
                                "spec": SPEC}, headers=ALICE).json()
    return created["id"]


def widen(client: TestClient, system_id: str) -> dict:
    """Save a second version that both widens access and changes prose."""
    current = client.get(f"/api/designer/systems/{system_id}",
                         headers=ALICE).json()["record"]
    spec = copy.deepcopy(SPEC)
    spec["roles"][0]["permissions"].append(
        {"action": "read", "resource_kind": "data_class", "resource": "customer_pii"})
    spec["organization"]["members"][0]["description"] = "reads more things"
    saved = client.put(f"/api/designer/systems/{system_id}",
                       json={"spec": spec, "base_version": current["version"]},
                       headers=ALICE).json()
    assert saved["status"] == "saved"
    return saved


def save_spec(client: TestClient, system_id: str, spec: dict) -> dict:
    current = client.get(f"/api/designer/systems/{system_id}",
                         headers=ALICE).json()["record"]
    return client.put(f"/api/designer/systems/{system_id}",
                      json={"spec": spec, "base_version": current["version"]},
                      headers=ALICE).json()


# -- the gate ---------------------------------------------------------------

def test_gate_returns_the_contracted_shape(client, system):
    response = client.get(f"/api/designer/systems/{system}/gate", headers=ALICE)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"agents"}
    entry = body["agents"]["analyst"]
    assert set(entry) == {"state", "reason", "required"}
    assert entry["required"] is True
    assert isinstance(entry["reason"], str) and entry["reason"]


def test_never_evaluated_is_not_evaluated_and_not_failed(client, system):
    entry = client.get(f"/api/designer/systems/{system}/gate",
                       headers=ALICE).json()["agents"]["analyst"]
    assert entry["state"] == "not_evaluated"
    assert entry["state"] != "failed"


def test_reading_the_gate_creates_no_evaluation_run(client, system):
    service = EvaluationService(client.app.state.platform.store)
    assert service.runs("review-demo") == []
    for _ in range(3):
        client.get(f"/api/designer/systems/{system}/gate", headers=ALICE)
    # The whole point of the gate: reporting on evidence may not manufacture it.
    assert service.runs("review-demo") == []


def test_a_real_failing_run_reports_failed(client, system):
    service = EvaluationService(
        client.app.state.platform.store,
        runner=lambda agent_id, prompt: CaseResponse(text="42"),
    )
    service.run(load_spec_text(yaml.safe_dump(SPEC)))
    entry = client.get(f"/api/designer/systems/{system}/gate",
                       headers=ALICE).json()["agents"]["analyst"]
    assert entry["state"] == "failed"


def test_gate_refuses_a_spec_that_does_not_compile(client, system):
    save_spec(client, system, UNCOMPILABLE)
    response = client.get(f"/api/designer/systems/{system}/gate", headers=ALICE)
    assert response.status_code == 422
    assert "does not compile yet" in response.json()["detail"]


def test_gate_404s_for_an_unknown_system(client):
    assert client.get("/api/designer/systems/sys_nope/gate",
                      headers=ALICE).status_code == 404


def test_gate_refuses_a_user_without_read_access(client, system):
    response = client.get(f"/api/designer/systems/{system}/gate", headers=MALLORY)
    assert response.status_code == 403


# -- the diff ---------------------------------------------------------------

def test_diff_returns_the_contracted_shape(client, system):
    widen(client, system)
    response = client.get(f"/api/designer/systems/{system}/diff", headers=ALICE)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"changes", "summary"}
    assert set(body["summary"]) == {"total", "security_findings", "worst_severity"}
    for change in body["changes"]:
        assert {"severity", "direction", "path", "summary", "rationale",
                "security_relevant"} <= set(change)


def test_diff_ranks_a_widened_permission_above_a_cosmetic_change(client, system):
    widen(client, system)
    body = client.get(f"/api/designer/systems/{system}/diff",
                      headers=ALICE).json()
    first = body["changes"][0]
    assert first["direction"] == "widened"
    assert first["security_relevant"] is True
    assert "customer_pii" in first["summary"]
    cosmetic = next(c for c in body["changes"] if c["path"].endswith(".description"))
    assert cosmetic["severity"] == "info"
    assert body["changes"].index(cosmetic) > 0
    assert body["summary"]["security_findings"] == 1
    assert body["summary"]["worst_severity"] == first["severity"]


def test_default_versions_are_the_previous_against_the_current(client, system):
    widen(client, system)
    default = client.get(f"/api/designer/systems/{system}/diff",
                         headers=ALICE).json()
    explicit = client.get(f"/api/designer/systems/{system}/diff?from=1&to=2",
                          headers=ALICE).json()
    assert default == explicit


def test_a_single_version_has_nothing_to_compare(client, system):
    body = client.get(f"/api/designer/systems/{system}/diff", headers=ALICE).json()
    assert body["changes"] == []
    assert body["summary"] == {"total": 0, "security_findings": 0,
                               "worst_severity": None}


def test_diff_refuses_an_incomparable_pair_with_its_own_message(client, system):
    widen(client, system)
    # A revision saved against another target is not another version of the
    # same thing; the refusal must say so rather than list phantom changes.
    repository = client.app.state.designer.repository
    record = repository.get_system(system)
    record.binding = {"target": "terraform"}
    repository.save_system(record, expected_version=record.version,
                           author="alice", message="re-targeted")
    response = client.get(f"/api/designer/systems/{system}/diff", headers=ALICE)
    assert response.status_code == 409
    assert "refusing to diff these IRs" in response.json()["detail"]
    assert "different targets" in response.json()["detail"]


def test_diff_refuses_a_spec_that_does_not_compile(client, system):
    save_spec(client, system, UNCOMPILABLE)
    response = client.get(f"/api/designer/systems/{system}/diff", headers=ALICE)
    assert response.status_code == 422
    assert "does not compile yet" in response.json()["detail"]


def test_diff_404s_for_an_unknown_system_and_an_unknown_version(client, system):
    widen(client, system)
    assert client.get("/api/designer/systems/sys_nope/diff",
                      headers=ALICE).status_code == 404
    response = client.get(f"/api/designer/systems/{system}/diff?from=1&to=99",
                          headers=ALICE)
    assert response.status_code == 404


def test_diff_refuses_a_user_without_read_access(client, system):
    widen(client, system)
    assert client.get(f"/api/designer/systems/{system}/diff",
                      headers=MALLORY).status_code == 403


# -- the palette can place what the canvas can draw ------------------------


def test_the_palette_offers_a_mission(client):
    """The canvas drew a mission capsule that nobody could place.

    A vocabulary the palette cannot produce is a drawing, not an editor — the
    visual pass surfaced this, and it is a one-entry gap rather than a design
    question (ADR-0039).
    """
    groups = client.get("/api/designer/palette").json()["groups"]
    kinds = {k["kind"]: k for g in groups for k in g.get("kinds", [])}
    assert "mission" in kinds


def test_a_mission_must_declare_when_it_ends(client):
    # A mission that never ends is a reorganization and belongs in the org
    # chart, so the form cannot let somebody omit the date.
    groups = client.get("/api/designer/palette").json()["groups"]
    kinds = {k["kind"]: k for g in groups for k in g.get("kinds", [])}
    fields = {f["name"]: f for f in kinds["mission"]["fields"]}
    assert fields["ends_on"]["required"] is True
    assert fields["objective"]["required"] is True
    assert "leader" in fields and "members" in fields
