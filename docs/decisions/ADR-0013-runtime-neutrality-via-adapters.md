---
id: ADR-0013
title: Agent frameworks are pluggable adapters, chosen at binding time
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Developer Experience]
informed: [All engineering]
scope: [spec, runtime, targets]
workstreams: [WS-008]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0005, ADR-0010]
tags: [runtime]
---

# ADR-0013: Agent frameworks are pluggable adapters, chosen at binding time

## Context
Agent frameworks are moving faster than the systems built on them. LangChain
deep agents, the OpenAI Agents SDK and LangGraph all express roughly the same
loop with incompatible APIs, and each will look different in a year. A design
that names its framework inherits that churn, and a customer with an approved
framework cannot adopt a design written for another.

## Decision
The spec never names a framework. The **binding** selects a runtime adapter per
agent or per system, from: LangChain deep agents, the OpenAI Agents SDK, native
LangGraph, or the deterministic `echo` adapter used for tests and dry runs.

Adapters take the compiled agent — system prompt, tool set, sub-agent
definitions, limits — and run one turn. Policy is enforced *before* the adapter
(in harness assembly), never inside it, so no adapter can widen an agent's
reach. Adapters import their framework lazily, so a deployment installs only
what it uses.

Encoded workflows are declarative graphs that compile onto LangGraph when it is
present and run on a built-in interpreter when it is not, so workflow semantics
do not depend on the framework choice either.

## Scope
The agent execution loop and workflow execution. Does not cover model provider
selection, which is a separate binding field.

## Implementation
`runtime/adapters.py` defines the adapter protocol and the four implementations;
`harness/builder.py` assembles the toolset and enforces policy; `spec` carries
only abstract runtime *requirements* (planning, sub-agents, handoffs), which the
binding satisfies with a concrete adapter.

## Timeline
Phase 1 for the adapter protocol; additional adapters as demand appears.

## Advantages
- Framework churn is absorbed at one seam instead of across the codebase.
- Customers can mandate their approved framework without redesigning.
- The `echo` adapter makes the whole system testable with no model calls.
- Policy sits outside the adapter, so swapping frameworks cannot widen access.

## Disadvantages
- The adapter interface is a lowest common denominator; framework-specific
  strengths (deep agents' virtual filesystem, SDK handoffs) are underused.
- Four adapters mean four code paths that can diverge in behaviour, and only the
  `echo` path is cheap to test exhaustively.
- Debugging crosses our abstraction into a third-party loop, which is where
  most production issues will actually live.
- Lazy imports hide dependency problems until runtime.

## Alternatives considered
- **Pick one framework** — simplest, but bets the product on someone else's
  roadmap and blocks customers with a different standard.
- **Write our own agent loop** — full control, and a permanent tax competing
  with well-funded frameworks.
- **Generate framework code directly with no runtime abstraction** — plausible
  for a pure compiler, but leaves us no way to test semantics.

## Verification
The full test suite runs on the `echo` adapter; a test asserts policy gates fire
identically regardless of adapter, using a stub that records tool availability.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Framework chosen in the binding, policy outside the adapter. |
