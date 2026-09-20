---
id: WS-028
title: Tenancy and isolation
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Security Engineering]
scope: [compiler, targets, security]
decisions: [ADR-0049, ADR-0050]
depends_on: [WS-004, WS-005, WS-007]
tags: [tenancy, security]
---

# WS-028: Tenancy and isolation

## Objective
Let one fabric host many agentic organizations without any of them being able
to reach, collide with or observe another — and without a design having to know
which tenant it belongs to.

## Deliverables
- A `Tenant` in the fabric: id, namespace prefix, isolation domain,
  entitlements, lifecycle.
- Tenant-scoped compilation: every generated identifier, identity, volume,
  network and secret reference is tenant-qualified, and an untenanted compile
  is refused.
- Per-target enforcement: separate Compose project/networks/volumes locally;
  separate project, account or subscription boundaries per cloud target.
- Mapping reports that name what enforces the boundary per target, and say
  plainly where the enforcement is coarser than the model.
- Validation refusing any spec that references something outside its tenant.

## Scope
In: the boundary between tenants. Out: the security model within a tenant
(ADR-0008/0009, unchanged), quotas and fair sharing (WS-030).

## Approach
Isolation by construction, not by vigilance. The spec stays tenant-free so it
remains portable and so a tenant cannot describe its own boundary. Everything
that could collide gets a prefix; everything that could be shared must be an
explicit fabric offering.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Tenant model and registry | Phase 5 | Not started |
| M2 Tenant-scoped compilation with refusal of untenanted builds | Phase 5 | Not started |
| M3 Local target isolation (project, networks, volumes) | Phase 5 | Not started |
| M4 Cloud target isolation and mapping reports | Phase 5 | Not started |
| M5 Cross-tenant denial proven by generation tests | Phase 5 | Not started |
| M6 Breach attempt against a running deployment | Phase 6 | Not started |

## Dependencies
WS-004 for the within-tenant security model, WS-005 for the compiler, WS-007
for the cloud targets whose IAM enforces the boundary.

## Advantages
- Collisions between tenants become impossible rather than unlikely.
- A tenant cannot widen its own boundary, because it does not describe it.
- Specs remain portable between installations.

## Disadvantages
- Prefixed names are long, and anything reading meaning out of a resource name
  breaks.
- The fabric can mis-assign a domain, and the evidence lives only in the fabric.
- Coarse cloud IAM makes the guarantee weaker than the model in places; a
  footnote in a mapping report is thin protection.
- No cross-tenant sharing is possible at all, which some organizations will
  genuinely want.
- **Isolation nobody has attacked is a claim**: M5 proves generation, not
  resistance. M6 is the real test and needs infrastructure this environment
  does not have.

## Exit criteria
- One spec compiled for two tenants shares no identifier, volume, network,
  identity or secret reference (M5).
- Each target's mapping report names what enforces the boundary and flags where
  it is coarser (M4).
- A deliberate breach attempt against a running deployment fails (M6).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened alongside ADR-0049 and ADR-0050. |
