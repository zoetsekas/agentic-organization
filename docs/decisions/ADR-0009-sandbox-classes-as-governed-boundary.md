---
id: ADR-0009
title: Abstract environment classes are the governed execution boundary
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering, Platform Architecture]
consulted: [Platform SRE, Compliance]
informed: [All engineering]
scope: [spec, security, targets, runtime]
workstreams: [WS-004, WS-006, WS-007]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0011, ADR-0012, ADR-0015]
tags: [security, sandbox]
---

# ADR-0009: Abstract environment classes are the governed execution boundary

## Context
Agents run code. The isolation boundary around that code is the single control
that limits damage when a prompt injection, a bad tool call or an ordinary bug
turns an agent destructive. If each agent describes its own environment, the
boundary becomes a per-agent configuration exercise and the weakest one defines
the organization's exposure.

The spec must stay neutral (ADR-0004), but a container image, a machine type and
an egress rule are deeply implementation-specific. So the *class* of environment
must be abstract while its realization stays in the binding.

## Decision
The spec declares **environment classes**: named, reviewed, abstract execution
profiles stating *capability needs*, not implementations — required toolchains
by category, a resource tier (`small`/`medium`/`large`/`gpu`), network posture
(`none`/`allowlist`/`internal`/`open`), which data classes may be mounted,
filesystem persistence, and a hard timeout.

An agent **selects** a class and may only **narrow** it. Widening is rejected at
validation time, not silently ignored: extra egress on a `none` class is an
error, a longer timeout is an error.

Each target **binds** a class to a concrete environment — a container image and
resource limits locally, a job spec and network policy in the cloud. The binding
may only produce something at least as restrictive as the class.

## Scope
The spec's `environments` block, the IR's per-agent resolved environment, and
every target's execution backend. It does not govern the platform's own control
plane.

## Implementation
`spec.model.EnvironmentClass` with narrow-only override semantics enforced in
`spec.validate` and re-checked in `compiler.ir`. The existing eight sandbox
templates become the reference *binding* for the local and Kubernetes targets.
Terraform targets emit the class as a job/task definition with the network
policy attached.

## Timeline
Phase 1 for the class model; bindings land with each target.

## Advantages
- One reviewed set of boundaries instead of per-agent improvisation.
- Narrow-only means a design cannot escalate its own isolation.
- The same class deploys to a laptop and to a regulated cloud account with the
  posture preserved.
- Security review happens on a handful of classes, not hundreds of agents.

## Disadvantages
- Abstraction loss: a class cannot express provider-specific controls (e.g. a
  confidential-computing node pool) without a target-shaped extension.
- Classes will be too coarse for someone; the pressure to add per-agent
  overrides will be constant and must be refused.
- Verifying that a binding is "at least as restrictive" is only partly
  automatable — network posture is checkable, image contents are not.
- More indirection to debug when an agent cannot reach something it needs.

## Alternatives considered
- **Per-agent environment definitions** — maximum flexibility, no review
  surface, and the weakest configuration sets the exposure.
- **Concrete templates in the spec** — breaks neutrality; the design would name
  container images and machine types.
- **No sandbox; trust the runtime** — unacceptable for code execution.

## Verification
Tests assert narrow-only resolution (extra egress dropped on `none`, timeouts
clamped) and that each target's binding preserves network posture and mounts.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Abstract classes in the spec, concrete bindings per target. |
