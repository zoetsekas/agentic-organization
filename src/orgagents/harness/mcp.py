"""MCP mounting for agent harnesses.

The registry resolves an agent's declared `MCPServerRef`s into callable tool
proxies. Two backends exist:

* `stdio`/`http` refs are handed to a real MCP client when one is installed
  (``mcp`` package); this is the production path.
* An in-process backend, used by tests, the designer preview and the bundled
  relational server, registers plain Python callables under a server name.

Either way the agent only ever sees `ToolBinding`s, so swapping backends does
not change the agent definition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..models import MCPServerRef, ToolBinding

ToolFn = Callable[..., Any]


@dataclass
class InProcessServer:
    """A locally implemented MCP server."""

    name: str
    tools: dict[str, ToolFn] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)

    def tool(self, name: str, description: str = "") -> Callable[[ToolFn], ToolFn]:
        def deco(fn: ToolFn) -> ToolFn:
            self.tools[name] = fn
            self.descriptions[name] = description or (fn.__doc__ or "").strip()
            return fn

        return deco


class MCPToolProxy:
    """A single MCP tool, bound to the agent that may call it."""

    def __init__(self, server: str, name: str, fn: ToolFn, read_only: bool) -> None:
        self.server = server
        self.name = name
        self.fn = fn
        self.read_only = read_only
        self.input_schema: Optional[dict[str, Any]] = None
        self.description = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.server}__{self.name}"

    def __call__(self, **kwargs: Any) -> Any:
        return self.fn(**kwargs)


class MCPRegistry:
    """Resolves harness MCP declarations into callable proxies."""

    def __init__(self) -> None:
        self._servers: dict[str, InProcessServer] = {}
        self._remote: dict[str, Any] = {}

    # -- registration ------------------------------------------------------

    def register(self, server: InProcessServer) -> InProcessServer:
        self._servers[server.name] = server
        return server

    def register_remote(self, name: str, client: Any) -> None:
        """Attach an already-connected MCP client session."""
        self._remote[name] = client

    def server(self, name: str) -> Optional[InProcessServer]:
        return self._servers.get(name)

    # -- resolution --------------------------------------------------------

    def resolve(self, ref: MCPServerRef) -> list[MCPToolProxy]:
        server = self._servers.get(ref.name)
        if server is None:
            remote = self._remote.get(ref.name)
            if remote is None:
                raise KeyError(
                    f"MCP server '{ref.name}' is not mounted; register it first"
                )
            return self._resolve_remote(ref, remote)
        names = ref.allowed_tools or list(server.tools)
        proxies = []
        for n in names:
            if n not in server.tools:
                raise KeyError(f"MCP server '{ref.name}' exposes no tool '{n}'")
            proxies.append(MCPToolProxy(ref.name, n, server.tools[n], ref.read_only))
        return proxies

    def _resolve_remote(self, ref: MCPServerRef, client: Any) -> list[MCPToolProxy]:
        listed = client.list_tools()
        tools = getattr(listed, "tools", listed)
        allowed = set(ref.allowed_tools)
        proxies = []
        for t in tools:
            name = getattr(t, "name", None) or t["name"]
            if allowed and name not in allowed:
                continue

            def make(tool_name: str) -> ToolFn:
                def call(**kwargs: Any) -> Any:
                    return client.call_tool(tool_name, kwargs)

                return call

            proxy = MCPToolProxy(ref.name, name, make(name), ref.read_only)
            # What the server says the tool takes, so a framework shows the
            # model real parameters rather than `**kwargs`.
            schema = getattr(t, "inputSchema", None) or (
                t.get("inputSchema") if isinstance(t, dict) else None)
            proxy.input_schema = schema
            proxy.description = getattr(t, "description", None) or (
                t.get("description", "") if isinstance(t, dict) else "")
            proxies.append(proxy)
        return proxies

    def mount_http(self, ref: MCPServerRef, *, token: Optional[str] = None,
                   agent_id: str = "") -> "HttpMCPClient":
        """Mount a streamable-HTTP server with no third-party client (ADR-0109).

        Several capabilities may land on one server under different
        credentials — that is how a separation survives a shared system
        (ADR-0071) — so one client per server carries a token *per tool*, and
        each call presents the credential of the capability it belongs to.
        """
        if not ref.url:
            raise ValueError(f"http server '{ref.name}' declares no url")
        client = self._remote.get(ref.name)
        if not isinstance(client, HttpMCPClient):
            client = HttpMCPClient(ref.url, agent_id=agent_id)
            self.register_remote(ref.name, client)
        client.grant(ref.allowed_tools, token)
        return client

    def mount_langchain(self, ref: MCPServerRef) -> None:
        """Mount a server over `langchain-mcp-adapters`' transport.

        Their transport, our policy (ADR-0067 rule 5). The adapter package
        handles stdio/SSE/streamable-HTTP better than we would, and it returns
        tools ready to hand straight to a LangChain agent — which is exactly
        what we must not do, because a tool handed directly to the framework
        never passes the allowlist, the `read_only` flag, or the permission,
        mandate and approval checks in `HarnessBuilder`.

        So the tools come back through `register_remote` and the ordinary
        resolution path, and nothing about the agent definition changes.
        """
        self.register_remote(ref.name, LangChainMCPClient(ref))

    def bindings(self, ref: MCPServerRef) -> list[ToolBinding]:
        return [
            ToolBinding(
                name=p.qualified_name,
                description=(
                    self._servers[ref.name].descriptions.get(p.name, "")
                    if ref.name in self._servers
                    else ""
                ),
                source="mcp",
                ref=ref.name,
                requires_approval=not p.read_only,
            )
            for p in self.resolve(ref)
        ]


# --------------------------------------------------------------------------
# langchain-mcp-adapters as a transport backend
# --------------------------------------------------------------------------


def connection_for(ref: MCPServerRef) -> dict[str, Any]:
    """Translate an `MCPServerRef` into an adapter connection config.

    Kept separate from the client so the mapping is testable without the
    package installed — the translation is ours and is where a mistake would
    silently mount the wrong server.
    """
    if ref.transport == "stdio":
        if not ref.command:
            raise ValueError(f"stdio server '{ref.name}' declares no command")
        return {"transport": "stdio", "command": ref.command, "args": list(ref.args)}
    if not ref.url:
        raise ValueError(f"{ref.transport} server '{ref.name}' declares no url")
    # `http` in our model is streamable HTTP, which is the adapter's own
    # default for a URL-addressed server; `sse` stays distinct because the
    # wire protocols differ.
    transport = "sse" if ref.transport == "sse" else "streamable_http"
    return {"transport": transport, "url": ref.url}


class LangChainMCPClient:
    """Adapts `MultiServerMCPClient` to the small client shape we resolve.

    `get_tools` is async and everything above here is synchronous, so the
    coroutine is driven on a private loop. A caller already inside a running
    loop gets a clear error rather than a deadlock.
    """

    def __init__(self, ref: MCPServerRef) -> None:
        self.ref = ref
        self._tools: Optional[dict[str, Any]] = None

    def _load(self) -> dict[str, Any]:
        if self._tools is not None:
            return self._tools
        import asyncio

        from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore

        client = MultiServerMCPClient({self.ref.name: connection_for(self.ref)})
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "LangChainMCPClient cannot load tools from inside a running "
                "event loop; mount the server before the loop starts"
            )
        tools = asyncio.run(client.get_tools(server_name=self.ref.name))
        self._tools = {t.name: t for t in tools}
        return self._tools

    def list_tools(self) -> list[Any]:
        return list(self._load().values())

    def call_tool(self, name: str, kwargs: dict[str, Any]) -> Any:
        tool = self._load().get(name)
        if tool is None:
            raise KeyError(f"MCP server '{self.ref.name}' exposes no tool '{name}'")
        return tool.invoke(kwargs)


# --------------------------------------------------------------------------
# A dependency-free streamable-HTTP client
# --------------------------------------------------------------------------


class MCPCallError(RuntimeError):
    """The server answered a JSON-RPC error rather than a result."""


class HttpMCPClient:
    """MCP over streamable HTTP, speaking JSON-RPC with the standard library.

    The production path is `langchain-mcp-adapters`; this exists so a runtime
    with no LangChain installed, and the workstation stack, can still reach an
    HTTP MCP server (ADR-0109). It implements the part of the transport a tool
    call needs — `initialize`, `tools/list`, `tools/call`, the session header,
    and a response sent either as JSON or as a single SSE event — and nothing
    else: no server-initiated requests, no resumption.

    Every request carries the calling agent's id, and the call's credential if
    its capability has one, so the system on the other end can tell two hands
    apart the way the phase gate assumed it could.
    """

    def __init__(self, url: str, *, agent_id: str = "", timeout: float = 30.0) -> None:
        self.url = url
        self.agent_id = agent_id
        self.timeout = timeout
        self._tokens: dict[str, str] = {}
        self._default_token: Optional[str] = None
        self._session: Optional[str] = None
        self._ids = 0

    def grant(self, tools: list[str], token: Optional[str]) -> None:
        if not token:
            return
        if not tools:
            self._default_token = token
        for tool in tools:
            self._tokens[tool] = token

    def _post(self, payload: dict[str, Any], token: Optional[str] = None) -> Any:
        import json
        import urllib.request

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        if self.agent_id:
            headers["X-Orgagents-Agent"] = self.agent_id
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers,
            method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            self._session = response.headers.get("Mcp-Session-Id") or self._session
            body = response.read().decode()
            kind = response.headers.get("Content-Type", "")
        if "id" not in payload or not body.strip():
            return None
        if kind.startswith("text/event-stream"):
            data = [line[5:].strip() for line in body.splitlines()
                    if line.startswith("data:")]
            body = data[-1] if data else "{}"
        message = json.loads(body)
        if "error" in message:
            raise MCPCallError(message["error"].get("message", str(message["error"])))
        return message.get("result")

    def _rpc(self, method: str, params: dict[str, Any],
             token: Optional[str] = None) -> Any:
        if self._session is None and method != "initialize":
            self._initialize()
        self._ids += 1
        return self._post({"jsonrpc": "2.0", "id": self._ids, "method": method,
                           "params": params}, token)

    def _initialize(self) -> None:
        self._ids += 1
        self._post({"jsonrpc": "2.0", "id": self._ids, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "orgagents",
                                              "version": "0.1.0"}}})
        self._session = self._session or ""
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict[str, Any]]:
        return list((self._rpc("tools/list", {}) or {}).get("tools", []))

    def call_tool(self, name: str, kwargs: dict[str, Any]) -> Any:
        import json

        token = self._tokens.get(name, self._default_token)
        result = self._rpc("tools/call", {"name": name, "arguments": kwargs}, token) or {}
        text = "\n".join(c.get("text", "") for c in result.get("content", [])
                         if c.get("type") == "text")
        data = result.get("structuredContent")
        if data is None:
            try:
                data = json.loads(text) if text else {}
            except ValueError:
                data = {"text": text}
        if result.get("isError"):
            if isinstance(data, dict) and "error" in data:
                return {**data, "ok": False}
            return {"ok": False, "error": text or "the server refused the call"}
        return data
