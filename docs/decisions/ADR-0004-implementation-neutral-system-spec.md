---
id: ADR-0004
title: An implementation-neutral System Spec is the single source of truth
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Developer Experience, Platform SRE]
informed: [All engineering]
scope: [spec, compiler, ui, sdk]
workstreams: [WS-002, WS-005]
supersedes: [ADR-0002]
superseded_by: []
related: [ADR-0003, ADR-0005, ADR-0006, ADR-0013]
tags: [foundational, spec]
---

# ADR-0004: An implementation-neutral System Spec is the single source of truth

## Context
ADR-0003 makes the platform a compiler. A compiler needs a source language. If
that language mentions LangGraph node types, Terraform resources, or a specific
model provider, then every design written in it is already a deployment
decision, and the promise of "define it abstractly, then choose an
implementation" is void.

The hard part is drawing the line. Too abstract and nothing can be generated
without a pile of per-target guesswork; too concrete and the spec is a
templating language for one vendor.

## Decision
A single declarative document — the **System Spec** — is the authoritative
definition of an agentic system. It is YAML or JSON, carries its own
`spec_version` (semver), and is written entirely in **capability terms**: what
an agent is responsible for, what data classes it may touch, what kind of
execution environment it needs, which other agents it may reach.

The spec names **no** vendor, SDK, cloud provider or resource type. Anything
implementation-specific lives in a **binding** — a separate, target-scoped
document that says how an abstract capability is realized here. A spec with no
binding is still valid and still reviewable; it simply cannot be deployed until
a binding is chosen.

## Scope
Binds the spec language, the SDK that builds it, the UI that edits it, and every
target that consumes it. Bindings and target modules are explicitly *out* of
this neutrality requirement — that is their job.

## Implementation
`orgagents.spec.model` defines the document: `metadata`, `organization`
(teams, agents, roles — ADR-0006, ADR-0007), `security` (roles, permissions,
policies — ADR-0008), `data_classes`, `environments` (abstract sandbox classes —
ADR-0009), `capabilities` (abstract tool/data access — ADR-0010), `workflows`,
`channels` and `deployment` (target selection only).

`orgagents.spec.loader` loads and validates; `orgagents.spec.validate` enforces
the structural and least-privilege rules. Bindings are separate documents keyed
by target id.

## Timeline
Phase 1, first deliverable. Every later component consumes it, so it is the
critical path.

## Advantages
- One artifact to review, diff, sign and promote across environments.
- The same design deploys locally and to three clouds without being rewritten.
- Non-programmers can author a system; the UI and SDK are two editors of one
  document.
- Decouples design lifetime from framework lifetime — SDKs churn, the spec does not.

## Disadvantages
- Abstraction leaks: some capabilities exist on one provider and not another,
  and the spec must either lose fidelity or grow target-shaped escape hatches.
- Two documents (spec + binding) to keep in sync, and a class of errors that
  only appears when they are combined.
- Spec evolution needs its own migration story; `spec_version` bumps will hurt.
- Expressiveness is capped by the compiler — users will want something the spec
  cannot say, and the answer is a product change, not a code change.

## Alternatives considered
- **Python objects as the source of truth (ADR-0002)** — superseded; excludes
  non-programmers and gives no reviewable artifact.
- **Target-specific specs (one per cloud)** — abandons neutrality and multiplies
  the design surface by the number of providers.
- **A general IaC language with agent modules** — inherits that language's
  concepts and its blast radius; agent semantics would be second-class.

## Verification
`orgagents spec validate` runs in CI over `examples/`. A dedicated test asserts
the spec schema mentions no provider or SDK identifier, so neutrality is a
failing test rather than a convention.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Supersedes ADR-0002. Spec + binding split. |
