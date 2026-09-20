---
id: ADR-0060
title: Evaluation cases assert what a machine can check, and unrun or stale evidence never reads as passed
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Compliance]
informed: [All engineering]
scope: [runtime, security, docs]
workstreams: [WS-014]
supersedes: []
superseded_by: []
related: [ADR-0022, ADR-0035, ADR-0037, ADR-0045, ADR-0052]
tags: [governance, evaluations, lifecycle]
---

# ADR-0060: Evaluation cases assert what a machine can check, and unrun or stale evidence never reads as passed

## Context
ADR-0022 gave the platform `EvaluationCase`, `PromotionGate` and the gate
requirement `evaluations_passed`. Cases have been declared in every example
system since, the registry prints the gate, and the phase report checks that
cases *exist*. Nothing has ever run one. `evaluations_passed` was a
requirement the registry recorded and no code verified — a governance control
that is only a promise, and the reason WS-014 stayed open.

Building the runner forces three questions that ADR-0022 left unanswered.

1. **What may a case assert?** `EvaluationCase.expect` is free text
   ("An answer naming the warehouse tables used"). Prose is written for a human
   reviewer. The tempting implementation asks a model whether the response
   "satisfies" the prose, which yields a pass rate that changes between runs,
   between models and between prompt revisions — a number nobody can reproduce
   sitting under a promotion gate.
2. **What does the gate say before anything has run?** A boolean has only two
   answers and the honest state needs three: not run, ran and passed, ran and
   failed.
3. **How long is a result evidence?** A pass recorded against an agent that has
   since been rewritten says nothing about the agent that exists now. ADR-0052
   settled the same trap for catalog figures and health observations: a stale
   belief is reported as unknown, never as health.

## Decision
1. **A case asserts something deterministic, chosen by a prefix on `expect`.**
   `exact:`, `contains:`, `schema:<output contract id>`,
   `guardrail:<guardrail id>` and `refuses` are implemented. `must_not` entries
   are literal forbidden text and are checked on every case, whatever the
   expectation was. The spec is not changed to carry a discriminator; the
   prefix lives in the value the spec already has.
2. **An unprefixed expectation is prose, and prose is `UNSUPPORTED`.** There is
   no LLM judge in this platform and this ADR does not add one. An unverifiable
   case counts in the denominator of the pass rate and never in the numerator,
   so a suite of prose cases drives the gate to *failed*, which is the true
   statement about it. A case that declares neither an expectation nor a
   `must_not` is `UNSUPPORTED` for the same reason.
3. **`GateState` has four states**: `not_evaluated`, `passed`, `failed`,
   `stale`. `not_evaluated` is the state of an agent nobody has run cases
   against, including an agent no declared case applies to. Only `passed` is a
   pass.
4. **A runner failure is `ERROR`, not `FAILED`.** A harness that breaks is not
   a misbehaving agent, and reporting it as one teaches people to ignore
   failures.
5. **Staleness rule: a result is evidence only about the definition it ran
   against.** A recorded run carries the declared `metadata.version`, a
   fingerprint of the lifecycle block (cases and gates) and a fingerprint of
   each agent's own spec. The gate reports `stale` if the declared version
   moved, if the lifecycle fingerprint changed, or if that agent's fingerprint
   changed. Two triggers because two different mistakes happen: the version is
   what a reviewer cites, and the fingerprints catch the edit that shipped
   without a bump. Fingerprints are deliberately narrow — renaming a channel
   does not invalidate an evaluation, because a staleness rule that fires on
   everything trains people to ignore it.
6. **Every result is recorded** through the existing `Store`, with the
   expectation, the response excerpt, each assertion's verdict and the reason,
   so a failure can be read without re-running it.

## Scope
`orgagents.evaluations` and the `evaluations_passed` gate, plus the registry
column that prints its state. It does not change `spec/model.py`, the CLI, the
runtime or any other gate requirement (`human_approval`, `security_review`,
`cost_within_budget`, `owner_assigned`, `permissions_reviewed` remain
unexecuted assertions, and this ADR makes no claim about them). It does not
decide how often evaluations must be re-run.

## Implementation
`evaluations.py` carries pure pieces — `parse_expectation`, `spec_fingerprint`,
`agent_fingerprint`, `_verdict` — so the policy can be read without a store,
plus `EvaluationService`, which executes cases through an injected
`CaseRunner`, persists an `EvaluationRun` and each `CaseResult`, and answers
`gate_state`. The default runner is `EchoRunner`, over the existing `echo`
adapter: no provider call, no credential, so the gate runs in CI. Schema
assertions reuse `guardrails.validate_shape` (ADR-0037) and guardrail
assertions reuse `GuardrailEngine` (ADR-0035), so an evaluation checks the same
code the runtime enforces rather than a second copy of it.

## Timeline
Phase 3, with WS-014 M3. The assertion vocabulary is expected to grow; adding a
judged kind would need its own ADR and a story for reproducibility.

## Advantages
- `evaluations_passed` is a verdict from an execution instead of a promise.
- Every number under the gate is reproducible: same spec, same runner, same
  result.
- "Nobody ran this" survives as a distinct fact, so an unevaluated fleet cannot
  look like a passing one.
- A result cannot outlive the agent it judged.
- The gate runs offline, so it can be a CI check rather than a ceremony.
- Executed results are the first feedback signal the platform produces, which
  is what roadmap item 7.2 needs before any "learning agent" is credible.

## Disadvantages
- **Every case in the shipped example system is prose, so the acme system's
  gate now reads *failed*.** That is the honest reading, but it is a
  regression in appearance, and rewriting those cases into checkable ones is
  work this ADR creates and does not do.
- **A deterministic assertion is a weak proxy for quality.** `contains:` passes
  on a response that includes the word and is otherwise wrong. The gate becomes
  precise about something narrow, which can feel like more assurance than it
  is.
- **Refusing to judge prose means most real expectations cannot be expressed.**
  "Escalates a SEV1 to a human" is not a substring, and until there is a judge
  or a tool-call assertion, that case can only be declared, not verified.
- **The echo runner proves wiring, not behaviour.** A green run against `echo`
  says the harness works; nothing in this environment has ever evaluated a real
  model, and a reader who misses `run.runtime` could take it for more.
- **Prefixes are a discriminator hidden in a string.** It is invisible to spec
  validation, a typo (`contain:`) silently degrades to prose, and it exists
  only because this change may not touch the spec model.
- **Two staleness triggers produce more churn than one.** A version bump for an
  unrelated edit invalidates good evidence and forces a re-run.
- A pass rate that puts unsupported cases in the denominator will read as
  pessimistic to anyone who has not read this ADR.

## Alternatives considered
- **An LLM judge for prose expectations** — the only way to check what most
  cases actually say; rejected for now because the result is not reproducible,
  needs a provider credential the platform cannot assume, and would put a
  non-deterministic number under a promotion gate. It is the obvious next ADR,
  with a story for pinning the judge and recording its model.
- **Treat prose as `contains:` on the whole string** — trivial, and it would
  fail nearly every real case for the wrong reason, which is worse than saying
  "not checkable".
- **Skip prose cases entirely** — a skipped case disappears from the
  denominator, so a system full of prose would show a clean 100%. That is the
  exact dishonesty this milestone exists to remove.
- **A boolean gate** — collapses "never run" into "failed" and loses the fact
  that nobody looked; or into "passed", which is where we started.
- **A time-based staleness horizon, as in ADR-0052** — right for an
  observation of a live target, wrong here: a spec that has not changed in six
  months has not become less true, and a spec edited a minute ago has.
- **Fingerprint the whole spec** — fires on every unrelated edit, so every run
  is stale and staleness stops meaning anything.
- **Add an `assert:` field to `EvaluationCase`** — cleaner and validatable, and
  the right end state; not done here because the spec model is owned elsewhere
  in this change. Recorded as a follow-up.

## Verification
`tests/test_evaluations.py` asserts that each assertion kind executes and
produces a verdict, that a failing case fails the gate, that a never-run gate
is `not_evaluated` and distinct from `failed`, that a prose expectation is
`unsupported` and does not pass the gate, that a broken runner reports `error`
rather than blaming the agent, that results record the expectation, the
response and the failing assertion, that a bumped spec version and an unbumped
agent edit both read as `stale`, and that the default runner needs no
credentials.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted with WS-014 M3. |
