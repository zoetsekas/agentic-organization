---
id: ADR-0088
title: A planning flag, and deepagents parameter fidelity
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering]
informed: [All engineering]
scope: [spec, compiler]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0028, ADR-0067, ADR-0073, ADR-0082, ADR-0083, ADR-0086]
tags: [langgraph, deepagents, spec, target, hitl, memory, skills]
---

# ADR-0088: A planning flag, and deepagents parameter fidelity

## Context
A review against the deepagents API (`create_deep_agent`) found that the
`langgraph` target expressed most of a design in the **system-prompt prose**
rather than in the deepagents **parameters** that carry the same facts
structurally. deepagents had, by this point, promoted `skills` and `memory` to
first-class parameters, and already had `permissions`, `response_format`,
`interrupt_on` and a `middleware` seam. The design model itself already
expressed skills (ADR-0083 companion), two-tier memory (ADR-0028), sandboxes
(ADR-0082), human-in-the-loop (interrupt_on + approvers), sub-agents and output
contracts — so the gap was emission fidelity, not a missing model.

One genuine model gap remained: **planning**. deepagents has a planning tool
(`write_todos` via `TodoListMiddleware`); the design had no way to say "this
agent keeps an explicit task plan".

## Decision
Two changes.

1. **A `planning` flag on an agent** (spec → IR). Default `false`: an agent that
   coordinates a multi-step task can be marked `planning: true`, and a runtime
   with a planning tool is given it. This is the one new design concept; the
   rest of the review's items were already modelled.

2. **deepagents parameter fidelity in the `langgraph` target.** The design
   facts already in the IR are lowered into the matching deepagents parameters
   instead of only the prompt:
   - `skills=` from the agent's skills;
   - `memory=` from its long-term memory namespaces (the backing store stays
     the infrastructure target's, ADR-0028);
   - `permissions=[FilesystemPermission(...)]` mirroring the runtime's virtual-
     filesystem rule — allow read (and write, if the design grants any) under
     the workspace, deny elsewhere (the explicit floor, ADR-0008);
   - `response_format=` from an output contract's schema (the retry-on-violation
     policy stays the harness);
   - `middleware=[TodoListMiddleware(), ModelCallLimitMiddleware(...)]` for
     planning and the turn ceiling;
   - `interrupt_on={tool: True}` as before, now with the paired approver named
     in a comment beside the call, because *which* human resumes a paused run is
     this platform's model, not deepagents' (ADR-0067 rule 5).

   The conformance report (ADR-0073) moves these rows from "prose only" / "not
   carried" to "emitted", and states honestly what still is not: the sandbox's
   **network posture** (the infrastructure target's), the **approver routing**,
   and the authority model (roles, mandates, separation, autonomy, data-class
   egress).

Streaming was considered and deliberately **not** modelled: it is how LangGraph
serves a graph, a runtime/deployment property, not an organizational fact.

## Scope
The spec model (`AgentSpec.planning`), the IR (`AgentIR.planning`), and the
`langgraph` target's `create_deep_agent` emission. The `adk` target is
unchanged: `LlmAgent` has no skills/memory/permissions/interrupt parameters to
lower into. SPEC_VERSION becomes 1.4.0 with an additive, no-op migration.

## Implementation
`AgentSpec.planning` / `AgentIR.planning` and the build_ir wiring; a 1.3.0 →
1.4.0 migration step (`_planning_flag`, no data change). In
`compiler/targets/langgraph.py`: `_response_format`, `_permissions`,
`_middleware`, `_approver` helpers and the extended call assembly, plus the
imports (`FilesystemPermission`, `langchain.agents.middleware`). AYC's
ecommerce agent is marked `planning: true` and holds a skill and long-term
memory, so its regenerated graph exercises every new parameter.

## Timeline
Accepted 2026-09-22; shipped with the regenerated examples at spec_version
1.4.0.

## Advantages
- The generated deepagents graph carries the design structurally, not as prose
  a reader must trust the model to honour.
- The design/runtime line is drawn honestly: what deepagents can enforce is a
  parameter; what only this platform can (approver routing, network posture,
  the authority model) is named as such.
- One small, additive model change (planning) rather than inventing concepts
  the model already had.

## Disadvantages
- More generated surface tied to deepagents' current parameter names; if the
  library renames one, the target must follow.
- `memory=` emits namespace names, not the store; a reader could over-read it as
  a full memory deployment. The conformance row says otherwise.
- `TodoListMiddleware` / `ModelCallLimitMiddleware` are imported from
  `langchain.agents.middleware`, the path this codebase's runtime already uses;
  a different deepagents packaging would need a one-line change.

## Alternatives considered
- **Extend the design model broadly (memory, sandboxes, HITL, streaming).**
  Rejected: the model already expresses all of these; only planning was
  missing, and streaming is a runtime property, not a design fact.
- **Leave the facts in the prompt.** Rejected: a fact the model is merely told
  in prose is weaker than a parameter the framework enforces, and it reads as
  carried when it is not (ADR-0073).

## Verification
`tests/test_langgraph_target.py` asserts skills lower into `skills=`, long-term
memory into `memory=`, filesystem permissions with the explicit deny floor, an
output contract into `response_format`, a `planning` agent into
`TodoListMiddleware`, and that the approver the interrupt routes to is named;
and that the conformance report now claims skills, memory, structured output
and planning. `tests/test_spec_migration_and_schema.py` covers the 1.4.0
migration; every example loads and compiles at 1.4.0.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. An additive agent `planning` flag (SPEC_VERSION 1.4.0), and the `langgraph` target lowering skills, long-term memory, filesystem permissions, response_format, planning/turn-limit middleware and the named approver into deepagents parameters. |
