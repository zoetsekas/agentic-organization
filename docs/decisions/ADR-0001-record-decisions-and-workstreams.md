---
id: ADR-0001
title: Record architecture decisions and delivery workstreams in the repository
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Platform SRE, Developer Experience]
informed: [All engineering]
scope: [docs, process]
workstreams: [WS-001]
supersedes: []
superseded_by: []
related: [ADR-0003, ADR-0004]
tags: [foundational, process]
---

# ADR-0001: Record architecture decisions and delivery workstreams in the repository

## Context
This system generates code and infrastructure from a declarative specification.
Decisions taken here — how the spec is versioned, what a sandbox may reach, how
permissions resolve — outlive the people who take them, and are re-derived
badly when the reasoning is lost. Chat logs, tickets and slide decks all decay
at a different rate than the code they describe.

Two distinct things need recording, and conflating them is the common failure:
*what we decided and why* (stable, long-lived, occasionally superseded) and
*how we are delivering it* (volatile, owned, dated). One document format for
both produces either a decision log full of sprint noise or a plan with no
rationale.

## Decision
Keep two linked record sets in the repository, as markdown with YAML front
matter:

* **ADR-nnnn** under `docs/decisions` — one architectural decision each.
* **WS-nnn** under `docs/workstreams` — one delivery workstream each.

Both carry a unique never-reused identifier, a lifecycle status, a semver
version, a changelog whose newest row must equal that version, and typed
cross-references. Supersession is explicit and symmetric: a replaced record
keeps its number, flips to `Superseded`, and names its replacement.

Both formats require the same narrative spine — why, who, what, where, how,
when — plus **both** an advantages and a disadvantages section, neither of
which may be empty.

## Scope
Binds all architectural and platform decisions in this repository, and the
workstreams delivering them. It does not bind ordinary code review, day-to-day
task tracking, or product/UX copy decisions.

## Implementation
`src/orgagents/records.py` parses the records and enforces the rules:
unique ids matching filenames, allowed statuses, semver, resolvable references,
symmetric supersession, required sections, changelog/version agreement. The
same module regenerates `index.md` for each set and emits a node/edge graph.

`orgagents records validate|index|graph|new` drives it; `validate` runs in CI
and exits non-zero on any violation, so a dangling reference is a build failure
rather than a broken link someone notices a year later.

## Timeline
Phase 0, ahead of any further platform work — the records describe the
architecture being built, so they must exist before it is.

## Advantages
- Rationale lives beside the code it governs and is versioned with it.
- Cross-references are checked, so the record set stays a navigable graph.
- Mandatory disadvantages sections make trade-offs explicit at decision time.
- Supersession preserves history instead of quietly rewriting it.

## Disadvantages
- Real overhead: every substantive decision costs a document and a review.
- Records rot when not maintained; the validator catches structure, not
  truthfulness — a stale but well-formed ADR still passes.
- Semver over a *decision* is a judgement call, and the minor/major line will be
  argued about.
- Two record kinds mean contributors must learn which one to reach for.

## Alternatives considered
- **Ticket tracker only** — decisions vanish into closed issues, and history is
  hostage to the vendor.
- **A wiki** — drifts from the code, no review gate, no CI validation.
- **ADRs alone, no workstreams** — either the plan pollutes the decision log or
  delivery goes unrecorded.
- **Unvalidated markdown** — what we had. Links rot silently.

## Verification
`tests/test_records.py` runs the validator across the real record set, so a
malformed or dangling record fails the suite. CI runs `orgagents records
validate` and fails the build on violations.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Two record kinds, machine-validated. |
