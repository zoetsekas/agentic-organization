---
id: ADR-0076
title: The fabric declares the rules a design is judged by
status: Accepted
version: 1.1.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Product]
informed: [All engineering]
scope: [spec, compiler, security]
workstreams: [WS-002, WS-028]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0050, ADR-0052, ADR-0060, ADR-0072, ADR-0073, ADR-0077]
tags: [governance, fabric, controls]
---

# ADR-0076: The fabric declares the rules a design is judged by

## Context
A design is already checked before it is built, and the check already blocks.
`compile_system` runs the validator first and raises before generating
anything, so a non-conforming design produces no artifacts at all — not
artifacts with a warning. The phase gate composes the same validator into its
definition phase, so there is one gate rather than two rule sets: roughly 151
rule sites and 54 phase checks.

What does not exist is a **platform mandate**. Those 151 rules are the
platform's opinions compiled into Python: uniform, unversioned, and not
something a fabric operator can state. Three consequences.

**A design declares the strictness it is judged by.** Fourteen rules promote
from warning to error only when `metadata.environment == "production"` — and
that field is in the design. A spec that calls itself `development` is simply
not judged by the strict rules. Self-asserted severity is not a control.

**No ruleset version is recorded.** Nothing says which rules a design was
judged against, so a design that passed six months ago cannot be re-judged and
an auditor cannot establish what "passed" meant at the time. This is the
staleness problem ADR-0060 solved for evaluations, unsolved one layer up.

**Entitlements gate the wrong thing.** ADR-0052 decides which *catalog entries*
a tenant may bind. Nothing lets a fabric say "in this tenant, every design
declares separations", or "no agent runs unattended over a payment".

## Decision
**A platform policy is a declared, versioned document owned by the fabric. The
gate evaluates it alongside the built-in rules, and its identity is stamped
into the IR.**

1. **It is the fabric's, never the design's.** A spec cannot name the policy it
   is judged by, for the same reason it cannot name its own tenant (ADR-0050):
   a design that chooses its own rules is not judged.
2. **It sets strictness.** `treat_as: production` judges a design at that
   severity whatever it declares about itself. Where no policy is in force,
   `metadata.environment` still decides, which is the old behaviour.
3. **It may re-rank the built-in rules.** Raising a severity needs no
   justification. **Lowering one requires a reason**, refused without it, and
   the lowered set travels with every verdict — in the gate report and in the
   IR stamp. A control switched off quietly is the failure this layer exists to
   prevent.
4. **It may require, forbid and cap.** Require a block be declared
   (`separations`, `guardrails`, `decisions`, `evaluations`, …); forbid
   unattended work over named decision classes; cap autonomy across a design.
   A policy naming a block this platform cannot check is refused — the same
   discipline ADR-0073 applies to controls.
5. **The verdict is recorded.** `SystemIR.platform_policy` carries the id,
   version, strictness and lowered set. It is present even when no policy was
   in force, with `id: "none"`, so **an unpoliced compile is visibly
   unpoliced** rather than indistinguishable from a policed one.
6. **It blocks before anything is built.** A design failing the house rules
   raises at the same point spec validation already raises, and the refusal
   names the policy that produced it.

**Naming.** `spec.policies` are a *design's* RBAC allow and deny rules. This is
`PlatformPolicy`, a different thing one layer up, and the two are near enough
that the code keeps them apart by name everywhere — the parameter is
`platform_policy` because `policy` was already taken inside the validator, and
was silently shadowed on the first attempt.

## Scope
The validator's ranking of findings, the phase gate's report, the compile
refusal and the IR stamp. It changes no built-in rule and adds no runtime
enforcement.

## Implementation
`orgagents.platform_policy` with the model, severity application and the
policy's own rules. `validate_spec(..., platform_policy=)`,
`review(..., platform_policy=)`, `build_ir(..., platform_policy=)` and
`compile_system(..., platform_policy=)` thread it through;
`--platform-policy` on `spec validate`, `phase` and `compile`.
`examples/house.platform-policy.yaml` is a worked fabric policy that refuses
Northwind — which passes on its own — by judging it at production strictness
and forbidding unattended work over money leaving the company.

## Timeline
Phase 5, WS-028. Implemented with this record.

## Advantages
- Strictness stops being self-asserted: the fabric decides, and a design that
  calls itself development is judged all the same.
- "This passed `house/1.0.0`" is a fact somebody can re-check, and a policy
  bump re-judges every design without editing any of them.
- A fabric operator can state house rules once instead of forking the
  validator or reviewing every spec by hand.
- Weakening a rule is possible and never quiet: it needs a reason and the
  reason travels into the IR.
- An unpoliced build looks different from a policed one, so nobody can mistake
  the absence of a policy for passing one.

## Disadvantages
- **`ignore` is a real off switch.** A fabric can disable any built-in rule
  with a sentence of justification nobody validates. The reason travels with
  the verdict, which makes the choice visible and does not make it good, and
  the pressure to use it will come from whoever is shipping.
- **A policy was itself unreviewed.** It had a version and no lifecycle.
  **Closed by ADR-0077**: four states, a signed and dated approval, an optional
  lapsing review, and a fingerprint over the substantive fields so a version
  claim is checkable. What remains open there is that the approval is still a
  string in a file.
- **The rule vocabulary is small and ours.** Require, forbid, cap, re-rank. A
  real house policy says things like "designs touching customer data need a
  named DPO", and none of that is expressible, so it will live in a wiki
  beside a policy that looks authoritative.
- **Two things called policy.** `spec.policies` and `PlatformPolicy` are one
  word apart and one layer apart, and the name already caused a silent
  shadowing bug while implementing this.
- **Severity overrides are keyed on finding codes**, which are internal names
  with no compatibility promise. A policy pinned to `wildcard_resource` breaks
  silently if that code is ever renamed — it stops matching and the rule quietly
  returns to its default severity.
- **It judges the design, not the deployment.** A policy cannot say anything
  about what the fabric actually runs, only about what it agrees to build.

## Alternatives considered
- **Keep the rules in code and fork per fabric.** No new concept, and every
  operator maintains a patched validator and no two agree.
- **Put the policy in the spec.** Simplest to plumb, and it lets a design
  choose the rules it is judged by, which is the defect this record names.
- **Express the policy as executable checks.** Maximally expressive, and it
  puts arbitrary code in the gate path, where every rule must be auditable by
  somebody who does not write Python.
- **Use the catalog for house rules.** The governance is already there
  (ADR-0062), and a catalog entry is a thing a design *binds to*, while a
  policy is a thing that judges it — a different relationship.
- **Record only the verdict, not the ruleset.** Cheaper, and "it passed" with
  no statement of what it passed is the audit gap this exists to close.

## Verification
Tests assert: a design that passes on its own fails under a policy that judges
it at production strictness; a required block a design omits is refused and one
it declares passes; a policy naming an uncheckable block is itself refused;
unattended work over a forbidden decision class is refused; an autonomy ceiling
applies across the design; raising a severity needs no reason and lowering one
without a reason is refused; an ignored rule stops being reported and the
lowered set appears in the gate report and the IR; the IR records the policy's
id and version, and records `none` when there was no policy; and a design
failing the house rules raises with the policy named and writes no artifacts.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-21 | The missing lifecycle is ADR-0077: only an approved, current policy may decide a build, and the stamp carries a fingerprint. |
| 1.0.0 | 2026-09-21 | Accepted and implemented. A fabric-owned, versioned policy that sets strictness, re-ranks built-in rules with lowering reported, requires/forbids/caps, and is stamped into the IR. |
