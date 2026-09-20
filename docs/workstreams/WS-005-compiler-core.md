---
id: WS-005
title: Compiler core — IR, target plugin API and generation engine
status: Active
version: 1.2.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Developer Experience]
scope: [compiler]
decisions: [ADR-0005, ADR-0014, ADR-0004, ADR-0003]
depends_on: [WS-002, WS-004]
tags: [compiler, foundational]
---

# WS-005: Compiler core — IR, target plugin API and generation engine

## Objective
Turn a validated spec into a normalized, reviewable intermediate representation,
and give targets a stable plugin API so new deployment shapes are additions
rather than surgery.

## Deliverables
- `compiler.ir` — `SystemIR`, `TeamIR`, `AgentIR`, `GrantIR`, resolved
  identities and the provider-neutral resource set.
- `compiler.base` — `Target` protocol, `GeneratedFile`, registry.
- `compiler.engine` — phase orchestration, output directory, `manifest.json`
  with content hashes, overlay preservation, `--force` semantics.
- `orgagents spec ir` / `orgagents compile` CLI.
- A test asserting no target imports the spec package, keeping phases separate.
- `compiler.diff` — `diff_ir` over two `SystemIR`s, keyed by stable ids, with
  each change carrying a direction (widened / narrowed) and a justified
  severity, rendered as a text report or a machine-readable payload.

## Scope
In: normalization, the plugin boundary, generation mechanics and output
ownership. Out: the targets themselves (WS-006, WS-007).

## Approach
Resolve everything once — inheritance, roles, permissions, environments,
identities — then hand targets a flat structure they may only render. Treat the
IR as a published artifact: versioned, serializable, diffable, and the thing a
security reviewer reads. Make output compiler-owned with a manifest so
regeneration is always safe.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 IR model and resolver | Phase 1 | Done |
| M2 Target protocol and registry | Phase 1 | Done |
| M3 Engine, manifest, overlays | Phase 2 | Done |
| M4 IR diffing for change review | Phase 3 | Done |

## Dependencies
WS-002 for the spec, WS-004 for permission resolution semantics.

## Advantages
- Permission and inheritance logic exists once, so targets cannot disagree.
- New targets are plugins; the semantics stay put.
- Targets are testable from fixture IR without any spec parsing.

## Disadvantages
- An extra representation to design, version and document, and a boundary that
  slows target work whenever a target needs something the IR lacks.
- Debugging spans two phases; the IR dump is the only way to localize a fault.
- Over-normalization can discard author intent a target could have used well.

## Exit criteria
- The example spec produces a stable IR that two targets consume unchanged.
- Regeneration is byte-identical for an unchanged spec; modified output is
  refused without `--force`; overlays survive.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.2.0 | 2026-09-20 | M4 done: `compiler.diff` ranks IR changes by consequence, separates widening from narrowing, and refuses cross-tenant or cross-target comparisons. |
| 1.1.0 | 2026-09-20 | Milestone statuses reconciled with what has shipped. |
| 1.0.0 | 2026-09-20 | Opened. IR, plugin API and engine in progress. |
