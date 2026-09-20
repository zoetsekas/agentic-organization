---
id: WS-001
title: Records and governance — ADR/WS process and validation tooling
status: Complete
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Developer Experience]
scope: [docs, process]
decisions: [ADR-0001]
depends_on: []
tags: [process]
---

# WS-001: Records and governance — ADR/WS process and validation tooling

## Objective
Give the programme a durable, checkable memory: every architectural decision and
every delivery workstream recorded, cross-linked and validated, so the reasoning
behind the system survives staff changes and is not re-litigated each year.

## Deliverables
- `docs/decisions/` and `docs/workstreams/` with process READMEs and templates.
- `orgagents.records` — parser, validator, index generator, graph emitter.
- `orgagents records validate|index|graph|new` CLI.
- The initial record set: ADR-0001…ADR-0018, WS-001…WS-010.
- Test coverage asserting the real record set passes validation.

## Scope
In: record formats, lifecycle, versioning, supersession, cross-reference
checking, index generation. Out: product documentation, API reference, tutorials.

## Approach
Records are markdown with YAML front matter so they review as prose and parse as
data. The validator enforces structure (ids, statuses, semver, required
sections, changelog agreement) and graph integrity (resolvable references,
symmetric supersession). Indexes are generated, never hand-maintained. CI runs
the validator, so a dangling reference fails the build.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Process, templates, validator | Phase 0 | Done |
| M2 Initial ADR/WS set written | Phase 0 | Done |
| M3 CI gate and tests | Phase 0 | Done |
| M4 Record graph surfaced in the designer UI | Phase 2 | Not started |

## Dependencies
None. This workstream deliberately precedes the platform work it documents.

## Advantages
- Decisions are reviewable in the same pull request as the code implementing them.
- Machine-checked cross-references keep the set navigable as it grows.
- New contributors can reconstruct the reasoning without asking anyone.

## Disadvantages
- Ongoing overhead on every substantive change; some decisions will go
  unrecorded because the cost feels high in the moment.
- Structural validation cannot detect a record that is well-formed and wrong,
  which is the most dangerous state for a record to be in.
- The initial set was written in one pass, so it reflects one group's view and
  will need correcting as reality pushes back.

## Exit criteria
- `orgagents records validate` passes on the full set and runs in CI. ✔
- Every accepted ADR is referenced by at least one workstream. ✔
- Templates and process documented for contributors. ✔

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Complete. Process, tooling, tests and the initial record set. |
