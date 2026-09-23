---
id: ADR-0108
title: Authority is a review, reached where review happens — not a tab
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
related: [ADR-0065, ADR-0069, ADR-0070, ADR-0107]
tags: [designer, authority, publishing, review]
---

# ADR-0108: Authority is a review, reached where review happens — not a tab

## Context
The Authority screen shows what a design *grants*, resolved: each agent's
effective decisions once team inheritance, roles and mandates combine; the
separations and whether anyone holds both sides; placements; people's
accountabilities; the gate's verdict. Nothing else shows the resolved result.
But as a top-level tab beside the screens people edit in, it read as clutter:
read-only, needed only at review time, and unexplained. A user asked whether it
was needed at all.

## Decision
The content stays; the tab goes. The review is reached where review happens:

- **Publish…** makes it the first step: *Review what this design grants*, with
  a button to open it and a box to tick. *Request deployment* is enabled only
  when the design compiles, the person may publish, **and** the review is
  ticked — and the tick is cleared for each publish.
- **Review authority…** on the Org chart opens it any time; *← Back to the
  design* returns to the screen it was opened from.
- **Effective authority** — an agent's resolved decisions, each marked declared
  here or inherited, with the line it comes through — is shown in the agent's
  Properties and on the Agents screen, from the same resolution, as of the last
  save.

## Scope
Designer UI only; the resolution and its routes are unchanged.

## Implementation
- `web/index.html`: tab removed; review step in the publish bar; the review's
  header and back button; Org chart button.
- `web/canvas.js`: `syncPublishRequest`, `effectiveAuthority`.
- `web/app.js`: entry points and "Back".

## Timeline
Delivered with this ADR.

## Advantages
- The resolved view is in front of the reviewer at the moment it matters, and
  in front of the editor on the agent being edited.
- One fewer tab.

## Disadvantages
- A tick box is an attestation, not a proof of reading; the audit log records
  who requested each deployment.

## Alternatives considered
- **Delete the view.** Rejected: nothing else shows the resolved result.

## Verification
- `scripts/interaction_check.py`: no Authority tab; Review authority opens the
  resolved review and Back returns; an agent's Properties shows its effective
  authority; Request deployment waits for the review.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. |
