---
id: ADR-0008
title: Deny-by-default RBAC with attribute-scoped policies and least privilege
status: Accepted
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering, Platform Architecture]
consulted: [Platform SRE, Compliance]
informed: [All engineering]
scope: [spec, security, compiler, targets, runtime]
workstreams: [WS-004]
supersedes: []
superseded_by: []
related: [ADR-0006, ADR-0007, ADR-0009, ADR-0015]
tags: [security, foundational]
---

# ADR-0008: Deny-by-default RBAC with attribute-scoped policies and least privilege

## Context
Agents act autonomously, compose tools, and delegate to other agents. The blast
radius of an over-permissioned agent is larger than that of an
over-permissioned human, because it acts faster and at machine scale, and its
behaviour is not fully predictable from its configuration.

Plain RBAC is too coarse — "may query the warehouse" is not a useful grant when
the question is *which rows*. Plain ABAC is too diffuse to review. And critically,
whatever model we pick must survive compilation: the permissions the IR resolves
must be the permissions the generated cloud IAM enforces, or the design review
was theatre.

## Decision
A layered model, **deny by default** at every layer:

* **Permissions** are `(action, resource, conditions)` triples over abstract
  resources — `data_class`, `capability`, `agent`, `team`, `workflow`,
  `environment`. Nothing is implicitly granted.
* **Roles** aggregate permissions (ADR-0007) and are the only way an agent
  obtains one.
* **Policies** are explicit `allow`/`deny` rules with attribute conditions
  (data classification, environment, time window, delegation depth, human
  approval). **Deny always wins**, and a deny cannot be overridden downstream.
  A rule's `conditions` must hold for it to apply; a separate `unless` guard
  disapplies it. Exception-shaped denies — "never touch this data outside the
  clean room" — are written with `unless` rather than by negating conditions,
  because a reviewer who misreads the polarity of a deny misreads it in the
  unsafe direction.
* **Inheritance narrows only.** A team grant flows to members; a member's
  assignment may constrain it further but never widen it. There is no
  privilege escalation path through the hierarchy — including for leaders.

The compiler computes each agent's **effective permission set** once, in the IR,
and every target derives its enforcement from that set: the runtime's tool
gate, the sandbox's mounts and egress, and the generated cloud IAM bindings.

Least privilege is mechanical, not advisory: `spec validate` fails on wildcard
resources in production deployments, on a capability granted but unused by any
responsibility, and on an agent whose effective set exceeds its team's.

## Scope
Everything from the spec to the generated IAM. Excludes authentication of human
users to the platform, and network policy beyond the sandbox egress boundary.

## Implementation
`orgagents.security.rbac` — `Permission`, `PolicyRule`, `PolicyEngine`
(`decide()` returning allow/deny with the deciding rule for audit).
`compiler.ir` resolves effective sets; `targets.terraform` maps them onto
provider IAM; the runtime's `HarnessBuilder` gates tool calls on the same set.
Every decision is logged with the rule that produced it.

## Timeline
Phase 1 with the spec, because targets cannot be written against an unresolved
permission model. Provider IAM mapping lands with the cloud targets in phase 3.

## Advantages
- One resolved permission set drives runtime, sandbox and cloud IAM alike.
- Deny-wins and narrow-only inheritance remove the usual escalation paths.
- Violations are build failures, so least privilege does not depend on review
  diligence.
- Every enforcement decision names the rule, so audits are answerable.

## Disadvantages
- Strictness costs velocity: deny-by-default means every new capability needs an
  explicit grant, and users will experience that as friction.
- Mapping abstract permissions onto three providers' IAM models is lossy;
  some grants will be coarser in the generated cloud policy than in the IR,
  and that gap must be documented per target rather than hidden.
- Condition evaluation at runtime costs latency on every tool call.
- A rich policy language invites complexity we will struggle to keep reviewable.
- Two condition fields (`conditions` and `unless`) is one more concept than a
  single negatable one, and authors will occasionally reach for the wrong one.

## Alternatives considered
- **Allow-by-default with denies** — operationally easier, indefensible for
  autonomous agents.
- **Pure RBAC** — too coarse for row- and classification-level decisions.
- **Pure ABAC/OPA** — expressive, but effective permissions become
  uncomputable ahead of time, so nothing can be reviewed or compiled to IAM.
- **Delegating to each cloud's IAM** — no local enforcement, no neutrality, and
  the design would differ per provider.

## Verification
Tests cover deny-wins, narrow-only inheritance, leader non-escalation, and the
wildcard-in-production validator. A property test asserts that no resolution
path produces a permission absent from the union of assigned roles.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Added the explicit `unless` guard after implementation showed that conditions on a deny read ambiguously, and ambiguity on a deny fails unsafe. |
| 1.0.0 | 2026-09-20 | Accepted. Deny-by-default, narrow-only inheritance, resolved in the IR. |
