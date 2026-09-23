---
id: ADR-0098
title: An agent can leave, and leaving takes effect
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Security Engineering]
informed: [All engineering]
scope: [runtime, compiler]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0065, ADR-0070, ADR-0093, ADR-0094, ADR-0073]
tags: [lifecycle, offboarding, mandates, delegation, security]
---

# ADR-0098: An agent can leave, and leaving takes effect

## Context
`OrgChart` has `add_agent`. It has no way to remove one. An agent joins an
organization and never leaves it.

That is not a missing convenience. Reconciling a design that no longer contains
an agent against a running system shows what it actually costs:

```
cs_agent removed from the design; still in the runtime: True
  still holds mandate: ['issue_refund']
  may the CGO still delegate to it?  True
```

The design says this agent is gone. The running system says it may still be
handed work, and still holds the authority to issue a refund. In human terms
somebody left the company and their badge still opens the door.

`load_system` is where the gap lives: it adds every agent in the IR and removes
nothing, so removal from the design is the one change that does not propagate.
Every other kind of change — a narrowed mandate, a dropped permission, a
changed reporting line — lands. Deletion is silently ignored, which is the
worst of the three possible behaviours, because the reviewer who approved the
removal has no way to tell it did not happen.

This is the mirror of ADR-0094. That ADR covers an agent that *cannot run
right now*. Nothing covered one that is *not coming back*.

## Decision
An agent can be **decommissioned**: kept as a record, inert as a principal.

**1. Decommissioning is recorded, never deleted.** The agent stays in the
store with `decommissioned_at` and a reason. Deleting it outright would take
its sessions' `agent_id` with it and break every trace that ran through it, and
an audit trail that loses the agents is not one. What ends is its standing as a
principal, not its existence as a fact.

**2. An agent that has left may not be delegated to, and may not delegate.**
`can_delegate` refuses in both directions, first, before any reach rule is
consulted — reports, peers, missions, subtree and shared services alike. A
shared service is the case that would otherwise slip through, because anyone
may call one.

**3. It holds no mandate.** `mandate_holder` walks past it rather than
returning it, so a decision that used to be its lands on whoever is next up the
line — which is what already happens when nobody in the line holds it, and is
the behaviour the rest of the platform is written against.

**4. Removal from the design takes effect on load.** `load_system` reconciles:
an agent in the store that the IR no longer contains is decommissioned, with
the reason saying so. A design is the statement of record, and a change to it
that a deployment ignores is the class of thing this platform refuses
(ADR-0073).

**5. Leaving settles the work, and names what it could not.** Its unsettled
sessions are failed with a reason (the same rule ADR-0093 applies to a handle
no worker holds), any standing-in it was doing ends, and any standing-in *for*
it ends. The return value names what it was holding, because "this agent left
with four things in flight" is exactly what somebody needs to see and exactly
what a silent removal destroys.

**6. A successor that has left is a broken succession, caught at the gate.**
Removing an agent that another named as its successor already fails validation
(`unknown_successor`) — no new rule is needed, and this ADR only records that
the case is covered, because "who covers me now" is the question a departure
most often leaves dangling.

## Scope
`Agent`, `OrgChart`'s delegation and mandate resolution, and the runtime
loader's reconciliation. No change to the spec's shape: an agent leaves by
being removed from the design, which is how an organization already says it.

## Implementation
- `Agent.decommissioned_at` and `Agent.decommission_reason`; `is_active`.
- `OrgChart.decommission(agent_id, reason)` returning what was left behind.
- The refusals in `can_delegate` and the skip in `mandate_holder`.
- `load_system` decommissions agents the IR no longer names.
- The runtime settles a departing agent's unsettled sessions.

## Timeline
Delivered with this ADR.

## Advantages
- Removing an agent from a design does what the person approving the removal
  believes it does.
- Authority ends when the agent does, in both directions and including the
  shared-service path that reach rules would otherwise leave open.
- Work in flight when somebody leaves is named rather than abandoned quietly.
- The audit trail survives the departure, because the record does.

## Disadvantages
- **Reconciliation on load is a blunt instrument.** Loading a partial or
  mistaken IR against a live store would decommission agents nobody meant to
  remove. The record and reason make it recoverable and visible, and it is
  still a foot-gun that did not exist before.
- **Inert-but-present is a third state to reason about**, after "there" and
  "not there", and code that iterates `agents()` sees it unless it asks.
- **No return path is specified.** Recommissioning is not modelled, because an
  agent that comes back is better expressed as the design containing it again —
  but that reasoning is asserted here rather than demonstrated.
- **Decommissioning does not revoke anything outside this platform.** The
  generated IAM bindings, service accounts and secrets are Terraform's, and
  applying the new configuration is what actually closes those doors. The
  conformance reports already say a target carries what it carries; this is a
  case where an organization could believe more happened than did.

## Alternatives considered
- **Delete the agent.** Rejected: its sessions reference it, and a trace whose
  agent cannot be resolved is a worse record than one naming an agent that has
  left.
- **Refuse to load an IR that drops an agent.** Rejected: an organization is
  allowed to let somebody go, and making the common case an error would train
  people to force it.
- **Decommission only on an explicit call, never on load.** Considered
  seriously — it avoids the foot-gun above. Rejected because it leaves the
  demonstrated bug open in the exact case that matters: somebody edits the
  design, review approves it, it deploys, and nothing happens.
- **Keep the mandate and only cut delegation.** Rejected: a mandate is the
  right to decide, and an agent that has left deciding anything is the failure
  this ADR is named for.

## Verification
- An agent removed from the IR is decommissioned when the design is loaded, and
  the reason says the design no longer contains it.
- A decommissioned agent may not be delegated to, from any reach rule,
  including as a shared service.
- A decommissioned agent may not delegate to anybody.
- `mandate_holder` walks past it to the next holder in the line.
- Its unsettled sessions settle, and the report names what it was holding.
- Standing-in by it, and standing-in for it, both end.
- Its sessions and their traces still resolve afterwards.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. An agent can be decommissioned: kept as a record, inert as a principal, with removal from the design taking effect on load and work in flight named rather than abandoned. |
