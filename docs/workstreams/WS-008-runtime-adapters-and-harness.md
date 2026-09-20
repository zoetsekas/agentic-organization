---
id: WS-008
title: Runtime adapters and harness binding
status: Active
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Developer Experience]
scope: [runtime, targets]
decisions: [ADR-0013, ADR-0010]
depends_on: [WS-005]
tags: [runtime]
---

# WS-008: Runtime adapters and harness binding

## Objective
Execute a compiled agent on whichever framework a customer has approved, without
the design naming one, and without any framework being able to widen what an
agent may reach.

## Deliverables
- Adapter protocol plus implementations: LangChain deep agents, OpenAI Agents
  SDK, native LangGraph, and a deterministic `echo` adapter.
- Harness assembly from the IR: tools, MCP mounts, capability constraints,
  approval gates, budgets.
- Declarative workflow execution that compiles onto LangGraph when installed and
  runs on the built-in interpreter otherwise.
- Tests asserting identical policy behaviour across adapters.

## Scope
In: the agent execution loop, harness assembly and workflow execution. Out:
model provider selection (a binding field) and prompt engineering quality.

## Approach
Keep policy enforcement outside the adapter, in harness assembly, so swapping
frameworks cannot change what an agent may do. Import frameworks lazily so a
deployment installs only what it uses. Use the `echo` adapter as the test and
dry-run path, which keeps the suite free of network calls and API keys.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Adapter protocol and echo runtime | Phase 1 | Done |
| M2 Harness assembly from IR | Phase 2 | Done |
| M3 Deep agents and OpenAI SDK adapters verified against live models | Phase 2 | Not started |
| M4 LangGraph workflow compilation verified | Phase 2 | Not started |

## Dependencies
WS-005 for the IR the harness is assembled from.

## Advantages
- Framework churn is absorbed at one seam.
- Customers can mandate an approved framework without redesigning.
- The whole system is testable with no model calls.

## Disadvantages
- The adapter interface is a lowest common denominator, so each framework's
  distinctive strengths are underused.
- Four code paths, only one of which is cheap to test exhaustively; the
  live-model adapters are the least covered and the most used in production.
- Most real failures will occur inside third-party loops, below our abstraction.

## Exit criteria
- The same compiled agent runs on all four adapters with identical policy
  outcomes.
- Workflow semantics are identical with and without LangGraph installed.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Milestone statuses reconciled with what has shipped. |
| 1.0.0 | 2026-09-20 | Opened. Protocol and echo adapter done; harness binding in progress. |
