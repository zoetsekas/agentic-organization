---
id: WS-018
title: Memory management — session and long term
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Data Platform, Security Engineering]
scope: [spec, compiler, targets, runtime, security]
decisions: [ADR-0028]
depends_on: [WS-004, WS-005]
tags: [memory, foundational]
---

# WS-018: Memory management — session and long term

## Objective
Give agents memory that is useful within a session and durable across them,
governed by the same classification as every other kind of data — so an agent
stops re-deriving yesterday's work without quietly accumulating a store nobody
declared.

## Deliverables
- `Memory`, `MemoryPolicy`, `MemoryNamespace` and a per-agent override.
- `memory.py`: `MemoryManager` (remember, recall, promote, forget, expire,
  stats) and `ResolvedMemory`.
- Runtime tools `memory_remember`, `memory_recall`, `memory_promote`,
  `memory_forget`, plus automatic pre-loading where the policy asks for it.
- Validation: namespace scoping, class containment, trace-excluded classes,
  session retention that is actually short.
- Target bindings: a local memory service and per-provider stores; registry
  tables for per-agent memory and namespaces.

## Scope
In: what an agent remembers and who can recall it. Out: grounding knowledge
(WS-015), the session event trace (observability), and model context management
inside a single turn.

## Approach
Two tiers because the needs are genuinely different, and one narrow bridge
between them. Reuse the data-classification rules rather than inventing memory
ACLs. Make session memory always expire, so it cannot silently become the
durable store. Keep recall explainable first and fast later.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Contract, manager and governance rules | Phase 2 | Done |
| M2 Runtime tools and automatic recall | Phase 2 | Done |
| M3 Target bindings and registry views | Phase 2 | Done |
| M4 Embedding-backed recall | Phase 3 | Not started |
| M5 Memory review and redaction UI | Phase 3 | Not started |
| M6 Consolidation — merging near-duplicate memories | Phase 3 | Not started |

## Dependencies
WS-004 for classification and group membership; WS-005 for the IR.

## Advantages
- Memory is governed exactly like other data, with no second ACL model.
- Promotion makes the durable set deliberate and reviewable.
- Session memory cannot become long-term memory by omission.
- An agent that must not remember — the clean room — simply does not.

## Disadvantages
- **Token-overlap recall is weak.** It misses paraphrases and returns the wrong
  memory often enough to matter; until M4 this is the honest limit of the
  feature, and it is the part users will judge.
- Promotion is the agent's judgement, and agents are poor judges of future
  usefulness — expect hoarding and forgetting in the same fleet.
- Per-namespace ACLs make recall fan out across scopes; it will get slow before
  it gets smart.
- Redaction is by declared class, so mislabelled sensitive content passes
  through untouched.
- Nothing yet merges near-duplicates, so long-term memory drifts toward noise
  (M6).

## Exit criteria
- Recall returns the right memory often enough to be trusted (M4).
- Nothing reaches long-term memory that its namespace does not permit. ✔
- An operator can review and redact what an agent remembers (M5).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Contract, manager, runtime tools and bindings landed. |
