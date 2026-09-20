---
id: WS-011
title: Landscape research and the capability roadmap
status: Complete
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Product
contributors: [Platform Architecture]
scope: [docs, process]
decisions: [ADR-0019]
depends_on: [WS-001]
tags: [research]
---

# WS-011: Landscape research and the capability roadmap

## Objective
Establish what comparable systems actually do well, decide which of those
capabilities our designer must have to be credible in an enterprise, and record
what we decline — so the roadmap is an argued position rather than a list of
features competitors happen to ship.

## Deliverables
- `docs/LANDSCAPE.md`: per-system analysis (OpenClaw, Microsoft 365 Copilot /
  Copilot Studio / Agent 365, Claude Cowork, Agency Swarm, Swarms, LangGraph
  Platform, Slack/Teams HITL practice), a capability matrix, the resulting gap
  list and an explicit decline list, with sources.
- Seven decisions closing the identified gaps: ADR-0019 through ADR-0025.
- Five workstreams to deliver them: WS-011 through WS-015.

## Scope
In: capability comparison and the adopt/decline argument. Out: benchmarking,
pricing analysis, and any commitment to interoperate with a specific product.

## Approach
Research each system for what it is genuinely good at rather than feature-count
parity, then ask one question per capability: does a *compiler* need this in
the spec, or is it a runtime nicety? Anything a target cannot generate is a
structural gap; anything that only affects one runtime is not.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Research and capability matrix | Phase 2 | Done |
| M2 Gap decisions written (ADR-0019…0025) | Phase 2 | Done |
| M3 Delivery workstreams opened | Phase 2 | Done |
| M4 Re-run against products we could not identify | Phase 3 | Not started |

## Dependencies
WS-001 for the record process. Feeds WS-012 through WS-015.

## Advantages
- The roadmap states why each capability exists and what it costs.
- The decline list is as explicit as the adopt list, so scope creep has to
  argue against a written position.
- Sources are recorded, so the analysis can be re-run when products move.

## Disadvantages
- A snapshot of a market moving monthly; parts of it are stale on arrival.
- Research was done from public documentation and search, not from running each
  product, so depth is uneven and some claims are second-hand.
- One product named in the brief ("here message") could not be identified, so
  the matrix may have a hole nobody can see.
- Deciding from a competitor matrix risks building their product rather than
  ours; the decline list is the only guard, and it is a judgement call.

## Exit criteria
- Every gap in the matrix is either decided (an ADR) or explicitly declined. ✔
- Every resulting decision has a workstream. ✔

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Complete. Landscape, matrix, seven decisions, five workstreams. |
