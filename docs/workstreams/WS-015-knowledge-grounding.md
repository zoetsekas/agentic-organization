---
id: WS-015
title: Knowledge grounding sources and retrieval
status: Proposed
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Data Platform
contributors: [Platform Architecture]
scope: [spec, compiler, targets, runtime]
decisions: [ADR-0023]
depends_on: [WS-005, WS-011]
tags: [knowledge]
---

# WS-015: Knowledge grounding sources and retrieval

## Objective
Give agents governed access to the material they actually read most — handbooks,
runbooks, glossaries, tickets — with classification, citation and a visible
record of who reads what.

## Deliverables
- `KnowledgeSource` in the spec and `AgentSpec.knowledge` references.
- `KnowledgeBinding` naming the concrete system, index and credential
  reference per target.
- `KnowledgeIR` merging contract and binding; the source's secret attached to
  the reading agent's identity.
- A `knowledge_index` neutral resource and per-provider index resources.
- Registry section listing sources, their classes and citation requirements.
- Retrieval path: chunking, ranking, freshness enforcement and citation
  rendering (phase 3).

## Scope
In: declaring, binding and governing grounding sources. Out: writing to systems
of record (that is a capability, ADR-0010), and building our own vector store.

## Approach
Treat grounding as classified, read-only, citation-bearing access, so the
placement and egress rules that already exist apply to it. Keep the source
abstract in the spec and let the binding name the index, exactly as
capabilities work.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Spec model, binding and IR resolution | Phase 2 | Done |
| M2 Target resources and registry section | Phase 2 | Done |
| M3 Retrieval path with citation rendering | Phase 3 | Not started |
| M4 Freshness enforcement | Phase 3 | Not started |
| M5 Retrieval quality evaluation | Phase 3 | Not started |

## Dependencies
WS-005 for the IR; WS-014, whose evaluation runner is how retrieval quality
will eventually be judged.

## Advantages
- Who reads what becomes visible in the registry and enforced in IAM.
- Grounding material inherits data classification.
- Citation by default makes grounded answers checkable.
- Swapping the backing index is a binding change.

## Disadvantages
- Declaring a source says nothing about retrieval quality — the thing users
  actually judge — and the spec cannot express it.
- `freshness_seconds` is declared but unenforced until M4, so it currently
  reads as a guarantee we do not provide.
- Per-source credentials multiply what each identity holds.
- The abstract `kind` list will not fit every source, and binding options are
  the escape hatch that slightly erodes neutrality.

## Exit criteria
- An agent answers from a declared source with citations the reader can follow.
- Stale material is refused or refreshed per the declared freshness bound.
- The registry shows every source, its classes and its readers. ✔

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Declaration, binding and IR landed; retrieval is phase 3. |
