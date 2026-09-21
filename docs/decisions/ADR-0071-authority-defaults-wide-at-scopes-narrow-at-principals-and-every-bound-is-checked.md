---
id: ADR-0071
title: Authority defaults wide at scopes, narrow at principals, and every bound is checked
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Runtime Engineering]
informed: [All engineering]
scope: [spec, compiler, runtime, security]
workstreams: [WS-003, WS-009]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0008, ADR-0010, ADR-0063, ADR-0065, ADR-0070]
tags: [authority, controls, org-model]
---

# ADR-0071: Authority defaults wide at scopes, narrow at principals, and every bound is checked

## Context
Extending the Northwind finance example to the full function taxonomy — AR and
AP, accounting and statements, treasury with cash, debt, placement and hedging,
FP&A with budgeting and scenario modelling, corporate development, tax and
internal audit — produced a spec that compiled with zero errors and was wrong
in four ways.

**Conditions bounded nothing.** ADR-0065 gave a mandate `conditions`, the IR
carried them, the loader put them on the agent, and no code read them. A cash
manager declared `max_facility_gbp: 5000000` and could have drawn any amount.
This is the defect ADR-0063 fixed for `AgentKind` — a field that decides
nothing — reintroduced two records later by the same author.

**Narrowing made authority a chore and a trap.** A mandate is the intersection
down the tree, so a decision must be enumerated at every unit between the root
and the agent. Four agents that declared real authority resolved to the empty
set because an ancestor had never listed it, and the signal was a *warning*. In
a finance function that reads as work stopping for no stated reason. It is also
why the CFO held every decision in the first place: the model required the root
to enumerate the union, and ADR-0070 then had to forbid what ADR-0065 had
forced.

**Silence read backwards.** A corporate development team written with no
mandate — "it recommends, it does not decide" — inherited the root's entire
authority. It was caught only because a separation happened to overlap it.

**Separation stopped at the spec.** `invoice_entry` and `invoice_approval` are
kept apart by a separation rule and could both bind to one MCP server under one
DSN. Segregation of duties is enforced by the ERP and the bank, not by our
mandate table; two capabilities on one connection is one place to defeat it.

## Decision
**A scope may default wide. A principal may not. Every bound is evaluated or
refused, and separation is checked against the binding as well as the spec.**

1. **The root team defaults to the declared vocabulary.** A team is a scope and
   nobody exercises it (ADR-0070), so a root holding every decision is not a
   hazard — and requiring it to enumerate them is what made authority
   accumulate. Intermediate teams inherit, as before. Declaring a mandate on a
   unit is now what it should always have been: a deliberate narrowing.
2. **The root's leader must declare its own mandate.** It is a principal, it
   inherits its unit's, and at the root that is everything. `decisions: []` says
   it decides nothing, and saying so is the point: a principal's authority is
   the one thing that is never silent. This replaces ADR-0065's
   `root_without_mandate` error, moving it from the scope to the principal.
3. **Claiming authority the line does not hold is an error.** It used to be a
   warning, and the unit decided nothing of the kind. With the root defaulting
   wide, reaching this at all means an ancestor deliberately narrowed, so the
   author is told to widen the ancestor or drop the claim.
4. **Conditions are evaluated at the tool boundary**, with a small explicit
   grammar: `max_<field>`, `min_<field>`, `<field>_in`. The field is an argument
   the call carries.
5. **A condition naming a field the call does not supply is a refusal.**
   Otherwise omitting the amount removes the ceiling.
6. **A condition this platform cannot parse is a refusal.** Silently ignoring an
   unevaluable bound is precisely how these became decorative.
7. **Separation is checked against the binding.** Where two decisions a
   separation keeps apart resolve to the same MCP server *and* the same
   credential, the implementation phase fails, naming both sides and the
   connection. Different servers, or one server under different credentials, so
   the downstream system can tell the two principals apart.

## Scope
Mandate resolution, the spec validator, the phase gate's implementation checks,
and the tool boundary in `HarnessBuilder`. It does not change permissions,
delegation, placements, or what a mandate means.

## Implementation
`mandates.resolve` takes the declared vocabulary and uses it for a root that
declares nothing. The validator gains `root_leader_without_mandate`, promotes
`mandate_overreach` to an error, and drops `root_without_mandate`.
`HarnessBuilder.condition_failure` evaluates the grammar and is called from
`refusal`, which now receives the call arguments. `phases.review_implementation`
gains a check per separation rule. Northwind's root and Finance stop
enumerating; Acme's chief executive declares `decisions: []`; a Northwind
binding demonstrates the two sides of the payment control on separate
credentials.

## Timeline
Phase 5, WS-003. Implemented with this record.

## Advantages
- A threshold means something. Every ceiling in a finance function — capital
  expenditure, a facility, a placement, materiality — is now enforceable.
- Adding a function to a leaf stops requiring an edit at every level above it,
  which removes the pressure that made leaders hold everything.
- An agent can no longer silently decide nothing; the spec refuses first.
- A unit written as "advisory" is still a decision the author has to make, but
  the separations now in place catch the common case where they did not.
- The control survives into the binding, where the enterprise system actually
  enforces it.

## Disadvantages
- **The condition grammar is ours and it is small.** `max_`, `min_`, `_in` and
  nothing else. A real delegation of authority says "up to 250k, or 1m with the
  CFO's counter-signature, excluding related parties" and none of that is
  expressible. Rule 6 turns every such condition into a refusal, which is safe
  and will read as the platform being unable to model the business.
- **Rule 5 will produce confusing refusals.** A tool whose arguments do not
  happen to include the bounded field refuses every call, and the fix is to
  change the tool's signature — which is not where anybody will look first.
- **Defaulting the root wide trades one failure for another.** Authority no
  longer has to be enumerated, so nobody will enumerate it, and what an
  organization may decide is now implicit in its decision vocabulary. That is
  the right default and it is less legible than a written list.
- **Rule 2 protects one principal.** Every other leader still inherits its
  unit's mandate silently; only separations catch it, and only where one was
  declared.
- **Rule 7 checks a proxy.** Two different credentials to one system is
  evidence of separation, not proof of it: the ERP may map both to the same
  role. We can see the connection string and not what it grants.
- **Conditions are checked against arguments an agent supplies.** A model that
  wants to move more money than it may can, in principle, understate the
  amount; the bound is only as honest as the tool's own validation.

## Alternatives considered
- **A full condition expression language.** Expressive, and it puts a policy
  interpreter in the tool path where every rule must be auditable by somebody
  who does not write code.
- **Ignore conditions we cannot parse.** Keeps old specs working, and restores
  exactly the decorative-field defect this record exists to fix.
- **Keep the root enumerating.** One honest list of everything the organization
  may decide, and it is the list that made every leader hold everything.
- **Require every unit to declare a mandate.** Maximally explicit, unusable at
  the size of a real function, and it would have made the twelve-agent example
  nine more declarations that mostly repeat their parent.
- **Check separation only in the spec.** Simpler, and it stops at the layer
  where the control is not enforced.

## Verification
Tests assert: a root team with no mandate holds the declared vocabulary; a root
leader with no mandate fails validation, and `decisions: []` satisfies it; a
claim no ancestor holds is an error; a call above a ceiling is refused and one
below it proceeds; omitting the bounded field is a refusal; an unparseable
condition is a refusal; conditions chain so the tighter bound in a line applies;
a membership condition is evaluated both ways; and the worked Northwind binding
keeps both sides of the payment control on different credentials, with the check
failing when they are collapsed onto one.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. Root scope defaults to the vocabulary, the root leader must declare, overreach is an error, conditions are evaluated with refusal on missing or unparseable bounds, and separation is checked against the binding. |
