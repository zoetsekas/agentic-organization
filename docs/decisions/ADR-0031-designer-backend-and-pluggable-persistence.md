---
id: ADR-0031
title: The designer is a backend service with pluggable persistence
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Product]
consulted: [Developer Experience, Platform SRE]
informed: [All engineering]
scope: [designer, ui, sdk]
workstreams: [WS-020]
supersedes: []
superseded_by: []
related: [ADR-0018, ADR-0032, ADR-0033, ADR-0034]
tags: [designer, product]
---

# ADR-0031: The designer is a backend service with pluggable persistence

## Context
The designer UI held its own state and edited one system at a time. That is
fine for a demo and wrong for the product: an organization designs *several*
agentic systems, several people work on them, and the result has to live
somewhere the organization controls. A browser is not a datastore.

Where "somewhere" is differs by customer. A small team wants JSON files in git,
where designs diff and review like code. A shared installation wants a database
with concurrent access. An evaluation wants nothing persisted at all.

## Decision
The designer is a **backend service** with a small repository protocol, and the
UI is one of its clients. Three backends ship: **filesystem** (JSON laid out to
live in git), **relational** (the platform's document store), and **memory**
(tests and previews). The backend is chosen in settings, not in code.

The protocol is deliberately small — list, get, save, delete, plus revisions,
workspaces, locks and settings — so a fourth backend is a day's work. Every
accepted write captures an immutable **revision**, so history and restore are
properties of the design rather than features of one backend.

`DesignerService` holds every rule: permissions, locks, merges, validation. The
frontend asks and draws; it decides nothing. That is what makes the UI
genuinely replaceable rather than nominally so.

## Scope
Persistence and the service surface for designing systems. It does not change
the System Spec (ADR-0004) or where a *compiled* system is deployed.

## Implementation
`designer/repository.py` defines the `Repository` protocol and the three
implementations; `designer/service.py` is the whole API surface;
`designer/models.py` holds workspaces, system records, revisions, locks and
settings. The same test suite is parameterized over all three backends —
a seam nobody exercises is a seam that does not exist. File writes are
write-then-rename, so a crash mid-write never leaves a half file.

## Timeline
Phase 3, with the canvas it serves.

## Advantages
- Customers keep their designs where they want them, including in git.
- The service holds the rules once, so UI, SDK and CLI cannot diverge.
- Revisions come free with every write, on every backend.
- A fourth backend is an afternoon, not a refactor.

## Disadvantages
- Three backends is three times the surface for bugs that only one exhibits;
  the parameterized suite catches shape, not performance or locking behaviour
  under load.
- The filesystem backend has no real transactions: concurrent writes rely on
  the version check and rename atomicity, which is weaker than a database.
- A small protocol constrains what a clever backend could offer — no queries,
  no partial loads, so a workspace with thousands of systems will feel it.
- Every write stores a full snapshot, which is simple and wasteful.

## Alternatives considered
- **One backend (database only)** — simplest, and it refuses the git-native
  workflow that engineering-led teams actually want.
- **Git as the only backend** — elegant for review, poor for concurrent editing
  and for people who do not use git.
- **Keep state in the browser and export** — what we had; loses multi-user,
  history and any shared source of truth.

## Verification
`tests/test_designer.py` runs the full suite against memory, filesystem and
relational backends, covering create/read/update/delete, revisions, restore,
isolation between systems, and direct version enforcement at the repository.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Repository protocol with filesystem, relational and memory backends. |
