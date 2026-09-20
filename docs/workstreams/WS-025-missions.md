---
id: WS-025
title: Missions — short-lived teams
status: Active
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
owner: Product
contributors: [Platform Architecture]
scope: [spec, compiler, runtime, ui]
decisions: [ADR-0039]
depends_on: [WS-003]
tags: [organization]
---

# WS-025: Missions — short-lived teams

## Objective
Let an organization form a task force from people it already has: a named
objective, deliverables, a leader, a start and an end — without reorganizing
the standing structure or handing anyone new access.

## Deliverables
- `Mission` in the spec, with status, leader, members, sponsor, window,
  deliverables and success criteria.
- Validation: leader membership, members from the organization, objective and
  end date, sane and bounded dates, references, and the intersection rule.
- IR resolution with per-member granted intersection and mission peers that
  never invert the hierarchy.
- Mission membership in the composed prompt, with the end date.
- A missions table in the registry, flagging any mission with no end.
- Runtime expiry: grants carry their window, the delegation gate re-checks it
  on every call, and `orgagents missions list|sweep` inspects and closes
  records.

## Scope
In: temporary working arrangements between existing agents. Out: creating
agents, changing reporting lines, granting permissions, and measuring whether a
mission succeeded.

## Approach
Keep missions strictly additive to the org model and strictly non-granting.
Intersect rather than union so a mission can never be a permission side-door,
and compute lateral reach so that a member never gains the ability to task
their own leader.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Spec model and validation | Phase 4 | Done |
| M2 IR resolution and prompt composition | Phase 4 | Done |
| M3 Registry reporting | Phase 4 | Done |
| M4 Runtime expiry — a past end date stops conferring reach | Phase 4 | Done |
| M5 Mission view on the canvas | Phase 4 | Not started |
| M6 Deliverable tracking against success criteria | Phase 4 | Not started |

## Dependencies
WS-003 for the standing organization missions draw from.

## Advantages
- Cross-functional work is recorded rather than improvised.
- The org chart stays stable while work moves weekly.
- Lateral reach is bounded by a date rather than by memory.

## Disadvantages
- A finished mission left `active` is a stale record that misreports the
  organization, even though it no longer confers reach.
- Delegation depends on the clock now, so the same call is allowed one day and
  refused the next — correct, and harder to diagnose from the spec alone.
- Sweeping is manual; nothing runs it on a schedule.
- An agent on several missions has an effective reach that only the registry
  shows in full.
- The intersection rule silently drops permissions a mission asked for; safe,
  and it can read as the mission "not working".
- Success criteria are declared and unverified (M6).

## Exit criteria
- ~~A mission past its end date confers nothing, automatically~~ — met (M4).
- A mission is visible and editable on the canvas (M5).
- Deliverables can be marked met, and the mission closed against them (M6).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | M4 done: mission grants carry their window, `can_delegate` enforces it per call, `missions sweep` closes stale records. |
| 1.0.0 | 2026-09-20 | Opened. Model, validation, IR and registry landed; runtime expiry pending. |
