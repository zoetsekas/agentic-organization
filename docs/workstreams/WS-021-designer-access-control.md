---
id: WS-021
title: Designer access control for people
status: Active
version: 1.2.0
date: 2026-09-20
updated: 2026-09-20
owner: Security Engineering
contributors: [Platform Architecture]
scope: [designer, security]
decisions: [ADR-0032, ADR-0043, ADR-0047]
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
- OIDC token verification with JWKS caching and key rotation, and an
  explicit trusted-proxy mode for installs that terminate identity upstream.
- Deny-by-default group-to-role mapping with a documented precedence.
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
| M3 OIDC integration and group mapping | Phase 3 | Done |
| M4 Per-system access within a workspace | Phase 3 | Not started |
| M5 Audit log of designer actions | Phase 3 | Done |

## Dependencies
WS-020 for the service the checks live in.

## Advantages
- Designer access and agent access cannot be confused.
- Five roles are memorable, so they get reviewed.
- Denials name the role and the missing permission.

## Disadvantages
- **Trusted-proxy mode is still forgeable.** Headers are now only read when
  `auth_mode` says so, but nothing in the product checks the proxy is there;
  anyone reaching the port directly is whoever they claim (ADR-0047).
- OIDC adds failure modes header identity did not have: clock skew widens
  expiry, JWKS availability gates unseen keys, and a token stays good until it
  expires however quickly the directory revokes access (ADR-0047).
- Only RSA-signed tokens are verified; an issuer publishing EC keys cannot be
  used yet.
- Workspace-scoped roles are coarse; per-system access is a real need (M4).
- No time-bounded or delegated access: an admin stays an admin until removed.
- The audit log (M5) has no retention policy and holds personal data — actor
  ids, names and denial reasons — which sits awkwardly with append-only.
- In trusted-proxy mode the actor recorded in the audit log is only as
  trustworthy as that proxy (ADR-0043); in OIDC mode a signature vouches for
  it.

## Exit criteria
- Identity comes from a real provider, with group-to-role mapping (M3).
  **Done** — ID tokens are verified against the issuer's JWKS and groups map to
  workspace roles deny-by-default; the header path survives only as an
  explicitly configured trusted-proxy mode (ADR-0047).
- Every designer action is attributable in an audit log (M5). **Done** —
  creates, saves, deletes, locks, merges, settings, membership and denials are
  recorded across all three backends (ADR-0043).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.2.0 | 2026-09-20 | M3 done: OIDC token verification, JWKS rotation, group-to-role mapping and explicit trusted-proxy mode (ADR-0047). |
| 1.1.0 | 2026-09-20 | M5 done: append-only audit log of designer actions, including denials (ADR-0043). |
| 1.0.0 | 2026-09-20 | Opened. Roles, enforcement and membership landed; SSO pending. |
