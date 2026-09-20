---
id: WS-031
title: Task intake — human-assigned work
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Product
contributors: [Platform Architecture]
scope: [spec, runtime, security, docs]
decisions: [ADR-0057]
depends_on: [WS-016, WS-013]
tags: [integration]
---

# WS-031: Task intake — human-assigned work

## Objective
Let the people paired with an agent give it work in the tools they already use,
and follow that work to a finished result — without committing the runtime to a
product whose identity model we have not confirmed.

## Deliverables
- A `TaskPort` protocol: list, claim, progress, complete, fail, comment.
- A `TaskRecord` holding what a backend cannot be relied on to carry: agent,
  session, tenant, mission, approval state.
- A local reference backend over the existing `Store`, exercising the whole
  port without a vendor.
- A conformance suite every adapter must pass.
- Bind-time refusal for a backend that cannot give an agent its own identity.
- Divergence reporting between task state and session state.

## Scope
In: the port, its semantics, and one reference implementation. Out: choosing a
product, and building a task manager — if the reference backend grows a board,
we have gone wrong.

## Approach
Port first, product later. The research (`docs/TASK_SERVICES.md`) could not
verify the decisive capability of the leading candidate, so the decision waits
for evidence while the work that does not depend on it proceeds.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Port, record and local backend | Phase 5 | Not started |
| M2 Conformance suite | Phase 5 | Not started |
| M3 Runtime integration — a task becomes a run, the session id returns | Phase 5 | Not started |
| M4 Divergence reporting | Phase 5 | Not started |
| M5 Identity verification against a real product | Phase 6 | Not started |
| M6 First real adapter | Phase 6 | Not started |

## Dependencies
WS-016 for the pairing model that decides who may assign work, WS-013 for the
channels that notify about it.

## Advantages
- The unverifiable decision is deferred at no cost.
- Humans get a delegation primitive that triggers and channels cannot provide.
- The identity requirement is enforced at a boundary rather than found during
  an incident.

## Disadvantages
- A port with one implementation proves less than it looks like; the first real
  adapter will move its shape.
- The local backend is a temptation, and growing it is how we would accidentally
  write a bad task manager.
- Until an adapter exists, nobody can assign work from a tool they already use,
  so the feature is invisible to its users.
- Rule 2 of ADR-0057 may disqualify the candidate with the best MCP story.
- Surfacing divergence rather than resolving it makes work for a human, and an
  unattended deployment will pile up conflicts nobody reads.

## Exit criteria
- The port is exercised end to end by a reference backend and a conformance
  suite (M1, M2).
- A task produces exactly one run and carries its session id back (M3).
- Divergence is reported, never silently reconciled (M4).
- A real product's identity model is confirmed by standing it up (M5), before
  any adapter is written (M6).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened alongside ADR-0057. |
