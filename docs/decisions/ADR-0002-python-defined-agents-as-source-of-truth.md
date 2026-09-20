---
id: ADR-0002
title: Python-defined agent objects are the source of truth
status: Superseded
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Developer Experience]
informed: [All engineering]
scope: [runtime]
workstreams: [WS-002]
supersedes: []
superseded_by: [ADR-0004]
related: [ADR-0003, ADR-0005]
tags: [historical]
---

# ADR-0002: Python-defined agent objects are the source of truth

## Context
The first cut of the platform needed a way to describe an organization of
agents quickly enough to test delegation, data planes and harness policy end to
end. Pydantic models were already in use for validation and serialization.

## Decision
Define the organization directly as Python objects (`Agent`, `Harness`,
`OrgUnit`, …) constructed in code and persisted as JSON documents. The Python
model is authoritative; any file format is a serialization of it.

## Scope
The runtime library and its seeded example organization. No code generation or
infrastructure provisioning was in scope at the time.

## Implementation
`src/orgagents/models.py` holds the pydantic model; `seed.py` constructs an
example organization; `store.py` persists each object as a JSON document.

## Timeline
Phase 0 prototype, superseded within the same programme once the designer and
compiler scope was confirmed.

## Advantages
- Fastest possible path to a working runtime with real policy enforcement.
- Type checking and IDE completion over the whole organization model.
- One representation to maintain while the domain was still moving.

## Disadvantages
- The organization is only expressible by people who write Python, which
  excludes the designers and architects the product is for.
- No stable artifact to diff, review, sign or promote between environments.
- Generation targets would have to consume live Python objects, coupling every
  target to the runtime's import graph and its version.
- Versioning the *definition* independently of the *code* is impossible.

## Alternatives considered
- **A declarative spec from day one** — correct, and what ADR-0004 adopts; it
  was deferred only to get a working runtime first.
- **A database-first model** — worse: no reviewable artifact at all.

## Verification
Superseded; no ongoing verification. The runtime model it introduced survives
as the *compiled* form, now produced from the spec rather than hand-written.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Superseded by ADR-0004 once the designer/compiler scope was set. |
| 1.0.0 | 2026-09-20 | Accepted for the initial runtime prototype. |
