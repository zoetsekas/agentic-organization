---
id: WS-023
title: Drag-and-drop canvas designer
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Product
contributors: [Developer Experience, Platform Architecture]
scope: [designer, ui]
decisions: [ADR-0034]
depends_on: [WS-020, WS-021, WS-022]
tags: [designer, ui]
---

# WS-023: Drag-and-drop canvas designer

## Objective
Let an architect build an agentic system by dragging components onto a canvas
and filling in forms, with the design validating as they work.

## Deliverables
- Component palette served from the backend and derived from the spec model.
- Drag-and-drop placement that attaches a component to what it was dropped near.
- Node dragging with grid snapping, selection and removal.
- An inspector rendering typed forms per component kind, including the
  many-to-many human pairing editor.
- SVG edges derived from the spec, never stored.
- Live validation beside the canvas; version, lock and conflict indicators.
- Workspace, system, history, people and settings controls in the toolbar.

## Scope
In: the canvas editor and its forms. Out: auto-layout, swimlanes and
annotations, and anything the canvas cannot express (the YAML path remains).

## Approach
Edit the spec directly and keep layout beside it. Derive every drawn edge from
the spec so the picture cannot lie. Serve the palette from the model so the UI
cannot offer something that would fail validation.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Palette, drop, drag, inspector | Phase 3 | Done |
| M2 Derived edges and live validation | Phase 3 | Done |
| M3 Locks, versions, conflict resolution in the UI | Phase 3 | Done |
| M4 Auto-layout and grouping frames | Phase 3 | Not started |
| M5 Full spec coverage in forms | Phase 3 | In progress |

## Dependencies
WS-020, WS-021 and WS-022 — the canvas is a client of all three.

## Advantages
- Domain owners can author a system without writing YAML.
- The diagram always matches what would compile.
- Validation appears while designing rather than at save time.

## Disadvantages
- The canvas covers a subset of the spec, and users will find the boundary by
  hitting it (M5).
- No auto-layout, so a large system dropped by hand looks like it (M4).
- Visual affordances people expect — grouping, annotations — have nowhere to
  live except layout, which the compiler ignores.
- Deleting a component must clean up its node; a bug there leaves orphans.

## Exit criteria
- Every spec block is editable from the canvas or an attached form (M5).
- A design authored on the canvas compiles without touching YAML. ✔

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Palette, canvas, inspector, locks and conflicts landed. |
