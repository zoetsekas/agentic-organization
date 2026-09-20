---
id: ADR-0025
title: Durability and resumability are declared properties, not runtime accidents
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Platform SRE]
consulted: [Developer Experience]
informed: [All engineering]
scope: [spec, compiler, targets, runtime]
workstreams: [WS-012, WS-010]
supersedes: []
superseded_by: []
related: [ADR-0020, ADR-0016, ADR-0013]
tags: [reliability]
---

# ADR-0025: Durability and resumability are declared properties, not runtime accidents

## Context
Unattended work makes failure semantics matter. An overnight close pack that
dies at step 40 and restarts from zero is worse than no automation: it burns
the budget twice and delivers late. LangGraph's answer — durable state that
survives failure, checkpointing at every step, interruptible execution a human
can inspect and steer — is the right shape, but it is a property of one
framework. Ours must hold across four adapters and four targets, or a design
deployed to one target quietly behaves differently on another.

Idempotency is the other half. At-least-once delivery is the norm for cloud
schedulers and event buses, so a trigger will occasionally fire twice.

## Decision
`resilience` is a spec block: `checkpoint_each_step`, `resume_on_failure`,
`idempotent_triggers`, `max_run_seconds` and a `dead_letter_channel`. Every
target must satisfy it, and the compiler enforces the parts it can: a trigger's
`max_runtime_seconds` is clamped to `max_run_seconds` in the IR, and a trigger
declaring more than the system allows is a validation error.

Runs that exhaust their retries land in the declared dead-letter channel, where
a human sees them — the same routing contract as any other human contact
(ADR-0021).

## Scope
Execution durability for triggered and interactive runs. Storage durability
(backups, replication) is a deployment concern the targets inherit from the
customer's infrastructure.

## Implementation
`spec.model.Resilience`; clamping in `compiler.ir`; validation of
trigger-versus-system run budgets; the Terraform targets emit the dead-letter
topic; the scheduler service applies retry and halt policy and escalates rather
than looping.

## Timeline
Phase 2 for declaration, clamping and dead-lettering. True step-level
checkpointing depends on the runtime adapter and lands with WS-008's
live-adapter work.

## Advantages
- Failure behaviour is designed and reviewed rather than discovered in an
  incident.
- Run-length budgets cannot be exceeded by a trigger that asks for more.
- Exhausted runs reach a human instead of disappearing.
- One declaration describes the guarantee across adapters and targets.

## Disadvantages
- We declare more than we currently enforce: `checkpoint_each_step` and
  `resume_on_failure` are honoured by the LangGraph adapter and are aspirational
  for the others, which is a gap a reader could mistake for a guarantee.
- Idempotency cannot be provided by the platform alone — a tool with side
  effects must cooperate, and nothing here verifies that it does.
- Checkpointing every step costs storage and latency, and the spec gives no way
  to trade that off per workflow.
- Per-provider at-least-once semantics differ, so duplicate-run frequency will
  vary by target.

## Alternatives considered
- **Leave durability to the chosen framework** — identical specs would behave
  differently per adapter with nothing in the design saying so.
- **Mandate one durable runtime** — contradicts adapter neutrality (ADR-0013).
- **Handle failure only in the scheduler** — covers triggered runs and leaves
  long interactive runs unprotected.

## Verification
Compiler tests assert trigger runtimes are clamped to the system budget;
validator tests reject a trigger exceeding it; scheduler tests cover retry
exhaustion, halting and escalation to the dead-letter channel.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Declared resilience, clamped run budgets, dead-lettering. |
