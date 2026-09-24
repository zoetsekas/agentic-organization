---
id: ADR-0115
title: The public API is /api/v1, described by a committed OpenAPI document, and the MCP servers act through it as a named user with no reach of their own
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Designer, Security, Operations]
informed: [All engineering]
scope: [ui, security, runtime, docs]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0018, ADR-0031, ADR-0032, ADR-0033, ADR-0043, ADR-0047, ADR-0049, ADR-0051, ADR-0102, ADR-0103, ADR-0114]
tags: [api, openapi, versioning, sdk, client, mcp, rbac, identity]
---

# ADR-0115: The public API is /api/v1, described by a committed OpenAPI document, and the MCP servers act through it as a named user with no reach of their own

## Context
ADR-0018 made the UI and the SDK peers over the spec. In practice the only
client of the HTTP API was the bundled UI: ~100 routes under unversioned
`/api/...` paths, no tags, an OpenAPI document nobody had looked at, and no
promise that a path would still mean the same thing next month. People now
want to drive the designer from scripts and from AI assistants (Claude Code,
Claude Desktop) over the Model Context Protocol. An assistant is a new kind
of caller with a new failure mode: it acts on a prompt, and a prompt can be
wrong or hostile. If it reached the store by any path the UI does not use, it
would need its own copy of every permission check, and two copies of a check
are two things that can disagree.

## Decision
1. **`/api/v1` is the public contract.** Every `/api/...` route is also
   served at `/api/v1/...` by the *same endpoint function with the same
   dependencies*, so identity, RBAC, validation and audit cannot differ
   between them. The unversioned paths remain for the bundled UI and are left
   out of the schema. A breaking change to v1 is a new `/api/v2` prefix beside
   it, never an edit in place.
2. **The contract is a committed file.** `docs/api/openapi.json` is written
   by `orgagents api schema`; a test fails when it no longer matches the code.
   Every route carries a tag from one table keyed by path prefix; a route no
   row matches fails a test.
3. **Clients send an identity, never a role.** `orgagents.client` speaks each
   auth mode the designer has (ADR-0047, ADR-0114): `none` sends `X-User`;
   `trusted_proxy` sends `X-User` plus the proxy secret, i.e. the client *is*
   the proxy; `oidc` sends a bearer id token.
4. **MCP servers are clients of v1, not of the store.** `orgagents mcp
   designer` and `orgagents mcp runtime` turn every tool call into a v1
   request under the caller's identity. RBAC parity with the UI is therefore
   structural: a viewer's assistant cannot save, an editor's cannot publish,
   because the designer refuses the request, and the refusal (403) is what the
   assistant is shown.
5. **Identity is required and comes from configuration.** stdio: one user per
   process from `ORGAGENTS_USER` (or `ORGAGENTS_TOKEN` against an OIDC
   designer); the server will not start without it. Streamable HTTP: every
   request must present a bearer token that an operator-written table
   (`ORGAGENTS_MCP_TOKENS`, tokens ≥ 16 characters) maps to a user; anything
   else is a 401 before MCP sees it. HTTP binds to 127.0.0.1 by default and
   keeps the SDK's DNS-rebinding protection. HTTP in front of an OIDC designer
   is refused: forwarding a caller's token to another audience is token
   passthrough, which the MCP specification forbids.
6. **What MCP may do.** The designer server reads designs, validates,
   explains issue codes, reads the UML profile, applies model operations —
   the operation a canvas gesture sends (ADR-0102, ADR-0103) — saves with
   `expected_version` (ADR-0033), diffs revisions, preflights a compile and
   *requests* a publish, which the fabric acts on under its own authority
   (ADR-0049). **What it may not do:** manage workspace members or settings,
   break locks, restore or delete designs, read the audit log. Those are acts
   on other people's access or work, and remain in the UI and the REST API.
   The runtime server is read-only unless started with `--allow-mutations`,
   which adds exactly `run_agent`, `resume_session` and `ack_alert` — through
   the same routes the UI uses, recording `mcp:<user>` as the actor.

## Scope
Binds `src/orgagents/api.py` (via `api_contract.apply_contract`),
`src/orgagents/client.py`, `src/orgagents/mcp_server/`, and
`docs/api/openapi.json`. It does not add authentication to routes that have
none today (the runtime's `/api/agents`, `/api/sessions`, `/api/ops`); that is
ADR-0114's ground, and the MCP runtime server inherits whatever those routes
enforce.

## Implementation
- `orgagents.api_contract`: tag table, `/api/v1` aliases, description,
  `schema_text()`/`write_schema()`; called last in `create_app`.
- `orgagents api schema [--out PATH|-]`.
- `orgagents.client`: `DesignerClient`, `RuntimeClient`, `ApiError`, typed
  result shapes; extra `client`.
- `orgagents.mcp_server`: FastMCP servers, `Backend` (a URL, or the same
  FastAPI app in-process with `--in-process DB`), `BearerGate`; extra `mcp`
  pinned `>=1.10,<2`.
- Docs: `docs/DEVELOPERS.md`, `.mcp.json.example`.

## Timeline
Lands with WS-003's designer work; v1 is frozen from this ADR's date.

## Advantages
- One set of permission checks for every caller; the MCP surface cannot drift
  from the UI's because it has no checks of its own.
- A generated, tested schema gives outside clients something stable to build
  on, and makes an accidental breaking change a failing test.
- Assistants get useful tools (validate → explain → fix → preflight) without
  gaining any authority the user lacks.

## Disadvantages
- Every route exists twice in the router (~200 entries), and a bundled-UI
  path can still change without notice — by design, but it must be said.
- An MCP call is an HTTP round trip even in-process; fine at human speed.
- HTTP MCP needs a static token table; there is no OAuth flow yet, and OIDC
  designers get stdio only.
- The schema file must be regenerated whenever any route changes.

## Alternatives considered
- **MCP servers over `DesignerService` directly** — shorter path, but the
  publish, preflight and diff logic lives in route handlers; the MCP server
  would re-implement or bypass it. Rejected: parity by construction beats
  parity by review.
- **`tags=` on every decorator** — ~100 edits and a convention everyone must
  remember; the prefix table cannot be forgotten.
- **Version by header** — invisible in logs and browser URLs; a path prefix
  is what proxies and people can see.
- **Rewriting `/api/v1` to `/api` in middleware** — the schema would not
  describe v1 at all.

## Verification
- `tests/test_api_contract.py`: every route tagged; every `/api` route has a
  v1 twin; the schema documents v1 only; v1 and unversioned refuse the same
  caller; the committed schema is current; the client's identity headers per
  mode; a client round trip (validate, operation, save, diff, preflight) with
  an editor refused a publish.
- `tests/test_mcp_servers.py`: tool/resource/prompt listing and the absence
  of administrative tools; validate with issue ids; save with optimistic
  concurrency; a viewer refused a save with the designer's 403; the runtime
  server read-only without the flag; HTTP refused without a known token;
  refusal to start without an identity or with HTTP in front of OIDC.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted with the implementation. |
