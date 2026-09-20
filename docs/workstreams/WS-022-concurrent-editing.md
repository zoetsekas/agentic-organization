---
id: WS-022
title: Concurrent editing — locks, versions and merge
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Product]
scope: [designer]
decisions: [ADR-0033]
depends_on: [WS-020]
tags: [designer, concurrency]
---

# WS-022: Concurrent editing — locks, versions and merge

## Objective
Let several people work on one agentic system without losing anyone's work and
without blocking the design on whoever opened it first.

## Deliverables
- Optimistic versioning on every write, with the base version stated.
- Advisory, expiring locks at system and component scope, with break control.
- Three-way structural merge keyed by `id`, with conflicts reported not guessed.
- Resolution round-trip: choose per conflict, resend, merge.
- Layout merge that keeps the saver's positions and adopts new nodes.

## Scope
In: concurrent editing of a design. Out: real-time collaborative editing with
live cursors.

## Approach
Layer the three mechanisms so each covers the others' failure mode: versions
catch everything, locks prevent most collisions, merging resolves the rest.
Make the conflict report carry both values so a person can choose rather than
read a diff.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Versions and revisions | Phase 3 | Done |
| M2 Locks with expiry and break | Phase 3 | Done |
| M3 Structural merge and resolution | Phase 3 | Done |
| M4 Presence — who else has this open | Phase 3 | Not started |
| M5 Live collaborative editing | Phase 4 | Not started |

## Dependencies
WS-020 for revisions, which the merge needs as the common ancestor.

## Advantages
- No silent data loss: stale writes merge or report.
- Component locks make parallel work on one design normal rather than risky.
- Expiring locks mean the common failure resolves itself.

## Disadvantages
- The merge understands the shapes we taught it; unkeyed object lists fall back
  to a conflict, which will look arbitrary to the person who hits it.
- Two valid changes can merge into something neither person intended; only
  validation catches that, and only if it is a rule we check.
- Conflicts are modal interruptions, and people click through modals.
- Without presence (M4), the first sign that someone else is editing is a
  conflict.

## Exit criteria
- Independent edits never require a human decision. ✔
- Same-field edits always require one. ✔
- A person can see who else has a design open before they collide (M4).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Versions, locks and merge landed; presence pending. |
