---
id: WS-020
title: Designer backend and pluggable persistence
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Developer Experience]
scope: [designer]
decisions: [ADR-0031]
depends_on: [WS-009]
tags: [designer]
---

# WS-020: Designer backend and pluggable persistence

## Objective
Move the designer's state out of the browser and into a service the customer
controls, so an organization can design many agentic systems and keep them
where it wants — in git, in a database, or nowhere at all.

## Deliverables
- `Repository` protocol with filesystem, relational and memory backends.
- `DesignerService` holding every rule: permissions, locks, merges, validation.
- Immutable revisions on every write, with restore.
- Workspaces, membership and installation settings.
- `/api/designer/*` endpoints and a component palette derived from the model.
- A parameterized test suite that runs against all three backends.

## Scope
In: persistence and the service surface for designing systems. Out: the System
Spec itself, and deploying compiled systems.

## Approach
Keep the protocol small enough that a fourth backend is an afternoon, and put
every rule in the service so the UI holds none. Run the same tests against
every backend, because an unexercised seam is not a seam.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Protocol and three backends | Phase 3 | Done |
| M2 Service, revisions, settings | Phase 3 | Done |
| M3 API and palette | Phase 3 | Done |
| M4 Git-native backend with real commits | Phase 3 | Not started |
| M5 Export and import between installations | Phase 3 | Not started |

## Dependencies
WS-009 for the spec both the UI and the SDK edit.

## Advantages
- Designs live where the customer wants, including under review in git.
- One place holds the rules, so UI, SDK and CLI cannot diverge.
- History and restore come free on every backend.

## Disadvantages
- Three backends means bugs that appear in only one, and the shared suite
  checks shape rather than behaviour under load.
- The filesystem backend has no transactions; it relies on the version check
  and atomic rename, which is weaker than a database.
- Full snapshots per revision are simple and wasteful.
- A deliberately small protocol will feel limiting at thousands of systems.

## Exit criteria
- Every backend passes the same suite. ✔
- A design can be moved between backends without loss (M5).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Protocol, three backends, service and API landed. |
