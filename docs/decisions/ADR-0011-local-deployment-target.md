---
id: ADR-0011
title: Local target generates a self-contained Compose stack and dev loop
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Developer Experience, Platform Architecture]
consulted: [Platform SRE]
informed: [All engineering]
scope: [targets, compiler]
workstreams: [WS-006]
supersedes: []
superseded_by: []
related: [ADR-0005, ADR-0009, ADR-0012]
tags: [targets, local]
---

# ADR-0011: Local target generates a self-contained Compose stack and dev loop

## Context
A designer must be able to run the system they just designed, on their own
machine, without a cloud account, a credit card or a platform team. If the only
way to see a design work is a cloud deployment, the feedback loop is measured in
hours and most designs are never tested at all.

Local must be the *same system*, not a mock. A local mode that diverges
semantically teaches users the wrong thing and hides real failures until
production.

## Decision
The `local` target generates a self-contained stack: a Docker Compose file with
the agent runtime, one container per bound MCP server, a Postgres for state, an
OTel collector, and the sandbox execution backend — plus a `Makefile`, a `.env`
template referencing (never containing) secrets, and a README.

The same target also emits a **single-process** mode that runs everything in one
Python process with SQLite, for machines without Docker and for fast iteration.
Both modes consume the identical IR and enforce identical permissions; only the
bindings differ.

## Scope
Local development and demonstration. Explicitly not a production deployment
path, and the generated README says so.

## Implementation
`compiler.targets.local` emits `docker-compose.yaml`, `Makefile`, `.env.example`,
per-agent manifests and the runtime config. Environment classes bind to
container images with resource limits and Compose network policies; `none` maps
to an internal network with no egress.

## Timeline
Phase 2, immediately after the compiler core — it is how every later target gets
smoke-tested.

## Advantages
- Minutes from design to a running system, with no cloud dependency.
- Identical permission semantics to cloud, so local testing is meaningful.
- Works offline and in air-gapped evaluation environments.
- Doubles as the integration-test harness for the compiler itself.

## Disadvantages
- Compose cannot honestly reproduce cloud network policy or IAM, so some
  security behaviour is approximated — a gap we must document rather than paper
  over.
- Two local modes (Compose and single-process) is a maintenance cost and a
  support-question generator.
- Resource limits on a laptop are advisory; a `large` class will not behave as
  it does in the cloud.
- Generated stacks age against Docker and Compose schema changes.

## Alternatives considered
- **Local Kubernetes (kind/k3d)** — much closer to cloud semantics, far heavier
  to install and slower to iterate; a candidate for a third mode later.
- **Single-process only** — fastest, but cannot exercise MCP servers as
  separate deployments or network posture at all.
- **No local target** — unacceptable for the product's core loop.

## Verification
CI compiles the example spec to the local target and asserts the Compose file
parses, services resolve, no secret values are emitted, and `none`-class
services have no egress network attached.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Compose stack plus single-process mode from one IR. |
