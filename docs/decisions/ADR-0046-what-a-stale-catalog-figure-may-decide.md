---
id: ADR-0046
title: What a stale catalog figure may decide
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering]
consulted: [Platform Architecture, Compliance]
informed: [Product]
scope: [compiler, security]
workstreams: [WS-026, WS-027]
supersedes: []
superseded_by: []
related: [ADR-0040, ADR-0041]
tags: [models, catalog, governance]
---

# ADR-0046: What a stale catalog figure may decide

## Context
A `ModelPolicy` decides on catalog figures: cost per million tokens, context
window, regions. Those figures were typed in by hand, with nothing recording
where they came from or when anyone last checked them. Providers reprice and
move regions without telling us, so a cost ceiling could permit a model that is
now over it, or refuse one that is now under it, and the build would look the
same either way.

Automatic fallback (ADR-0040 v1.1.0) sharpened this. Fallback picks the
*cheapest permitted* model, ordering candidates by exactly these figures, so a
stale price no longer only mis-reports a decision — it changes which model an
agent runs on, quietly, in the direction of whatever number happens to be
lowest in the catalog.

## Decision
Every catalog entry carries `FigureProvenance`: how the figures got there
(`operator_entered`, `imported`, `placeholder`), the named source, when they
were last confirmed, and a staleness horizon (default 180 days). Figures never
confirmed are stale, not fresh; a placeholder is reported as a placeholder and
never as a figure.

A stale figure then does three things and no more:

1. **It does not refuse a build on its own.** A model that satisfies the policy
   on a stale figure is still permitted, and the verdict carries the marker
   `stale figure` with the provenance, through `ModelDecision.stale_figures`
   into `ModelIR.approval_reason` and onto a registry review flag.
2. **A refusal on a stale figure still refuses**, naming the staleness. The
   safe side of a cost ceiling is "too expensive"; the reason tells the
   operator which number to refresh and re-run.
3. **Fallback prefers figures we still believe.** `permitted_models` orders by
   `(stale, cost)`, so the automatic choice is the cheapest model whose price
   is within its horizon, and a stale row is reached only when nothing fresh
   qualifies — where it is flagged on the IR and in the registry.

## Scope
Binds the catalog's model decisions and the compile-time model approval on the
IR and registry. It does not bind non-model entries (nothing decides on their
numbers), does not change the spec language, and does not make refreshing
mandatory — an installation with no source keeps operator-entered figures and
watches them age.

## Implementation
- `catalogs/models.py`: `FigureProvenance`, `FigureMethod`, `CatalogEntry.
  provenance`, `figures_stale()`, `figure_state`.
- `catalogs/sources.py`: the `FigureSource` protocol, `FileFigureSource`, and
  `HttpFigureSource` whose transport is injected so no HTTP client becomes a
  dependency. Only the figure fields are refreshable; class tags and summaries
  stay editorial.
- `catalogs/service.py`: `refresh_figures` (recording provenance, leaving
  uncovered entries untouched and reporting them), `stale_entries`, staleness
  in `check_model`/`permitted_models` and `stats`.
- `compiler/registry.py`: a review flag for agents whose model verdict used a
  stale figure.
- `orgagents catalogs refresh|stale|models`.

## Timeline
Phase 4, with WS-026 M6.

## Advantages
- The catalog can now say how old a number is, which it could not before.
- The consequence is proportionate: nothing breaks that worked yesterday, and
  the decision that silently turns on a stale figure is the one flagged.
- Fallback stops preferring a model merely because its price record is old.
- A source that does not cover an entry cannot blank it or launder it as fresh.

## Disadvantages
- **A stale figure can still permit a model that is now too expensive.** We
  chose visibility over refusal, so an organization that reads no registry gets
  the old behaviour with a footnote.
- The horizon is a guess. 180 days is arbitrary: too long for a provider that
  reprices quarterly, too short for a stable on-premises deployment, and it is
  a single global default rather than per-provider.
- Ordering fallback by `(stale, cost)` can pick a *more expensive* model than
  the policy would otherwise have chosen — freshness beats price, and that is
  a cost somebody pays without being asked.
- Provenance is per entry, not per field, so refreshing a price re-confirms the
  context window too even if the source said nothing about it. Mitigated by
  recording which fields the source actually covered, not by preventing it.
- Staleness is detected, never repaired: with no network here, refreshing is an
  operator running an import against a file they maintain, which can itself be
  stale and will still read as `imported`.
- The marker travels as a substring in a reason string, which the registry
  matches on. A typed field on `ModelIR` would be better; that file belongs to
  another workstream's change right now.

## Alternatives considered
- **Refuse any build whose decision turns on a stale figure** — correct in
  principle and unusable in practice: the day a horizon passes, every build in
  the organization fails for a reason nobody caused.
- **Exclude stale entries from `permitted_models`** — silently narrows what is
  approved, and against a thin catalog leaves a strict policy with nothing.
- **Keep trusting figures, report staleness only in `stats`** — what we had.
  Nobody reads stats during a build, and fallback would still prefer the stale
  cheap row.
- **Downgrade a stale entry's status to `in_review` automatically** — a status
  is a review decision with an owner; having a clock change it would forge one.

## Verification
`tests/test_catalog_freshness.py`: provenance recorded on import, a figure past
its horizon reported stale, an uncovered entry untouched and reported, refresh
idempotent, staleness in `stats`, a permitted-but-stale verdict marked, a stale
refusal still refusing, fallback preferring fresh figures, and the marker
present on the IR and in the registry.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Provenance, horizons, and a proportionate consequence. |
