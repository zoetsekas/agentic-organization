---
id: WS-030
title: Fabric common services and operations
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Product]
scope: [runtime, targets, security, docs]
decisions: [ADR-0049]
depends_on: [WS-010, WS-014, WS-027, WS-028]
tags: [tenancy, operations]
---

# WS-030: Fabric common services and operations

## Objective
Make the fabric worth being a tenant of: shared services every organization
draws on, and an operational grip over what it generated — health, quotas,
drift, lifecycle — without dissolving the boundary between tenants.

## Deliverables
- A named, auditable set of **common services**: the catalog of building
  blocks, observability sinks, the record layer, identity. What is shared is a
  listed fabric decision, never an emergent consequence of naming.
- **Deployment lifecycle** per tenant: requested, generated, deployed, running,
  stopped, quarantined, retired — with the transitions an operator may make.
- **Quotas and entitlements** per tenant, including which catalog entries a
  tenant may use.
- **Health and drift**: what the fabric believes is deployed versus what the
  target reports, and a signal when they disagree.
- Operational signals surfaced to the command centre (WS-029), and recorded for
  the registry (WS-014).

## Scope
In: shared services, tenant lifecycle, quotas, health and drift. Out: the
isolation mechanism (WS-028), the operator UI (WS-029), within-tenant agent
behaviour.

## Approach
Model the operations first and keep every adapter injectable, because no cloud
account or container daemon exists in the development environment: the fabric's
view of a deployment is a contract that a real backend fills in later.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Common-service registry, explicitly listed | Phase 5 | Not started |
| M2 Deployment lifecycle and transitions | Phase 5 | Not started |
| M3 Quotas and per-tenant entitlements | Phase 5 | Not started |
| M4 Health and drift contract with a stub backend | Phase 5 | Not started |
| M5 Real backend adapters against a running target | Phase 6 | Not started |
| M6 Incident capture and escalation | Phase 6 | Not started |

## Dependencies
WS-028 for tenants, WS-027 for the catalog being shared, WS-010 for
observability, WS-014 for the registry the operational record feeds.

## Advantages
- Tenants get common services without a per-tenant copy of everything.
- An operator can answer "what is running, for whom, and is it healthy" in one
  place.
- Drift is detected rather than discovered.

## Disadvantages
- **The operational half cannot be proven here.** No cloud account, no Docker
  daemon: health, drift and lifecycle will be modelled and tested against
  stubs. A control plane whose adapters have never met a real target is a
  design, not an operation — M5 is where that is settled, and it is the most
  likely place this workstream is wrong.
- Every common service is a shared failure domain and a potential cross-tenant
  channel; the catalog especially, since all tenants read it.
- Quotas invite the question of what happens when one is hit, and "refuse" is
  rarely the answer an operator wants at 3am.
- The fabric's belief about a deployment can be stale, and a stale control plane
  is worse than none if anybody trusts it.

## Exit criteria
- Every shared service is listed, with what it exposes across tenants.
- A tenant's deployment lifecycle is recorded and its transitions are audited.
- Drift between believed and actual state raises a signal (M4), against a real
  target (M5).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened alongside ADR-0049. |
