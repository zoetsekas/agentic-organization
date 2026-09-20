---
id: WS-006
title: Local deployment target — Compose stack and single-process dev loop
status: Active
version: 1.3.0
date: 2026-09-20
updated: 2026-09-20
owner: Developer Experience
contributors: [Platform Architecture]
scope: [targets]
decisions: [ADR-0011, ADR-0005, ADR-0009, ADR-0014, ADR-0053, ADR-0056, ADR-0059]
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
- A service workflow engine in the stack (ADR-0056): when a binding names an
  out-of-process engine, the target emits a pinned, tenant-scoped `langflow`
  service on the tenant's egress network, with its flows mounted read-only and
  its own secret, plus a README section stating that a flow running there is
  outside the agent's sandbox.
- A message bus in the stack (ADR-0059): a per-tenant `nats:2.15.0-alpine`
  service with JetStream enabled, on the tenant's network, with its own named
  volume and a tenant-prefixed subject namespace the agent services carry as
  `ORGAGENTS_BUS_SUBJECT_PREFIX`. Agents default to the in-process bus;
  `ORGAGENTS_BUS=nats` selects the broker.

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
| M6 Out-of-process workflow engine service | Phase 5 | Generated, never started |
| M7 Per-tenant message bus service | Phase 5 | Generated, never started |

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
- The Langflow service is generated and parsed by `docker compose config`, and
  nothing more: no daemon here has pulled the image, so its tag is pinned but
  unresolved, and the engine has never been started or called.
- The same is true of the NATS service: it is generated and parsed by
  `docker compose config`, never run. Its tag and digest are verified against
  the registry, but no broker has carried a message here.

## Exit criteria
- `orgagents compile --target local && make up` runs the example system.
- Generated Compose parses, emits no secret values, and `none`-class services
  have no egress network.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.3.0 | 2026-09-20 | Local stack emits a per-tenant NATS/JetStream bus on the tenant's network and volume, with a tenant-prefixed subject namespace; generated and parsed, never started (ADR-0059). |
| 1.2.0 | 2026-09-20 | Local stack emits a pinned, tenant-scoped Langflow service with mounted flows, its own secret and the sandbox caveat in the README (ADR-0056). |
| 1.1.0 | 2026-09-20 | Milestone statuses reconciled with what has shipped. |
| 1.0.0 | 2026-09-20 | Opened. Compose and single-process generation in progress. |
