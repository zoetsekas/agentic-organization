---
id: WS-029
title: The command centre
status: Active
version: 1.2.0
date: 2026-09-20
updated: 2026-09-20
owner: Product
contributors: [Platform Architecture]
scope: [ui, security, docs]
decisions: [ADR-0049, ADR-0051]
depends_on: [WS-021, WS-028, WS-030]
tags: [ui, tenancy]
---

# WS-029: The command centre

## Objective
Give whoever operates the fabric one place to see and act on every tenant —
without giving them the ability to edit anybody's organization, and without
giving designers any view across the boundary.

## Deliverables
- A separate front-end application at `/command/`, over the shared backend.
- A platform-operator role set distinct from workspace roles, with holding both
  recorded as two grants rather than inferred from one.
- Tenant list and detail: deployments, health, quotas, drift, recent incidents.
- Operator actions: deploy, stop, quarantine, re-quota, re-deploy — never edit
  a spec.
- Every operator action, and every cross-tenant read, in the audit log.

## Scope
In: the operator surface and its access model. Out: what the fabric does
(WS-030), how isolation works (WS-028), designer RBAC (unchanged).

## Approach
Structural separation over convention: a different application, different
roles, different routes. Read across tenants freely, author inside none.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Operator roles and route separation | Phase 5 | Done |
| M2 Tenant list and detail views | Phase 5 | Done |
| M3 Operator actions with audit | Phase 5 | Done |
| M4 Health, quota and drift surfacing | Phase 5 | Done |
| M5 Incident view and acknowledgement | Phase 6 | Not started |

## Interfaces
The operator API contract is `docs/COMMAND_CENTRE_API.md`, with a generated
fixture at `docs/fixtures/command-centre.sample.json` so the front end (M2,
M4) can be built without a running backend.

## Dependencies
WS-028 for tenants, WS-030 for the operational signals to display, WS-021 for
the identity the operator roles hang from.

## Advantages
- A designer session cannot render an operator view, because it is not the same
  application.
- Operations get a surface built for operating rather than a mode inside an
  editor.
- One backend, so the operator view and the truth cannot disagree.

## Disadvantages
- Two front-ends with duplicated plumbing that will drift.
- A shared backend is a shared failure domain: the separation is in the
  applications and roles, not the process.
- In a small installation one person holds both role sets, which makes the
  separation feel like friction exactly where it will be worked around.
- Auditing every cross-tenant read produces volume nobody has yet decided how
  to review.

## Exit criteria
- Designer-only credentials get 403 on every command centre route.
- No operator role can mutate a spec.
- Every cross-tenant read appears in the audit log.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.2.0 | 2026-09-20 | M2 and M4 done: the operator front end ships as its own bundle in `web/command/`, mounted at `/command/`. Tenant list and detail, deployment state and history with buttons driven by the API's `allowed_transitions`, operator actions and re-quota surfacing the backend's own 403/409 text, health with `fresh`/`stale`/`unobserved` confidence rendered as its own state, quota soft-limit breaches shown apart from refusals, and an offline fallback to the generated fixture that says on screen it is not live. Covered by `tests/test_command_centre_ui.py`. |
| 1.1.0 | 2026-09-20 | M1 and M3 done: platform-operator roles in `fabric/rbac.py`, the `fabric` API namespace, operator actions routed through the lifecycle table, and an append-only operator log recording actions and cross-tenant reads. Contract written down in `docs/COMMAND_CENTRE_API.md`. |
| 1.0.0 | 2026-09-20 | Opened alongside ADR-0051. |
