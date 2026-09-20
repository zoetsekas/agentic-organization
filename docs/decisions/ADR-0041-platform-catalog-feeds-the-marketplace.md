---
id: ADR-0041
title: A governed platform catalog is what the marketplace and the designer offer
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Security Engineering]
consulted: [Product, Compliance, Data Platform]
informed: [All engineering]
scope: [spec, designer, security, ui]
workstreams: [WS-027]
supersedes: []
superseded_by: []
related: [ADR-0022, ADR-0031, ADR-0034, ADR-0040]
tags: [catalog, governance, product]
---

# ADR-0041: A governed platform catalog is what the marketplace and the designer offer

## Context
A designer drawing an agent has to choose: which model, which MCP server, which
sandbox template, which plugin, which permission set. Those choices were
scattered — sandbox templates in code, MCP servers in bindings, permissions
written by hand per role, models named in a config file. There was no answer to
"what may we use here", and no way to retire something and have it stop being
used.

Our existing marketplace (`catalog.py`) lists what *this organization built*:
agents, skills, workflows to share. That is a different question from what the
*platform* has approved for use.

## Decision
A **platform catalog**: the inventory of building blocks a design may choose
from. Twelve kinds, one governance spine.

Kinds: `model`, `mcp_server`, `plugin`, `tool`, `environment_template`,
`skill`, `guardrail`, `permission_set`, `knowledge_source`, `workflow`,
`agent_template`, `endpoint`.

Every entry carries: an **owner**, a **status** (proposed → in review →
approved / restricted / deprecated / retired / rejected), a version, tags,
typed attributes for its kind, what using it **requires** elsewhere, and an
**entitlement** naming the groups, workspaces and environments that may select
it.

Three rules follow:

1. **Only approved entries are selectable.** Proposed, rejected and retired
   entries exist so that "we looked at this" survives the person who looked.
2. **Restricted entries need an entitlement.** A repository MCP server is
   available to engineering and to nobody else.
3. **Retiring propagates.** A retired model stops satisfying any policy, and
   the refusal names its replacement.

The catalog is what the designer's palette and the marketplace both read, and
what model policy resolves against (ADR-0040). It ships seeded, so a new
installation is not an empty shelf — with Anthropic models listed by their real
identifiers and other providers as **proposals an operator must complete**,
because publishing guessed pricing and context windows would put confident
wrong numbers in front of a cost policy.

## Scope
The inventory of platform building blocks and their approval state. It does not
replace the organization's own marketplace of agents and skills, and it does
not deploy anything.

## Implementation
`catalogs/models.py` (entries, statuses, entitlements, typed attributes per
kind), `catalogs/service.py` (publish, search, review, entitle, retire, install
counts, stats, and the model-policy resolution), `catalogs/seed.py` (the
starting shelf). API under `/api/catalogs`, a CLI (`orgagents catalogs`), and a
Catalog view in the UI with approve / restrict / retire.

## Timeline
Phase 4.

## Advantages
- One answer to "what may we use here", with an owner and a review trail.
- Retiring something removes it from every future design at once.
- Entitlements express that some blocks belong to some teams.
- The designer's choices are exactly the governed set — the palette cannot
  offer something nobody approved.
- Seeded with real figures where we have them and honest placeholders where we
  do not.

## Disadvantages
- A catalog is only as current as the person maintaining it; stale model
  pricing silently misinforms cost policy, and nothing detects that.
- Twelve kinds is a lot of surface, and several (plugin, tool, skill) overlap
  with objects that also exist in the spec — the boundary between "catalogued"
  and "declared" will confuse people.
- Approval adds a step between wanting a building block and using one, which is
  the point and also the friction.
- Entitlement by group assumes group hygiene we do not manage.
- Two catalogs — this and the organization's marketplace — is a naming problem
  we have not fully solved.

## Alternatives considered
- **Extend the existing marketplace** — conflates "what we built to share" with
  "what the platform permits", which are different questions with different
  owners.
- **Configuration files per kind** — what we had; no status, no owner, no
  entitlement, no retirement.
- **No catalog; validate against the binding** — leaves the designer with no
  list to choose from and no way to see what is approved.

## Verification
Tests cover every seeded kind being present, only approved entries being
selectable, restricted entries requiring an entitlement, environment-scoped
entitlements, retirement removing an entry from selection and naming its
successor, review recording who decided, search by kind/status/text, stats
surfacing unreviewed entries, publishing and install counts, and idempotent
seeding.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Twelve kinds, one governance spine, feeding the designer and model policy. |
