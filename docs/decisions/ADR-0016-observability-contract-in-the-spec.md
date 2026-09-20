---
id: ADR-0016
title: The observability contract is declared in the spec and compiled into every target
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform SRE, Platform Architecture]
consulted: [Security Engineering, Compliance]
informed: [All engineering]
scope: [spec, runtime, targets]
workstreams: [WS-010]
supersedes: []
superseded_by: []
related: [ADR-0005, ADR-0012, ADR-0017]
tags: [operations]
---

# ADR-0016: The observability contract is declared in the spec and compiled into every target

## Context
Observability bolted on after deployment is inconsistent by construction: each
target instruments differently, session traces stop at target boundaries, and
the questions operators actually ask — what did this agent do, what did it cost,
what is waiting on a human — become unanswerable across a fleet.

Agents make this worse than ordinary services: a single request fans out through
delegations, sub-agents and workflows, and the useful unit of analysis is the
whole tree, not one span.

## Decision
The spec declares an **observability contract**: the required signals (session
traces, tool-call spans, delegation edges, token and cost counters, approval
events), their retention class, and the alert conditions that matter
(failure rate, cost budget, approval backlog, tool-error spike).

Every target must satisfy that contract. Traces are OpenTelemetry;
`trace_id` is allocated at the root session and propagated through every
delegation and workflow step, so a delegation tree reconstructs as one trace.
Targets bind the contract to a concrete sink — a collector container locally,
the provider's tracing and metrics services in the cloud.

Alert conditions are predicates over the same metric set in every target, so a
threshold means the same thing on a laptop and in production.

## Scope
Signals emitted by generated systems. Excludes the platform's own internal
telemetry and product analytics.

## Implementation
`spec.model.Observability` → IR → target bindings. The runtime already emits
session events with `trace_id`/`span_id`/`parent_span_id` and computes metrics
and alerts from the event store; `observability.export_span` forwards to the
bound exporter.

## Timeline
Phase 2 with the local target; cloud sinks in phase 3.

## Advantages
- Delegation trees are one trace, so the fan-out is actually debuggable.
- Cost and token accounting is uniform across targets and per agent.
- Alert conditions travel with the design instead of living in someone's
  monitoring tool.
- Approval backlogs are observable, which is the main human-in-the-loop failure.

## Disadvantages
- Trace volume from agent fan-out is high; sampling is unavoidable and will hide
  exactly the rare pathological runs we most want to see.
- Cost attribution depends on provider pricing data that goes stale.
- A uniform contract is a floor, not a ceiling — teams with mature observability
  stacks will find it redundant and will want to opt out.
- Tracing adds latency and, for regulated data, a new place for payloads to leak
  unless span attributes are carefully scoped.

## Alternatives considered
- **Per-target observability** — less work, and cross-target comparison becomes
  impossible.
- **Logs only** — cheap, but cannot express delegation trees.
- **Vendor APM SDK** — good tooling, breaks provider neutrality.

## Verification
Tests assert trace-id propagation from parent to child sessions and that the
metric set and alert rules are identical across targets.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Declared contract, OTel, uniform alert predicates. |
