---
id: ADR-0045
title: Guardrail judgement and summarization are pluggable
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Developer Experience]
informed: [All engineering]
scope: [spec, runtime, security]
workstreams: [WS-024]
supersedes: []
superseded_by: []
related: [ADR-0035, ADR-0036, ADR-0037, ADR-0040]
tags: [runtime, security, guardrails]
---

# ADR-0045: Guardrail judgement and summarization are pluggable

## Context
ADR-0035 shipped guardrails whose verdicts come from regular expressions and a
phrase list, and ADR-0036 shipped compaction whose "summary" keeps the first
and last turn and counts the rest. Both were honest about their limits and
both are wrong often enough to matter:

* `you are now` fires on a support ticket quoting a late customer, and
  `disregard everything you were told earlier` — an actual injection attempt —
  matches nothing in the list.
* The `email` pattern flags every address in a message where addresses are the
  legitimate subject, so operators set the guardrail to `flag` and stop reading
  the flags.
* A thread compacted by the structural summarizer loses the middle entirely.
  That is fine when a person reads the trace and bad when an agent must
  continue the work.

Making a model the detector is the obvious fix and the obvious trap: it puts a
network call on every boundary crossing, in a platform whose whole point is
that policy compiles into every target, including ones with no egress.

## Decision
Judgement is a **protocol**, not an implementation.

* `classifiers.Classifier` — `classify(guardrail, check, text, context) ->
  Classification` — is what the guardrail engine calls. `PatternClassifier` is
  the default and keeps today's behaviour exactly. `ModelClassifier` is a thin
  adapter over a `complete(prompt) -> str` callable the deployment supplies.
* `context.Summarizer` — `summarize(turns) -> str` — is what compaction calls.
  `FirstLastSummarizer` is the default and still *says* it is structural.
  `ModelSummarizer` is the same shape of adapter. A plain callable still works.
* Only judgement checks (`pii`, `secrets`, `prompt_injection`) are ever sent to
  a model. `max_length`, `url_allowlist`, `pattern`, `data_class` and `schema`
  are arithmetic or set membership and stay deterministic whatever classifier
  is configured.
* The model-backed classifier **unions** with the pattern floor rather than
  replacing it: a model may add a finding, and may not talk the deterministic
  detector's finding away.
* **On failure it falls back to the deterministic classifier** and marks the
  verdict `degraded`, which is recorded on the violation and in the session
  log. Concretely: it fails **open relative to the model** (no model verdict is
  not a block) and **closed relative to the patterns** (the regex floor still
  blocks). See Disadvantages for why.
* The spec names a **class**, never a vendor: `Guardrail.classifier` and
  `ContextPolicy.summarizer` take a `ModelClass` (ADR-0040). Which concrete
  model satisfies it is a binding/catalog decision, as for every other model
  choice.
* No provider SDK is imported anywhere in this path.

## Scope
Binds the guardrail engine, the context manager and the runtime that owns both.
It does not change which guardrails exist, which boundaries they sit on, or
what the four actions do. It does not give the compiler a way to *supply* a
classifier — a deployment injects one into `AgentRuntime` — and it does not
attempt to measure classifier recall, which WS-024's exit criteria still want.

## Implementation
`src/orgagents/classifiers.py` holds the protocol, the patterns (moved from
`guardrails.py`, which re-exports them), `PatternClassifier`, `structural_check`
and `ModelClassifier`. `GuardrailEngine(guardrails, classifier=...)` routes
every check through it, and `Violation` gained `source` and `degraded` so a
record says who decided and whether they were working. `context.py` gained
`Summarizer`, `FirstLastSummarizer` and `ModelSummarizer`; `ContextManager.
compact` accepts either. `AgentRuntime(classifier=..., summarizer=...)` wires
both, and `compact_thread` compacts with the configured summarizer while the
no-regression rule stays in compaction, where a summarizer cannot argue with
it. Spec: `Guardrail.classifier` and `ContextPolicy.summarizer`, both optional
`ModelClass`, both additive and so needing no spec migration.

## Timeline
Phase 4, WS-024 M5, alongside M6 (contract retry). Measuring recall against a
labelled corpus is the remaining exit criterion and is not in this decision.

## Advantages
- What gets blocked can be improved without touching the engine, the spec or
  any target.
- The default needs no network, no credential and no budget, so an air-gapped
  or offline deployment is unchanged.
- Tests exercise the model path with a stub, so behaviour under a broken or
  babbling model is covered rather than hoped about.
- The pattern floor means enabling a classifier can only widen detection,
  which makes it a safe thing to turn on.
- Compaction can become real summarization without loosening the rule that a
  compaction must actually save tokens.

## Disadvantages
- **A model-backed guardrail is non-deterministic.** The same content can be
  allowed on Tuesday and blocked on Wednesday, and a refusal is no longer fully
  reproducible from the spec. Audit records now carry the verdict's source for
  exactly this reason, and it is still not the same as reproducibility.
- **It costs a model call per boundary crossing per judgement check.** An agent
  with input, output, tool-input and tool-output guardrails and three checks
  each can spend more on screening than on the work. Nothing here budgets that;
  a deployment that enables it should expect the bill.
- **It can fail open or closed, and we chose open relative to the model.** When
  the classifier is unreachable or returns nonsense, we fall back to pattern
  matching and continue, so content a model would have caught gets through
  during an outage. We chose this because the alternative — blocking every
  boundary crossing when a model is down — turns a provider incident into a
  total outage of every agent in the organization, and because the
  deterministic floor still holds, so an outage degrades detection to what we
  had before this ADR rather than to nothing. The trade is explicit: a
  deployment where a miss is worse than a stop must not rely on this and should
  express the requirement as a `block` on a deterministic check.
- A classifier prompt is content sent to a model, which means the guardrail
  itself is now a place where sensitive text crosses a boundary — the thing
  guardrails exist to control.
- The classifier prompt is itself injectable: content under review can address
  the classifier. The union with patterns limits the damage to suppressing a
  model finding, not a pattern one.
- Two implementations of every check is more surface to keep honest; a
  divergence between them will surface as a confusing verdict.

## Alternatives considered
- **Replace patterns with a model outright** — simpler to explain, and it makes
  every guardrail depend on a network call and a bill, which no offline target
  can satisfy.
- **A model verdict that can clear a pattern match** — fewer false positives,
  and it lets a model be talked into clearing a leaked key. Declined.
- **Name the provider in the spec** — direct, and it breaks ADR-0004's
  implementation neutrality; `ModelClass` already exists for this.
- **Fail closed on classifier failure** — safer in a security reading, and it
  makes an agent organization unavailable whenever one provider is degraded.
  Rejected, with the escape hatch stated above.
- **Leave it at patterns and document the gap** — what WS-024 did at M1, and
  the gap is what this milestone exists to close.

## Verification
`tests/test_guardrail_classifiers.py` covers: the pattern classifier as the
default; a custom classifier changing what is blocked; a model classifier
catching a rephrased injection the list misses and clearing an innocent phrase
it trips on; low-confidence verdicts not counting; the pattern floor surviving
a model that sees nothing; structural checks never reaching the model;
unreadable and unreachable classifiers degrading and saying so; redaction still
masking through the pattern's span; the spec naming a `ModelClass` and no
vendor (asserted by scanning `src/orgagents/spec/`); the model summarizer in
use, degrading on failure, and compaction still refusing a summary that saves
nothing.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Classifier and summarizer protocols; pattern default, model adapter, fall back to patterns on failure. |
