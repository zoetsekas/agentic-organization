---
id: ADR-0083
title: An agent carries its own instructions, distinct from its description
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Designer]
informed: [All engineering]
scope: [spec, compiler, designer]
workstreams: [WS-003, WS-032]
supersedes: []
superseded_by: []
related: [ADR-0026, ADR-0065, ADR-0073, ADR-0082]
tags: [spec, agent, prompt]
---

# ADR-0083: An agent carries its own instructions, distinct from its description

## Context
An agent had a `description` and no `instructions`. The composed system prompt
(`AgentIR.system_prompt`) wove the description in among the accountability and
organization context, so `description` was doing two jobs at once: the blurb
another agent reads to decide whether to delegate here, *and* the only free
text the author could put into the running prompt.

A sub-agent, by contrast, already had `instructions`. So the spec said an
author may tell a *sub*-agent how to behave but not a top-level agent — an
asymmetry with no reason behind it.

A review of the agent abstraction in Deep Agents, the OpenAI Agents SDK and
the Microsoft Agent Framework found the same split in all three: a
discovery/hand-off description kept separate from the operating instructions
(`system_prompt` in Deep Agents; `handoff_description` vs `instructions` in the
OpenAI SDK; `description` vs `instructions` in MAF). The separation is load
bearing: fold them together and either the discovery blurb leaks into the
prompt, or the operating instructions leak into the router that decides who
gets the work.

## Decision
`AgentSpec` gains `instructions: str`. It is the agent's system prompt in the
author's own words. `description` keeps its single job — what the agent is for,
read by whatever decides to delegate.

The compiler weaves `instructions` into the composed system prompt **after**
the description and **before** the accountability and organization sections,
under its own heading. Placement is the whole point: the author says how the
agent works, and then the organization states, unalterably, who it answers to,
what it may decide, and what it may reach. The instructions shape behaviour;
they do not get to rewrite the org facts (ADR-0065, ADR-0073). An agent cannot
instruct its way out of its mandate.

This is the only attribute added from the framework review. Sampling settings
(temperature, top-p and the like) were considered and left out: they are
implementation tuning, not organizational-design facts, and the vendor-neutral
question this spec does answer — *which class of model, under what cost and
region governance* — already lives in `model_policy`. Middleware, backends,
harness profiles and control-transfer handoffs were likewise judged
single-vendor mechanisms or already expressible (`interaction_flows` carries
delegate/consult/notify/escalate between agents; `subagents` are the
call-and-return case).

## Scope
`AgentSpec`, `AgentIR`, the composed system prompt, the MAF target (which
already emitted an `instructions:` block, previously from the description
alone), and the designer palette. It changes no security surface: instructions
are prompt text and grant nothing.

## Implementation
**Implemented.** The field is optional and defaults to empty, so every
existing design is unchanged and no migration is needed — the spec version does
not move. The designer's agent form gains an `instructions` control beside
`description`, each with help text saying which is which.

## Timeline
Phase 6, with the multi-sandbox work (ADR-0082).

## Advantages
- The two facts are separable, so neither pollutes the other.
- Symmetry with sub-agents, which already had it.
- The org context keeps precedence in the prompt by construction: author text
  cannot displace the accountability and mandate sections.

## Disadvantages
- One more free-text field is one more place a design can carry stale prose.
  It is optional, so a design that does not need it pays nothing.

## Alternatives considered
- **Keep overloading `description`.** What we had; it conflates discovery with
  behaviour and has no place for the org context to sit relative to the
  author's words.
- **Let instructions be the whole prompt.** Rejected: the accountability,
  mandate and people sections are not the author's to override, and a spec
  whose security facts can be prompted away is not one.
- **Add sampling settings too.** Rejected as implementation detail, per the
  project's vendor-neutral thesis.

## Verification
Tests assert: `instructions` appears in the composed system prompt under its
own heading and after the description; it does not replace the accountability
or organization sections; an agent with no instructions composes exactly as
before; and the field survives a designer save.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. `AgentSpec.instructions` added, distinct from `description`, woven into the system prompt after the description and before the org context. The only attribute taken from the framework review; sampling settings deliberately excluded. |
