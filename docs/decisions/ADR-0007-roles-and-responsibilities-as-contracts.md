---
id: ADR-0007
title: Roles and responsibilities are first-class, assignable contracts
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Product, Security Engineering]
informed: [All engineering]
scope: [spec, security, runtime]
workstreams: [WS-003, WS-004]
supersedes: []
superseded_by: []
related: [ADR-0006, ADR-0008, ADR-0010]
tags: [organization, security]
---

# ADR-0007: Roles and responsibilities are first-class, assignable contracts

## Context
"What is this agent for?" and "what may this agent do?" are different questions
that systems routinely conflate. Prompt text answers the first and binds
nothing; an IAM policy answers the second and explains nothing. When they are
separate artifacts they drift, and the prompt ends up describing work the agent
has no permission to perform — or worse, the permissions outlive the purpose.

Teams have responsibilities too, and those are not the sum of their members'.

## Decision
A **role** is a named, versioned, reusable contract with three parts:

1. **Responsibilities** — what the holder is accountable for, in prose, which
   compiles into the agent's operating instructions;
2. **Capabilities** — the abstract capabilities the role needs (ADR-0010);
3. **Permissions** — the permission set the role grants (ADR-0008).

Roles are defined once and assigned to agents and to teams. An **agent role**
applies to one agent; a **team role** applies to the team as a unit and is
inherited by its members unless the member's own role narrows it. A role
assignment may be further constrained at the assignment site, never widened.

Responsibilities and permissions therefore ship together: you cannot grant the
capability without stating the accountability, and a permission with no
responsibility naming it is a validation warning.

## Scope
The spec's `roles` block, assignment in `organization`, resolution in the IR,
and prompt composition in the runtime. Does not cover human RBAC in the UI —
that is platform access, a separate concern.

## Implementation
`spec.model.Role` (`id`, `version`, `kind: agent|team`, `responsibilities`,
`capabilities`, `permissions`, `constraints`). `compiler.ir` resolves each
agent's effective role as: team role (inherited) ∩ assignment constraints ∪ own
role, with permissions resolved per ADR-0008 and responsibilities concatenated
into the generated system prompt with provenance.

## Timeline
Phase 1, with the organization model.

## Advantages
- One artifact answers both "what for" and "what may"; they cannot drift apart.
- Roles are reusable, so a fleet of analyst agents is defined once.
- Least privilege becomes reviewable in business terms, not just policy JSON.
- Generated prompts carry provenance — every instruction traces to a role.

## Disadvantages
- Role proliferation: teams will create near-duplicate roles rather than reuse,
  and the catalog becomes unmanageable without curation.
- Resolving inheritance plus constraints is genuinely subtle; users will be
  surprised by effective permissions at least some of the time.
- Prose responsibilities are unverifiable — nothing checks that an agent
  actually does what its role claims.
- Versioning roles independently of agents adds a second upgrade problem.

## Alternatives considered
- **Prompts only** — no enforceable permissions, no reuse.
- **Permissions only** — nothing explains intent, and reviews become mechanical.
- **Per-agent inline definitions** — no reuse and no way to audit "who holds
  this capability" across the organization.

## Verification
Tests assert constraint-only narrowing (an assignment cannot widen a role), the
inheritance resolution order, and that every granted permission is referenced by
at least one responsibility (warning-level, reported by `spec validate`).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Roles bind responsibilities, capabilities and permissions. |
