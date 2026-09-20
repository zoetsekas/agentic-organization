---
id: ADR-0035
title: Guardrails check what passes, as permissions check what is reachable
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering, Platform Architecture]
consulted: [Compliance, Product]
informed: [All engineering]
scope: [spec, compiler, runtime, security]
workstreams: [WS-024]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0017, ADR-0030, ADR-0037]
tags: [security, guardrails]
---

# ADR-0035: Guardrails check what passes, as permissions check what is reachable

## Context
Reading the OpenAI Agents SDK made an omission obvious: guardrails are one of
its handful of core primitives, and we had nothing equivalent. Our model
answered *what may this agent reach* thoroughly — roles, permissions, data
classes, capability constraints — and never answered *what may pass*.

Those are different questions. An analyst agent legitimately permitted to query
the warehouse can put a customer's email address into a Slack message. Nothing
in ADR-0008 is violated. The data classification in ADR-0017 governs stores and
placement, not the text of an answer. An agent handed a credential in a prompt
can repeat it in its output.

## Decision
A **guardrail** is a declared check on content crossing a boundary. Four
boundaries — `input`, `output`, `tool_input`, `tool_output` — and named checks
rather than free-form rules: `secrets`, `pii`, `prompt_injection`,
`data_class`, `url_allowlist`, `pattern`, `max_length`, `schema`.

Each guardrail states what happens when it trips: **block** (refuse; the agent
is told why), **redact** (remove and continue), **flag** (allow, record,
surface) or **escalate** (stop and ask a human on a named channel).

Two structural rules:

1. **System guardrails apply to every agent.** An agent may add guardrails; it
   can never remove one. Safety posture is set for the organization, not
   negotiated per agent.
2. **Guardrails are enforced outside the agent loop**, before the model sees
   input and before output leaves. A jailbroken prompt cannot talk its way past
   a check it never reaches. The composed prompt tells the agent the boundaries
   exist and to report what it needed rather than work around one.

Checks are named rather than arbitrary regular expressions because a guardrail
nobody can read is a guardrail nobody reviews — and a guardrail that cries wolf
is one people switch off.

## Scope
Content entering and leaving an agent or a tool. It does not replace
permissions (ADR-0008), data classification (ADR-0017) or endpoint trust
(ADR-0030); it is the layer those three do not cover.

## Implementation
`spec.model.Guardrail` with `GuardrailKind`, `GuardrailCheck` and
`GuardrailAction`; `guardrails.py` holds the engine and the pattern library;
the IR resolves system plus per-agent guardrails onto every agent; the runtime
screens the prompt before the adapter runs and the result before it is
returned, logging a `guardrail` event with the deciding check and raising an
alert on escalation.

## Timeline
Phase 3, after the second landscape pass.

## Advantages
- Closes the gap between "may reach" and "may pass" that permissions alone left.
- Enforcement sits outside the model, so prompt manipulation cannot disable it.
- Named checks make a guardrail set reviewable in a few minutes.
- Four actions cover the real range: refuse, mask, record, ask a human.
- System-wide application means safety cannot be quietly narrowed per agent.

## Disadvantages
- **Pattern-based detection is crude.** The PII and secret patterns will miss
  real cases and flag innocent ones; a credit-card-shaped order number will
  trip `pii`, and a novel credential format will not trip `secrets`. This is
  the honest limit of the feature and the reason `flag` exists alongside
  `block`.
- Every check runs on every boundary crossing, which costs latency on long
  outputs.
- `prompt_injection` detection by phrase list is trivially evaded; it is a
  signal for the trace, not a defence, and treating it as one would be worse
  than having nothing.
- Redaction changes content the agent believed it produced, which can make a
  downstream step incoherent in ways that are hard to debug.
- Over-blocking trains users to route around the system; the temptation to
  weaken a guardrail after a false positive is the main failure mode.

## Alternatives considered
- **Rely on permissions and classification alone** — what we had; leaves the
  output channel entirely ungoverned.
- **Model-based classifiers for each check** — far better recall, at the cost
  of latency, spend and a second model to evaluate; a candidate once the
  pattern approach demonstrably fails.
- **Guardrails as prompt instructions** — free, and disabled by the first
  successful injection.
- **A policy language (e.g. OPA) for content** — powerful and unreviewable by
  the people who need to review these.

## Verification
`tests/test_guardrails_and_context.py` covers blocking credentials, redacting
identifiers, flagging injection, escalating on a restricted data class, clean
content passing untouched, URL allowlists and length limits, boundary scoping,
and that no agent in the example holds fewer guardrails than the system. A
runtime test asserts a credential in output is withheld and recorded.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Four boundaries, named checks, four actions, enforced outside the loop. |
