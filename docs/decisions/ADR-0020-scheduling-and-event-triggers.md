---
id: ADR-0020
title: Scheduling and event triggers are first-class spec objects
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Platform SRE, Security Engineering, Product]
informed: [All engineering]
scope: [spec, compiler, targets, runtime]
workstreams: [WS-012]
supersedes: []
superseded_by: []
related: [ADR-0015, ADR-0021, ADR-0025, ADR-0011, ADR-0012]
tags: [scheduling, automation]
---

# ADR-0020: Scheduling and event triggers are first-class spec objects

## Context
Most of the value in an agentic organization is unattended: the flash report
drafted before anyone is awake, the close pack assembled on the first of the
month, the incident triaged the moment monitoring fires. Comparable products
have converged on this — Cowork packages a prompt and runs it on a cadence with
the same tools and skills as interactive work; Copilot agents have autonomous
triggers; LangGraph Platform ships cron alongside durable execution.

We had none of it. Worse, for a compiler the omission is structural: a runtime
can bolt on scheduling as a feature, but a compiler that cannot *express*
scheduling cannot generate it for any target, and every customer would build
their own cron alongside the system — with its own credentials, its own
failure handling and no relationship to the agent's permissions.

That last point is the real risk. A scheduler that runs agents under a
privileged service account silently reverses least privilege: the unattended
path becomes the powerful one.

## Decision
A **trigger** is a spec object. It names what starts a run — a `schedule`
(cadence), an `event` (abstract event class plus filters), a `webhook`, an
inbound `message` on a channel, or `manual` — and the agent that runs.

Every trigger carries its operational policy explicitly: `overlap` (skip,
queue, cancel previous, allow), `catch_up` for runs missed during downtime
(skip, run once, run all), `max_runtime_seconds`, a `failure` policy (retries,
backoff, escalate after N, halt after N) and `deliver_to` channels.

Two invariants bind everything else:

1. **A triggered run has exactly the owning agent's identity and permissions.**
   The scheduler holds no credentials of its own; it wakes the agent.
2. **Unattended work reports somewhere a human looks.** A trigger with neither
   a delivery channel nor a failure channel is a validation warning, and an
   error in production. Automation that fails silently is worse than none.

Cadences are expressed as five-field cron or as a plain interval ("every 15
minutes"), with a timezone. One interpreter (`orgagents/scheduling.py`) serves
the validator, the designer preview, the local scheduler and the cloud
schedulers, so they cannot disagree about when something fires.

## Scope
Everything that starts a run without a person asking, across the spec, the IR,
every target and the runtime. It does not cover workflow-internal scheduling
(a step that waits), which is the workflow engine's concern.

## Implementation
`spec.model.TriggerSpec` / `Cadence` / `FailurePolicy`; `scheduling.py` for
cron and interval parsing, fire-time computation, catch-up and retry policy;
`compiler.ir.TriggerIR` with normalized cadence and precomputed next runs;
`runtime/scheduler.py` for the service, with an injectable clock and runner so
the policies are testable without waiting; `targets/local.py` emits a scheduler
service plus `triggers.json`; the Terraform targets emit each provider's
scheduler or event-subscription resource, bound to the **agent's** service
account.

## Timeline
Phase 2 for the spec, the interpreter and the local scheduler; cloud scheduler
mappings ride with the existing Terraform targets.

## Advantages
- Unattended work is designed, reviewed and compiled like everything else.
- One cadence interpreter means the preview and every target agree.
- Scheduled runs inherit the agent's least-privilege envelope automatically.
- Overlap, catch-up and halt behaviour are explicit rather than emergent.
- A failing trigger escalates to a human instead of retrying into a budget.

## Disadvantages
- Cron in a spec is cron: timezones, DST and "the last Friday of the month"
  will all produce surprises, and our own interpreter owns those bugs.
- Precomputed fire times in the IR go stale the moment the IR is stored; they
  are for review, and treating them as authoritative would be wrong.
- Catch-up is a genuine trade-off with no safe default — `run_all` can stampede
  after an outage, `skip_missed` silently loses work.
- Per-provider scheduler semantics differ (deadlines, retries, at-least-once
  delivery), so identical specs will behave slightly differently per target.
- More unattended surface means more ways to spend money while nobody watches,
  which is why ADR-0022's budgets are a dependency, not a nicety.

## Alternatives considered
- **Leave scheduling to the customer's existing cron** — guarantees a second
  credential path around our permission model.
- **A runtime-only scheduler feature, not in the spec** — unbindable by the
  cloud targets; the design would not describe half of what the system does.
- **Library-based cron parsing** — fewer bugs of our own, but the semantics
  would be the library's and would differ from the cloud schedulers we compile
  to; we would still need our own normalization.

## Verification
`tests/test_scheduling.py` covers cron field parsing, weekday skipping,
epoch-anchored intervals, DST transitions, invalid expressions and every
policy. `tests/test_scheduler_service.py` covers overlap, retry, escalation,
halting, catch-up, concurrency and delivery. Compiler tests assert the local
scheduler holds no credentials and the Terraform triggers reference the agent's
service account.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Triggers in the spec; runs inherit the agent's identity. |
