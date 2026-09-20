---
id: ADR-0027
title: Sub-agents are tools, not members of the organization
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Product]
informed: [All engineering]
scope: [spec, compiler, runtime]
workstreams: [WS-017]
supersedes: []
superseded_by: []
related: [ADR-0006, ADR-0008, ADR-0024, ADR-0029]
tags: [organization, runtime]
---

# ADR-0027: Sub-agents are tools, not members of the organization

## Context
The runtime could already spawn a sub-agent, and it did so by creating a real
`Agent`: an org-unit member with a reporting line, a human counterpart
inherited from its parent, its own session and its own place in the tree. That
is the wrong shape for what people actually want sub-agents for — "research
this topic", "review this draft", "check these figures". Those are *calls*, not
hires.

Treating them as org members has three costs. The org chart fills with
ephemeral rows that mean nothing to anyone reading it. Every sub-agent inherits
a human counterpart who never agreed to be accountable for it. And the
permission story gets harder, because a new org member is a new subject to
reason about.

## Decision
A sub-agent is a **tool**. It is declared inline on its parent, exposed as
`subagent_<id>`, called with a task, and returns a result. It has:

* **no reporting line** — it is not in the org tree and not a delegation target;
* **no human counterpart** — its parent's owner remains accountable;
* **no session of its own** — its call is an event in the parent's session;
* **no memory beyond the call** — nothing it learns survives it;
* **no authority** — it cannot delegate, escalate or contact a human.

The safety property is **narrow-only inheritance**: a sub-agent runs under its
parent's identity with a subset of the parent's capabilities, tools and
knowledge, and cannot change the isolation boundary. Naming nothing means
reaching nothing, not inheriting everything — the default is the safe one.

Recognized kinds (`research`, `review`, `summarize`, `extract`, `critique`,
`plan`, `verify`) ship with instructions, so a useful sub-agent is a few lines
of spec. Each declares what it `returns`, because a tool with an undefined
result is hard to use well.

Real delegation between real agents remains the org tree (ADR-0006) and
declared flows (ADR-0024). Sub-agents do not replace it.

## Scope
Task-scoped workers invoked by one agent. It does not cover delegation to
another agent, which stays hierarchical and creates a child session.

## Implementation
`spec.model.SubAgentSpec` declared inline on the agent;
`runtime/subagents.py` resolves them with narrow-only enforcement and composes
a prompt that states what the sub-agent is *not*; `SubAgentRunner` exposes them
as callables; the runtime engine filters the parent's toolset to the named
slice and logs each call as a `subagent` event in the parent's session. The
validator rejects any sub-agent that widens capabilities, tools, knowledge or
environment.

## Timeline
Phase 2. The previous `spawn_subagent` runtime path is superseded in practice
by this declarative form.

## Advantages
- The org chart stays a picture of the organization, not of a call stack.
- No ephemeral worker inherits an accountable human who never agreed.
- Narrow-only inheritance means a sub-agent needs no permission model of its
  own — it is provably a subset of its parent.
- Declared kinds make the common cases short and consistent.
- Calls appear in the parent's trace, where the work actually happened.

## Disadvantages
- Sub-agents are invisible in the org chart by design, so a reader cannot see
  how much work an agent actually farms out without reading the registry.
- Inline declaration means no reuse: two agents wanting the same reviewer
  declare it twice, and they will drift.
- A tool-shaped call is synchronous and bounded, so genuinely long-running work
  must still become delegation — and the boundary is a judgement call.
- Filtering the parent's toolset by name matching is coarse; a precise
  capability-to-tool mapping would be better and is not yet built.

## Alternatives considered
- **Keep sub-agents as org members** — what we had; pollutes the org, invents
  accountability, and complicates permissions.
- **A shared library of sub-agents referenced by id** — solves reuse, and adds
  a second place permissions must be checked; a candidate once the drift is
  real.
- **No sub-agents; use workflows** — workflows are for auditable processes,
  not for "go and look this up", and forcing everything into a graph is worse.

## Verification
`tests/test_subagents_and_pairing.py` asserts sub-agents become tools and not
org members, that widening capabilities, tools or the environment is refused
both at resolve time and by the validator, that the composed prompt states the
constraints, and that a call is logged inside the parent's session.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Sub-agents are tools with narrow-only inheritance. |
