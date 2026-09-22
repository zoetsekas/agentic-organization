"""Turn the binding's servers catalog into real client wiring, not stubs.

The platform targets used to emit every tool as `raise NotImplementedError`,
because a tool's *callable* is the host's, not the design's. But that is only
true for a tool behind no declared backend. The binding's servers catalog
(ADR-0085) already says, per capability, *what kind* of system backs it and
*how it is reached* — an MCP endpoint's URL, or a database's engine and DSN
reference. That is enough to emit a working client, so a bound capability need
not be a stub.

This module is the shared, framework-neutral half: it resolves a capability to
a **backend plan** (mcp / database / none) from `ir.binding`, and it emits the
`_backends.py` runtime shim both the LangGraph and ADK targets drop beside their
agents. It reads `ir.binding` by attribute only and imports nothing from
`orgagents.spec` — the target-plugin rule (ADR-0005) holds.

What is still the host's, and honestly so:
- **credentials** — emitted as an env-var *name*, never a value (ADR-0015);
- **an MCP tool's exact remote name** — we call the tool named for the
  capability (or its catalog server), which is the convention; a mismatch is a
  one-line fix and a clear `LookupError`, not silence;
- **a database query** — the design says a capability may read a class of data
  under `allowed_operations`/`max_rows`, not the SQL; so the emitted SQL tool
  takes the query as an argument and *enforces the bound* (operation allowlist,
  row cap), which is the part the design actually carries.
"""
from __future__ import annotations

import json
from typing import Any, Optional


def _mod(identifier: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in identifier)
    return safe if safe and not safe[0].isdigit() else f"a_{safe}"


def _pystr(value: Optional[str]) -> str:
    return json.dumps(value or "")


#: JSON-schema-ish type words → a Python annotation.
_PYTYPE = {
    "string": "str", "integer": "int", "number": "float", "boolean": "bool",
    "array": "list", "object": "dict", "null": "None",
}


def tool_surface(agent: Any) -> list[dict[str, Any]]:
    """The tools an agent exposes: its declared tools (which carry schemas) and
    any capability not already named by one.

    Shared by the platform targets so 'what tools does this agent have' has one
    answer. A tool that wraps a capability inherits that capability's read
    bound, so a database-backed one can enforce it.
    """
    out: list[dict[str, Any]] = []
    gated = set(agent.requires_approval_for)
    by_cap = {c.id: c for c in agent.capabilities}
    for tool in agent.tools:
        cap = by_cap.get(tool.wraps) if tool.wraps_kind == "capability" else None
        out.append(_with_constraints({
            "name": tool.id,
            "description": tool.description,
            "gated": tool.requires_approval or tool.id in gated,
            "input_schema": dict(tool.input_schema or {}),
            "output_schema": dict(tool.output_schema or {}),
            "wraps_kind": tool.wraps_kind,
            "wraps": tool.wraps,
        }, cap))
    for capability in agent.capabilities:
        if any(t["name"] == capability.id for t in out):
            continue
        out.append(_with_constraints({
            "name": capability.id,
            "description": capability.description,
            "gated": capability.id in gated
            or bool(capability.constraints.requires_approval),
            "input_schema": {},
            "output_schema": {},
            "wraps_kind": "capability",
            "wraps": capability.id,
        }, capability))
    return out


def _with_constraints(tool: dict[str, Any], cap: Any) -> dict[str, Any]:
    if cap is not None:
        tool["allowed_operations"] = list(cap.constraints.allowed_operations)
        tool["max_rows"] = cap.constraints.max_rows or 0
    else:
        tool["allowed_operations"] = []
        tool["max_rows"] = 0
    return tool


def _params(input_schema: dict[str, Any]) -> str:
    if not input_schema:
        return "**kwargs"
    parts = []
    for pname, ptype in input_schema.items():
        parts.append(f"{_mod(pname)}: {_PYTYPE.get(str(ptype), 'Any')}")
    return ", ".join(parts)


def emit_stub_def(tool: dict[str, Any]) -> list[str]:
    """A typed, documented function stub for a tool to be implemented.

    A tool grants nothing new — it names and narrows something the agent
    already holds (ADR-0029) — so its body is application logic, not something
    the design carries. Rather than a bare `**kwargs` that raises, this emits a
    real signature from the tool's input schema, a docstring naming what it
    wraps, and a `TODO` in the body, so it is a scaffold a developer fills in.
    """
    name = _mod(tool["name"])
    inp = tool.get("input_schema") or {}
    outp = tool.get("output_schema") or {}
    ret = "dict" if outp else "Any"
    lines = [f"def {name}({_params(inp)}) -> {ret}:"]
    doc = [f'    """{tool["description"] or tool["name"]}', ""]
    wraps_kind, wraps = tool.get("wraps_kind"), tool.get("wraps")
    if wraps and wraps != tool["name"]:
        doc.append(f"    Wraps {wraps_kind} '{wraps}' (ADR-0029): implement by "
                   f"calling it and narrowing to this tool's contract.")
        doc.append("")
    if inp:
        doc.append("    Args:")
        for pname, ptype in inp.items():
            doc.append(f"        {pname} ({_PYTYPE.get(str(ptype), 'Any')})")
    if outp:
        doc.append("    Returns:")
        fields = ", ".join(f"{k}: {v}" for k, v in outp.items())
        doc.append(f"        dict with {fields}")
    if tool.get("gated"):
        doc.append("")
        doc.append("    Approval-gated: the harness stops this for a human "
                   "before it runs.")
    doc.append('    """')
    lines += doc
    lines.append(
        f'    raise NotImplementedError("TODO: implement {tool["name"]}")')
    return lines


def stub_module(ir: Any) -> str:
    """A shared module of typed stubs — one per distinct tool that has no bound
    backend, across the whole system — for agents to import and implement.

    One definition per tool, imported by each agent that has it, so a tool
    implemented once is used wherever the design gives it (rather than a
    diverging copy per agent module).
    """
    seen: dict[str, dict[str, Any]] = {}
    for agent in ir.agents:
        _, stubbed = wired_and_stubbed(ir, tool_surface(agent))
        for tool in stubbed:
            seen.setdefault(tool["name"], tool)
    lines = [
        '"""Tool stubs to implement (generated).',
        "",
        "Each function is a tool the design gives one or more agents but whose",
        "code is the host's — a wrapper that narrows an existing grant, or a",
        "capability with no server bound. Fill in the body; the signature and",
        "docstring come from the design. Every agent that has the tool imports",
        "it from here, so implement it once.",
        '"""',
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "",
    ]
    for name in sorted(seen):
        lines += emit_stub_def(seen[name])
        lines += ["", ""]
    return "\n".join(lines).rstrip() + "\n"


def stubbed_names(ir: Any) -> set[str]:
    """The module-safe names of every stubbed tool, system-wide."""
    out: set[str] = set()
    for agent in ir.agents:
        _, stubbed = wired_and_stubbed(ir, tool_surface(agent))
        out.update(t["name"] for t in stubbed)
    return out


def emit_wired_def(tool: dict[str, Any]) -> list[str]:
    """Emit a real tool function that calls the declared backend.

    Framework-neutral plain Python; the LangGraph and ADK targets each wrap the
    resulting callable in their own tool type.
    """
    backend = tool["backend"]
    name = _mod(tool["name"])
    doc = tool["description"] or tool["name"]
    if backend["kind"] == "mcp":
        secret = _pystr(backend["secret_env"]) if backend["secret_env"] else "None"
        return [
            f"def {name}(**kwargs):",
            f'    """{doc}"""',
            "    return mcp_call(",
            f"        {_pystr(backend['server_id'])}, {_pystr(backend['url'])},",
            f"        {_pystr(backend['transport'])}, "
            f"{_pystr(backend['tool_name'])},",
            f"        secret_env={secret},",
            "        **kwargs,",
            "    )",
        ]
    ops = json.dumps([o.lower() for o in tool.get("allowed_operations", [])])
    max_rows = int(tool.get("max_rows") or 0)
    return [
        f"def {name}(query, params=None):",
        f'    """{doc}',
        "",
        f"    Bounded to operations {ops} and {max_rows or 'no'} row cap; the "
        "query is yours, the bound is the design's.",
        '    """',
        f"    return bounded_sql({_pystr(backend['dsn_env'])}, {ops}, "
        f"{max_rows}, query, params)",
    ]


def shim_imports(needs: set[str]) -> list[str]:
    """The names to import from `_backends` for the given backend kinds."""
    out = []
    if "mcp" in needs:
        out.append("mcp_call")
    if "database" in needs:
        out.append("bounded_sql")
    return sorted(out)


def _transport_for(value: str) -> str:
    """Map the binding's transport onto what langchain-mcp-adapters expects."""
    return {"http": "streamable_http", "sse": "sse", "stdio": "stdio"}.get(
        value or "stdio", "streamable_http")


def resolve_backend(ir: Any, cap_id: str) -> Optional[dict[str, Any]]:
    """Resolve a capability id to a backend plan, or None if it is unbound.

    Returns a dict with `kind` in {"mcp", "database"} plus the connection
    facts the shim needs. An unbound capability (no `server`/`server_name`, or
    a kind we do not wire) returns None, and the caller keeps the stub.
    """
    binding = getattr(ir, "binding", None)
    if binding is None:
        return None
    cb = binding.capability_binding(cap_id)
    if cb is None:
        return None
    server = binding.server(cb.server) if cb.server else None
    kind = (server.kind if server else None) or (
        "database" if cb.engine else "mcp")
    server_id = cb.server or cb.server_name or cap_id

    if kind in ("mcp", "http_api"):
        url = cb.url or (server.url if server else None)
        if not url:
            return None                 # nothing to connect to; keep the stub
        secret_env = (server.secret_ref if server else None)
        return {
            "kind": "mcp",
            "server_id": server_id,
            "url": url,
            "transport": _transport_for(cb.transport),
            # The remote tool is named for the capability: one MCP server backs
            # many capabilities, each its own tool. The catalog id names the
            # mount, not the tool.
            "tool_name": cap_id,
            "secret_env": secret_env,
        }
    if kind == "database":
        dsn_env = cb.dsn_secret_ref or (server.dsn_secret_ref if server else None)
        if not dsn_env:
            return None
        return {
            "kind": "database",
            "server_id": server_id,
            "engine": cb.engine or (server.engine if server else "") or "",
            "dsn_env": dsn_env,
        }
    return None                          # object_store / process / reporting: stub


def wired_and_stubbed(ir: Any, tools: list[dict[str, Any]]) -> tuple[
        list[dict[str, Any]], list[dict[str, Any]]]:
    """Split an agent's tool surface into (wired, stubbed).

    `tools` is the emitter's own tool-surface dicts (name/description/gated). A
    tool whose capability resolves to a backend gains a `backend` key; the rest
    stay stubs.
    """
    wired: list[dict[str, Any]] = []
    stubbed: list[dict[str, Any]] = []
    for tool in tools:
        plan = resolve_backend(ir, tool["name"])
        if plan is None:
            stubbed.append(tool)
        else:
            wired.append({**tool, "backend": plan})
    return wired, stubbed


#: The runtime shim, emitted verbatim beside the agents. It is plain Python and
#: framework-neutral: both targets wrap these callables in their own tool type.
BACKENDS_SHIM = '''"""Real backend clients for bound capabilities (generated).

A capability the binding puts behind a declared server is wired here to that
server, so the agent tool that calls it is a working client, not a stub. What
stays the host's is named honestly: a credential is read from an env var by
name (never a value), and a database tool enforces the design's bound
(operation allowlist, row cap) while taking the query as an argument, because
the design carries the bound, not the SQL.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional


def _auth_headers(secret_env: Optional[str]) -> dict[str, str]:
    if not secret_env:
        return {}
    token = os.environ.get(secret_env)
    return {"Authorization": f"Bearer {token}"} if token else {}


def mcp_call(server_id: str, url: str, transport: str, tool_name: str,
             secret_env: Optional[str] = None, **kwargs: Any) -> Any:
    """Call one tool on a declared MCP server and return its result.

    The remote tool is the one named `tool_name` (the capability, or its
    catalog server id). A mismatch raises LookupError naming what the server
    does expose — a clear one-line fix, not a silent no-op.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore

    async def _run() -> Any:
        client = MultiServerMCPClient({
            server_id: {"url": url, "transport": transport,
                        "headers": _auth_headers(secret_env)}
        })
        tools = await client.get_tools()
        tool = next((t for t in tools if t.name == tool_name), None)
        if tool is None:
            raise LookupError(
                f"MCP server {server_id!r} exposes no tool {tool_name!r}; "
                f"available: {sorted(t.name for t in tools)}")
        return await tool.ainvoke(kwargs)

    return asyncio.run(_run())


def bounded_sql(dsn_env: str, allowed_operations: list[str], max_rows: int,
                query: str, params: Optional[dict] = None) -> list[dict]:
    """Run a bounded query against a declared database and return rows.

    The design's constraints are enforced here: the leading SQL verb must be in
    `allowed_operations` (e.g. only `select`), and at most `max_rows` come back.
    The DSN is read from the env var the binding named, never hard-coded.
    """
    from sqlalchemy import create_engine, text  # type: ignore

    verb = query.strip().split(None, 1)[0].lower() if query.strip() else ""
    if allowed_operations and verb not in [o.lower() for o in allowed_operations]:
        raise PermissionError(
            f"operation {verb!r} is not in allowed_operations "
            f"{allowed_operations}")
    dsn = os.environ.get(dsn_env)
    if not dsn:
        raise RuntimeError(f"database DSN env var {dsn_env!r} is not set")
    engine = create_engine(dsn)
    with engine.connect() as conn:
        result = conn.execute(text(query), params or {})
        mappings = result.mappings()
        rows = mappings.fetchmany(max_rows) if max_rows else mappings.fetchall()
        return [dict(r) for r in rows]
'''
