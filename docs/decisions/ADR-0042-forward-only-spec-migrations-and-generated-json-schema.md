---
id: ADR-0042
title: Forward-only spec migrations and generated JSON Schema
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Developer Experience, Security Engineering]
informed: [Compiler, Designer]
scope: [spec, docs]
workstreams: [WS-002]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0026, ADR-0039, ADR-0040]
tags: [spec, versioning, schema]
---

# ADR-0042: Forward-only spec migrations and generated JSON Schema

## Context
ADR-0004 makes the System Spec the single source of truth, and the document
has already moved twice — 1.0.0 to 1.1.0 (human pairing became a list,
ADR-0026) and 1.1.0 to 1.2.0 (missions and model policy, ADR-0039/ADR-0040).
Until now the loader could only refuse what it could not read, and only on a
major mismatch: a 1.0.0 document loaded by accident, leaning on an ad-hoc
`before` validator in the model to reinterpret its `human:` field. Nothing told
the author their document had been reinterpreted, and nothing existed to bring
it forward on disk.

At the same time the spec is meant to be authored outside our own UI. Every
editor that could help — IDEs, form builders, review tools — speaks JSON
Schema, and we published none, so third parties would have to reimplement the
model by hand and watch it drift at every bump.

## Decision
Spec versioning gets two supporting artifacts, both derived from the model
rather than maintained beside it:

1. `orgagents.spec.migrations` holds an ordered registry of single-hop steps,
   one per released `spec_version`. `migrate(data, to=CURRENT)` applies them in
   turn and returns the upgraded document together with a human-readable list
   of every change. Migration is **forward-only**: a document newer than this
   compiler raises `SpecVersionError` and is never stripped down to fit. A bump
   that needs nothing of the data still gets a registered step whose
   description says so, rather than an absence.
2. `orgagents.spec.schema` exports the spec as draft 2020-12 JSON Schema,
   generated from the pydantic model with `$id`, `$schema`, title, description
   and the spec version it describes.

Every step must state what it changed, and a step that cannot infer intent
safely does nothing and says why — it never guesses at data.

## Scope
Binds the spec package, its loader and the `orgagents spec` CLI. It does not
cover `Binding` documents (target-scoped, versioned with their targets), the
IR (`IR_VERSION`, WS-005), or downgrade: there is no path from a newer document
to an older compiler, by design.

## Implementation
- `src/orgagents/spec/migrations.py` — `MigrationStep`, the `MIGRATIONS` list,
  `migrate()`, and `SpecVersionError`, which the loader re-exports so existing
  callers are unaffected.
- `src/orgagents/spec/loader.py` — `load_spec`/`load_spec_text` migrate
  silently so older documents keep working; `load_spec_with_migration` and
  `load_spec_text_with_migration` return the change list for callers that want
  to report it. `dump_spec` now always writes `metadata.spec_version`, which
  `exclude_defaults` would otherwise drop from a current document.
- `src/orgagents/spec/schema.py` — `system_spec_schema()` and its JSON form.
- CLI: `orgagents spec migrate <path> [--write]` and
  `orgagents spec schema [-o FILE]`. `migrate` revalidates the upgraded
  document before `--write` overwrites the original.

## Timeline
Phase 2, WS-002 M4 and M5, landed 2026-09-20. Every future `spec_version` bump
adds its step in the same commit as the model change.

## Advantages
- An old document has a supported, reviewable path forward instead of a
  reinterpretation the author never sees.
- The change list makes a migration a diff a human can approve.
- Refusing newer documents means a stale compiler cannot silently delete
  fields from someone's design.
- A generated schema cannot drift from the model, and third-party editors stop
  being second-class.

## Disadvantages
- The registry is a permanent, growing tail of code that must be kept correct
  for versions nobody runs any more, and its steps are the hardest code in the
  repo to test realistically once the old documents are gone.
- Migration history is only as good as our memory of what each bump meant. The
  1.1.0 to 1.2.0 step is a documented no-op inferred from the model diff; if
  that reading was wrong, the step will be confidently wrong too.
- Loading now mutates documents by default, so a `load` followed by a `dump`
  can rewrite a file the user did not ask to change.
- Pydantic's schema output is verbose and its `$defs` names are class names, so
  the published `$id` commits us to a shape the model refactoring can disturb.
- Two sources of truth for "what is valid" now exist in practice: the model and
  the exported schema. They agree only because one generates the other, and an
  editor validating against a cached copy can disagree with the compiler.

## Alternatives considered
- **Keep reinterpreting old fields in model validators** — invisible to the
  author, untestable per version, and it accumulates in the model forever.
- **One bespoke converter per version pair** — quadratic and duplicative; the
  chain of single hops is what keeps each step small enough to review.
- **Hand-written JSON Schema** — drifts from the model at the first bump.
- **Allow best-effort downgrade of newer documents** — silently discards the
  fields the newer spec exists to express; a refusal is the honest answer.

## Verification
`tests/test_spec_migration_and_schema.py` covers the chain's continuity, a
1.0.0 document migrating and loading, the accuracy of the change list,
idempotency, refusal of both newer and pre-1.0.0 documents, round-tripping
through `dump_spec`, and the exported schema accepting
`examples/acme.system.yaml` (validated with `jsonschema` where installed,
structurally otherwise).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Migration registry, loader wiring, schema export and CLI. |
