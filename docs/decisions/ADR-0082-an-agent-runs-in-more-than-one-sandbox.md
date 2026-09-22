---
id: ADR-0082
title: An agent runs in more than one sandbox
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture, Security Engineering]
consulted: [Runtime Engineering, Designer]
informed: [All engineering]
scope: [spec, compiler, runtime, security]
workstreams: [WS-003, WS-028]
supersedes: []
superseded_by: []
related: [ADR-0055, ADR-0065, ADR-0069, ADR-0072, ADR-0073, ADR-0080]
tags: [security, sandbox, environment, isolation]
---

# ADR-0082: An agent runs in more than one sandbox

## Context
An agent declared exactly one execution environment: `agent.environment` was a
single `EnvironmentOverride`, and placement (ADR-0069) put the agent in one
sandbox — one volume, one process namespace, one network posture.

Real agents do work of more than one shape. Northwind's audit agent reads a
case and tests a scorecard against the book; Northbeam's campaign operator
pushes to ad platforms *and* reads performance; Lumière's investigator works
an alert in isolation. Each of these is two blast radii, and one sandbox
holding both means the wider one wins: the scorecard model shares a volume
with the case file, the behavioural data sits in the sandbox that talks to the
ad platforms, and the isolation the design was drawn to get is quietly gone.

Reviewing the agent abstraction of Deep Agents, the OpenAI Agents SDK and the
Microsoft Agent Framework confirmed the shape rather than the count: all three
treat *where code executes* as a first-class, swappable thing (Deep Agents'
`CompositeBackend` with per-prefix routing; OpenAI's `ShellTool` container
config with its own network policy; MAF's per-client sandboxing). None fixes
an agent to a single execution environment, and neither should we.

The user's request was direct: an agent or sub-agent may have one or more
sandbox environments.

## Decision
`agent.environment` becomes `agent.environments: list[EnvironmentOverride]`,
and `subagent.environment` becomes `subagent.environments: list[str]`. An agent
runs in every sandbox it declares.

Which sandbox a given *call* belongs in is **derived, not declared**. A
capability names its data classes (ADR-0073); a data class names the
environments it is allowed in (`allowed_environments`); the intersection of
those, restricted to the sandboxes the agent has, is where that call may run.
Declaring the mapping per capability as well would be a second source that can
disagree with the first, so it is computed.

That derivation gives the two new checks their teeth:

- **`capability_without_a_sandbox`** (error). An agent holds a capability
  whose data classes admit no sandbox the agent runs in. The work has nowhere
  to happen, and the fix is a modelling decision — widen the class, or give the
  agent the sandbox — not a default. Enforced at the phase gate, so it is
  resolved once, before anything is built.
- **`sandbox_without_work`** (warning). An agent declares a sandbox no
  capability it holds can use. A boundary nobody is inside is not a security
  control, it is a line on a diagram.

Placement follows: an agent with two sandboxes is in two placements, and
`co_resident` / `same_placement` are about sharing *any* one of them, because
sharing one volume in one place out of two is still sharing it.

Where a downstream target can express only one execution environment per
workload — a single Cloud Run service, one Docker service — it takes the
**widest** sandbox (highest network posture, then highest tier) and says so in
its conformance notes. A deployed workload is one shape; under-sizing or
under-permitting it would make the design undeployable. The narrower sandboxes
still bound what the agent's *code execution* may reach, which is where the
isolation actually lives (ADR-0055): the `sandbox-<class>` services are one per
class regardless.

A sub-agent may run in any of its parent's sandboxes and none beyond: one it
could pick for itself would be a way to reach a boundary its caller was never
given (ADR-0027). Naming none inherits the parent's first.

## Scope
The spec's agent and sub-agent models, the migration to spec 1.3.0, the
validator, placement resolution, the IR (one runner and one network boundary
per sandbox), the local and terraform targets (widest-sandbox rule), the
runtime loader and harness (`Agent.sandboxes`, with `sandbox` kept as the
first, read-only), and the canvas's placement derivation. It does not change
the permission resolver, the mandate model, or what a sandbox *is*.

## Implementation
**Implemented.** Spec bumped to 1.3.0 with a migration step that lifts a
single `environment:` into a one-element `environments:` list and says so.
Reading a pre-1.3.0 document through the raw model — bypassing the migration —
is refused rather than silently dropping the sandbox, because a dropped
sandbox is the exact failure this ADR exists to prevent. Every shipped example
carries the new shape; three of them gained a genuine second sandbox, and the
`capability_without_a_sandbox` check found the need in each case rather than
the author remembering to.

## Timeline
Phase 6, alongside the sandbox-plane work (ADR-0055).

## Advantages
- The isolation a design draws is the isolation it gets: two blast radii are
  two boundaries, not one enclosing both.
- The mapping from work to sandbox is derived, so it cannot drift from the
  data-class declarations it depends on.
- The two checks make an empty or idle sandbox a finding, not a surprise.

## Disadvantages
- Targets that deploy one workload per agent must pick a sandbox for the
  service shell and document the loss. The widest-sandbox rule is a defensible
  default, not a free lunch.
- More sandboxes is more images to build (ADR-0055's treadmill), one per
  class per agent that uses it.

## Alternatives considered
- **Keep one sandbox and take the widest.** What the code did by accident, and
  the thing this ADR exists to stop: it collapses two boundaries into their
  union and calls it isolation.
- **Declare the capability→sandbox mapping explicitly.** A second source of
  truth beside `allowed_environments`, free to disagree with it. Derivation
  cannot.
- **A sandbox per capability.** Precise and unusable: dozens of near-identical
  boundaries, and no reviewer could hold the picture.

## Verification
Tests assert: an agent with two sandboxes that each do work validates; a
capability whose data classes admit no held sandbox is refused; a sandbox no
capability uses warns; a duplicate sandbox is refused; such an agent resolves
to two placements and two IR runners, never one for the wider; a sub-agent may
use a parent's sandbox and not a new one; the 1.2.0→1.3.0 migration lifts the
key and names the change; and reading a pre-1.3.0 document raw is refused
rather than dropping the sandbox.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. `environment` becomes `environments` on agents and sub-agents; the work→sandbox mapping is derived from data classes; empty and idle sandboxes are findings; targets that deploy one workload take the widest and say so. |
