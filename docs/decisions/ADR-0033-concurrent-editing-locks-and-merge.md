---
id: ADR-0033
title: Concurrent editing uses optimistic versions, advisory locks and three-way merge
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Product, Developer Experience]
informed: [All engineering]
scope: [designer]
workstreams: [WS-022]
supersedes: []
superseded_by: []
related: [ADR-0031, ADR-0032, ADR-0034]
tags: [designer, concurrency]
---

# ADR-0033: Concurrent editing uses optimistic versions, advisory locks and three-way merge

## Context
Several people will edit one agentic system, and the two obvious answers are
both bad. **Last write wins** silently destroys work, and the person who lost
it usually finds out weeks later. **Pessimistic locking alone** blocks the
whole design on whoever opened a tab and went to lunch.

The good news is that a spec is structured data, not prose: teams, agents,
roles and capabilities are keyed by `id`. Two people adding different agents to
the same team is not a conflict, and a system that reports one is training its
users to click through warnings.

## Decision
Three mechanisms, layered:

1. **Optimistic versions.** Every system carries a version; every write states
   the version it was based on. A stale write is never applied silently.
2. **Advisory, time-boxed locks**, at whole-system or **component** scope. A
   component lock is what makes concurrent work pleasant: two people editing
   different agents never collide. Locks expire, so an abandoned tab does not
   freeze a design, and breaking one requires `lock.break`.
3. **Three-way structural merge.** When a write is stale, the service merges
   against the common ancestor revision. Lists keyed by `id` merge by identity,
   so independent additions and edits to different fields both survive.

**What cannot be merged is reported, never guessed.** A conflict names the
path, the ancestor value and both sides. Nothing is written until a person
chooses, and the choices are sent back with the retry.

Canvas layout merges differently and deliberately: the saver's positions win
and the other side's positions are kept for nodes they added. Layout is
presentation (ADR-0034), and a position is not worth a conflict dialog.

## Scope
Concurrent editing inside the designer. It does not address real-time
collaborative editing (multiple cursors), which is a different product.

## Implementation
`designer/merge.py` for the structural merge and resolution application;
`designer/locks.py` for acquire, heartbeat, release, break and expiry;
`DesignerService.save_system` sequences them: check lock, attempt the write,
merge on version conflict, return conflicts if any remain. The merge strategy
is a setting, so an installation can choose to reject instead.

## Timeline
Phase 3, with the canvas.

## Advantages
- No silent data loss: every stale write is either merged or reported.
- Component locks let a team work on one design at the same time.
- Locks expire, so the common failure (a closed tab) resolves itself.
- Conflicts arrive with both values and a choice, not a diff to read.

## Disadvantages
- Structural merge understands the shapes we taught it; an unkeyed list of
  objects falls back to a conflict, which will feel arbitrary when it happens.
- Merging is not free of surprises: two people can each produce a valid change
  that merges into something neither intended, and only validation catches it.
- Component locks are advisory and coarse — they do not stop a whole-system
  save from touching a locked component, the version check does.
- Every conflict is a modal interruption, and people click through modals.

## Alternatives considered
- **CRDTs / operational transforms** — real-time collaboration without
  conflicts, at a large cost in complexity and in explainability when something
  goes wrong. A design document is not a text editor.
- **Locking only** — predictable, and the lunch problem is real.
- **Optimistic only** — every collision becomes the user's manual re-work.
- **Git-style branch and PR** — excellent for engineers, foreign to the domain
  owners the designer is for.

## Verification
Tests cover component locks allowing parallel work, system locks blocking other
saves, expiry, release and break permissions, independent edits merging, the
same field conflicting rather than guessing, resolution by choosing, the reject
strategy, and that a refused write never loses the other edit. The browser path
is exercised end to end: two users, a real conflict, resolution, merged result.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Versions, advisory component locks, structural three-way merge. |
