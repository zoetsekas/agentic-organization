---
id: WS-032
title: Designer completeness — authoring the whole spec
status: Active
version: 1.4.0
date: 2026-09-21
updated: 2026-09-21
owner: Product
contributors: [Platform Architecture]
scope: [ui, spec, docs]
decisions: [ADR-0018, ADR-0034, ADR-0066, ADR-0072, ADR-0076]
depends_on: [WS-009, WS-023]
tags: [designer]
---

# WS-032: Designer completeness — authoring the whole spec

## Objective
Close the gap between what the spec language can express and what the designer
can author, so a design built entirely in the UI is a design that can be built.

## Deliverables
- Palette kinds for the spec blocks the designer cannot currently author,
  beginning with `lifecycle` evaluation cases and `guardrails`.
- The platform policy's verdict surfaced in the designer, so a refusal by house
  rules is seen while editing rather than at compile.
- Undo for canvas edits.
- A keyboard path to creating a node, and a focus treatment worth the name.
- A check that keeps the palette and the spec model from drifting apart again.

## Scope
In: what the designer can author and what it shows about a design's standing.
Out: the runtime views, the command centre, and anything about how a spec is
compiled — the designer is a peer client of the spec (ADR-0018), and this
workstream does not change the spec language.

## Approach
The review of 2026-09-21 (`docs/DESIGNER.md`) found the palette offers 16 kinds
against roughly 30 authored blocks. Two of those gaps interact, and that pair
is where this starts: a design authored in the UI cannot declare an evaluation
case, and ADR-0072 rule 5 requires evaluation evidence before an activity may
run unattended. **The designer can set an autonomy posture it has no way to
satisfy** — a contradiction introduced by a decision, not by the UI, and the
cheapest thing on this list to put right.

After that, coverage in the order somebody reaches for a block, not
alphabetically. Each kind is a palette entry plus whatever control the field
actually needs: the mandate picker and posture selector (ADR-0072) cost more
than a text box and are the reason the fields they edit mean anything.

The drift check comes last on purpose. It is worth having once the gap is
small enough that failing it is a prompt rather than a permanent red mark.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Evaluation cases in the palette — close the autonomy contradiction | Phase 5 | Done |
| M2 Guardrails, output contracts and model policy | Phase 6 | Done |
| M3 Skills, plugins, tools and operating principles | Phase 6 | Done |
| M4 The platform policy verdict in the designer | Phase 6 | Not started |
| M5 Undo for canvas edits | Phase 6 | Not started |
| M6 Keyboard node creation and a focus treatment | Phase 6 | Not started |
| M7 A drift check between the palette and the spec model | Phase 6 | Not started |
| M8 Wire the placements route into the view — volumes, what crosses, who is placed nowhere | Phase 6 | Done |
| M9 The publish path: validate → compile → request deployment, with the gate's refusal as the reason | Phase 6 | Not started |

## Dependencies
WS-009 for the designer itself and WS-023 for the canvas. Nothing here blocks
the alpha: every gap has a working answer today, which is to edit the YAML.

## Advantages
- A design authored in the UI stops being a second-class design.
- The autonomy contradiction goes away at M1, which is a day's work against a
  rule that is already shipped and already enforced.
- Surfacing the policy verdict moves a refusal from the build to the edit,
  which is the only place it can be acted on cheaply.
- A drift check stops this gap reopening quietly, which is how it opened.

## Disadvantages
- **Palette coverage is a treadmill.** Every spec block added from now on owes
  a palette entry, and the ones that need real controls owe a widget. The drift
  check at M7 makes that visible and does not make it smaller.
- **Some blocks resist a form.** `policies` carries `conditions` and `unless`
  maps whose keys are attribute names with no closed vocabulary, and a form
  over them is either a text box in disguise or a schema we do not have.
- **Undo across a shared design is not obvious.** Locks mean one editor at a
  time on a node, not on a design, and an undo stack that crosses somebody
  else's merged change is worse than no undo.
- **The accessibility work is unbounded as written.** M6 names two concrete
  things because "make the canvas accessible" is a project, not a milestone,
  and pretending otherwise would park it forever.

## Exit criteria
Every spec block a design can contain has a palette kind or a recorded reason
it does not; a design authored entirely in the designer passes the phase gate
under the fabric's policy; the drift check runs in CI; and the review section
in `docs/DESIGNER.md` no longer lists coverage as the largest gap.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.4.0 | 2026-09-21 | M8 done. Placements render in the Authority view and their findings merge into one gate list. The category is now checked rather than reviewed: a test fails on any served designer route the bundle does not reference, with `lock/heartbeat` allowlisted and its reason named. |
| 1.3.0 | 2026-09-21 | M8 and M9 opened. Coverage measured rather than estimated: 9 spec blocks have no UI and 5 are partial, `missions` and `policies` being the two that matter. `GET .../placements` is served and nothing calls it. |
| 1.3.0 | 2026-09-21 | M3 done. Operating principles went to the organisation form rather than the palette: instructions every agent carries are an organisation-wide statement, not a node. 23 palette kinds. |
| 1.2.0 | 2026-09-21 | M2 done. It needed three field types the inspector did not have — `multi` for closed vocabularies, `json` for a schema, and `object` for a nested policy — which is the widget cost this workstream's Disadvantages predicted. |
| 1.1.0 | 2026-09-21 | M1 done. It also surfaced two palette kinds — `decision` and `separation` — that were offered and could not be placed, because they were never wired into the canvas's collection map. |
| 1.0.0 | 2026-09-21 | Proposed, from the designer review of 2026-09-21. |
