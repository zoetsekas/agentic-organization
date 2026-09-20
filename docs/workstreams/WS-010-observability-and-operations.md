---
id: WS-010
title: Observability and operations across targets
status: Proposed
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform SRE
contributors: [Platform Architecture]
scope: [spec, runtime, targets]
decisions: [ADR-0016]
depends_on: [WS-005, WS-006]
tags: [operations]
---

# WS-010: Observability and operations across targets

## Objective
Make a deployed agentic system operable by someone who did not design it: one
trace per delegation tree, uniform cost and token accounting, and alerts that
mean the same thing on a laptop and in production.

## Deliverables
- Observability contract in the spec: required signals, retention classes,
  alert conditions.
- OpenTelemetry trace propagation across delegations, sub-agents and workflows.
- A uniform metric set (session states, tokens, cost, approval backlog,
  tool errors) computed identically in every target.
- Target bindings: collector container locally, provider tracing and metrics
  services in the cloud.
- Operations console over the metric set, with alert acknowledgement.

## Scope
In: signals emitted by generated systems and the operator surfaces over them.
Out: the platform's own internal telemetry and product analytics.

## Approach
Declare the contract in the spec and make every target satisfy it, rather than
instrumenting per target. Allocate `trace_id` at the root session and propagate
through every delegation so the fan-out reconstructs as one trace. Keep alert
conditions as predicates over the shared metric set so thresholds are portable.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Contract in the spec | Phase 2 | Done |
| M2 Trace propagation verified end to end | Phase 2 | Done |
| M3 Local collector binding | Phase 2 | Not started |
| M4 Cloud sink bindings per provider | Phase 3 | Not started |
| M5 Sampling strategy for high fan-out | Phase 3 | Not started |

## Dependencies
WS-005 for the IR, WS-006 for the first target binding.

## Advantages
- Delegation trees are debuggable as a unit rather than as scattered spans.
- Cost attribution per agent and per team, uniformly across targets.
- Alert conditions travel with the design instead of living in a monitoring tool.

## Disadvantages
- Agent fan-out generates high trace volume; sampling is unavoidable and will
  tend to hide exactly the rare pathological runs worth seeing.
- Cost accounting depends on provider pricing data that goes stale silently.
- Span attributes are a leak path for regulated data unless carefully scoped.
- Teams with mature observability stacks will find the uniform contract
  redundant and will want to opt out, fragmenting the guarantee.

## Exit criteria
- A delegation three levels deep appears as one trace in every target.
- The metric set and alert rules are identical across targets (tested).
- Regulated data classes never appear in span attributes (tested).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Milestone statuses reconciled with what has shipped. |
| 1.0.0 | 2026-09-20 | Opened as Proposed; trace propagation already in progress in the runtime. |
