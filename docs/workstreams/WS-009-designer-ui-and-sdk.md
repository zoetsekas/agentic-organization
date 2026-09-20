---
id: WS-009
title: Designer UI and SDK as peer clients of the spec
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Product
contributors: [Developer Experience, Platform Architecture]
scope: [ui, sdk]
decisions: [ADR-0018, ADR-0003]
depends_on: [WS-002]
tags: [product, devex]
---

# WS-009: Designer UI and SDK as peer clients of the spec

## Objective
Let architects design visually and engineers design in code, on the same system,
with neither path second-class and no lossy conversion between them.

## Deliverables
- A typed Python SDK that builds and validates a System Spec.
- Designer UI views: organization (teams and leaders), agent and harness
  definition, roles and permissions, environment classes, marketplace, sessions,
  operations.
- Spec API endpoints shared by both clients, with one validator.
- Round-trip tests (SDK → spec → UI payload → spec) asserting stability.
- A record-graph view surfacing ADRs and workstreams beside the design.

## Scope
In: authoring surfaces for the spec. Out: operational views' SDK parity — the
plain API is their equivalent — and visual layout metadata beyond what the spec
can carry.

## Approach
Ship spec features first, then expose them in both clients; treat a capability
reachable from only one client as a defect. Derive the UI's component palette
from the spec schema so it cannot drift from what the spec accepts.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 UI over the current model | Phase 1 | Done |
| M2 SDK builder over the spec | Phase 2 | In progress |
| M3 UI migrated to spec-backed editing | Phase 2 | Not started |
| M4 Round-trip stability tests | Phase 2 | Not started |
| M5 Record-graph view | Phase 3 | Not started |

## Dependencies
WS-002 for the spec both clients edit.

## Advantages
- One authoring model; mixed teams collaborate on the same artifact.
- Specs live in git with review and CI however they were authored.
- Shared validation means both clients reject the same mistakes.

## Disadvantages
- Parity is a permanent tax; shipping is gated on the slower client.
- The UI is confined to what the spec can express, so purely visual affordances
  have nowhere to live without polluting the spec.
- Lossless YAML round-tripping (comments, ordering) has fiddly edge cases that
  will generate bug reports for a long time.

## Exit criteria
- A spec authored in either client opens and re-saves in the other unchanged.
- No spec feature is reachable from only one client.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. UI shipped over the runtime model; spec-backed editing next. |
