---
id: ADR-0028
title: Memory is two-tiered — session-scoped and long-term — with governed promotion
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Security Engineering]
consulted: [Data Platform, Compliance, Product]
informed: [All engineering]
scope: [spec, compiler, targets, runtime, security]
workstreams: [WS-018]
supersedes: []
superseded_by: []
related: [ADR-0017, ADR-0022, ADR-0023, ADR-0027]
tags: [memory, foundational]
---

# ADR-0028: Memory is two-tiered — session-scoped and long-term — with governed promotion

## Context
An agent with no memory repeats its mistakes and re-derives what it worked out
yesterday. An agent that remembers everything forever is a compliance incident
waiting for an auditor: it accumulates customer data in an undeclared store,
recalls it into sessions that had no right to it, and nobody can say what it
knows.

Both failure modes come from treating memory as an implementation detail of the
runtime. It is not. What an agent may remember, for how long, and who can
recall it are the same governance questions as any other data — and our data
classification (ADR-0017) already answers them for everything except memory.

The two needs are genuinely different. Within a session an agent needs cheap,
private scratch space for what it is working on now. Across sessions it needs a
small, deliberate store of things worth keeping.

## Decision
Two tiers, with a narrow bridge between them.

**Session memory** is short term and scoped to one session. Private to the
agent, capped by item count, and it **always expires** — a session memory with
no retention still expires after a day, because an unbounded "session" store is
a long-term store nobody governed.

**Long-term memory** survives sessions and is recalled into them. Every entry
carries a data class and lives in a **namespace** with a sharing scope
(private, protected by group, or public), so who can recall what is decided by
exactly the rules that decide who can read anything else. Namespaces declare
which classes they hold and for how long.

**Promotion** is the only bridge, and it is deliberately narrow:

1. the tier's policy must allow promotion at all;
2. the data class must be one the namespace holds;
3. the agent must be able to read that class in the first place;
4. where the policy says so, a human must agree.

An agent may only promote or forget **its own** session memories. Classes
excluded from traces are excluded from durable memory unless redacted — a spec
that retains them unredacted fails validation.

Recall is `on_demand` or `automatic`; automatic recall pre-loads relevant
long-term memories into a session and records that it did.

## Scope
Agent memory across the spec, the IR, the runtime and every target. It does not
cover grounding knowledge (ADR-0023), which is read-only material the agent
consults rather than something it learned, nor the session event trace, which
is observability.

## Implementation
`spec.model.Memory`, `MemoryPolicy`, `MemoryNamespace` and a per-agent
`AgentMemoryOverride` that narrows the system contract. `memory.py` holds
`MemoryManager` (remember, recall, promote, forget, expire, stats) and
`ResolvedMemory`. The runtime exposes `memory_remember`, `memory_recall`,
`memory_promote` and `memory_forget` inside a session and pre-loads on
automatic recall. Targets bind a memory store; the local target emits a memory
service, the Terraform targets a per-provider store. Recall ranking is token
overlap — simple and explainable; a target may bind a vector index instead.

## Timeline
Phase 2 for the contract, the manager and the runtime tools. Vector-backed
recall and a memory-review UI are phase 3.

## Advantages
- Memory is governed by the same classification as every other kind of data.
- Two tiers match the two genuinely different needs instead of splitting the
  difference.
- Promotion makes "what does this agent know permanently" a deliberate,
  reviewable set rather than an accident of what it happened to see.
- Session memory cannot quietly become long-term memory.
- The clean-room agent's memory can simply be switched off, and is.

## Disadvantages
- Token-overlap recall is weak: it will miss paraphrases and return the wrong
  memory often enough to matter, and the fix (embeddings) is not built.
- Promotion is a judgement the agent makes, and agents are not good at knowing
  what will matter later; expect both hoarding and forgetting.
- Per-namespace ACLs mean a recall query fans out across scopes, which will get
  slow before it gets smart.
- Redaction is by data class, so a memory that *contains* sensitive content but
  is labelled otherwise passes straight through — classification remains manual
  and therefore wrong sometimes.
- An agent whose long-term memory is disabled silently loses context that its
  peers keep, and nothing surfaces that asymmetry except the registry.

## Alternatives considered
- **One memory store with a retention field** — simplest, and it makes session
  scratch space and durable knowledge the same governed thing, which pushes
  teams to turn governance off.
- **Memory as a capability with an MCP server** — consistent with our
  integration model, but memory is read and written on nearly every turn; the
  hop is not worth it, and the governance would be the server's, not ours.
- **No long-term memory at all** — safest, and it gives up the main reason to
  run a persistent agent organization.
- **Automatic promotion by heuristic** — removes the judgement call and makes
  the durable set unpredictable, which is the worst of both.

## Verification
`tests/test_memory.py` covers session scoping, mandatory expiry, item caps,
promotion carrying the namespace scope, promotion refused by policy and by
missing approval, owner-only promotion and forgetting, class/namespace
mismatch, refusing to remember an unreadable class, redaction, scope-respecting
recall, relevance ranking and expiry sweeps. Phase tests cover the unbound
store and the session-masquerading-as-long-term case.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Two tiers, classified namespaces, governed promotion. |
