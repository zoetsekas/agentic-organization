---
id: ADR-0010
title: Agents reach systems through declared capabilities bound to MCP servers
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Data Platform]
informed: [All engineering]
scope: [spec, runtime, targets, security]
workstreams: [WS-004, WS-008]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0008, ADR-0015]
tags: [integration, security]
---

# ADR-0010: Agents reach systems through declared capabilities bound to MCP servers

## Context
An agent is only useful when it can reach systems of record. The naive approach
gives it a connection string and an SDK, which means the credential is inside
the model's context, the reachable surface is whatever the credential allows,
and the design cannot state what the agent may touch.

The spec must say *what* access is needed without naming *how* it is provided.

## Decision
The spec declares **capabilities**: abstract, named access needs — `query`
against a `data_source` of a given classification, `read`/`write` against a
document store, `notify` on a channel class. A capability states the action, the
resource class, and the constraints (row limits, masked fields, statement
classes, approval requirement).

Each target **binds** a capability to a concrete **MCP server** and enforces the
constraints at the server, outside the model's reach. Agents receive tools, never
credentials. Relational access specifically is always mediated: reachable
tables, allowed statement classes, row limits and column masking are enforced
server-side and are part of the compiled artifact.

## Scope
All external access from an agent: databases, APIs, document stores, messaging.
Excludes agent-to-agent communication, which is the org model's concern.

## Implementation
`spec.model.Capability` → IR grants → `harness/mcp.py` mounting and
`harness/relational.py` policy enforcement in the runtime; targets emit MCP
server deployments plus the binding config. Secrets are referenced by name and
resolved by the platform at bind time (ADR-0015).

## Timeline
Phase 1 for the declaration; per-target bindings with each target.

## Advantages
- Credentials never enter model context or agent code.
- Constraints are enforced at the boundary, so a jailbroken prompt still cannot
  widen access.
- The design states its data reach in reviewable business terms.
- MCP gives one integration shape across every runtime and target.

## Disadvantages
- An extra network hop and process per capability — latency and operational cost.
- MCP is young; we are betting on an evolving protocol and will carry
  compatibility work.
- Every integration needs a server implementation, so breadth is slower than
  handing an agent an SDK.
- Server-side enforcement centralizes risk: a flawed MCP server is a single
  point of compromise for everything bound to it.

## Alternatives considered
- **Direct SDK access with scoped credentials** — faster, but puts credentials
  in scope and makes reach unreviewable.
- **A bespoke tool-proxy protocol** — same architecture, no ecosystem.
- **Per-target native integrations** — breaks neutrality and multiplies work by
  the number of providers.

## Verification
Tests assert that statement class, table scope, row limits and column masking
are enforced, and that no generated artifact contains a resolved credential.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Abstract capabilities, MCP bindings, no credentials in agent scope. |
