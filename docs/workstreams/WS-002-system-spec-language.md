---
id: WS-002
title: System Spec language, schema and versioning
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Security Engineering, Developer Experience]
scope: [spec]
decisions: [ADR-0004, ADR-0002, ADR-0017]
depends_on: [WS-001]
tags: [spec, foundational]
---

# WS-002: System Spec language, schema and versioning

## Objective
Define the implementation-neutral document that is the single source of truth
for an agentic system, so one design can be reviewed once and deployed to a
laptop or to any of three clouds.

## Deliverables
- `orgagents.spec.model` — typed document: metadata, organization, roles,
  security, data classes, environment classes, capabilities, workflows,
  channels, deployment.
- `orgagents.spec.loader` — YAML/JSON load, `spec_version` handling, defaults.
- `orgagents.spec.validate` — structural, referential and least-privilege rules.
- `examples/acme.system.yaml` — a complete worked specification.
- A neutrality test asserting the schema names no vendor, SDK or provider.

## Scope
In: the spec document and its validation. Out: bindings (target-specific, owned
by the target workstreams) and the IR (WS-005).

## Approach
Start from the domain the runtime already proves out, then strip every
implementation detail into bindings. Keep the abstraction line at *capability
needs*: what access, what isolation, what accountability — never which image,
which SDK, which resource type. Version the document with its own semver and
write the migration path before the first breaking change, not after.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Model and loader | Phase 1 | In progress |
| M2 Validator with least-privilege rules | Phase 1 | In progress |
| M3 Worked example specification | Phase 1 | In progress |
| M4 `spec_version` migration tooling | Phase 2 | Not started |
| M5 JSON Schema export for third-party editors | Phase 2 | Not started |

## Dependencies
WS-001 for the record process. Blocks WS-003, WS-004, WS-005 and every target.

## Advantages
- One reviewable artifact that outlives frameworks and providers.
- Non-programmers can author systems; the UI and SDK become two editors.
- Validation catches design errors before any infrastructure exists.

## Disadvantages
- The spec is on the critical path for everything else, so its mistakes are
  expensive and its schedule risk is systemic.
- Drawing the neutrality line is a judgement call that will be wrong in places,
  and each correction is a `spec_version` bump.
- Users will want expressiveness the spec lacks; every such gap is a product
  change rather than a quick fix.

## Exit criteria
- The example spec validates and compiles to at least two targets.
- The neutrality test passes.
- `spec_version` and its migration policy are documented.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Model, loader, validator and worked example in progress. |
