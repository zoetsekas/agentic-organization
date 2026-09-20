---
id: ADR-0005
title: Compile in two phases — Spec to IR, IR to target plugins
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Developer Experience, Platform SRE]
informed: [All engineering]
scope: [compiler, targets]
workstreams: [WS-005, WS-006, WS-007]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0011, ADR-0012, ADR-0014]
tags: [compiler]
---

# ADR-0005: Compile in two phases — Spec to IR, IR to target plugins

## Context
A spec is written for humans: defaults are omitted, roles are referenced by
name, team membership implies permissions, and the same intent can be expressed
several ways. A generator wants the opposite — everything resolved, explicit
and flat. If every target does its own resolution, they will disagree, and the
permissions a Terraform module grants will quietly differ from the ones the
local runtime enforces. That is the failure mode that matters most here.

## Decision
Compile in two phases with a published boundary between them:

1. **Spec → IR.** One resolver normalizes the spec: inherit team permissions
   down the hierarchy, expand roles into concrete permission sets, resolve
   effective sandbox and data-class access per agent, assign workload
   identities, and reject anything that violates the security rules.
2. **IR → artifacts.** Target plugins consume only the IR and emit files. A
   target never re-interprets the spec and never re-derives a permission.

The IR is versioned, serializable and diffable. `orgagents spec ir` prints it,
because it is the artifact a security reviewer should actually read.

## Scope
The compiler and every target plugin. Bindings are applied during phase 1 so the
IR is already binding-resolved when a target sees it.

## Implementation
`orgagents.compiler.ir` builds the IR (`SystemIR`, `AgentIR`, `TeamIR`,
`GrantIR`). `orgagents.compiler.base` defines the `Target` protocol —
`id`, `describe()`, `generate(ir) -> list[GeneratedFile]` — plus a registry.
`orgagents.compiler.engine` runs phase 1 then dispatches to the selected
targets, writing to an output directory with a manifest.

## Timeline
Phase 1 with the spec. The first target lands immediately after, to prove the
boundary is real and not an aspiration.

## Advantages
- Permission resolution happens exactly once, so targets cannot disagree.
- Adding a target is a plugin, not a change to the semantics.
- The IR is the natural review and audit artifact, and it diffs cleanly.
- Targets become testable in isolation from hand-written IR fixtures.

## Disadvantages
- An extra representation to design, version and document.
- The IR boundary is a commitment: a target needing something the IR lacks
  forces a core change, which slows target development.
- Debugging spans two phases; an incorrect artifact may be a resolver bug or a
  target bug, and the IR dump is the only way to tell.
- Over-normalizing risks losing author intent that a target could have used.

## Alternatives considered
- **Direct spec → artifacts per target** — fastest initially, guarantees
  divergence between targets on exactly the rules that must not diverge.
- **Three phases with per-target lowering** — more correct in principle, too
  much machinery for the current target count.

## Verification
Targets are tested against fixture IR; a test asserts no target module imports
`orgagents.spec`, making the phase boundary mechanically enforced.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Two-phase compiler with a plugin registry. |
