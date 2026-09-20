---
id: ADR-0011
title: Docker defines the local deployment
status: Accepted
version: 1.1.0
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

# ADR-0011: Docker defines the local deployment

## Context
A designer must be able to run the system they just designed, on their own
machine, without a cloud account, a credit card or a platform team. If the only
way to see a design work is a cloud deployment, the feedback loop is measured in
hours and most designs are never tested at all.

Local must be the *same system*, not a mock. A local mode that diverges
semantically teaches users the wrong thing and hides real failures until
production.

## Decision
The `local` target expresses the whole system **in Docker**: a `Dockerfile` for
the platform runtime, **one `Dockerfile` per environment class** built from the
binding's image and packages, a `.dockerignore`, a `requirements.txt` for the
build, and a Compose file wiring agents, MCP servers, the scheduler, channel
bridges, memory and state together — plus a `Makefile`, a `.env` template
referencing (never containing) secrets, and a README.

Every service either **builds from a generated Dockerfile** or names an
upstream image explicitly (Postgres, the OTel collector). Nothing references an
image that this output does not build, because a compose file that pulls images
nobody can build is a demo, not a deployment.

An agent's container is built from **its environment class**, so the image it
runs in is the isolation boundary the spec declared rather than a convenient
default.

The same target also emits a **single-process** mode that runs everything in one
Python process with SQLite, for machines without Docker and for fast iteration.
Both modes consume the identical IR and enforce identical permissions; only the
bindings differ.

## Scope
Local development and demonstration. Explicitly not a production deployment
path, and the generated README says so.

## Implementation
`compiler.targets.local` emits `Dockerfile`, `docker/Dockerfile.<environment>`
per environment class in use, `.dockerignore`, `requirements.txt`,
`docker-compose.yaml`, `Makefile`, `.env.example`, per-agent manifests,
`triggers.json`, `channels.json`, `memory.json` and the IR. Images run as a
non-root user with a healthcheck. Environment classes carry their tier, network
posture, timeout and mounts into the Dockerfile header as comments, so the file
explains the boundary it implements; `none` maps to an internal Compose network
with no gateway.

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
- Building an image per environment class multiplies build time and disk on a
  laptop; six classes means six images, and layer sharing only helps so much.
- The generated `requirements.txt` is unpinned, so two builds a week apart can
  differ; pinning is left to the customer and that is a real reproducibility
  gap.
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
| 1.1.0 | 2026-09-20 | Docker now *defines* the system: a Dockerfile per environment class plus a runtime image, with every service building from generated files rather than pulling images nobody publishes. |
| 1.0.0 | 2026-09-20 | Accepted. Compose stack plus single-process mode from one IR. |
