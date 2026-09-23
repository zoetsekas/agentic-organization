---
id: ADR-0105
title: The canvas filters by relationship kind; every side panel resizes, minimises and maximises
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Design]
informed: [All engineering]
scope: [designer]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0101, ADR-0103]
tags: [designer, canvas, layout, accessibility]
---

# ADR-0105: The canvas filters by relationship kind; every side panel resizes, minimises and maximises

## Context
ADR-0101 predicted it: drawing every association makes a busy diagram busier,
and a design with thirty capabilities wants filtering by relationship kind.
It also turned out that most associations were not drawn at all — the canvas
drew containment, held components, flows, unit links and triggers by hand, so
a knowledge source linked to two agents (the request that started ADR-0101)
was linked in the model and invisible on the canvas.

Separately, the side panels on every screen were fixed-width columns: a long
Properties form or a deep explorer tree could not be given more room, nor put
away to give the canvas all of it.

## Decision

### Every drawable relationship is drawn, and tagged
The canvas draws every relationship the model's link rules mark as drawn as an
edge — a knowledge source to the agents that consult it, a role to what it
grants, a policy to its principals — alongside the ones it already drew in its
own way. Every edge carries its **UML kind** (composition, association, usage,
realization, dependency) and its **relationship**, and those drawn only from
the rules are styled by their kind and labelled.

### Filtering by kind, and by relationship within it
A strip above the canvas lists the UML kinds present on the open diagram, with
counts. Unticking a kind hides its edges; its list opens to untick single
relationships within it. Hiding an edge hides a line, never a link: the model
is unchanged, and the choice is the viewer's, remembered in this browser.

### Side panels, on every screen
Every screen's layout — the canvas and agents screens' three columns, and the
two-column screens — gives each side panel:

- a **resize** grip on its inner edge (double-click resets it);
- **minimise**, to a strip holding only its controls, giving the room to the
  rest of the screen;
- **maximise**, over the whole screen, and **restore**, to the width it had.

One panel is maximised at a time per screen. Choices are remembered per
screen and side in this browser. Below the width at which the layouts stack
into one column, there are no columns to size and the grips are not shown.

### A bug the filter exposed
A context menu opened near the bottom of the window put its items below the
fold, where they could not be clicked. Menus are now kept inside the window.

## Scope
Designer only: edges, the filter strip, the panel manager, the context menu.

## Implementation
- `web/canvas.js`: edges tagged with `uml` and `rel`; `modelEdges()` from the
  link rules; `renderEdgeFilter()`, `edgeShown()`; context menu clamped.
- `web/app.js`: `initPanels()` and the panel manager, over `.canvas-layout`,
  `.designer` and `.split`.
- `web/styles.css`: the filter strip and the panel controls, tokens only.

## Timeline
Delivered with this ADR.

## Advantages
- What the model holds is what the canvas can show, and a busy diagram can be
  thinned by meaning rather than by deleting boxes.
- Every screen's panels behave the same way.

## Disadvantages
- **More edges by default.** Every drawable relationship is now drawn; the
  filter is how a reader thins them.
- **Preferences are per browser.** Filter and panel choices are not shared
  between viewers or devices, by design: they are a way of looking.

## Alternatives considered
- **Filter per diagram, stored in the layout.** Rejected: it would make a way
  of looking into a change to the design that others receive.

## Verification
- `scripts/interaction_check.py`: edges tagged by UML kind; a knowledge source
  drawn to each agent that consults it; unticking a kind hides its edges,
  unticking one relationship hides only it; the canvas's left panel resizes,
  minimises and restores to its width; the right maximises over the screen and
  restores; the org chart's side panel minimises too.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. |
