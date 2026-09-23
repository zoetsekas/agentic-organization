---
id: ADR-0103
title: The designer is a view of the model — every gesture is one model operation
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Design]
informed: [All engineering]
scope: [designer, metamodel]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0019, ADR-0081, ADR-0100, ADR-0101, ADR-0102]
tags: [designer, canvas, model-driven, gestures]
---

# ADR-0103: The designer is a view of the model — every gesture is one model operation

## Context
ADR-0102 made the model's behaviour executable: constraints, operations with
UML's semantics, and scenarios that specify them. The designer still carried
its own copy of that behaviour. `applyLink` had a branch per relationship word,
each writing the spec its own way; deleting pruned lists by hand; dropping the
first team *replaced* the organisation. Each branch was a rule the model did
not know about, and two of them were wrong in ways only the model could see:
a new team was seeded with a mandate the model refuses, and replacing the
organisation silently discarded every collection it owns (ADR-0101).

A model-driven designer is specified against the model before it is built
further: what each gesture means is the model's, and the canvas only shows it.

## Decision

### Every gesture is one operation
`orgagents/designer/gestures.py` is the designer's specification: a catalogue
of gestures, each naming the one operation of `metamodel.operations` it sends
and what the canvas shows when the model accepts or refuses it. It is
**derived from the profile**, so a relationship added to the metamodel is a
gesture with no designer code:

| Gesture | Operation |
|---|---|
| drop a palette kind (onto its whole, when it is a part) | `create` |
| drag an existing part onto a whole | `link` on the composition — a move |
| drag a worker into / out of an environment box | `link` / `unlink` on the deployment |
| draw an edge between two boxes, from either end where the model allows | `link` |
| Delete on an edge / on a box | `unlink` / `delete` |
| edit a field in Properties; tick "Leads its team" | `update`; `set_leader` |

It is rendered as `docs/designer/gestures.md` (`orgagents designer gestures`)
and served at `GET /api/designer/gestures`.

### The canvas asks the model
`POST /api/designer/operations` takes the draft the canvas holds and one
operation request, and returns the model's answer: accepted, with the new draft
and every effect (the links a delete destroyed, a leadership a move ended), or
refused, with the violations. It is **stateless** — nothing is stored — so the
canvas keeps its draft, undo, locks and save exactly as before; what changes is
that the model, not the canvas, decides what an edit does. A refusal is a 200
with `accepted: false`; a request the model cannot read is a 422.

The draft comes back complete, defaults included: the canvas reads a flow's
kind whether or not its author wrote it.

### What conforms now, and what follows
Drawing a link and deleting a box go through the model. A relationship that
may be drawn from either end is offered from both — "Link from here" on a
knowledge source reaches the agents that consult it. The remaining gestures —
palette creation, drag-to-compose, deploy by nesting in a resizable environment
box, Properties edits — are specified and tested at the API; the canvas moves
them onto the operations in the next change, each against its row in the
catalogue.

## Scope
The gesture catalogue; the operations route; linking and deleting on the canvas
through it; the canvas seeding fixes the model exposed. No change to saving,
locking or versions.

## Implementation
- `orgagents/designer/gestures.py`: `gestures()`, `catalogue()`, `evaluate()`.
- `orgagents/metamodel/operations.py`: `update`, a generic `create` that finds
  its owner's composition in the profile, and `apply` for the JSON request.
- `orgagents/api.py`: `POST /api/designer/operations`, `GET /api/designer/gestures`.
- `web/canvas.js`: `modelOperation`, `applyLink` and `deleteNode` through it;
  `linkRules`/`legalTargetsFrom` in both directions; the team seed and the
  first-team merge.

## Timeline
Delivered with this ADR. The remaining gestures move onto the operations next;
then the transformation from the model to the physical representation.

## Advantages
- One source of rules: what the designer does is what the model says, tested
  once, in Python, and by the same scenarios.
- A new relationship is drawable without designer code.
- Deletions and moves say what else they changed, from the model, instead of
  doing it silently.

## Disadvantages
- **A round trip per gesture.** Linking now waits for the server. It is one
  small request on a local service; an offline canvas would need the model in
  the browser, which is not planned.
- **A stricter canvas.** A draft the model cannot read refuses every gesture
  until it is fixed, where the canvas used to write anything. That exposed two
  canvas bugs here, and will expose others.

## Alternatives considered
- **Persist each operation as a version.** Rejected: it would bypass the
  canvas's draft, undo and save, and make every click a version.
- **Port the model's rules to JavaScript.** Rejected: two implementations of
  one model is the problem this ADR removes.

## Verification
- `tests/test_designer_gestures.py`: every drawable relationship and every
  palette kind has a gesture; every drawing gesture, played on the base
  organisation, gets an answer from the model; knowledge linked to two agents,
  deployment in and out, move by drag, refusal as an answer, effects of a
  delete, 422 for an unreadable request; the catalogue is current.
- `scripts/interaction_check.py`: a knowledge source linked from its own end
  reaches two agents and stays one element; the existing link, sub-agent and
  leadership checks pass through the model.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. The designer specified as gestures on the model; linking and deleting through `POST /api/designer/operations`. |
