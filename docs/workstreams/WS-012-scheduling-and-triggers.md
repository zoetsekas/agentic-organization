---
id: WS-012
title: Scheduling, event triggers and durable unattended work
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform SRE
contributors: [Platform Architecture]
scope: [spec, compiler, targets, runtime]
decisions: [ADR-0020, ADR-0025]
depends_on: [WS-005, WS-011]
tags: [scheduling, reliability]
---

# WS-012: Scheduling, event triggers and durable unattended work

## Objective
Let a designed system do the work nobody is watching — on a cadence, on an
event, on an inbound message — with the same permissions, the same budget and
the same reporting path as interactive work, and with failure behaviour that is
designed rather than discovered.

## Deliverables
- `TriggerSpec`, `Cadence`, `FailurePolicy` in the spec; validation of cadence,
  delivery, ownership and run budgets.
- `scheduling.py`: one interpreter for cron and interval cadences, fire-time
  computation, catch-up, retry and halt policy.
- `runtime/scheduler.py`: the service, with injectable clock and runner.
- Local target: a scheduler service plus `triggers.json`.
- Terraform targets: per-provider scheduler and event-subscription resources
  bound to the **agent's** identity, plus the dead-letter topic.
- `orgagents schedule` (preview and simulate) and `orgagents scheduler` (run).
- `resilience` block with run-budget clamping and dead-lettering.

## Scope
In: what starts a run, what happens when it overlaps, fails or was missed, and
where the result goes. Out: workflow-internal waits, and step-level
checkpointing in adapters other than LangGraph (WS-008).

## Approach
Express cadence in a form both a person and every target can read, and
interpret it in exactly one place so the preview and the cloud schedulers
agree. Make the scheduler credential-free: it wakes the agent, which runs under
its own identity. Make every policy explicit in the spec, and test them with an
injected clock rather than by waiting.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Trigger model, cadence interpreter, validation | Phase 2 | Done |
| M2 Scheduler service with overlap/retry/halt | Phase 2 | Done |
| M3 Local and Terraform target generation | Phase 2 | Done |
| M4 Event-source bindings beyond the abstract class | Phase 3 | Not started |
| M5 Step-level checkpointing across adapters | Phase 3 | Not started |

## Dependencies
WS-005 for the IR. WS-013 for the channels triggered runs report to. Budgets
from WS-014 matter here: unattended work is the main way spend escapes.

## Advantages
- Unattended work is designed, reviewed and compiled like everything else.
- Scheduled runs inherit least privilege automatically.
- One cadence interpreter removes a whole class of "why did it fire then".
- Failure escalates to a human instead of retrying into a budget.

## Disadvantages
- We own a cron implementation, and therefore its timezone and DST bugs.
- Catch-up has no safe default: `run_all` can stampede after an outage,
  `skip_missed` silently drops work, and the designer must choose.
- Per-provider scheduler semantics differ, so identical specs behave slightly
  differently across targets — documented, not solved.
- Precomputed fire times in the IR are review aids that look authoritative.

## Exit criteria
- The example compiles to a working scheduler on every target.
- Overlap, catch-up, retry, escalation and halt are covered by tests. ✔
- No generated scheduler holds a credential of its own. ✔

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Model, interpreter, service and target generation landed. |
