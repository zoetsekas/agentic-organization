---
id: WS-027
title: Platform catalog of building blocks
status: Active
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Security Engineering, Product]
scope: [designer, security, ui]
decisions: [ADR-0041, ADR-0046]
depends_on: [WS-020]
tags: [catalog, governance]
---

# WS-027: Platform catalog of building blocks

## Objective
Give the design system one governed inventory — models, MCP servers, plugins,
tools, environment templates, permission sets, guardrails, knowledge sources —
so a designer chooses from what is approved rather than writing it by hand.

## Deliverables
- Catalog entries with owner, status, version, typed attributes, requirements
  and entitlements; twelve kinds.
- `CatalogService`: publish, search, review, entitle, retire, install counts,
  stats, and model-policy resolution.
- A seeded starting catalog: Anthropic models with real identifiers, other
  providers as proposals an operator completes.
- `/api/catalogs` endpoints, `orgagents catalogs` CLI, and a Catalog view in
  the UI with approve, restrict and retire.

## Scope
In: the inventory of platform building blocks and their approval state. Out:
the organization's own marketplace of agents and skills (WS-011), and
deploying anything.

## Approach
One governance spine across every kind, so review, entitlement and retirement
work the same way whatever the block. Seed with real figures where we have them
and honest placeholders where we do not, because guessed pricing in front of a
cost policy is worse than an empty row.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Entries, statuses, entitlements, typed attributes | Phase 4 | Done |
| M2 Service, API, CLI | Phase 4 | Done |
| M3 Seeded catalog and UI view | Phase 4 | Done |
| M4 Canvas pickers backed by the catalog | Phase 4 | In progress |
| M5 Usage tracking — which designs use which entry | Phase 4 | Done |
| M6 Import from provider and registry sources | Phase 4 | Not started |

## Dependencies
WS-020 for the designer the catalog serves.

## Advantages
- One answer to "what may we use here", with an owner and a trail.
- Retirement propagates to every future design immediately.
- Entitlements express that some blocks belong to some teams.

## Disadvantages
- **A catalog is only as current as its maintainer**; figures now carry
  provenance and a horizon (ADR-0046), but nothing refreshes itself and an
  import is only as good as the source an operator points it at.
- Twelve kinds overlap with objects also declared in the spec, and the boundary
  between catalogued and declared will confuse people.
- Approval is friction by design, and teams under deadline will route around it
  by declaring things directly in the spec — which nothing currently prevents.
- Usage is recorded from compiled designs, so a design nobody has compiled
  since the entry was added is invisible to it, and `force=True` still retires
  an entry out from under its users — the index makes that a decision, not a
  guarantee.

## Exit criteria
- The canvas picks models, servers and templates from the catalog (M4).
- ~~Retiring an entry lists the designs that use it first (M5).~~ Done:
  references are recorded from the IR and retirement is refused while an entry
  is in use unless forced.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | M5 done: catalog references recorded from compiled designs, `usage_report`, and retirement refused while an entry is in use. |
| 1.0.0 | 2026-09-20 | Opened. Entries, service, API, CLI, seed and UI landed. |
