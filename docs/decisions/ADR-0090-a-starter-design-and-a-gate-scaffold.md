---
id: ADR-0090
title: A starter design and a gate scaffold
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering]
informed: [All engineering]
scope: [cli, spec]
workstreams: [WS-002]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0019, ADR-0073]
tags: [onboarding, cli, phase-gate, adoption]
---

# ADR-0090: A starter design and a gate scaffold

## Context
An adoption review measured the real path from nothing to an agentic
organization. The floor is genuinely low — fourteen lines of spec validate and
compile to a running local stack — and the phase gate (ADR-0019) names exactly
what a design still owes, eleven checks for a starter design. Both are
strengths.

The gap was between them. Nothing scaffolded a spec: `records new` created an
ADR, but the fastest real route to a first design was "copy the nearest of the
shipped examples", which works and was nowhere written down. And the gate,
having named a gap precisely, left the author at a blank page — it knew that
`budgets:` was missing and what a budget is for, but printed only the sentence.

## Decision
Two commands, sharing one table that maps a gate check to the block of spec
that answers it.

`orgagents spec new <name>` writes a starter design that is **valid from the
first save**: it validates with no errors and compiles for `local`, so an
author sees something real before doing any governance work. It carries every
remaining gate check as a commented block, in the order worth working them —
what data exists, who is accountable, where work runs, then how it is bounded.

`orgagents phase <spec> --scaffold [-o FILE]` prints the blocks for the checks
*this* design still fails, so the scaffold shrinks as the design grows.

Both emit **commented** YAML, always. A scaffold that silently declared a
budget, a data class or a production gate would be inventing governance the
organization never agreed — exactly the lie the phase gate exists to catch
(ADR-0073). The author has to mean it. For the same reason `phase --scaffold`
still exits non-zero: scaffolding an answer is not having answered it.

A failing check with no template — a separation of duties, say — is listed
under "only you can decide these" with the gate's own `fix` text rather than
guessed at.

## Scope
The CLI (`spec new`, `phase --scaffold`) and a new `orgagents.scaffold` module.
No change to the spec model, the IR, the gate's checks or any target.

## Implementation
`src/orgagents/scaffold.py` holds `GATE_BLOCKS` (one `GateBlock` per check id,
keyed to the ids `phases.review` already emits), `starter_spec()` and
`scaffold_for()`, plus `BINDING_TEMPLATE` for the implementation-phase gap.
`cli.py` dispatches `spec new` alongside `schema` and `migrate` — the three
commands that read no document — and threads `--scaffold`/`-o` through the
existing phase handler.

## Timeline
Accepted 2026-09-22.

## Advantages
- The blank page is gone: an author goes from nothing to a compiling design in
  one command, and from there to a green definition phase by working a printed
  list.
- The gate stops being only a judge and becomes a teacher, without becoming a
  liar — nothing is declared on anyone's behalf.
- The shapes are kept honest by a test that fills the scaffold in and requires
  the definition gate to close; if the spec model moves, that test fails and
  names the scaffold as stale.

## Disadvantages
- A second place that knows spec shapes, which can drift from the model. The
  filled-fixture test is the guard, and it is the only thing standing between
  a helpful template and a misleading one.
- The blocks teach one reasonable shape per check, not the full space; an
  author with a more complex need still reads an example or the schema.
- `GATE_BLOCKS` is keyed on check ids, so renaming a check silently drops its
  block from the scaffold (the check still fails and is listed as undecidable).

## Alternatives considered
- **Ship archetype templates per industry.** Rejected as the first move: the
  nine worked examples already are archetypes, and duplicating them into the
  package would double the maintenance. They are now documented as the "copy
  the nearest" route instead.
- **Emit uncommented YAML the author deletes.** Rejected: a design that
  accidentally ships a scaffolded budget or data class is precisely the
  unreviewed governance the gate exists to prevent.
- **A `--fix` that edits the spec in place.** Rejected for now: inserting
  blocks into a document the author is editing invites clobbering, and the
  values are theirs to choose anyway.

## Verification
`tests/test_scaffold.py`: the starter design validates with no errors and
compiles; it does *not* pass the gate (the honesty property); it carries a
block per check; the scaffold answers only the checks that actually fail;
every emitted line is a comment; a design written from the scaffold's shapes
closes the definition phase; and the CLI journey works end to end, including
that `spec new` refuses to clobber and `phase --scaffold` still exits 1.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. `orgagents spec new` writes a valid starter design carrying the gate's remaining checks as commented blocks; `orgagents phase --scaffold` prints the blocks a design still owes. |
