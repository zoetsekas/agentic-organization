---
id: ADR-0003
title: The platform is a designer and compiler, not only a runtime
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Product]
consulted: [Developer Experience, Security Engineering, Platform SRE]
informed: [All engineering]
scope: [spec, compiler, targets, ui, sdk]
workstreams: [WS-002, WS-005, WS-009]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0005, ADR-0014]
tags: [foundational, product]
---

# ADR-0003: The platform is a designer and compiler, not only a runtime

## Context
An agent platform can be built two ways. As a **runtime**, it hosts agents and
you configure them against its APIs — which binds every customer to that
runtime's availability, release cadence and pricing. As a **designer and
compiler**, it captures the intended system abstractly and emits the code and
infrastructure the customer runs themselves.

Our users are architects and platform teams defining how an organization of
agents should work. They need to review the design before anything runs, to
deploy the result into environments we do not operate, and to keep running when
we are not in the request path. A hosted-only runtime cannot serve that, and an
enterprise procurement process will not accept it for regulated workloads.

## Decision
The product is a **designer and compiler**. A user defines an agentic system —
through the UI or the SDK — and the platform generates the agent code *and* the
infrastructure to run it, for a local machine or for a cloud account they own.

The runtime we ship is one *generation target* and the reference implementation
of the semantics, not the centre of the product. Nothing in the design may
assume the platform is present at execution time.

## Scope
Binds the product architecture: the spec, the compiler, every target, the UI
and the SDK. It does not mean the runtime is unimportant — it means the runtime
is replaceable, and its semantics must be expressible as generated artifacts.

## Implementation
* `orgagents.spec` — the implementation-neutral system specification (ADR-0004).
* `orgagents.compiler` — spec → IR → target plugins (ADR-0005).
* `orgagents.compiler.targets` — local (ADR-0011) and Terraform (ADR-0012).
* The designer UI and SDK are peers over the same spec (ADR-0018).
* The existing runtime becomes the `python-runtime` target's output plus the
  library that executes it.

## Timeline
Phase 1: spec and compiler core. Phase 2: local target. Phase 3: cloud targets.
The runtime library already exists and is refactored behind the spec, not
rewritten.

## Advantages
- Customers own and can audit what they run; no lock-in to our hosting.
- The design is reviewable and approvable *before* anything executes.
- Regulated and air-gapped deployments become possible at all.
- Targets can be added without touching the semantics of the design.

## Disadvantages
- Substantially more work than a hosted runtime: a compiler, multiple targets,
  and generated artifacts that must stay correct across provider changes.
- We lose the operational feedback loop a hosted runtime gives — we cannot see
  most deployments, so telemetry and support get harder.
- Generated code must be good enough for an engineer to own and debug, which is
  a much higher bar than internal runtime code.
- Every feature now has to be expressible in the spec *and* implementable in
  each target, or it fragments the product.

## Alternatives considered
- **Hosted runtime only** — simpler and faster to ship, but unsellable into the
  enterprise and air-gapped cases that motivate the product.
- **Config-driven runtime with an export button** — export becomes a
  second-class path that rots; the compiler must be the primary path.
- **Codegen without a runtime** — leaves us unable to demonstrate or test the
  semantics we generate.

## Verification
The compiler's targets are tested against the example specification; the
runtime is exercised through generated manifests rather than hand-built
objects, so a divergence between spec semantics and runtime behaviour fails the
suite.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Product is a designer/compiler; runtime is one target. |
