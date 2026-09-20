---
id: ADR-0037
title: Output contracts are checkable shapes, not prose promises
status: Accepted
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Developer Experience]
informed: [All engineering]
scope: [spec, compiler, runtime]
workstreams: [WS-024]
supersedes: []
superseded_by: []
related: [ADR-0027, ADR-0029, ADR-0035]
tags: [runtime, contracts]
---

# ADR-0037: Output contracts are checkable shapes, not prose promises

## Context
ADR-0027 made every sub-agent declare what it `returns`, and that declaration
is prose: *"a cited findings list"*. It tells a person what to expect and tells
the calling code nothing it can verify. When the sub-agent returns a paragraph
instead, the caller discovers it by misbehaving.

Both the OpenAI Agents SDK (output types) and Agency Swarm (Pydantic-validated
tools) treat the returned shape as part of the contract. We treated it as
documentation.

## Decision
An **output contract** is a named, reusable shape declared in the spec and
referenced by an agent, a sub-agent or a tool. The schema is a deliberately
small JSON-Schema subset — `type`, `properties`, `required`, `items`, `enum` —
large enough to make "a list of findings, each with its source" checkable and
small enough to read in the spec.

Each contract states what happens on violation: `retry` (with the errors fed
back, up to a limit), `block`, or `flag`. Prose `returns` stays, because a
human still needs to know what the thing is *for*; the contract says what it
must *look like*.

Tools opt in with `validate_output`, since many wrap something whose shape is
already fixed elsewhere.

## Scope
The shape of what an agent, sub-agent or tool returns. It does not attempt to
validate *correctness* — a conforming answer can be entirely wrong, and nothing
here says otherwise.

## Implementation
`spec.model.OutputContract`; `guardrails.validate_shape` implements the subset
and reports every problem rather than the first; the IR resolves an agent's
contract onto it and mentions it in the composed prompt; the runtime checks the
result and records an `output_contract` event with the errors. `schema` is
checked by the `SCHEMA` guardrail check, so a shape violation can also be a
boundary decision.

## Timeline
Phase 3 for the contracts themselves; automatic retry-with-errors landed in
Phase 4 (WS-024 M6).

## Advantages
- A caller can rely on a shape rather than parsing prose hopefully.
- Reusable contracts stop each sub-agent inventing its own format.
- Errors name every problem and its path, so a retry has something to act on.
- The same validator serves guardrails, so shape is enforceable at a boundary.

## Disadvantages
- **Shape is not correctness.** A perfectly conforming findings list can cite
  sources that do not support it, and this decision does nothing about that.
- A JSON-Schema subset will be too small for someone; the escape hatch is a
  looser schema, which weakens the guarantee it was adopted for.
- Declaring a contract makes models return JSON, which is often worse prose;
  where the consumer is a person that is a downgrade.
- Retry spends a whole extra model call per attempt on a formatting problem,
  and an agent that cannot produce the shape burns the full budget before
  failing — so a badly-drawn contract is now expensive as well as wrong.
- A retried answer is a second answer: it may fix the shape and change the
  substance, and nothing here checks that it did not.

## Alternatives considered
- **Full JSON Schema** — complete, and unreadable in a spec people review.
- **Pydantic models in the spec** — expressive, and couples the
  implementation-neutral document to Python (ADR-0004).
- **Prose only** — what we had; uncheckable.

## Verification
Tests cover schema validation reporting every problem with its path, enum and
scalar type checks, and the runtime recording a violation for prose returned
where a contract was declared, while accepting conforming JSON.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Retry-with-errors implemented in the runtime, bounded and recorded per attempt. |
| 1.0.0 | 2026-09-20 | Accepted. Reusable contracts over a small schema subset, checked at runtime. |
