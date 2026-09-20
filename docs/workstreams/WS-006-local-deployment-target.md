---
id: WS-006
title: Local deployment target — Compose stack and single-process dev loop
status: Active
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
owner: Developer Experience
contributors: [Platform Architecture]
scope: [targets]
decisions: [ADR-0011, ADR-0005, ADR-0009, ADR-0014]
depends_on: [WS-005]
tags: [targets, local]
---

# WS-006: Local deployment target — Compose stack and single-process dev loop

## Objective
Get a designer from "I finished the design" to "it is running on my laptop" in
minutes, with the same permission semantics they will get in production.

## Deliverables
- `compiler.targets.local` producing `docker-compose.yaml`, `Makefile`,
  `.env.example`, per-agent manifests, runtime config and a README.
- A single-process mode over SQLite for machines without Docker.
- Environment-class binding to container images, resource limits and Compose
  networks, with `none` mapping to an egress-free internal network.
- CI job compiling the example spec and asserting the stack is well formed.

## Scope
In: local development and demonstration. Out: production deployment, local
Kubernetes (a later candidate), and Windows-specific packaging.

## Approach
Bind the same IR the cloud targets consume, so local is the identical system
with different bindings rather than a mock. Emit a `Makefile` as the single
entry point — `make up`, `make seed`, `make logs` — because the value is the
loop, not the YAML. Document explicitly where Compose cannot reproduce cloud
network policy or IAM instead of implying parity.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Compose generation from IR | Phase 2 | Done |
| M2 Single-process mode | Phase 2 | Done |
| M3 Makefile, env template, README | Phase 2 | Done |
| M4 CI smoke test of the generated stack | Phase 2 | Not started |
| M5 Local Kubernetes mode evaluation | Phase 3 | Not started |

## Dependencies
WS-005 for the IR and plugin API.

## Advantages
- Fast, offline, no cloud account; makes the product's core loop demonstrable.
- Doubles as the integration harness for the compiler itself.
- Identical permission semantics make local testing meaningful.

## Disadvantages
- Compose approximates network policy and cannot represent IAM, so part of the
  security behaviour is unverified locally — a documented gap, not a solved one.
- Two local modes to maintain and support.
- Laptop resource limits are advisory; `large` and `gpu` classes cannot be
  exercised honestly.
- Generated stacks age against Docker/Compose schema changes.

## Exit criteria
- `orgagents compile --target local && make up` runs the example system.
- Generated Compose parses, emits no secret values, and `none`-class services
  have no egress network.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Milestone statuses reconciled with what has shipped. |
| 1.0.0 | 2026-09-20 | Opened. Compose and single-process generation in progress. |
