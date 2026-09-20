---
id: ADR-0034
title: The canvas is drag-and-drop over the spec, and layout is never part of it
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Product, Platform Architecture]
consulted: [Developer Experience]
informed: [All engineering]
scope: [designer, ui, spec]
workstreams: [WS-023]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0018, ADR-0031]
tags: [designer, ui]
---

# ADR-0034: The canvas is drag-and-drop over the spec, and layout is never part of it

## Context
Architects and domain owners do not author YAML. They draw: a team here, its
agents under it, the capability they share on the right. The designer needed a
canvas — drag a component from a palette, drop it where it belongs, fill in a
form.

The trap is obvious once stated. If node positions end up in the spec, two
specs that deploy identically differ byte-for-byte because someone moved a box,
every diff is noise, and the implementation-neutral document (ADR-0004) now
carries pixels.

## Decision
The canvas edits the **spec document directly** — dropping an Agent adds an
agent to a team, and the inspector form edits that agent's fields — while
**layout lives in a separate `Layout`** on the system record: node positions,
sizes and the viewport.

Two consequences follow:

* **Edges are derived, not stored.** Lines are computed from the spec —
  membership, sub-agents, declared flows, triggers — so the picture always
  matches what would compile. You cannot draw a relationship that is not real.
* **A dropped component attaches to what it was dropped near.** An agent
  dropped on a team joins that team. Position carries intent, even though
  position is not part of the design.

The palette is served by the backend and derived from the spec model, so it
cannot drift from what validates. Live validation runs on every open, and shows
errors and warnings beside the canvas rather than at save time.

## Scope
The canvas editor and the layout it persists. It does not replace the YAML
path: the SDK and a text editor remain first-class (ADR-0018).

## Implementation
`web/canvas.js` with an HTML5 drag-and-drop palette, absolutely positioned
nodes, an SVG edge layer computed from the spec, and an inspector rendering
forms from the backend's palette definition. `designer/models.py` holds
`Layout`, `CanvasNode` and `CanvasEdge`. Layout merges by keeping the saver's
positions and adopting the other side's for nodes they added.

## Timeline
Phase 3.

## Advantages
- Domain owners can author a system without writing YAML.
- Derived edges mean the diagram cannot lie about the design.
- Specs stay free of presentation, so diffs stay meaningful.
- The palette comes from the model, so the UI cannot offer what would not
  validate.

## Disadvantages
- Two documents to keep aligned: deleting a component must clean up its node,
  and a bug there leaves orphans on the canvas.
- Purely visual affordances people expect — grouping frames, swimlanes,
  annotations — have nowhere to live except layout, which the compiler ignores.
- Auto-layout is absent: a large system dropped by hand looks like it was.
- The canvas exposes a subset of the spec; anything richer still needs the YAML,
  and users will discover that boundary by hitting it.

## Alternatives considered
- **Put layout in the spec under an ignored key** — simplest, and it puts
  pixels in the artifact we compile, review and sign.
- **A diagram that only visualizes, with forms elsewhere** — safer and much
  less useful; drawing is the point.
- **Free-form drawing, generate the spec afterwards** — inverts the source of
  truth and makes the diagram authoritative over the thing that deploys.

## Verification
The canvas is driven end to end in a browser: components dragged from the
palette onto the canvas create real spec objects, edges appear from the spec,
the inspector edits fields, validation updates live, and the result saves and
reloads. A test asserts layout is persisted and that no layout data reaches the
spec.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Canvas edits the spec; layout stays separate; edges are derived. |
