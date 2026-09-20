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

            proxies.append(MCPToolProxy(ref.name, name, make(name), ref.read_only))
        return proxies

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
