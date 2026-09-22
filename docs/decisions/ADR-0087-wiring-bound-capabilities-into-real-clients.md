---
id: ADR-0087
title: Wiring bound capabilities into real clients, not stubs
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Security Engineering]
informed: [All engineering]
scope: [compiler]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0005, ADR-0015, ADR-0073, ADR-0085, ADR-0086]
tags: [target, tools, mcp, database, binding]
---

# ADR-0087: Wiring bound capabilities into real clients, not stubs

## Context
The platform targets (`adk`, `langgraph`) emitted every tool as
`raise NotImplementedError`, on the reasoning that a tool's *callable* is the
host's, not the design's. That is true for a tool behind no declared backend.
It is not true for the common case: the binding's servers catalog (ADR-0085)
already declares, per capability, *what kind* of system backs it and *how it is
reached* — an MCP endpoint's URL and transport, or a database's engine and DSN
reference. A package that re-stubs a capability whose backend is fully declared
throws away information it was handed, and makes the generated output look
further from runnable than it is.

## Decision
A capability the binding puts behind a declared MCP or database server is
**emitted as a working client**, not a stub. Both platform targets drop a small
`_backends.py` shim beside their agents with two framework-neutral callables:

- `mcp_call(server_id, url, transport, tool_name, secret_env, **kwargs)` — opens
  an MCP client to the declared server and invokes the tool named for the
  capability; a name mismatch raises `LookupError` naming what the server does
  expose, never a silent no-op.
- `bounded_sql(dsn_env, allowed_operations, max_rows, query, params)` — runs a
  query against the declared database, **enforcing the design's bound**: the
  leading SQL verb must be in the capability's `allowed_operations` and at most
  `max_rows` rows come back.

Each agent module then emits one function per capability that calls the right
backend, and wraps it in the framework's tool type (`StructuredTool` for
deepagents, `FunctionTool` for ADK). The resolution and emission live in a
shared `compiler/targets/_wiring.py` that reads `ir.binding` by attribute only
and imports nothing from `orgagents.spec` (ADR-0005).

What stays the host's is emitted honestly: a **credential** is an env-var name,
never a value (ADR-0015); an **MCP tool's remote name** follows the convention
"named for the capability", a one-line fix if wrong; and a **database query** is
an argument, because the design carries the *bound* on a read, not the SQL. A
capability with no server bound stays a `NotImplementedError` stub, and the
conformance report (ADR-0073) says which tools are wired and which are not.

## Scope
The `adk` and `langgraph` platform targets and a new shared wiring helper. No
change to the spec, the IR shape, or the infrastructure targets — `terraform:*`
and `local` already run the harness, which mounts the same servers at run time.

## Implementation
`src/orgagents/compiler/targets/_wiring.py` (`resolve_backend`,
`wired_and_stubbed`, `emit_wired_def`, `shim_imports`, `BACKENDS_SHIM`). Both
`adk.py` and `langgraph.py` split their tool surface into wired and stubbed,
emit the wired functions, emit `_backends.py` when anything is wired, and add
`langchain-mcp-adapters` / `sqlalchemy` to `requirements.txt` as needed. AYC —
whose every capability binds to Shopify (MCP) or Fishbowl (database) — now
generates with zero stubs.

## Timeline
Accepted 2026-09-22, shipped with AYC's regenerated `adk/` and `langgraph/`
output.

## Advantages
- Generated packages are runnable against real backends once credentials are
  set, not a scaffold of stubs.
- The design's read bound (operation allowlist, row cap) is enforced in the
  emitted database tool, not just described.
- One shared helper keeps the two targets consistent and honest; the
  conformance report distinguishes wired from stubbed.

## Disadvantages
- The MCP remote-tool-name convention ("named for the capability") can miss;
  it fails loudly with a `LookupError`, but it is still a convention, not a
  guarantee the IR carries.
- More generated surface depends on external packages
  (`langchain-mcp-adapters`, `sqlalchemy`); they are added to requirements only
  when used.
- `mcp_call` opens a client per invocation, which is simple but not pooled; a
  host that cares can replace the shim.

## Alternatives considered
- **Keep stubbing everything.** Rejected: it discards the servers catalog and
  overstates how far from runnable the output is.
- **Discover the whole MCP toolset and mount it un-named.** Rejected: it breaks
  the capability→tool identity the design's approval gate and mandate map rely
  on, so a gate keyed on a capability id would silently stop matching.
- **Synthesize the SQL for a database capability.** Rejected: the design does
  not carry the query, only the bound; inventing SQL would be guessing. The
  emitted tool enforces the bound and takes the query as an argument.

## Verification
`tests/test_tool_wiring.py`: the shim parses and defines both clients;
credentials are env names; an MCP capability resolves to an MCP backend and a
database capability to a SQL backend; an unbound capability resolves to nothing;
`emit_wired_def` output parses; and AYC's `adk` and `langgraph` packages contain
**no** `NotImplementedError`, call the declared backends, enforce `["select"]`
on the stock check, and name the wired clients in requirements. The existing
`test_adk_target.py` / `test_langgraph_target.py` still hold for Northwind,
whose binding declares no servers, so its tools stay stubs.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. Bound MCP/database capabilities emitted as real clients via a shared `_backends.py` shim; only unbound capabilities stay stubs; AYC regenerates with zero stubs. |
