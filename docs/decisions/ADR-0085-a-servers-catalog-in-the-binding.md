---
id: ADR-0085
title: A servers catalog in the binding
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Security Engineering]
informed: [All engineering]
scope: [spec, compiler]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0004, ADR-0071]
tags: [binding, enterprise, mcp]
---

# ADR-0085: A servers catalog in the binding

## Context
A capability is linked to a concrete backend in the binding: each
`CapabilityBinding` names a `server_name` and a transport (an MCP `url`, or an
`engine` + DSN for a database). That is the right seam — implementation names
live in the binding, never the spec (ADR-0002).

It does not scale to an enterprise. A bank reaches a handful of systems — an
ERP, a data warehouse, an order-management system, a document store — through
dozens of capabilities. Binding each capability inline repeats the system's
URL, credential reference and trust posture on every one, and hides the fact
that twenty capabilities all land on one server. There was also no single
place that answered "what systems does this deployment touch?" — the inventory
an auditor asks for first.

## Decision
The binding gains a **servers catalog**. A `ServerBinding` declares one backing
system once — its `id`, `kind` (mcp / database / http_api / object_store /
process / reporting), connection, `trust` and egress — and a `CapabilityBinding`
references it with `server:` instead of repeating the connection. Anything set
inline on the capability still overrides the catalog, so a capability can share
a server and override just its DSN.

The inline form is unchanged and still valid: `server:` is optional, and a
capability that names neither a `server` nor an inline `server_name` is
refused. A dangling `server:` reference is refused.

Resolution is transparent. `capability_binding()` returns the *resolved* view —
the catalog's connection merged in, with the effective `server_name` set to the
catalog id. Every existing consumer (the compiler, the local target, and
crucially the phase gate's "does this separation survive the binding?" check,
ADR-0071) keeps working unchanged: two capabilities on one catalog server
collide exactly as two inline capabilities on one `server_name` do. So the
catalog does not weaken the barrier-survival guarantee — it makes the shared
server *more* visible, not less.

## Scope
The binding model and its resolution only. No spec-model change, no governance
change, no new target output. It is a convenience and an inventory, and it is
careful not to become a place where a control quietly dissolves.

## Implementation
**Implemented.** `ServerBinding`, `TargetBinding.servers`,
`CapabilityBinding.server`, a `_resolve_capability` merge, and a validator that
refuses a dangling reference or a capability with no connection at all. The
Atlas example's binding uses it: thirteen systems declared once, seventeen
capabilities referencing them, with the two sides of every separation on
different servers so the barriers survive (the phase gate confirms).

## Timeline
Phase 6, with the enterprise example.

## Advantages
- One declaration per system, not per capability — the repetition an
  enterprise hits immediately is gone.
- The catalog is the deployment's systems inventory, `kind` and `trust`
  included.
- Sharing is visible: a reader sees the four capabilities that all point at
  `oms`, which inline bindings scattered.

## Disadvantages
- Two ways to bind a capability (inline, by reference) is more surface. The
  validator keeps them from being ambiguous, and inline stays the simplest
  path for a small design.

## Alternatives considered
- **Leave it inline.** Fine for the examples that predate this; it does not
  scale and gives no inventory.
- **Put servers in the spec.** They are implementation — URLs, credentials,
  engines — and belong in the binding by ADR-0002. The spec keeps the neutral
  `resource_class` on the capability.

## Verification
Tests assert: a capability referencing a catalog server inherits its
connection; an inline field overrides the catalog; a dangling reference is
refused; a capability with neither form is refused; two capabilities on one
catalog server are seen as colliding by the separation-survival check; and the
inline form still resolves unchanged.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. A `servers:` catalog in the binding; capabilities reference a server by id and inherit its connection; resolution is transparent so the phase gate's barrier-survival check is unaffected. |
