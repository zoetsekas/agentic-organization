---
id: ADR-0070
title: Separation of duties constrains agents and a team is a scope
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Product]
informed: [All engineering]
scope: [spec, compiler, runtime, security]
workstreams: [WS-003, WS-009]
supersedes: []
superseded_by: []
related: [ADR-0006, ADR-0007, ADR-0008, ADR-0026, ADR-0065, ADR-0069, ADR-0079]
tags: [authority, controls, org-model]
---

# ADR-0070: Separation of duties constrains agents and a team is a scope

## Context
ADR-0065 made a mandate a scope of decision that narrows down the org tree.
Modelling a real finance function found the limit of that, and found it
empirically rather than by argument.

`examples/northwind.finance.system.yaml` is a nine-agent CFO organization built
to the conventional split — Controllership owns the historical numbers, FP&A the
forward look, Treasury cash, Tax compliance, with Internal Audit reporting
outside the CFO. Its leaves segregate perfectly: payables holds `raise_payment`,
the controller holds `approve_invoice`, the treasurer holds `release_payment`.
Then:

```
cfo  ['approve_invoice', 'change_vendor_master', 'close_period',
      'file_tax_return', 'post_journal_entry', 'publish_forecast',
      'raise_payment', 'release_payment']
```

The CFO held every side of every control, **by construction**. Narrowing means a
parent holds at least the union of its children, so authority accumulates
upward and the top of any branch ends up holding everything beneath it.

Worse, our own escalation routed around the control. ADR-0065 rule 4 sends a
refused decision to the smallest unit that holds it, so an accounts-payable
agent attempting `release_payment` escalated to `finance` — the one place where
the separation dissolved. A rule written to make authority legible had produced
an automated segregation-of-duties bypass.

The two constraints are genuinely contradictory. Narrowing requires
*every parent ⊇ the union of its children*. Separation requires
*no principal ⊇ {raise, release}*. They cannot both govern the same objects.

A third thing surfaced while looking: `MandateMap.holder` walked agent → home
team → parent teams and could return a **team id**, while
`OrgChart.mandate_holder` walked the management chain and could only return an
**agent**. Two implementations, two answers for the same question, and one of
them named something that cannot act.

## Decision
**A team is a scope, not a principal. Separation of duties constrains agents,
is declared in the spec, and is checked at the phase gate.**

1. **A team's mandate bounds its members and is exercised by nobody.** A unit
   may hold both sides of a control, because a unit cannot act. Narrowing is
   unchanged and still governs what its members may hold.
2. **Separation constrains agents.** A `separations` declaration names a set of
   decision classes no single agent's effective mandate may cover more than one
   of, with a reason — a rule without one is a rule nobody defends when it is
   inconvenient.
3. **A violation is an error, not a warning.** A segregation failure that
   shipped is what an auditor finds, and a warning is what a release ignores.
4. **A leader that inherits its unit's mandate into a violation must declare a
   narrower one.** ADR-0065 rule 5's inheritance stays — silence still means
   "inherit" — but where that inheritance breaks a separation, the spec is
   refused and the message says so. The convenient default survives for the
   cases where it is harmless and is forced into a decision exactly where it is
   not.
5. **Escalation lands on an agent or refuses.** `holder` walks leader to leader
   and tests each leader's **own** effective mandate, so a leader narrowed under
   rule 4 is walked past rather than treated as the holder. `None` is a refusal
   and never a promotion to the root.
6. **A refusal may name who does hold the decision, and may not route to
   them.** Telling an accounts-payable agent that release belongs to Treasury
   is a better answer than "nobody"; sending the decision there would be the
   bypass this record exists to close. An agent outside the line is reached
   through the process that owns the decision, never by escalating past a
   control.
7. **Separation between *people* is not modelled here.** A human approver who
   also owns the agent that raised the request is the same failure one layer up,
   and humans carry no mandate (ADR-0064). Stated so that nobody reads this
   record as covering it.

## Scope
The mandate model, the phase gate's checks, and the escalation path. It does
not change permissions, delegation, placements, or what a mandate means.

## Implementation
`SeparationRule` in the spec with `SystemSpec.separations`; the phase gate
resolves effective agent mandates and refuses a spec where one covers two
decisions from a rule. `MandateMap` gains `leader` so `holder` can walk leader
to leader and return an agent id, plus `holders` for naming without routing.
`OrgChart.mandate_holders` does the same at runtime, and the tool refusal in
`HarnessBuilder` names them. The worked finance example declares three
separations and narrows its CEO and CFO accordingly.

## Timeline
Phase 5, WS-003. Implemented with this record.

## Advantages
- The control a finance function is actually built around becomes expressible,
  and a spec that breaks it does not compile.
- Escalation stops being a path around the thing it escalates.
- One definition of "who holds this", returning a principal, at compile time
  and at runtime.
- The refusal is useful: it names Treasury instead of saying nothing.
- Leaders stop silently accumulating the union of everything beneath them,
  which was true of every organization the platform could express.

## Disadvantages
- **Separations must be declared, and nobody declares what they have not been
  burned by.** An organization that writes none gets the old behaviour exactly,
  leaders and all. This makes the failure expressible; it does not make it
  visible to somebody who has not thought of it.
- **It pushes work onto whoever writes the spec.** Every leader over both sides
  of a control now needs an explicit mandate, and the error arrives at compile
  time on a document somebody thought was finished.
- **Refusing rather than escalating will read as broken.** An agent that cannot
  get a payment released, and is told it belongs to Treasury, has to be taken
  through Treasury's process by a human or a delegation that already exists. If
  that process is not modelled, the work simply stops — correctly, and
  unhelpfully.
- **A team holding both sides still looks alarming**, and the defence is a
  definition: a unit cannot act. If anything ever *does* act as a unit — a
  future placement-level or service identity — that defence fails silently.
- **Separation is checked over declared mandates, not over what an agent can
  reach.** Two agents can still collude through delegation, a shared sandbox
  volume (ADR-0069) or a channel. This closes the single-principal case only.
- **Nothing covers the humans**, per rule 7, and in most real frauds the human
  is the principal.

## Alternatives considered
- **Stop authority accumulating upward — a parent need not hold its children's
  mandates.** Removes the conflict at its root, and destroys the property that
  makes narrowing comprehensible: a unit could then grant what it does not
  hold.
- **Treat teams as principals and forbid a team from holding both sides.**
  Consistent, and unimplementable: the team is the union of its members by
  definition, so a separated pair anywhere beneath it would refuse every spec.
- **Check separation at runtime only.** Catches real violations including
  inherited ones, and does it after deployment, on the action that matters, in
  front of a user.
- **Leave it to the approval gate.** `requires_approval` already stops an
  action for a human — and it does not say *which* human, so the approver may
  be the requester's own owner. It is a different control.

## Verification
Tests assert: an agent whose inherited mandate covers two decisions from a rule
fails validation with the remedy named; the same organization passes once its
leaders declare narrower mandates; a *team* holding both sides passes, because a
team cannot act; a separation naming an undeclared decision is refused; `holder`
returns an agent and never a team id; a leader narrowed under rule 4 is walked
past rather than returned; `holders` names an out-of-line holder that `holder`
refuses to route to; and the worked finance example holds every separation it
declares, with `release_payment` unreachable by escalation from payables.

## Follow-up
ADR-0079 extends separation to people: a person may not hold two decisions a rule keeps apart, and may not approve an action raised by an agent they own.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. Teams are scopes; separation constrains agents, is declared and gate-checked; escalation returns a principal or refuses, and a refusal may name a holder without routing to one. |
