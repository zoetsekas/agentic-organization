"""The running platform as an MCP server: `orgagents mcp runtime` (ADR-0115).

Read-mostly by construction. Sessions, their events and traces, metrics,
alerts, agent health and both catalogs are readable; nothing is written
unless the operator started the server with `--allow-mutations`, and then
only the three acts the UI's operators already have — run an agent, resume a
paused session, acknowledge an alert — each through the same `/api/v1` route
and the same platform checks the UI uses. The flag is off by default because
"an assistant can start agents" should be a decision someone made, not a
consequence of connecting one.

What the user may see and do is the runtime's own decision (ADR-0116): a
viewer's assistant lists its workspace's sessions and is refused `run_agent`
with the platform's 403, even with mutations enabled; there are no checks here
to drift from the UI's.
"""
from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import Context, FastMCP

from ..client import RuntimeClient
from ._common import Backend, ServerConfig, call, caller

INSTRUCTIONS = """\
Read-mostly tools over a running Agentic Designer platform: sessions, their
events and traces, operational metrics and alerts, agent health, and the
platform and marketplace catalogs. Mutating tools (run_agent, resume_session,
ack_alert) exist only when the operator enabled them; if they are absent, do
not look for a way around it.
"""


def _trim(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict]:
    return [{k: r.get(k) for k in keys if k in r} for r in rows]


def build_runtime_server(config: ServerConfig,
                         backend: Optional[Backend] = None) -> FastMCP:
    backend = backend or Backend(config)
    server = FastMCP("orgagents-runtime", instructions=INSTRUCTIONS,
                     streamable_http_path="/mcp")

    def rc(ctx: Context) -> RuntimeClient:
        return backend.client(RuntimeClient, caller(ctx, config))

    @server.tool()
    async def list_agents(ctx: Context, org_unit_id: Optional[str] = None) -> list[dict]:
        """Agents on the platform, optionally within one org unit."""
        client = rc(ctx)
        rows = await call(lambda: client.list_agents(org_unit_id))
        return _trim(rows, ("id", "name", "org_unit_id", "role", "runtime",
                            "status", "description"))

    @server.tool()
    async def list_sessions(ctx: Context, agent_id: Optional[str] = None,
                            limit: int = 25) -> list[dict]:
        """Recent sessions, newest first, optionally for one agent."""
        client = rc(ctx)
        return await call(lambda: client.list_sessions(agent_id, min(limit, 200)))

    @server.tool()
    async def get_session(ctx: Context, session_id: str) -> dict:
        """One session: state, agent, who started it, its URL."""
        client = rc(ctx)
        return await call(lambda: client.get_session(session_id))

    @server.tool()
    async def session_events(ctx: Context, session_id: str,
                             limit: int = 100) -> list[dict]:
        """A session's events in order: messages, tool calls, delegations."""
        client = rc(ctx)
        return await call(lambda: client.session_events(session_id, min(limit, 1000)))

    @server.tool()
    async def session_trace(ctx: Context, session_id: str) -> dict:
        """A session's trace: spans, timings and the delegation tree."""
        client = rc(ctx)
        return await call(lambda: client.session_trace(session_id))

    @server.tool()
    async def ops_metrics(ctx: Context) -> dict:
        """Operational metrics across the platform."""
        client = rc(ctx)
        return await call(client.metrics)

    @server.tool()
    async def ops_alerts(ctx: Context, include_acknowledged: bool = False) -> list[dict]:
        """Open alerts (and acknowledged ones if asked)."""
        client = rc(ctx)
        return await call(lambda: client.alerts(include_acknowledged))

    @server.tool()
    async def agent_health(ctx: Context, agent_id: str) -> dict:
        """One agent's health: error rate, latency, recent failures."""
        client = rc(ctx)
        return await call(lambda: client.agent_health(agent_id))

    @server.tool()
    async def search_catalogs(ctx: Context, query: str = "",
                              kind: Optional[str] = None,
                              environment: str = "development") -> list[dict]:
        """The governed platform catalog (models, runtimes, sandboxes, ...)
        entries matching a query, as visible in an environment."""
        client = rc(ctx)
        rows = await call(lambda: client.search_catalogs(query, kind, environment))
        return _trim(rows, ("id", "kind", "name", "summary", "status", "version"))

    @server.tool()
    async def catalog_entry(ctx: Context, entry_id: str) -> dict:
        """One platform catalog entry in full."""
        client = rc(ctx)
        return await call(lambda: client.catalog_entry(entry_id))

    @server.tool()
    async def search_marketplace(ctx: Context, query: str = "",
                                 kind: Optional[str] = None,
                                 limit: int = 25) -> list[dict]:
        """Marketplace skills, plugins and workflows matching a query."""
        client = rc(ctx)
        rows = await call(lambda: client.search_marketplace(query, kind, min(limit, 100)))
        return _trim(rows, ("id", "kind", "name", "summary", "installs",
                            "rating", "url"))

    if config.allow_mutations:
        @server.tool()
        async def run_agent(ctx: Context, agent_id: str, prompt: str,
                            session_id: Optional[str] = None) -> dict:
            """Run an agent on a prompt (enabled by --allow-mutations). The
            session records this server's user as who started it."""
            user = caller(ctx, config)
            client = rc(ctx)
            return await call(lambda: client.run_agent(
                agent_id, prompt, session_id=session_id, created_by=f"mcp:{user}"))

        @server.tool()
        async def resume_session(ctx: Context, session_id: str, response: str) -> dict:
            """Answer a session paused for a human (enabled by --allow-mutations)."""
            user = caller(ctx, config)
            client = rc(ctx)
            return await call(lambda: client.resume_session(
                session_id, response, actor=f"mcp:{user}"))

        @server.tool()
        async def ack_alert(ctx: Context, alert_id: str) -> dict:
            """Acknowledge an alert (enabled by --allow-mutations)."""
            client = rc(ctx)
            return await call(lambda: client.ack_alert(alert_id))

    return server
