---
id: ADR-0032
title: The designer has its own RBAC for people, separate from agent permissions
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering, Platform Architecture]
consulted: [Product, Compliance]
informed: [All engineering]
scope: [designer, security]
workstreams: [WS-021]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0026, ADR-0031]
tags: [designer, security]
---

# ADR-0032: The designer has its own RBAC for people, separate from agent permissions

## Context
Two access-control questions look similar and are not: *what may this agent
reach* (ADR-0008) and *what may this person do in the designer*. Conflating
them is a common and expensive mistake. Being an agent's accountable owner
(ADR-0026) says nothing about whether you may edit the design; being a
workspace administrator must not silently widen what any agent can reach.

## Decision
A separate, small permission model for people, scoped to a **workspace**:

| Role | May |
|---|---|
| `viewer` | read designs |
| `reviewer` | read and review |
| `editor` | create, edit, lock, restore revisions |
| `admin` | editor, plus delete, publish, break locks, manage members and settings |
| `owner` | admin, plus delete the workspace |

Same three properties as the agent model: **deny by default**, a role is the
only way to obtain a permission, and every decision names the reason. The
permission vocabulary is deliberately short, because a list nobody can hold in
their head is a list nobody reviews.

Identity comes from outside: a header in development, an OIDC proxy in
production. The service never trusts a client-sent role — it resolves the role
from workspace membership on every request.

## Scope
Human access to the designer. Agent permissions remain ADR-0008 and are
untouched by any role here.

## Implementation
`designer/rbac.py`: `Principal`, a role→permission matrix, `decide()` and
`require()`. Every service method takes the principal and checks before acting.
The API derives the principal from headers via a dependency; tests use the
service directly.

## Timeline
Phase 3, with the designer backend.

## Advantages
- Platform access and agent access cannot be confused or accidentally linked.
- Five roles cover the real cases and stay memorable.
- Denials name the role and the missing permission, so they are actionable.
- Identity is pluggable, so enterprises bring their own SSO.

## Disadvantages
- Workspace-level roles are coarse: you cannot grant edit on one system and
  read on another within the same workspace, and teams will want that.
- A second RBAC model is a second thing to audit and keep consistent.
- Header-based identity in development is trivially forgeable; it is only safe
  behind an authenticating proxy, and that is a deployment responsibility we
  state but cannot enforce.
- No delegation or time-bounded access — an admin is an admin until removed.

## Alternatives considered
- **Reuse the agent RBAC engine** — superficially DRY; couples two models that
  must evolve separately and invites permission leakage between them.
- **Per-system ACLs** — more precise and considerably more to administer; a
  candidate once workspaces prove too coarse.
- **No designer RBAC, rely on repository permissions** — works for the git
  backend only, and gives the shared installation nothing.

## Verification
Tests assert viewers cannot edit, editors cannot delete or manage members,
non-members see nothing at all, a workspace always keeps an owner, publishing
requires the permission, and `whoami` reports exactly the permissions the role
grants.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Five workspace roles, deny by default, identity from outside. |
