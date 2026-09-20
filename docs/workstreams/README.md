# Workstream Records

An ADR says *what was decided*. A **workstream record** says *how that decision
gets delivered*: the objective, the deliverables, the owner, the milestones and
the exit criteria. Together they answer the same six questions from two sides —
decision and delivery — and they cross-link, so from any decision you can reach
the work implementing it and back again.

## Identifiers

`WS-nnn`, allocated in order, never reused. Filename `WS-nnn-kebab-title.md`.
A workstream that grows a new phase gets a minor version bump; work that is
genuinely different gets a new workstream, and the old one closes as `Complete`
or `Cancelled` with the reason in its changelog.

## Lifecycle

| Status | Means |
|---|---|
| `Proposed` | Scoped, not yet resourced. |
| `Active` | Being worked now. |
| `Blocked` | Waiting on a dependency; the blocker is named in `Dependencies`. |
| `Paused` | Deliberately stopped; may resume. |
| `Complete` | Exit criteria met. |
| `Cancelled` | Abandoned; the changelog says why. |

## Versioning

Same semver discipline as ADRs, over the *plan*: patch for wording, minor for
added deliverables or milestones, major when the objective itself changes —
which usually means it should be a new workstream instead.

## Front matter

```yaml
---
id: WS-004
title: Security model — RBAC, policy and least privilege
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Security Engineering        # WHO is accountable for delivery
contributors: [Platform Architecture]
scope: [spec, security, targets]   # WHERE the work lands
decisions: [ADR-0007, ADR-0009]    # ADRs this workstream delivers
depends_on: [WS-002]
tags: [security]
---
```

## Required sections

`Objective` (why) · `Deliverables` (what) · `Scope` (where) · `Approach` (how) ·
`Milestones` (when) · `Dependencies` · `Advantages` · `Disadvantages` ·
`Exit criteria` · `Changelog`.

`Disadvantages` on a workstream is the honest cost of *doing the work this way*:
the parallel effort, the throwaway scaffolding, the team it blocks. Reviewers
should push back on an empty one.

## Working with records

```bash
orgagents records validate
orgagents records index
orgagents records new ws "Title of the workstream"
```

Index: [index.md](index.md) · Template: [_template.md](_template.md) ·
Decisions: [../decisions/index.md](../decisions/index.md)
