---
id: ADR-0039
title: Missions are short-lived teams drawn from the standing organization
status: Accepted
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Product]
consulted: [Security Engineering]
informed: [All engineering]
scope: [spec, compiler, runtime]
workstreams: [WS-025]
supersedes: []
superseded_by: []
related: [ADR-0006, ADR-0008, ADR-0024, ADR-0026]
tags: [organization]
---

# ADR-0039: Missions are short-lived teams drawn from the standing organization

## Context
ADR-0006 models the organization as a tree of teams, and that tree is right
about accountability and wrong about work. Org charts change yearly; actual
work is organized into task forces that form on Monday and disband in six
weeks. "Rebuild the Q4 forecast after the pipeline restatement" is a real team
with a real leader — and it is not a reorganization, so it must not be one.

Forcing this into the org tree gives two bad outcomes: either the tree churns
weekly and stops describing accountability, or the work happens informally and
nothing records who was on it, what it was for, or when it ended.

## Decision
A **mission** is a short-lived team drawn from the standing organization. It
has a name, an **objective**, **deliverables**, success criteria, a **leader**,
members referenced by agent id, a human sponsor, a start and — always — an
**end date**. Members keep their home team, their reporting line and their own
permissions.

Five rules make it a team rather than a label:

1. **Every mission has a leader**, and the leader is one of its members.
2. **Every mission ends.** A mission without an end date is refused: one that
   never ends is a reorganization and belongs in the org chart. Past roughly
   two quarters, the validator says so.
3. **A mission never grants access.** Roles assigned to a mission are
   **intersected** with what each member already holds. Something a member
   lacks is dropped and reported, never granted (ADR-0008).
4. **A mission's reach expires with it.** The window is enforced where the
   delegation happens, not where the spec was written: the compiler records
   each member's mission peers *with the window they are good for*, and the
   runtime re-checks that window on every delegation. A mission left `active`
   past its end date confers nothing, whether or not anybody closed the record.
   Sweeping the record (`orgagents missions sweep`) is tidiness, not the
   control.
5. **A mission never inverts the hierarchy.** Members may work laterally with
   each other for the duration; the mission *leader* may task members, and
   nobody gains the ability to task their own leader — in the mission or in the
   standing organization.

Missions are declared in the spec alongside the organization, resolved into the
IR, named in each member's prompt with the end date, and listed in the registry
with their window and deliverables.

## Scope
Temporary working arrangements between existing agents. It does not create
agents, change reporting lines, or alter anyone's permissions.

## Implementation
`spec.model.Mission` with `MissionStatus`; validation covers leader membership,
members existing in the organization, objective and end date, sane dates,
duration, channel and workflow references, and the intersection rule.
`compiler.ir` resolves `MissionIR` with the per-member granted intersection and
computes each agent's mission peers, excluding the mission leader and the
member's own management chain. Those peers are carried as `MissionGrantIR`
entries — peers plus status, start and end — separately from
`standing_delegates_to`, so the runtime can tell borrowed reach from permanent
reach. `orgagents.missions` holds the window arithmetic; `OrgChart.can_delegate`
consults it on every call, `orgagents missions list|sweep` inspects and closes
records, and the validator warns on an open mission past its end date. The
registry gains a missions table and flags any mission with no end date.

## Timeline
Phase 4.

## Advantages
- Cross-functional work is designed and recorded instead of improvised.
- The org chart stays a picture of accountability while work moves weekly.
- Every mission has one leader, an objective and a date it stops.
- Lateral collaboration is granted for a bounded time and then simply expires.
- Intersection means a mission cannot become a permission side-door.

## Disadvantages
- A second structure to keep current: a finished mission left `active` is a
  stale record that misreports the organization, even though it no longer
  confers anything.
- Delegation now depends on the clock, so the same call is allowed on Friday
  and refused on Monday. That is the point, and it makes a refusal harder to
  diagnose from the spec alone.
- Sweeping is manual: nothing runs it on a schedule, so closing records
  remains somebody's job.
- Membership in several missions makes an agent's effective reach harder to
  read than the tree alone, and only the registry shows the whole picture.
- The intersection rule silently drops permissions a mission asked for, which
  is safe and can look like the mission "not working" to whoever wrote it.
- Nothing measures whether a mission achieved its deliverables; success
  criteria are declared and unverified.

## Alternatives considered
- **Put task forces in the org tree** — churns the structure that exists to be
  stable, and conflates work with accountability.
- **Model missions as agents** — creates ephemeral org members with no
  accountable human, which ADR-0027 rejected for sub-agents too.
- **Let missions grant permissions** — convenient and exactly the side-door
  ADR-0008 exists to prevent.
- **Informal collaboration, no model** — what happens without this: no record
  of who was on it, what it was for, or that it ended.

## Verification
Tests cover missions crossing team boundaries, leader membership, refusal of a
mission with no leader, no objective, no end date, bad dates or unknown
members, the too-long warning, the intersection rule dropping rather than
granting, mission peers appearing in delegation, a completed mission conferring
nothing, and — specifically — that no member gains the ability to task its own
leader while the leader may task members. Runtime expiry is covered end to end:
a loaded system allows the lateral call inside the window, refuses it the day
after the mission ends and the day before it starts, leaves standing reporting
lines untouched either way, sweeps idempotently, and reports an overdue open
mission from the validator.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Mission reach expires at runtime: grants carry their window, `can_delegate` re-checks it per call, and `missions sweep` closes stale records. |
| 1.0.0 | 2026-09-20 | Accepted. Missions with leaders, end dates, intersected roles, no hierarchy inversion. |
