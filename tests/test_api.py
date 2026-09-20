import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.seed import seed


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = str(tmp_path / "api.db")
    seed(db)
    return TestClient(create_app(db))


def test_org_and_agent_endpoints(client):
    assert client.get("/healthz").json()["status"] == "ok"
    tree = client.get("/api/org/tree").json()
    assert tree[0]["name"] == "ceo-agent"
    harness = client.get("/api/agents/agt_fin_analyst/harness").json()
    assert "db_warehouse__query" in [t["name"] for t in harness["tools"]]
    assert harness["sandbox"]["resources"]["cpu"] == "2"


def test_designer_component_palette(client):
    c = client.get("/api/components").json()
    assert len(c["sandbox_templates"]) >= 8
    assert {"storage", "compute", "communication", "operations", "security"} <= set(
        c["infrastructure"]
    )
    assert {r["id"] for r in c["runtimes"]} >= {"langchain_deepagents", "openai_agents_sdk"}


def test_create_agent_through_designer(client):
    payload = {
        "name": "procurement-agent",
        "title": "Procurement Analyst",
        "kind": "individual",
        "manager_agent_id": "agt_cfo",
        "org_unit_id": "org_finance",
        "human": {"user_id": "u_p", "display_name": "Ana Silva", "email": "ana@acme.example"},
        "harness": {"runtime": "echo", "system_prompt": "You handle purchase orders."},
        "sandbox": {"template_id": "sbx_document_processing"},
    }
    created = client.post("/api/agents", json=payload).json()
    assert created["manager_agent_id"] == "agt_cfo"
    assert "finance" in created["groups"]  # inherited from the org unit

    run = client.post(f"/api/agents/{created['id']}/run",
                      json={"prompt": "Draft a PO."}).json()
    assert run["state"] == "completed"
    assert client.get(run["session_url"].replace("http://localhost:8000", ""),
                      follow_redirects=False).status_code in (200, 307)


def test_session_trace_endpoint(client):
    run = client.post("/api/agents/agt_cfo/run", json={"prompt": "Close the books."}).json()
    trace = client.get(f"/api/sessions/{run['session_id']}/trace").json()
    assert trace["session"]["agent_id"] == "agt_cfo"
    assert trace["url"].endswith(run["session_id"])


def test_marketplace_endpoints(client):
    entries = client.get("/api/catalog?kind=sandbox_template&sort=name").json()
    assert len(entries) >= 8
    entry = entries[0]
    installed = client.post(f"/api/catalog/{entry['id']}/install",
                            json={"agent_id": "agt_cro"}).json()
    assert installed["installed"] == "sandbox_template"
    assert client.get("/api/catalog/stats").json()["installs"] >= 1


def test_ops_endpoints(client):
    client.post("/api/agents/agt_sre/run", json={"prompt": "status"})
    m = client.get("/api/ops/metrics").json()
    assert m["sessions"]["total"] >= 1
    assert isinstance(client.get("/api/ops/alerts").json(), list)
    assert client.get("/api/ops/health/agt_sre").json()["sessions"] >= 1
