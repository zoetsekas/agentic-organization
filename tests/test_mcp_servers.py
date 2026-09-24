"""The MCP servers (ADR-0115), driven by the SDK's in-memory client.

They act through the API as a named designer user, so what these check is
that the MCP surface adds reach to nobody: a viewer's assistant is refused a
save with the designer's own 403, HTTP without a bearer token is refused
before MCP sees it, and the runtime server writes nothing unless told to.
"""
from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from orgagents.api import create_app  # noqa: E402
from orgagents.mcp_server._common import (  # noqa: E402
    Backend,
    ConfigurationError,
    ServerConfig,
    http_app,
    load_token_map,
)
from orgagents.mcp_server.designer import build_designer_server  # noqa: E402
from orgagents.mcp_server.runtime import build_runtime_server  # noqa: E402

TOKEN = "t" * 32


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A designer with alice (owner) and victor (viewer) on one design."""
    monkeypatch.setenv("ORGAGENTS_DESIGNER_PATH", str(tmp_path / "designer"))
    monkeypatch.delenv("ORGAGENTS_DESIGNER_AUTH", raising=False)
    app = create_app(str(tmp_path / "mcp.db"))
    http = TestClient(app)
    alice = {"X-User": "alice"}
    ws = http.post("/api/v1/designer/workspaces", json={"name": "W"},
                   headers=alice).json()
    http.post(f"/api/v1/designer/workspaces/{ws['id']}/members",
              json={"user_id": "victor", "role": "viewer"}, headers=alice)
    design = http.post("/api/v1/designer/systems",
                       json={"workspace_id": ws["id"], "name": "demo"},
                       headers=alice).json()
    return app, design["id"]


def designer_for(app, user: str):
    config = ServerConfig(transport="stdio", user=user)
    return build_designer_server(config, Backend(config, app=app))


def run(coro):
    return asyncio.run(coro)


async def _call(server, tool: str, **arguments):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(tool, arguments)


def _payload(result):
    if result.structuredContent is not None:
        return result.structuredContent.get("result", result.structuredContent)
    return json.loads(result.content[0].text)


def test_designer_lists_its_tools_resources_and_prompts(world):
    app, _ = world

    async def go():
        async with create_connected_server_and_client_session(
                designer_for(app, "alice")) as s:
            tools = {t.name for t in (await s.list_tools()).tools}
            templates = {t.uriTemplate for t in
                         (await s.list_resource_templates()).resourceTemplates}
            resources = {str(r.uri) for r in (await s.list_resources()).resources}
            prompts = {p.name for p in (await s.list_prompts()).prompts}
            return tools, templates, resources, prompts

    tools, templates, resources, prompts = run(go())
    assert {"list_workspaces", "list_designs", "get_design", "validate_design",
            "explain_issue", "list_profile", "describe_stereotype",
            "apply_model_operation", "save_design", "diff_versions",
            "compile_preview", "request_publish", "whoami"} <= tools
    # Administrative acts are not offered to an assistant at all.
    assert not tools & {"delete_design", "add_member", "break_lock", "restore"}
    assert {"orgagents://issues", "orgagents://profile"} <= resources
    assert "orgagents://designs/{design_id}" in templates
    assert {"review_authority", "add_agent_to_team"} <= prompts


def test_validate_returns_findings_with_issue_ids(world):
    app, design_id = world
    result = run(_call(designer_for(app, "alice"), "validate_design",
                       design_id=design_id))
    assert not result.isError
    body = _payload(result)
    assert body["ok"] is False
    assert any(f["issue_id"] == "OA-1201" for f in body["findings"])

    explained = _payload(run(_call(designer_for(app, "alice"), "explain_issue",
                                   code="OA-1201")))
    assert explained["code"] == "team_without_leader" and explained["fix"]


def test_model_operation_saves_with_optimistic_concurrency(world):
    app, design_id = world
    server = designer_for(app, "alice")
    created = _payload(run(_call(server, "apply_model_operation",
                                 design_id=design_id,
                                 operation={"op": "create", "kind": "agent",
                                            "id": "lead", "owner": "root",
                                            "attrs": {"name": "Lead"}},
                                 save=True, expected_version=1)))
    assert created["accepted"] and created["saved"]
    assert created["save"]["version"] == 2

    # A relationship the profile does not have is the model's refusal, said
    # in the model's words, and nothing is saved.
    refused = run(_call(server, "apply_model_operation", design_id=design_id,
                        operation={"op": "link",
                                   "source": {"kind": "agent", "id": "nobody"},
                                   "target": {"kind": "team", "id": "root"},
                                   "relationship": "members"}, save=True))
    assert refused.isError and "refused (422)" in refused.content[0].text
    stale = _payload(run(_call(server, "save_design", design_id=design_id,
                               spec={"metadata": {"name": "demo"},
                                     "organization": {"id": "root", "name": "x"}},
                               expected_version=1)))
    # Based on version 1 while 2 is stored: merged or refused, never a blind
    # overwrite (ADR-0033).
    assert stale["status"] in ("merged", "conflict", "stale")


def test_a_viewer_is_refused_a_save_by_the_designers_rbac(world):
    app, design_id = world
    result = run(_call(designer_for(app, "victor"), "save_design",
                       design_id=design_id, spec="metadata: {name: x}",
                       expected_version=1))
    assert result.isError
    assert "permission denied (403)" in result.content[0].text
    # And a stranger cannot read it.
    hidden = run(_call(designer_for(app, "mallory"), "get_design",
                       design_id=design_id))
    assert hidden.isError and "403" in hidden.content[0].text


def test_design_resource_is_read_as_the_servers_user(world):
    app, design_id = world

    async def read(user):
        async with create_connected_server_and_client_session(
                designer_for(app, user)) as s:
            return await s.read_resource(f"orgagents://designs/{design_id}")

    assert "organization" in run(read("alice")).contents[0].text
    with pytest.raises(Exception):
        run(read("mallory"))


def test_runtime_is_read_only_unless_mutations_are_enabled(world):
    app, _ = world

    async def tools(allow):
        config = ServerConfig(transport="stdio", user="alice",
                              allow_mutations=allow)
        server = build_runtime_server(config, Backend(config, app=app))
        async with create_connected_server_and_client_session(server) as s:
            names = {t.name for t in (await s.list_tools()).tools}
            sessions = await s.call_tool("list_sessions", {})
            return names, sessions

    read_only, sessions = run(tools(False))
    assert {"list_sessions", "session_trace", "ops_metrics", "ops_alerts",
            "search_catalogs"} <= read_only
    assert not read_only & {"run_agent", "resume_session", "ack_alert"}
    assert not sessions.isError
    enabled, _ = run(tools(True))
    assert {"run_agent", "resume_session", "ack_alert"} <= enabled


def test_runtime_tools_inherit_the_runtimes_enforcement(world):
    """ADR-0116 through ADR-0115: the runtime server has no checks of its own,
    so a viewer's assistant reads but cannot run, even with mutations enabled,
    and a stranger's cannot read another workspace's sessions."""
    app, design_id = world
    http = TestClient(app)
    agent = http.post("/api/v1/agents", headers={"X-User": "alice"}, json={
        "name": "clerk", "system_id": design_id,
        "harness": {"runtime": "echo", "system_prompt": "File things."}}).json()
    session = http.post(f"/api/v1/agents/{agent['id']}/run",
                        json={"prompt": "file"}, headers={"X-User": "alice"}).json()

    def runtime_for(user):
        config = ServerConfig(transport="stdio", user=user, allow_mutations=True)
        return build_runtime_server(config, Backend(config, app=app))

    listed = run(_call(runtime_for("victor"), "list_sessions"))
    assert not listed.isError
    assert session["session_id"] in json.dumps(_payload(listed))
    refused = run(_call(runtime_for("victor"), "run_agent",
                        agent_id=agent["id"], prompt="again"))
    assert refused.isError and "403" in refused.content[0].text
    ran = run(_call(runtime_for("alice"), "run_agent",
                    agent_id=agent["id"], prompt="again"))
    assert not ran.isError
    hidden = run(_call(runtime_for("mallory"), "get_session",
                       session_id=session["session_id"]))
    assert hidden.isError and "403" in hidden.content[0].text


def test_http_transport_requires_a_known_bearer_token(world):
    app, _ = world
    config = ServerConfig(transport="http", http_tokens={TOKEN: "alice"})
    server = build_designer_server(config, Backend(config, app=app))
    with TestClient(http_app(server, config),
                    base_url="http://127.0.0.1:8765") as client:
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}}
        accept = {"Accept": "application/json, text/event-stream"}
        assert client.post("/mcp", json=body, headers=accept).status_code == 401
        wrong = {**accept, "Authorization": "Bearer " + "x" * 32}
        assert client.post("/mcp", json=body, headers=wrong).status_code == 401
        good = {**accept, "Authorization": f"Bearer {TOKEN}"}
        assert client.post("/mcp", json=body, headers=good).status_code == 200


def test_servers_refuse_to_start_without_an_identity():
    with pytest.raises(ConfigurationError):
        ServerConfig(transport="stdio").check()
    with pytest.raises(ConfigurationError):
        ServerConfig(transport="http").check()
    with pytest.raises(ConfigurationError):   # would be token passthrough
        ServerConfig(transport="http", auth_mode="oidc",
                     http_tokens={TOKEN: "a"}).check()
    with pytest.raises(ConfigurationError):
        ServerConfig(transport="stdio", user="a", auth_mode="trusted_proxy").check()
    with pytest.raises(ConfigurationError):
        load_token_map("short=alice")
    assert load_token_map(f"{TOKEN}=alice") == {TOKEN: "alice"}
