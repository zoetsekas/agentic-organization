---
id: ADR-0073
title: A control declares who enforces it
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Product]
informed: [All engineering]
scope: [spec, compiler, security]
workstreams: [WS-002, WS-009]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0010, ADR-0065, ADR-0070, ADR-0071, ADR-0072, ADR-0074]
tags: [controls, enterprise-applications, governance]
---

# ADR-0073: A control declares who enforces it

## Context
An agentic organization does not replace its enterprise applications. The ERP
still owns the ledger, the treasury management system still owns the sweep, the
banking portal still owns the payment. Agents use those systems the way people
do — following the organization's processes, reading the results, and acting on
them. Every one of those systems carries its own controls: role-based
segregation tied to a document, daily and aggregate limits, approval workflows,
maker-checker.

This platform has accumulated its own controls alongside them — mandates,
conditions, separations, approval gates, guardrails — and has been building
them as though it were the system of record. Two pieces of evidence that this
is a mistake rather than an abundance of caution.

The mandate condition grammar (ADR-0071) reads like a limit and is not one:

```
one sweep of 60m : refused
ten sweeps of 49m: all accepted, 490m moved against a 50m bound
```

The bound is per call, and treasury limits are cumulative. The obvious fix is
to build stateful limits here — and that would duplicate what the treasury
system already enforces, producing two rule sets that drift, where the weaker
one wins silently and nobody can say which applied.

The deeper problem is that **nothing in the spec says who enforces a control.**
A reader cannot tell a bound this platform evaluates from a bound it merely
describes, and the second kind is worse than none: the spec reads as governed.
That was true of mandate conditions until yesterday, and it is a class, not an
instance.

## Decision
**Every control declares its enforcer, and a control this platform does not
evaluate may not read as one it does.**

1. **A control declares `enforced_by`: `application`, `platform`, or `both`.**
   This applies to anything that constrains an action — mandate conditions,
   approval requirements, separations, limits.
2. **`application`** means the enterprise system enforces it. The spec may
   describe it, for the reader and for the audit trail, and **must not claim to
   evaluate it**. The binding names the system, and the generated artifacts say
   which control is whose.
3. **`platform`** means the application has no such control, so ours is the
   only one. It must be **evaluable at our boundary** — if we cannot check it,
   it is not a control and the spec is refused. This is the mandate-conditions
   lesson written as a rule.
4. **`both`** is permitted and always reported. Two rule sets that can disagree
   is a real arrangement — defence in depth — and it is also how a bound gets
   quietly relaxed on one side. A `both` control names which side is
   **authoritative**, so a discrepancy has an answer rather than a debate.
5. **The tighter of two bounds is not automatically the effective one.** That
   reasoning holds only if both are actually evaluated, and we can see one of
   them. So a `both` control's report states what we check and what we are
   trusting the application to check.
6. **Ownership is per control, not per kind.** The same limit may be the
   treasury system's under one binding and ours under another, because which
   product sits behind a capability is a binding concern (ADR-0002).
7. **An `application` control still constrains us.** Our own bound may never be
   wider than what the application enforces; where the platform cannot verify
   the application's bound, that gap is reported rather than assumed closed.

## Scope
How controls are declared and reported across the spec, the phase gate and the
generated artifacts. It does not change what a mandate, a separation or an
approval gate *means*, and it does not move any existing enforcement.

## Implementation
`ControlEnforcement` on capability constraints, mandates and separations,
defaulting to `platform`
so an undeclared control is one we are claiming and must therefore evaluate.
The validator refuses a `platform` control it has no evaluator for, and refuses
a spec whose own bound is wider than a declared application bound. The phase
gate reports every `application` and `both` control with the system named, and
the generated README carries the same list — an operator should be able to read
which controls this deployment enforces and which it is relying on somebody
else for.

## Timeline
Phase 5, WS-009. Implemented with this record; ADR-0074 and ADR-0075 build on
it and are not.

## Advantages
- A reader can tell a bound we evaluate from a bound we describe, which is the
  distinction that was missing.
- It stops us re-implementing an ERP badly. Cumulative treasury limits,
  document-level segregation and maker-checker stay where they are enforced and
  where the data lives.
- `both` becomes visible rather than accidental, so drift between two rule sets
  is a reported condition instead of a surprise.
- The default — `platform` — is the one that forces a decision: claiming a
  control obliges us to evaluate it.

## Disadvantages
- **We are recording claims about systems we cannot inspect.** "The TMS
  enforces a daily limit" is a sentence somebody typed. Rule 7 reports the
  unverifiable gap and cannot close it, which is the same `verified=False`
  honesty the sandbox boundary statements carry, with the same limitation:
  writing down that we did not check is not checking.
- **`application` will become a parking space.** It is the cheapest way to make
  a control stop failing the gate, and nothing distinguishes "the ERP enforces
  this" from "somebody assumed the ERP enforces this".
- **Rule 5 makes the report longer and less quotable.** "We check A, we are
  trusting them for B" is the honest sentence and a worse headline than "this
  is enforced".
- **It adds a field to several models at once**, and a control whose enforcer
  nobody set inherits `platform`, which means existing specs acquire an
  obligation retroactively.
- **It does not help when the application's control is wrong.** We would
  faithfully report that the ERP enforces segregation while the ERP's roles are
  misconfigured, and nothing here looks.

## Alternatives considered
- **Enforce everything ourselves.** One rule set, one place to look, and it
  requires re-implementing an ERP's document model, a TMS's limit engine and a
  bank's payment controls — without the data any of them hold.
- **Enforce nothing; defer entirely to the applications.** Honest about where
  the data is, and it abandons the controls that span systems, which is exactly
  what an agentic organization adds: no single application sees an agent
  raising an invoice in one system and releasing payment in another.
- **Infer the enforcer from the binding.** Attractive, and it guesses: a
  capability bound to an ERP says nothing about whether that ERP's limits are
  configured.
- **Report the split without enforcing the rule.** A view rather than a
  decision; nothing then stops a `platform` control with no evaluator, which is
  the defect this exists to prevent.

## Verification
Tests assert: a `platform` control with no evaluator fails validation; a
control with no declared enforcer defaults to `platform` and is therefore
subject to that check; an `application` control is reported with its system
named and is not evaluated here; a `both` control names an authoritative side
and appears in the report with what we check and what we trust; a platform
bound wider than a declared application bound is refused; and the generated
README lists every control this deployment does not itself enforce.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted and implemented. Controls declare `enforced_by`; `platform` must be evaluable, `application` may not be claimed, `both` names an authoritative side and is always reported. `rate_per_minute` was the first casualty: declared in the model and enforced nowhere. |
