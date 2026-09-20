---
id: ADR-0051
title: The command centre is a separate application over a shared backend
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Product]
consulted: [Security Engineering]
informed: [All engineering]
scope: [ui, security, docs]
workstreams: [WS-029]
supersedes: []
superseded_by: []
related: [ADR-0021, ADR-0043, ADR-0047, ADR-0049]
tags: [ui, tenancy]
---

# ADR-0051: The command centre is a separate application over a shared backend

## Context
The fabric needs an operator surface: which tenants exist, what is deployed,
what is healthy, what is over quota, what drifted, who did what. The obvious
cheap answer is a tab in the designer. That answer makes the separation the
whole design depends on into a UI convention — one bad conditional away from an
operator editing a tenant's org chart, or a designer reading another tenant's
incidents.

## Decision
The command centre is a **separate front-end application** served at its own
path, over the **same backend** as the designer.

- **Separate application, shared backend.** One API process, one store, one
  audit log — two applications with different jobs. No shared UI state, no
  shared navigation, no route that renders both.
- **Operator roles are distinct from workspace roles.** A designer role never
  grants a command centre action, and an operator role grants nothing inside a
  design. Holding both is possible for a person and is recorded as two separate
  grants, never inferred from one.
- **The command centre reads across tenants; it does not author inside one.**
  It can stop, quarantine, re-deploy and re-quota a tenant. It cannot edit a
  tenant's spec. Changing what an organization *is* remains the designer's job,
  under the tenant's own people.
- **Every command centre action is audited** as a privileged action, including
  reads across tenants, because cross-tenant reads are exactly what an operator
  is trusted with and exactly what must be reviewable.

## Scope
The operator-facing surface and its access model. It does not decide what the
fabric does (ADR-0049), how isolation works (ADR-0050), or the designer's own
RBAC, which is unchanged.

## Implementation
`web/command/` as its own asset bundle mounted at `/command/`, a
`fabric`-scoped API namespace, and a platform-operator role set alongside the
existing workspace roles. The existing audit log gains operator actions, with
cross-tenant reads recorded rather than sampled.

## Timeline
Phase 5, WS-029.

## Advantages
- The boundary is structural: a designer session cannot render an operator view
  because it is not the same application.
- Operators get a view built for operations rather than a mode bolted onto an
  editor.
- One backend means one audit log and one source of truth about tenants.

## Disadvantages
- **Two front-ends to keep coherent**, with duplicated plumbing — auth, error
  handling, styling — and a real risk they drift in look and behaviour.
- **A shared backend is a shared failure domain.** An API outage takes both
  applications down, and a backend defect can cross the boundary the UIs keep
  apart. The separation is in the applications and the roles, not in the
  process.
- **A person holding both role sets is the common case in a small
  installation**, which makes the separation feel like friction precisely where
  it is most likely to be worked around.
- **Auditing cross-tenant reads will produce a lot of log** for an operator
  doing their job, and nobody has decided what reviewing it looks like.

## Alternatives considered
- **A mode inside the designer** — cheapest, and reduces the separation to a
  conditional; rejected for the reason the separation exists.
- **A fully separate service with its own backend** — strongest isolation, but
  duplicates the tenant registry and splits the audit log, so the operator view
  and the truth can disagree.
- **CLI only** — sufficient for the model, insufficient for the job; operations
  at more than a handful of tenants needs a view.

## Verification
Tests assert the command centre's routes reject designer-only credentials with
403, that no operator role can mutate a spec, that no designer role can read
another tenant's operational state, and that every cross-tenant read lands in
the audit log.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Separate operator application, shared backend, distinct operator roles, audited cross-tenant reads. |
