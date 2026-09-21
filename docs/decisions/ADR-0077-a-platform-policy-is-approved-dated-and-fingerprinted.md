---
id: ADR-0077
title: A platform policy is approved, dated and fingerprinted
status: Accepted
version: 1.1.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Product]
informed: [All engineering]
scope: [compiler, security]
workstreams: [WS-028]
supersedes: []
superseded_by: []
related: [ADR-0052, ADR-0060, ADR-0062, ADR-0076, ADR-0078]
tags: [governance, fabric, lifecycle]
---

# ADR-0077: A platform policy is approved, dated and fingerprinted

## Context
ADR-0076 gave the fabric a policy: declared house rules that judge a design
before it is built, with the id and version stamped into the IR. Its own
Disadvantages named what it was missing, and this closes it:

> **A policy is itself unreviewed.** It has a version and no lifecycle: no
> approval, no review interval, nothing that stops somebody editing the house
> rules and re-running the build.

Two specific holes. **Nobody approves it.** A file on disk decides whether a
design ships, and `severity: {rule: ignore}` disables any built-in check with a
sentence of justification nobody signs. **A version is a name somebody types.**
Edit `treat_as` from `production` to `development`, keep `1.0.0`, and every
subsequent build carries a stamp asserting it passed rules it did not pass.
That is worse than no stamp, because a stamp is what an auditor reads.

The catalog already solved the shape of this for entries somebody binds to
(ADR-0062): editorial fields mutable, substantive fields versioned, mutations
attributed. A policy is not a thing designs bind to — it is a thing that judges
them — so the states differ, but the discipline transfers directly.

## Decision
**A policy has a lifecycle, and only an approved, current one may decide
whether something is built.**

1. **Four states**: `draft`, `in_review`, `approved`, `retired`. New policies
   start as drafts.
2. **A draft may be evaluated and may not decide a build.** An author has to
   see what a rule does before asking anybody to accept it, so the two
   questions are kept apart: `spec validate --platform-policy` works against a
   draft, and `compile` refuses it, naming the state.
3. **An approval is signed and dated.** An approved policy without
   `approved_by` and `approved_on` is **refused at load** — an approval nobody
   signed and nothing dates is not an approval.
4. **An approval may lapse.** `review_interval_days` is optional; where set, a
   policy past its review date stops deciding builds and says by when it was
   due. House rules nobody has confirmed still apply are not house rules.
   Opting in is choosing the behaviour — a fabric that sets an interval means
   it, and one that sets none has a policy that does not expire.
5. **A retired policy judges nothing**, and stays readable, because the
   verdicts it produced are still on record.
6. **The stamp carries a fingerprint over the substantive fields** —
   `treat_as`, `severity`, `reasons`, `require_declared`,
   `forbid_autonomy_over`, `max_autonomy`. Editing any of them moves the
   fingerprint while the version stays whatever was typed, so two builds
   claiming one version with different fingerprints are visibly not the same
   rules. This is what makes "it passed `house/1.0.0`" checkable rather than
   asserted.
7. **Editorial changes do not move it.** A description rewritten or an owner
   corrected is not a change to what a design is judged by — ADR-0062's split,
   applied here.
8. **The IR records the whole verdict**: id, version, fingerprint, status, who
   approved it and when, the strictness applied and every rule the policy
   lowered.

## Scope
The policy model, the compile refusal and the IR stamp. It changes no built-in
rule, no design, and nothing about how a policy's rules are evaluated.

## Implementation
`PolicyStatus` and the approval fields on `PlatformPolicy`, with
`PlatformPolicy.refusal()` answering "may this decide a build, and if not,
why" in one place so the compiler and the phase gate cannot disagree.
`fingerprint` hashes the `SUBSTANTIVE` field set. `compile_system` raises on a
refusal before validating anything. The gate reports usability and staleness
as its own check, separately from what the policy lets through.
`examples/house.platform-policy.yaml` is approved, signed and reviewed every
180 days.

## Timeline
Phase 5, WS-028. Implemented with this record.

## Advantages
- A file on disk no longer decides whether a design ships; an approval does.
- Disabling a built-in rule now costs a signature as well as a sentence.
- "Passed `house/1.0.0`" became checkable: the fingerprint says which
  `house/1.0.0`.
- A draft is still testable, so the lifecycle does not make policy authoring
  guesswork.
- Nothing about staleness is inferred — a policy with no interval simply does
  not expire.

## Disadvantages
- **A lapsed review stops builds, and it will lapse on a Friday.** The remedy
  is trivial — re-approve, or drop the interval — and it will still be
  discovered at the worst moment by somebody who did not set it. We chose that
  over a review interval that reports and does not bite, because an interval
  nothing enforces is the decorative field this session has spent its time
  removing.
- **The approval is self-asserted.** `approved_by: Security Engineering` is a
  string in a file. Nothing authenticates it, nothing checks that person exists
  or agreed. It records an intent and creates an accountable name; it does not
  prove anything, and a policy file is as forgeable as it ever was.
- **The fingerprint detects a changed version, it does not prevent one.** Two
  builds have to be compared for it to say anything, and nothing here does that
  comparison — it makes the evidence available to whoever looks.
- **`SUBSTANTIVE` is a hand-maintained tuple.** A field added to the policy and
  not added there is silently editorial, and the fingerprint will keep
  asserting sameness across a real change. That is the same class of defect as
  a decorative field, one level up.
- **There was no history.** **Closed by ADR-0078**: attributed, append-only,
  and held to the document by version and fingerprint, so a substantive edit
  nobody recorded is refused. It remains self-reported — consistency, not
  truth.
- **Retirement is not supersession.** `supersedes` is a one-way string for the
  reader; nothing checks it resolves, and nothing links a retired policy to
  what replaced it.

## Alternatives considered
- **Store policies in the fabric with a full review workflow.** The right
  end state, and it needs a store, an API and a UI before the first rule can be
  enforced; the file keeps a policy reviewable the way a spec is.
- **Refuse to evaluate an unapproved policy at all.** Simpler rule, and it
  makes a policy impossible to test before approving it, which guarantees
  approvals of rules nobody has run.
- **Report staleness without blocking.** Kinder operationally, and it makes
  the review interval decorative.
- **Fingerprint the whole document.** One fewer list to maintain, and every
  typo in a description would invalidate a verdict, which trains people to
  ignore the signal.
- **Require a signature over the fingerprint.** Would make the approval real
  rather than asserted, and needs key management this platform does not have.
  Worth revisiting when it does.

## Verification
Tests assert: a new policy is a draft; an approved policy without a signature
or a date is refused at load; a draft evaluates a design and is refused at
compile with its state named, writing no artifacts; an approved policy decides;
a retired one judges nothing; a lapsed approval stops deciding and names the
date it was due; a policy with no interval does not lapse; an interval of zero
is refused; the same version over changed substance produces a different
fingerprint; an editorial change does not move it; the IR records status,
approver and fingerprint; the gate reports why a policy may not decide; and the
worked house policy is approved and current.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-21 | The missing history is ADR-0078, and it is checked against the document rather than merely recorded. |
| 1.0.0 | 2026-09-21 | Accepted and implemented. Four states, signed and dated approval, optional lapsing review, and a fingerprint over the substantive fields so a version claim is checkable. |
