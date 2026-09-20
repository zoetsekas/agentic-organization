---
id: ADR-0026
title: An agent pairs with one or more humans in named roles
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Product]
consulted: [Security Engineering, Compliance]
informed: [All engineering]
scope: [spec, compiler, runtime, ui]
workstreams: [WS-016]
supersedes: []
superseded_by: []
related: [ADR-0007, ADR-0019, ADR-0021, ADR-0022]
tags: [organization, human-in-the-loop]
---

# ADR-0026: An agent pairs with one or more humans in named roles

## Context
Every agent had exactly one `human`, and that single field was doing four
incompatible jobs: naming who is accountable, naming who approves gated
actions, naming who reviews the output, and naming who gets told what happened.
Those are different people in every organization we have modelled.

The consequences were concrete. An owner on holiday blocked every approval,
because approval authority was welded to accountability. Reviewers had nowhere
to be recorded, so review happened in a channel and left no trace in the
design. And a person who works with six agents appeared six times with no way
to ask "what is this person on the hook for".

## Decision
Pairing is **many-to-many and role-typed**. An agent carries a list of paired
humans, each with one or more roles:

| Role | Means |
|---|---|
| `owner` | accountable for the agent. **Exactly one**, always. |
| `approver` | may approve gated actions, optionally a named subset |
| `reviewer` | reviews output; does not gate it |
| `escalation` | contacted when the owner does not answer |
| `operator` | runs and maintains it day to day |
| `stakeholder` | informed; no decision rights |

A person may hold several roles on one agent and be paired with many agents.
Each pairing may name a preferred channel and the person's own working hours,
which feed the routing and escalation contract (ADR-0021).

Two rules are enforced, not advisory: **exactly one owner** per agent, and an
action that is gated must have at least one paired approver who covers it. An
agent that gates an action nobody can approve is a system that stops.

The pre-1.1 single `human:` field still loads, lifted into one owner pairing,
so existing specs keep working.

## Scope
The spec's agent block, the IR, the registry, the runtime and the phase gate.
It does not change who may *do* what — that remains roles and permissions
(ADR-0007, ADR-0008). This is about who the agent answers to.

## Implementation
`HumanCounterpart` gains `roles`, `channel` and `working_hours`;
`AgentSpec.humans` replaces `human`, with a `model_validator` lifting the old
field. `AgentSpec.owner`, `humans_with()` and `approvers_for()` resolve
pairings. The IR carries every pairing and composes them into the system
prompt. The registry gains a pairings table and a **person-centric** table
answering "what is this person on the hook for". Phase checks cover pairing,
single ownership, approver coverage and a fallback human.

## Timeline
Phase 2, with the channel work that routes to these people.

## Advantages
- Accountability, approval, review and notification stop sharing one field.
- Approvals survive one person's absence, because approver is its own role.
- The registry answers the question a manager actually asks: what is this
  person responsible for across the fleet?
- Per-pairing channels and hours make routing precise instead of generic.

## Disadvantages
- More to declare per agent, and the temptation is to pair one person with
  every role, which recreates the old problem with extra syntax.
- Pairings are named individuals, so they rot as people change jobs; nothing
  here detects a departed employee still listed as an approver.
- Exactly-one-owner is a real constraint that co-ownership arrangements will
  fight; we think ambiguous accountability is worse.
- Many-to-many pairing makes "who approved this" a query rather than a lookup.

## Alternatives considered
- **Keep one human, add an approvers list** — fixes the worst symptom and
  leaves reviewers and stakeholders unrecorded.
- **Pair with groups rather than people** — durable against staff changes, and
  nobody is accountable, which is the opposite of what this decision is for.
- **Derive approvers from the org chart** — the human org chart is not in the
  spec, and inferring authority from hierarchy is how over-approval happens.

## Verification
`tests/test_subagents_and_pairing.py` covers multi-role pairing, one person
across several agents, per-action approver resolution, the rejection of zero or
multiple owners, the gated-action-without-approver error, and that the legacy
`human:` field still loads. Phase tests cover the ownership and approver gates.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Many-to-many role-typed pairing; exactly one owner. |
