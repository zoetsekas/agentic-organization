---
id: ADR-0038
title: Shared operating instructions live at the organization and the team
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Product, Platform Architecture]
consulted: [Developer Experience]
informed: [All engineering]
scope: [spec, compiler, runtime]
workstreams: [WS-024]
supersedes: []
superseded_by: []
related: [ADR-0007, ADR-0006, ADR-0029]
tags: [organization, prompts]
---

# ADR-0038: Shared operating instructions live at the organization and the team

## Context
Agency Swarm ships an `agency_manifesto.md`: instructions every agent in the
agency carries, separate from each agent's own. We had nothing equivalent.
Responsibilities live on roles (ADR-0007) and skills carry instructions
(ADR-0029), so "how everyone here works" had only one home — repeated in every
role, where it drifts within a quarter.

Every organization has these. *Say what you do not know. Never move customer
identifiers outside the clean room. Declare a severity before doing anything
else.* Some apply company-wide; some belong to one function.

## Decision
Two levels of shared instruction, both declared:

* **`operating_principles`** on the system — carried by every agent.
* **`shared_instructions`** on a team — carried by its members, and inherited
  by nested teams from their parents.

They compose into the agent's prompt **with their source**, so a reader can see
that a rule came from the organization or from Finance rather than from the
agent's own role. Nothing is silently inherited without attribution.

This is instruction only. It grants nothing, constrains no access, and is not a
guardrail — a principle an agent ignores has no enforcement behind it, which is
exactly why guardrails (ADR-0035) exist separately.

## Scope
Prompt composition. It does not affect permissions, capabilities or any
enforced boundary.

## Implementation
`SystemSpec.operating_principles` and `Team.shared_instructions`;
`SystemSpec.shared_instructions_for(agent_id)` walks the team chain outermost
first; the IR carries `(source, text)` pairs onto every agent and
`AgentIR.system_prompt` renders them under "Shared operating principles" with
`_(from <source>)_` attribution.

## Timeline
Phase 3.

## Advantages
- One place to state how the organization works, instead of a copy per role.
- Team-level instructions match how functions actually differ.
- Attribution in the prompt makes provenance visible to the agent and to a
  reviewer reading the composed prompt.
- Changing a principle changes it everywhere at once.

## Disadvantages
- **Instructions are not enforcement.** A principle in a prompt is followed at
  the model's discretion; anything that must hold belongs in a guardrail or a
  permission, and confusing the two is the obvious misuse of this feature.
- Prompt bloat: principles plus team instructions plus role responsibilities
  plus skills is a lot of preamble, and every token is paid on every turn.
- Inheritance is implicit — a nested team's members carry instructions written
  three levels up, and only the composed prompt shows it.
- A tempting place to put rules that should be rules, because it is the easiest
  block to edit.

## Alternatives considered
- **Repeat the text in every role** — what we had; drifts immediately.
- **One global instruction file outside the spec** — invisible to review and to
  the compiler.
- **Make principles enforceable** — that is what guardrails are; overloading
  instructions with enforcement would blur the line this decision draws.

## Verification
Tests assert organization and team instructions reach the prompt with their
source, that an engineering agent does not receive Finance's instructions, and
that attribution appears in the composed prompt.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Organization principles and team instructions, composed with attribution. |
