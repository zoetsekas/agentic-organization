---
id: WS-021
title: Designer access control for people
status: Active
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
owner: Security Engineering
contributors: [Platform Architecture]
scope: [designer, security]
decisions: [ADR-0032, ADR-0043]
depends_on: [WS-020]
tags: [designer, security]
---

# WS-021: Designer access control for people

## Objective
Let many people share a designer installation with the access each should have,
without entangling platform access with what any agent may reach.

## Deliverables
- Five workspace roles with a short, reviewable permission vocabulary.
- `decide()` / `require()` with reasons on every denial.
- Membership management with the guarantee that an owner always remains.
- Principal resolution from headers, ready for an OIDC proxy.
- `whoami` reporting role and permissions per workspace.
- An append-only audit log of designer actions, readable by admins and owners.

## Scope
In: human access to the designer. Out: agent permissions (WS-004), and running
an identity provider.

## Approach
Keep the model small and separate. Resolve the role from membership on every
request and never trust a client-sent role. Make denials explain themselves so
they are actionable rather than mysterious.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Roles, matrix, enforcement | Phase 3 | Done |
| M2 Membership management | Phase 3 | Done |
| M3 OIDC integration and group mapping | Phase 3 | Not started |
| M4 Per-system access within a workspace | Phase 3 | Not started |
| M5 Audit log of designer actions | Phase 3 | Done |

## Dependencies
WS-020 for the service the checks live in.

## Advantages
- Designer access and agent access cannot be confused.
- Five roles are memorable, so they get reviewed.
- Denials name the role and the missing permission.

## Disadvantages
- **Header identity is forgeable** and is only safe behind an authenticating
  proxy; until M3 the deployment carries that responsibility, and nothing in
  the product enforces it.
- Workspace-scoped roles are coarse; per-system access is a real need (M4).
- No time-bounded or delegated access: an admin stays an admin until removed.
- The audit log (M5) has no retention policy and holds personal data — actor
  ids, names and denial reasons — which sits awkwardly with append-only.
- Because identity is still a header, the actor recorded in the audit log is
  only as trustworthy as the proxy in front of it (ADR-0043).

## Exit criteria
- Identity comes from a real provider, with group-to-role mapping (M3).
- Every designer action is attributable in an audit log (M5). **Done** —
  creates, saves, deletes, locks, merges, settings, membership and denials are
  recorded across all three backends (ADR-0043).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | M5 done: append-only audit log of designer actions, including denials (ADR-0043). |
| 1.0.0 | 2026-09-20 | Opened. Roles, enforcement and membership landed; SSO pending. |
