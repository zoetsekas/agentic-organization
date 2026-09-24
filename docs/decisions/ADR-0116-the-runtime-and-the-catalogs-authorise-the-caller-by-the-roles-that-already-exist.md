---
id: ADR-0116
title: The runtime and the catalogs authorise the caller by the roles that already exist, scoped by the workspace that owns the design
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform]
consulted: [Security, Designer, Operations]
informed: [All engineering]
scope: [security, runtime, ui]
workstreams: [WS-003, WS-006]
supersedes: []
superseded_by: []
related: [ADR-0032, ADR-0041, ADR-0043, ADR-0047, ADR-0049, ADR-0051, ADR-0114, ADR-0115]
tags: [rbac, authorization, runtime, catalog, audit, identity]
---

# ADR-0116: The runtime and the catalogs authorise the caller by the roles that already exist, scoped by the workspace that owns the design

## Context
ADR-0114 made every caller of a running stack prove who they are, and
ADR-0115 made the MCP servers act through `/api/v1` as a named user. Both
left one gap, and ADR-0115 named it: the runtime routes of the designer's API
(`/api/agents`, `/api/sessions`, `/api/ops`, `/api/org`, `/api/components`)
checked nobody, and the catalog routes (`/api/catalog`, `/api/catalogs`) took
the actor and the reviewer from a raw `X-User` header -- in `oidc` mode too,
where the designer itself ignores that header. Anyone who could reach the port
could read every session, start any agent, answer for a human, acknowledge
alerts and approve catalog entries under any name, and the runtime MCP server
inherited exactly that.

This is a separate decision from ADR-0114. That one is about *authentication*
(who the caller is, how the proxy and the approvals are trusted); this one is
about *authorisation* over the running organization -- which permissions
exist, which roles grant them, and what scopes a caller's view. It gets its
own record so that ADR-0114 stays the answer to "how do we know who you are".

## Decision
1. **One authenticator.** Every route takes the caller from `principal`
   (ADR-0047, ADR-0114). No route declares an identity header or reads
   `request.headers` itself; a test parses `api.py` and fails if one does.
   The catalog service is handed the authenticated user id as actor or
   reviewer, never a header. A run's `created_by` and a resume's `actor` are
   the caller (an MCP server's `mcp:<user>` label is kept only when `<user>`
   is the caller).
2. **Permissions.** `runtime.read`, `runtime.run`, `runtime.resume`,
   `runtime.manage` (agents, org units, sandbox templates, installing onto an
   agent), `ops.ack`, `catalog.read`, `catalog.publish` (propose, edit one's
   proposal, amend), `catalog.review` (approve/restrict, send back, retire),
   `catalog.delete`, `catalog.entitle`. Deny by default.
3. **No new roles: two existing models, each for what it already means.**
   - **Designer workspace roles (ADR-0032)** grant runtime permissions *in the
     workspace that owns the design an agent was loaded from*: viewer →
     read; reviewer → read, resume; editor → read, run, resume,
     catalog.publish; admin/owner → those plus manage and ops.ack. Scope
     follows ownership: a session belongs to its agent, an agent to its design
     (`Agent.system_id`, set by `load_system(..., system_id=)` and inherited by
     spawned sub-agents), a design to its workspace. The people who may edit a
     design are the people who may watch and drive it run; a second
     membership list for "runtime access" would drift from the first.
     An installation-wide designer grant from the identity provider (`*`,
     ADR-0047) applies to every scope.
   - **Fabric operator roles (ADR-0049, ADR-0051)** grant installation-wide
     reach -- every tenant, including agents no design owns -- for the
     operational acts only: `runtime.read` and `ops.ack` for automation,
     operators and admins; admins also govern the shared platform catalog
     (`catalog.publish/review/delete/entitle`), which is platform
     infrastructure like the entitlements they already manage. **An operator
     may not run an agent or answer in a session**: that is acting *as* the
     tenant's organization, which ADR-0051 keeps with the tenant's people.
     Operator grants are not per tenant today, so "operators see their
     tenants" means every tenant.
   - `catalog.read` is implied by `runtime.read` anywhere: the catalogs are the
     directory every design draws from.
4. **Scope and refusals.** Lists are filtered to what the caller may read;
   a caller who may read nothing anywhere gets 403 for the list. Reading or
   acting on a specific object outside one's scope is **403**, the designer's
   existing answer for a system in another workspace; a missing object is 404.
   Unauthenticated in `trusted_proxy`/`oidc` is 401 (from the authenticator).
   Agents with no design, and alerts about no agent, are installation-scoped.
   `/api/ops/metrics` is installation-wide for installation readers and
   computed over the caller's visible agents for everyone else.
   `/sessions/{id}` only redirects into the UI and no longer says whether the
   session exists; `/healthz` stays public and no longer reports a count.
5. **Audit.** Every runtime and catalog mutation is written to the designer's
   append-only audit log (ADR-0043) allowed or denied, with actor, permission,
   workspace and reason; a refused read is written too. Workspace admins read
   their workspace's rows through the existing `/api/designer/audit`.
6. **`none` mode.** The *local user* -- the identity of a request that names
   nobody (`anonymous`, or `ORGAGENTS_LOCAL_USER`), authenticated in `none`
   mode -- holds every permission installation-wide, so the bundled UI and the
   single-user flow are unchanged. A named `X-User` in `none` mode is resolved
   by membership like anyone else, so an assistant configured as a viewer
   acts as a viewer (ADR-0115). Whether a principal is the local user is read
   from how *that principal* was authenticated, not from configuration: an
   OIDC subject called `anonymous` is not the local user.

## Scope
Binds `src/orgagents/api.py` (runtime and catalog routes),
`src/orgagents/runtime_access.py`, `Agent.system_id`, the audit actions in
`designer/audit.py`, and through ADR-0115 the runtime MCP server and
`orgagents.client`. It does not change the designer's or the fabric's own
permission tables, and does not cover a worker's own API (ADR-0114).

## Implementation
- `orgagents.runtime_access`: permission names, the two role tables,
  `RuntimeAccess.grants(principal)` → `Grants` (installation set plus
  per-workspace sets), scope resolution by design.
- `api.py`: the authenticator and the operator registry are built before the
  runtime routes; a `caller` dependency; `_authorize` records refusals before
  raising; `_catalog_act` wraps catalog mutations.
- `Observability.metrics(agent_ids=)`, `OrgChart.to_tree(agents=)`.
- `load_system(platform, ir, system_id=)`; spawned sub-agents inherit it.

## Timeline
Lands with ADR-0115's public API, before the runtime MCP server is offered to anyone.

## Advantages
- The MCP runtime server and the client are enforced without code of their
  own; a viewer's assistant reads and cannot run.
- No second membership list, no third role vocabulary to review.
- Every refusal leaves a row; the runtime becomes auditable per workspace.

## Disadvantages
- Every request lists the workspaces to resolve grants: fine at designer
  scale, a cache if it ever is not.
- Agents loaded without a `system_id` (the demo seed, a CLI `serve` of a
  compiled IR) are visible only at installation scope until loaded with one.
- 403 on an out-of-scope id confirms the id exists, as the designer's does;
  choosing 404 would have been more private and inconsistent.
- Operator grants are installation-wide; per-tenant operator scoping is a
  change to the fabric's grants, not to this table.

## Alternatives considered
- **Fabric operator roles only** -- tenant designers would need an operator
  grant to read their own sessions, and ADR-0051 keeps operators out of
  acting as the organization.
- **Designer roles only** -- the command centre's operators would lose sight
  of the runtime they operate, and unowned agents would be unreachable.
- **A third, runtime-specific role set** -- a new vocabulary and a second
  membership list that would drift from the design's.
- **404 for out-of-scope objects** -- more private, but the designer already
  answers 403 and two answers for the same question confuse clients.

## Verification
`tests/test_runtime_access.py`: no route declares an identity header or reads
`request.headers` outside `principal`; the `none`-mode local user runs,
reviews and reads unchanged; no identity is 401 in `trusted_proxy` (a client
`X-User` without the proxy secret) and in `oidc` (no bearer); a viewer reads
but cannot run or resume, an editor cannot delete an agent or review a
catalog entry; another workspace's session, events, trace, agent and health
are 403 and lists are filtered; a caller with no grant is refused lists; an
operator sees every session, acknowledges alerts, cannot run; metrics are
scoped; `created_by` and catalog reviewers are the authenticated caller;
allowed and denied mutations are in the audit log and readable by the
workspace owner. `tests/test_mcp_servers.py`: through the runtime MCP server
a viewer lists sessions and is refused `run_agent` (403) with mutations
enabled, an owner runs, a stranger is refused another workspace's session.
`tests/test_api_contract.py`: `RuntimeClient` as a member reads, as a
stranger is refused.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. |
