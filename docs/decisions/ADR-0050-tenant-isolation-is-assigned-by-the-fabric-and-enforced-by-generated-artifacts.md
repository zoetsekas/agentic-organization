---
id: ADR-0050
title: Tenant isolation is assigned by the fabric and enforced by generated artifacts
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Security Engineering]
consulted: [Product]
informed: [All engineering]
scope: [compiler, targets, security]
workstreams: [WS-028]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0009, ADR-0049]
tags: [security, tenancy]
---

# ADR-0050: Tenant isolation is assigned by the fabric and enforced by generated artifacts

## Context
Once the fabric hosts more than one agentic organization, the question stops
being "can this agent read that data" and becomes "can this *organization*
reach that organization at all". Those are different questions with different
failure modes. A mistake inside a tenant costs that tenant; a mistake at the
boundary costs everyone.

The existing security model is thorough within a design — deny-by-default
permissions, data classification, environment classes, per-agent narrowing —
and says nothing between designs, because until now there was only one.

Two things make this urgent rather than theoretical. Generated artifacts are
named from ids in the spec, so two tenants that both call a team `finance`
collide on volumes, networks, identities and secret references. And a spec is
authored by a tenant's own designers, so anything a spec can ask for is
something a tenant can ask for.

## Decision
**A design never declares its own isolation. The fabric assigns it, and the
generated artifacts enforce it.**

1. **Every tenant has an isolation domain** assigned by the fabric: a stable
   tenant id, a namespace prefix applied to every generated resource name, its
   own identities and secret scope, its own data stores, and its own network
   boundary. The spec contains none of this, so a spec stays portable and a
   tenant cannot widen its own domain by editing a design.
2. **Nothing is shared between tenants except what the fabric explicitly
   offers as a common service** (ADR-0052 territory: the catalog, observability
   sinks, the record layer). Sharing is a fabric decision, listed and auditable,
   never an emergent consequence of naming.
3. **Cross-tenant reach is denied, not narrowed.** There is no "protected
   across tenants" scope. Data classification governs *within* a tenant; the
   tenant boundary is above it and is absolute.
4. **The boundary is only as strong as the target enforces it.** Each target
   states, in its mapping report, exactly what enforces the boundary there —
   and where the enforcement is weaker than the model, it says so in those
   words rather than implying a guarantee it cannot make.

Compilation is therefore **tenant-scoped**: the same spec compiled for two
tenants produces two artifact sets that share no name, volume, network,
identity or secret reference.

## Scope
Isolation between tenants of the fabric. It does not change the within-tenant
security model (ADR-0008, ADR-0009), and it does not decide quotas or fair
resource sharing, which are operational concerns (WS-030).

## Implementation
A `Tenant` in the fabric carrying id, namespace prefix, isolation domain and
entitlements. A tenant-scoped compile that prefixes every generated identifier
and every identity, and refuses to emit an artifact whose name is not
tenant-qualified. Per-target enforcement: separate Compose project, networks
and named volumes locally; separate project/account/subscription boundaries in
the Terraform targets, with the mapping report naming each one. A validator
refuses a spec that tries to reference anything outside its tenant.

## Timeline
Phase 5, WS-028.

## Advantages
- A collision between two tenants' ids becomes impossible rather than unlikely.
- Tenants cannot widen their own boundary, because they do not describe it.
- Specs stay portable between installations, since tenancy is not in them.
- The weakest link is documented per target instead of assumed away.

## Disadvantages
- **Prefixing everything makes generated names long and less readable**, and
  anything that reads a resource name to find meaning will need updating.
- **"The fabric assigns it" means the fabric can get it wrong**, and a
  mis-assigned domain is a cross-tenant defect with no in-spec evidence — the
  audit trail lives only in the fabric.
- **Coarse cloud IAM weakens the guarantee.** Where a target cannot express the
  boundary exactly, the generated grant is wider than the model, and a
  mapping-report footnote is thin protection against a reader who does not read
  it.
- **No cross-tenant use case is served at all.** A company that genuinely wants
  two organizations to share a data class must either merge them or go outside
  the model; absolute boundaries have that cost.
- **Untested against real infrastructure.** No cloud account or Docker daemon
  exists here, so enforcement is asserted by generation tests, not by attempting
  a breach on a running deployment. Isolation nobody has attacked is a claim.

## Alternatives considered
- **Declare tenancy in the spec** — lets a tenant describe its own boundary,
  which is the failure mode this exists to prevent.
- **Rely on target IAM alone, no namespacing** — leaves name collisions to
  chance and makes a shared target a shared blast radius.
- **A shared runtime with per-request tenant checks** — one missed check is a
  cross-tenant breach; isolation by construction beats isolation by vigilance.
- **Per-tenant installations of everything** — strongest isolation, but the
  catalog, records and operator view fragment, which is what the fabric exists
  to prevent.

## Verification
Tests compile one spec for two tenants and assert no shared identifier, volume,
network, identity or secret ref; that an untenanted compile is refused; that a
spec referencing another tenant's resource fails validation; and that each
target's mapping report names what enforces the boundary and flags where it is
coarser than the model.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Fabric-assigned isolation domains, tenant-scoped compilation, absolute cross-tenant denial. |
