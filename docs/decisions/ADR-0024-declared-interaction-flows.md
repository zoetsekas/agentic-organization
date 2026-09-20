---
id: ADR-0024
title: Lateral interaction between agents is declared, directional and typed
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Product, Security Engineering]
informed: [All engineering]
scope: [spec, compiler, runtime]
workstreams: [WS-013, WS-003]
supersedes: []
superseded_by: []
related: [ADR-0006, ADR-0008, ADR-0021]
tags: [organization]
---

# ADR-0024: Lateral interaction between agents is declared, directional and typed

## Context
ADR-0006 derives delegation from the org tree, which is right for hierarchy and
silent about everything else. Real organizations are full of lateral contact
that is not delegation: an analyst *consults* the SRE about pipeline health
without being able to task them; an engineer *notifies* finance that a schema
changed; an SRE *escalates* a SEV1 straight to the CTO, bypassing their lead.

Our only expression of this was `peers`, an untyped list that conflated all of
it with delegation. Agency Swarm's directional flows (`CEO > Developer`, and
not the reverse) are the better idea: interaction is declared, and direction is
part of the declaration. The alternative — mesh topology, every agent reachable
from every agent — destroys the reviewable permission story that is the point
of having an org model at all.

## Decision
An **interaction flow** is a declared, directional, typed edge:

| Kind | Means |
|---|---|
| `delegate` | may hand work over and expect completion |
| `consult` | may ask; may not instruct |
| `notify` | may inform; expects no reply |
| `escalate` | may raise for a decision, including across the hierarchy |

Flows are one-way: the reverse direction requires its own declaration. Only
`delegate` flows widen an agent's delegation set — `consult`, `notify` and
`escalate` are recorded and enforced as their own, narrower affordances, so
declaring a consult does not hand over the ability to assign work.

Undeclared lateral contact is not permitted; it escalates through the tree.

## Scope
Agent-to-agent interaction outside the hierarchy. Contact with humans is
ADR-0021; permissions over resources are ADR-0008.

## Implementation
`spec.model.InteractionFlow` with `FlowKind`; validation rejects unknown
endpoints and self-flows. `compiler.ir` resolves per agent into `consults`,
`notifies`, `escalation_flows` and, for delegate flows only, additional
`delegates_to` entries. The registry renders every flow with its stated reason,
so lateral contact is reviewable as a table.

## Timeline
Phase 2, with the channel work — the two together describe how anything reaches
anyone.

## Advantages
- Expresses the matrix relationships a strict tree cannot, without abandoning
  the tree.
- Typing the edge prevents "we let them talk" becoming "they can task us".
- Each flow carries a stated reason, which makes the registry a review artifact.
- Declared-only contact keeps the permission surface enumerable.

## Disadvantages
- Another thing to declare: teams will forget a flow and see escalation where
  they expected a quick question.
- Four kinds is a taxonomy, and taxonomies are argued about; the boundary
  between `consult` and `delegate` is genuinely fuzzy in practice.
- Flows are static; real organizations grant temporary contact for a project,
  and we have no expiry.
- A dense flow graph is as unreviewable as a mesh — nothing here stops that,
  only makes it visible.

## Alternatives considered
- **Untyped `peers`** — what we had; conflates consulting with tasking.
- **Mesh by default** — no reviewable permission story.
- **Inferring flows from observed traffic** — attractive for discovery,
  disastrous as an authorization source.

## Verification
IR tests assert that consult flows appear as `consults` and do **not** appear
in `delegates_to`, and that delegate flows do. Validator tests reject unknown
endpoints and self-flows.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Typed directional flows; only `delegate` widens delegation. |
