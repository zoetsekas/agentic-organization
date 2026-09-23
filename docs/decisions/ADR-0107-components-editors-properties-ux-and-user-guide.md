---
id: ADR-0107
title: A Components menu with an editor per kind, a Properties form people can read, and a User guide
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
related: [ADR-0101, ADR-0103, ADR-0106]
tags: [designer, forms, navigation, documentation]
---

# ADR-0107: A Components menu with an editor per kind, a Properties form people can read, and a User guide

## Context
Users found the Agents screen — a list, a full form, and what the change does —
the clearest way to work, and asked for it for every kind of component. The
Properties panel showed raw field names (`model_policy`), checkboxes drawn as
large white blocks by the text-box sizing every input inherited, and required
fields marked with a plain ` *` unlike every other form. And nobody could say
what the Authority screen was for: nothing in the product explained its
screens.

## Decision
- **Components menu.** The *Agents* tab becomes *Components ▾*, listing every
  kind grouped as the palette groups them. *Agents* keeps its own screen; every
  other kind opens the **Components editor**: its instances, a *New…* form
  (asking for the whole when the kind is a part — the team of an agent, the
  agent of a sub-agent), the Properties form full-size, and what refers to the
  selected component and what it refers to. Creating, editing and removing go
  through the model (ADR-0103) — the editor is Properties drawn elsewhere, not a
  second implementation.
- **Properties people can read.** Field names are shown as words ("Model
  policy"); required fields carry the same red `*` as every form; a yes/no is a
  real checkbox on one line with its meaning; reference fields are the pickers
  of ADR-0106.
- **User guide**, the last tab: every screen and gesture, what the model means,
  the command line, and troubleshooting — including what *Authority* is for:
  the one read-only screen that shows the design's *resolved* authority,
  separations, placements and accountabilities, which is what a reviewer signs
  off before publishing. Its contents are built from its own headings.

## Scope
Designer UI only.

## Implementation
- `web/canvas.js`: `renderComponents`, `selectComponent`, `renderNewComponentForm`,
  `wireComponentsMenu`; `renderInspector` draws into `canvas.formHost`;
  `humanise`, `fieldTitle`; boolean rows.
- `web/app.js`: `showView` knows the menu and the guide; `renderGuide`.
- `web/index.html`: the menu, `view-components`, `view-user-guide`.
- `web/styles.css`: checkbox sizing, boolean rows, menu, editor, guide.

## Timeline
Delivered with this ADR.

## Advantages
- One editing experience for every kind, and one form behind it.
- The product explains itself.

## Disadvantages
- The guide is hand-written and must be kept with the product; its contents
  list is generated so at least that cannot drift.

## Alternatives considered
- **A separate hand-built screen per kind.** Rejected: two dozen forms that
  would drift from Properties and from the model.

## Verification
- `scripts/interaction_check.py`: the menu lists every kind; a kind's editor
  lists its components; creating one without an id says so under the field;
  creating one goes through the model and opens its form; the guide has its
  contents.
- `scripts/view_check.py` reaches Agents through the menu; screenshots include
  the Components editor and the User guide.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. |
