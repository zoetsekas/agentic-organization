---
id: ADR-0079
title: A person is a principal for authority and never for access
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Product]
informed: [All engineering]
scope: [spec, compiler, security]
workstreams: [WS-003, WS-016]
supersedes: []
superseded_by: []
related: [ADR-0007, ADR-0026, ADR-0047, ADR-0064, ADR-0065, ADR-0070, ADR-0072, ADR-0073, ADR-0077]
tags: [identity, authority, org-model, hybrid]
---

# ADR-0079: A person is a principal for authority and never for access

## Context
The goal is an organization of agents that works the way a human organization
works, under the same controls. Agents have that: `Role` binds
responsibilities, capabilities and permissions into one contract, assignment
may narrow and never widen, permissions resolve deny-by-default exactly once at
the phase gate, and mandates, separations and autonomy postures sit above.

People have none of it. A `HumanCounterpart` carries `roles: list[HumanRole]` —
a five-value *pairing* enum (owner, approver, reviewer, escalation, operator) —
and an `approves` list. The spec has three `RoleAssignment` sites: agent, team
and mission. None is a person. So a person's **relationship to an agent** is
modelled and a person's **position in the organization** is not.

That single gap is the root of four limitations already recorded separately:

* ADR-0064 holds open that an agent cannot act for the person it is paired
  with; and, less obviously, that a board decides capital allocation, so
  `approve_capex` in the worked finance example is held by nobody and the work
  cannot complete inside the system.
* ADR-0070 rule 7 states plainly that separation of duties does not cover
  people — who are the principal in most real frauds.
* ADR-0072 rule 3 can check that an approver is not the agent's own owner, and
  cannot check that they are independent or senior enough.
* ADR-0077 concedes that an approval is a string in a file.

There is also a concrete defect making all of this worse. A person is declared
inline on each agent they are paired with, so in `northwind.finance.system.yaml`
one finance director appears **six times under six invented ids**. The same
human is six principals. No separation check over people could work against
that, and no mandate could be attached to them coherently.

## Decision
**A person is a first-class principal for authority and accountability, and is
never a principal for access.**

1. **People are declared once, in a `people` block**, with an id, and referenced
   by the agents they are paired with. The pairing keeps its capacity — owner,
   approver, reviewer, escalation, operator — and stops carrying a copy of the
   person. One human, one principal.
2. **A person may hold a mandate.** The same structured scope of decision an
   agent holds (ADR-0065), so escalation can land on a person who genuinely
   holds the decision, and `approve_capex` has somewhere to go.
3. **A person may not hold capabilities or permissions**, and declaring them is
   **refused**. We do not mediate a person's access: they sign into the ERP with
   their own account under their employer's IAM, and a permission this platform
   cannot enforce is worse than none — the rule ADR-0073 already applies to
   controls, applied to principals.
4. **A person may be attached to an org unit**, so a mandate narrows down the
   tree for people exactly as it does for agents. A finance director's authority
   is bounded by Finance.
5. **Separations cover people.** A person may not hold two decisions a rule
   keeps apart, and — the rule ADR-0072 rule 3 could only approximate — **a
   person may not approve an action raised by an agent they own.** That is
   four-eyes, expressed where it can be checked.
6. **A person's authority here is a claim, and the record says so.** Their real
   delegation of authority lives in their employer's approval matrix. This
   platform declares it because *this platform* routes the escalations and
   approvals, and nothing reconciles the two.
7. **This is not ADR-0064.** That record asks whether an agent may act *as* a
   person. This one is about a person's *own* authority. They are separable and
   the second does not decide the first.

## Scope
The spec's people, the mandate resolver, the separation checks and the
escalation path. It does not change what an agent may do, how permissions
resolve, or how a person authenticates to the designer (ADR-0032/0043).

## Implementation
A `people` list in the spec, each with an id, contact, optional position,
optional `unit`, and an optional `Mandate`. `HumanCounterpart` gains a
`person` reference and keeps its capacity; the inline fields stay valid and
migrate. `mandates.resolve` resolves people alongside agents so `holder` and
`holders` can return either. The separation check runs over people, and gains
rule 5's owner-approver case. The validator refuses `capabilities` or
`permissions` on a person with the reason. Northwind's six copies of one
director collapse to one.

## Timeline
Phase 6, WS-016 with WS-003. Nothing here blocks the alpha.

## Advantages
- One human is one principal, which is a precondition for every check below
  and is currently false in the worked example.
- Escalation can reach a person who actually holds the decision, so work that
  a board decides can complete rather than being refused as unheld.
- Four-eyes becomes checkable: the person who owns the agent that raised an
  invoice cannot be the person who approves it.
- The line is honest in both directions — we model the authority we route and
  refuse to model the access we do not mediate.
- Four recorded limitations reduce to one root cause with a decision against it.

## Disadvantages
- **A person's authority here will drift from the company's real one.** Rule 6
  admits it. Their approval matrix lives in a finance system we do not read,
  and a mandate in our spec that no longer matches it is worse than an absent
  one, because ours is the one that routes the escalation.
- **Authority attached to a named individual rots faster than a spec changes.**
  People change jobs; the mandate stays. Attaching it to a *position* would be
  right and we do not model positions, so ADR-0047's departed-person check is
  the only thing catching it, and only for people who have left entirely.
- **Two principal kinds in every authority check.** Mandate resolution,
  separation, escalation and the holder search all grow a second case, and the
  less-exercised one will be where the bug is.
- **It invites scope creep toward an HR system.** Positions, reporting lines
  for people, delegation calendars, out-of-office. Rule 3 is the wall; it will
  be pushed on, and "just let a person hold a capability" will sound reasonable
  the first time somebody wants to model a manual step.
- **Declaring people in the spec puts names in a design document** that is
  reviewed, diffed and shipped to targets. `directory` (ADR-0047) exists partly
  to avoid that, and this pulls in the other direction.
- **It does not make the approval true.** A person holding a mandate in our
  spec, approving via an authenticated click, is still a claim this platform
  makes about an organization it does not run.

## Alternatives considered
- **Leave people outside the org model.** Smallest, most honest about what we
  govern, and it leaves all four recorded limitations permanently open and the
  hybrid organization half-modelled — which is most of the stated goal.
- **Full parity: a person holds roles, capabilities and permissions like an
  agent.** Symmetric and satisfying, and it models access we cannot enforce.
  Every permission on a person would be decorative, which is the defect this
  session has spent its time removing.
- **Resolve people entirely from the directory (ADR-0047).** Keeps names out of
  the spec and makes the design depend on a live service to be reviewable, and
  a directory knows who someone is, not what they may decide.
- **Attach authority to positions rather than people.** Right, and it needs a
  position model — job titles, holders, vacancies, acting arrangements — which
  is an HR system. Recorded as the better answer we are not building.

## Verification
Tests assert: a person declared once and referenced by several agents is one
principal; a person holding `capabilities` or `permissions` is refused with the
reason; a person's mandate narrows against their unit; `holder` may return a
person and escalation reaches them; a decision held only by a person is not
reported as unheld; a person holding two decisions from a separation is
refused; a person approving an action raised by an agent they own is refused;
and Northwind declares one finance director rather than six.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. People are principals for authority and accountability, declared once, holding mandates and covered by separations — and never holders of capabilities or permissions. |
