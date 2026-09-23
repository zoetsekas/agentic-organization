---
id: ADR-0100
title: A canvas per aspect, and layouts that actually place things
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Design, Runtime Engineering]
informed: [All engineering]
scope: [designer]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0034, ADR-0081, ADR-0096, ADR-0099]
tags: [designer, canvas, layout, workflows]
---

# ADR-0100: A canvas per aspect, and layouts that actually place things

## Context
The designer has one kind of canvas. `Diagram` already supports many *views* —
each with a `root`, which is what makes a team's own diagram nest inside the
organisation's — but every one of them draws the same thing: units containing
agents, with edges derived from the reporting structure.

Two consequences.

**A process cannot be drawn.** ADR-0096 gave workflows a real editor, and it
lives in the inspector: a preview, a list of steps and a list of edges. That
is a large improvement on not being able to express a process at all, and it
is not a canvas. Nobody wires a graph by picking ids out of two dropdowns if
they can drag a line instead, and the tools people compare this to — LangGraph
Studio among them — put the graph on the canvas because the graph *is* the
artifact.

**Nothing places anything.** There is no layout algorithm in the product. The
screenshot script lays the example out by walking the tree and assigning a
column per depth and a slot per sibling, which cascades diagonally: by twelve
agents the organisation runs off the right edge of every capture taken so far.
A reader's first impression of a design is where its boxes are, and right now
that is decided by whoever dragged them last.

## Decision

### A diagram declares what it is
`Diagram` gains a `kind`. `organisation` is what every existing diagram is and
remains the default, so nothing migrates. `process` is a workflow's graph: its
`root` is the workflow id, its nodes are the steps, and its edges are the
workflow's own.

**The edge rule does not bend.** A diagram stores no edges, because a stored
edge is a second source that can disagree with the spec. On a process canvas
the edges are the workflow's `graph.edges` — still read from the spec, still
never stored in the layout. Drawing a line on the canvas *writes an edge into
the workflow*, which is the same act as typing it in the inspector, and the
picture continues to be a rendering of the model rather than a parallel copy
of it.

A kind is a claim about what a canvas can contain, so it is checked: a
`process` diagram whose root is not a workflow is refused, and so is an
`organisation` diagram that names one.

### Layout is computed, server-side, from named algorithms
Three, each chosen because a shape needs it, and computed in Python rather
than in the canvas:

- **`tree`** — an organisation. Parents centred over their children, siblings
  packed left to right, depth down the page. This is the one that replaces the
  diagonal cascade, and it is the default for `organisation`.
- **`layered`** — a process. Each step ranked by its longest path from the
  entry, ranks laid out down the page, a back edge excluded from ranking so a
  cycle does not push its own target below it. The default for `process`.
- **`grid`** — a flat collection with no useful structure to honour, which is
  what a set of policies or data classes is. Packed in reading order.

Server-side because it is then testable the way the rest of this platform is
testable: a layout is a function from a graph to coordinates, and "no two nodes
overlap" and "a child is below its parent" are assertions. A layout living
only in the canvas is a layout nothing checks.

**Arranging is an edit like any other.** It moves nodes, so it goes through the
same dirty-marking and undo the canvas already has. A layout that could not be
undone would make people afraid of the button.

## Scope
`Diagram.kind`; a layout module and the route that serves it; the canvas
rendering and editing a process diagram. No change to the spec, to the IR, to
any target, or to how edges are derived.

## Implementation
- `DiagramKind` on `Diagram`, defaulting to `organisation`, and validation of
  the root against it.
- `orgagents/designer/layout.py`: `tree`, `layered`, `grid`, behind
  `arrange(kind, graph, algorithm)`.
- `POST /api/designer/layout` returning positions for a set of nodes and edges.
- The canvas draws a process diagram's steps and the workflow's edges, and an
  Arrange control.

## Timeline
Delivered with this ADR.

## Advantages
- A process is drawn where a process belongs, and the inspector editor stays
  for the things a canvas is bad at — a branch's predicates, a step's
  arguments.
- The picture a reader first sees is computed rather than inherited from
  whoever last dragged a box.
- Layout becomes testable, which is the only way it stays correct.
- The model/representation split holds: one model, many diagrams, no stored
  edges.

## Disadvantages
- **Three algorithms is a choice, and somebody's graph will suit none of
  them.** A dense many-to-many process will look worse under `layered` than a
  hand arrangement, and the honest answer is to drag it.
- **Arrange discards a hand arrangement.** It is undoable, and it will still
  catch somebody out the first time.
- **A second canvas kind is a second thing to keep working.** Every canvas
  feature now has to ask which kinds it applies to, and some will get it wrong.
- **Server-side layout needs a round trip**, so arranging a large diagram is
  not instant, and the canvas has to stay usable while it is in flight.
- **Node sizes are assumed, not measured.** The server places boxes it cannot
  see, so a long label can still overlap its neighbour.

## Alternatives considered
- **Lay out in the canvas, in JavaScript.** Rejected: it is the only part of
  this platform that would then be untested, and layout is exactly the kind of
  code that rots silently because a bad result still renders.
- **Adopt a layout library (dagre, elk).** Considered, and the right answer if
  the shapes get harder. Rejected for now because three algorithms of forty
  lines each are readable, dependency-free and enough for a tree and a
  pipeline, and because a library's output is no more testable than ours.
- **Store edges on a process diagram.** Rejected: it is the one rule the
  diagram model has, and a process is exactly where a second copy would drift.
- **One canvas that changes behaviour based on what is selected.** Rejected as
  the thing that produces a canvas nobody can predict.

## Verification
- A diagram declares its kind, and an existing diagram loads as `organisation`.
- A `process` diagram whose root is not a workflow is refused, and an
  `organisation` diagram naming a workflow is refused.
- `tree` places every child below its parent and overlaps nothing.
- `layered` ranks a pipeline in order, and a back edge does not push its target
  below its source.
- `grid` places a flat collection in reading order without overlap.
- Every algorithm is deterministic: the same graph twice gives the same
  coordinates.
- A process diagram's edges come from the workflow and are not stored in the
  layout.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. Diagrams declare a kind; a process canvas draws a workflow's graph with edges still derived from the spec; three named layout algorithms computed server-side and tested. |
