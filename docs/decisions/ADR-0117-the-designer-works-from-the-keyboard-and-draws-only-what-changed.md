---
id: ADR-0117
title: The designer works from the keyboard, asks in one accessible dialog, and redraws only what changed
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Design]
informed: [All engineering]
scope: [ui, docs]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0103, ADR-0105, ADR-0106, ADR-0107]
tags: [designer, accessibility, keyboard, performance, navigation]
---

# ADR-0117: The designer works from the keyboard, asks in one accessible dialog, and redraws only what changed

> Numbered 0117 as the next free number when written; if another branch
> took it first, renumber at merge and regenerate the index.

## Context
A review of the designer's front end found that it could not be used without
a mouse, and that the way it drew itself was the reason why.

- **The canvas was mouse-only** (WCAG 2.1.1). Nodes were `div`s with
  `mousedown` and `click` handlers; nothing on the canvas could take focus, so
  a keyboard or switch user could not select, link, move or delete anything,
  and a touch or pen could not drag.
- **Every change redrew everything.** About thirty call sites ran one
  `renderCanvas` that rebuilt every node, region and edge, the edge filter, the
  Explorer, the Outline and the diagram bar. Selecting a box paid for all of
  it, and — the reason the first point could not simply be patched — replacing
  the element that has focus throws the focus away. A keyboard canvas needs
  its elements to survive a redraw.
- **Questions were native `alert`/`confirm`/`prompt`** (twenty-five of them).
  They cannot validate a field (`Number(prompt("Rating 1-5"))` accepted 3.7 and
  12), cannot say which field is wrong, block the page, and read to a screen
  reader as bare text with an unlabelled box.
- **The navigation was ten flat buttons**, with nothing saying which views use
  the design context bar and which ignore it.
- Smaller gaps: the status line was not a live region; the four tab sets were
  written four ways and none answered an arrow key; the global key handler
  acted on the canvas from any view and under a modal; empty views said
  nothing about what to do; a server that did not answer left "connecting…"
  on screen for ever; a delete asked "are you sure?" and then could not be
  noticed or undone.

## Decision
1. **Every node is reachable and operable from the keyboard.** The canvas is
   a named `role="application"` region described by its key help. Nodes carry
   a roving tabindex (one in the Tab order) and an accessible name; the keys
   are: arrows move focus to the nearest box that way, Enter/Space select and
   show Properties, Delete removes, L starts a link (arrows and Enter pick the
   target, Escape cancels), Shift+arrows move the box 10px, F2 renames,
   Shift+F10 opens the context menu. What changed is announced in a polite
   live region. Pointer events replace mouse events, with `touch-action:
   none` on nodes, so a pen or finger drags too.
2. **The canvas redraws only what changed.** Node elements are kept in a map
   keyed by id and rebuilt only when their signature (position, component,
   subtitle, held chips, link and lock state, edit right) or the objects they
   were drawn from change; they are reordered in place, never all replaced.
   Selection toggles a class and `aria-current`. A drag moves one box and the
   edges that touch it; regions follow once a frame. The Explorer, Outline,
   diagram bar and edge filter are rebuilt when the model revision, the open
   diagram, the system or the edit right changes — not on every redraw — and
   the stored viewport is restored only on arriving at a diagram.
3. **One dialog** (`web/ui.js`: `formDialog`, `confirmDialog`,
   `promptDialog`, `alertDialog`) replaces every native one. It is a modal
   `<dialog>` with a focus trap, focus returned to the opener, fields with
   labels, help and `aria-describedby`, and inline validation under the field
   (`integer` with `min`/`max`, `required`, custom `validate`). A
   destructive confirmation focuses Cancel. Choices that need a sentence each
   (which relationship a link is) are radio lists; choices from a known set
   (a link's kind, which version to restore) are selects, not free text.
4. **Removals are undone, not confirmed.** Deleting a node, removing an agent
   or a diagram, unlinking and arranging show a toast naming what happened,
   with an Undo that reverts that step only (if something else has been done
   since, it says so rather than undoing the wrong thing). Deleting a whole
   organisation, a catalog draft, retiring, and breaking a lock still ask,
   because nothing local can undo them.
5. **Navigation is grouped**: Design (Org chart, Canvas, Components), Library
   (Catalog, Marketplace), Run (Workspace, Sessions, Operations), Help (User
   guide). The current view is `aria-current="page"`. On Sessions,
   Operations and the guide the context bar dims and says it is not used by
   this view.
6. **One tabs pattern** (`ui.wireTabs`) gives every tab set — Palette/Explorer,
   Properties/Issues, the diagram tabs, Settings — `tablist`/`tab`/`tabpanel`,
   `aria-selected`, `aria-controls`, a roving tabindex and arrow/Home/End keys.
   The diagram tabs are their own tablist; the add and arrange buttons beside
   them are not tabs. F2 and Delete on a diagram tab rename and remove it.
7. **Live and alert regions**: `#status` is `role="status"`
   `aria-live="polite"`; the conflict bar, the org error line and the offline
   notice are `role="alert"`.
8. **Shortcuts respect context.** The document key handler does nothing while
   a modal is open; undo/redo act on the design views only (canvas, org chart,
   agents, components — they edit the same record); everything else on the
   canvas view only.
9. **Empty states say what to do next** (`ui.emptyState`): Sessions,
   Operations, Catalog, Marketplace and Issues. A server that does not answer
   shows "Can't reach the designer's server" with Try again instead of
   "connecting…" — after ten seconds as "still waiting", at once when a fetch
   fails outright.
10. **Focus stays visible.** No rule removes the outline without drawing one
    back; icon-only buttons have an `aria-label` and an `aria-hidden` glyph.

## Scope
The designer bundle (`web/`) and its user guide. Not the command centre
(`web/command/`, ADR-0051), not the API. It does not add zoom, keyboard
resizing of environment boxes, or a full tree pattern for the Explorer (its
rows are buttons in the Tab order).

## Implementation
- `web/ui.js` (new, loaded before `app.js`): the dialog, toast, tabs, empty
  state and live-region helpers.
- `web/canvas.js`: `nodeEls`, `nodeSignature`, `renderCanvas`,
  `renderPanels`/`schedulePanels`, `syncSelection`, `syncRovingFocus`,
  `handleNodeKey`, `nearestBox`, `edgeGeometry`/`updateEdgesFor`,
  `positionOutlineViewport`, `offerUndo`; `canvas.rev` is bumped by every
  recorded change. `canvas.renderStats` records the last render's time and how
  many nodes were built or kept.
- `web/app.js`: grouped navigation state, dialogs, empty states, the offline
  notice.
- `web/index.html`, `web/styles.css`: the nav groups, ARIA attributes, the
  canvas help text, the live region, dialog/toast/empty-state/offline styles
  (tokens only), the user guide's "With the keyboard" section.

The Outline is drawn on the next animation frame rather than inside the
redraw: it measures its panel, and measuring straight after the nodes changed
forced a synchronous layout of the whole page.

Measured on the AYC example (23 nodes, 31 edges on its main diagram) in
Chromium, same machine and session, median of 20, milliseconds of script /
script plus a forced layout. The machine was shared and under load, so the
absolute figures are noisy; the ratios held across repeated runs.

| Interaction | Before | After |
|---|---|---|
| Select a node (click; includes the Properties form) | 23.4 / 34.5 | 6.9 / 22.1 |
| One pointer-move step of a drag | 1.3 / 4.2 | 0.2 / 1.2 |
| Nudge a node one step from the keyboard | 20.7 / 22.3 | 3.0 / 6.3 |
| Redraw after editing one component | 15.0 / 16.4 | 2.8 / 8.4 (1 node rebuilt, 22 kept) |
| Undo or redo (every node is a new object) | 16.4 / 17.3 | 9.5 / 13.8 |

What remains of a selection is the Properties form, which this decision does
not change.

## Timeline
Lands with WS-003's designer hardening.

## Advantages
- The canvas meets WCAG 2.1.1 and 4.1.2 for its core operations; touch and
  pen work.
- Selecting, dragging and nudging no longer rebuild the page; focus survives
  every redraw.
- One place for dialogs, tabs, toasts and empty states: the next view gets
  them right by calling them.
- Removals are recoverable from where the person is looking.

## Disadvantages
- The node signature must name everything `renderNode` reads. A new input to
  a node that is left out of the signature will leave a stale box until the
  next change that is in it. The object-identity check limits this to
  mutations in place.
- The dialog helpers are asynchronous; code that used to read a `prompt()`
  inline now awaits, which turned a few handlers `async`.
- Two undo paths (the toolbar and the toast) for one history.

## Alternatives considered
- **A virtual-DOM library** — would give keyed reconciliation for free, at
  the cost of a build step and a dependency in a bundle that has neither.
- **`role="grid"` for the canvas** — a canvas is not rows and columns;
  spatial arrows over free positions match neither the grid nor the tree
  pattern, so it is an application region with its keys documented.
- **Keeping `confirm()` for deletes** — asked every time, answered without
  reading; an undo is there when the mistake is noticed.

## Verification
- `tests/test_designer_accessibility.py`: no native dialogs in the bundle;
  the dialog's trap, inline validation and focus return; the canvas region,
  node keys, roving tabindex, pointer events; selection does not redraw; the
  renderer keeps elements and does not rebuild panels; a drag redraws only its
  edges; delete offers undo; grouped navigation and `aria-current`; the tabs
  pattern on every tab set; live/alert regions; icon glyphs hidden; no focus
  removed without replacement; empty and offline states.
- Two `e2e` tests drive Chromium through Playwright where it is installed:
  keyboard navigation, delete and undo from the toast, and a dialog refusing
  3.5 stars under the field. Skipped where Playwright or its browser is
  absent.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. |
