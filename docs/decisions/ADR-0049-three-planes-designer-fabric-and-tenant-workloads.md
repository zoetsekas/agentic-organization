---
id: ADR-0049
title: The platform is three planes — designer, fabric and tenant workloads
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Product, Security Engineering]
informed: [All engineering]
scope: [spec, compiler, targets, runtime, security, ui, docs]
workstreams: [WS-028, WS-029, WS-030]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0003, ADR-0011, ADR-0020, ADR-0021, ADR-0047, ADR-0048]
tags: [architecture, tenancy]
---

# ADR-0049: The platform is three planes — designer, fabric and tenant workloads

## Context
Everything built so far lives in one application. The designer authors a System
Spec, the compiler resolves it, targets generate artifacts, and a runtime can
load the result — all in one process, one database, one UI, one RBAC model.
That was right while there was one design and one operator.

It stops being right the moment a company runs more than one agentic
organization. Finance's agents and Platform's agents are different tenants with
different owners, different data classifications and different blast radii, and
today nothing separates them: one designer database, one catalog, one identity
model, one set of generated artifacts. "Who may design an organization" and
"who may operate the fabric it runs on" are the same permission, which is
wrong — they are different jobs held by different people.

Conflating them also hides a real distinction in the product. Designing an
organization is *authoring*. Running many of them is *operations*. Merging the
two produces a tool that is worse at both.

## Decision
The platform is **three planes**, each with its own responsibility, its own
access control and its own failure domain.

1. **The designer plane** authors agentic organizations. It owns System Specs,
   the canvas, the catalog of building blocks, and the people who design with
   them. It is the workshop. It does not deploy, operate or hold tenant runtime
   state.
2. **The fabric plane** is the control plane. It owns **tenants**, their
   deployments, the common services every tenant draws on, the isolation
   between them, and the operational surface over the result — health, quotas,
   drift, incidents, audit. The **command centre** is the operator-facing
   application over this plane.
3. **The tenant plane** is where a designed organization actually runs. Each
   tenant is a separate workload with its own generated infrastructure, its own
   identities and its own data. Tenants never share a plane with each other.

Three rules make the separation real rather than decorative:

- **A design is not a deployment.** The designer produces a spec; the fabric
  decides whether, where and for whom it runs. Publishing from the designer is
  a request to the fabric, not an act of deployment.
- **Operator and designer are different principals.** A designer permission
  never confers a fabric permission, and a fabric operator has no implicit
  authority to edit somebody's design. Deny-by-default applies across the
  boundary in both directions (ADR-0008, ADR-0021).
- **Isolation is a property of the fabric, not of a design.** A tenant does not
  get to declare its own isolation; the fabric assigns it and the generated
  artifacts enforce it (ADR-0050).

The platform both **generates and operates**: the fabric compiles each tenant's
design into its own isolated infrastructure and then keeps an operational
control plane over what it generated. Neither half is optional — generating
without operating leaves a deployment nobody watches; operating without
generating leaves a control plane with nothing underneath it.

## Scope
The separation of planes, their access boundaries, and which concerns belong to
each. It does not decide the isolation mechanism per target (ADR-0050), the
command centre's own surface (ADR-0051), or anything about how agents inside a
tenant are organized — that is the org model, unchanged.

## Implementation
The designer keeps what it has. New: a `fabric` package owning tenants,
deployments and operations; a `command` UI surface distinct from `/ui/`; and a
platform-operator RBAC model distinct from workspace RBAC. The compiler gains a
tenant-scoped compile path so every generated artifact carries its tenant's
isolation (ADR-0050). The existing spec is unchanged — tenancy is assigned by
the fabric, not declared by a design, which keeps specs portable between
installations.

## Timeline
Phase 5. WS-028 (tenancy and isolation), WS-029 (command centre), WS-030
(fabric common services and operations).

## Advantages
- Designing and operating become different jobs with different permissions,
  which is what they already are in any organization large enough to need this.
- A tenant's blast radius is bounded by construction rather than by care.
- The designer stays portable: a spec carries no installation-specific
  tenancy, so it can move between installations and be reviewed on its merits.
- Operations get one place to look at many organizations, instead of one
  database per design.

## Disadvantages
- **Three planes is more system than one.** Every cross-plane interaction is
  now an explicit contract, and some of them will be tedious — publishing a
  design becomes a request with a lifecycle rather than a save.
- **Two RBAC models to keep coherent.** Designer roles and operator roles can
  drift apart or, worse, quietly overlap; nothing but tests will catch that.
- **The operational half cannot be fully exercised here.** No cloud account and
  no Docker daemon exist in this environment, so "operate what it generated"
  will be modelled and tested against stubs, not proven against running
  tenants. That gap is the most likely place for this design to be wrong.
- **A tenant boundary is only as strong as the target enforces it.** The fabric
  can assign isolation; on a target whose IAM is coarse, the guarantee is
  weaker than the model implies, and the mapping reports must say so.
- Migration: existing single-tenant installations have to acquire a tenant, and
  "the default tenant" is exactly the kind of special case that rots.

## Alternatives considered
- **One plane, separated by convention** — what we have; the separation lives
  in whoever remembers it, and the two permissions stay fused.
- **Designer per tenant** — isolates cleanly, but every tenant needs its own
  installation and the catalog, records and people fragment with them.
- **Tenancy declared in the spec** — makes specs installation-specific and lets
  a design ask for its own isolation, which is precisely the thing a tenant
  must not decide.
- **Fabric as a thin deployer with no operations** — leaves deployments nobody
  watches, and the user asked for the operational aspects specifically.

## Verification
Tests assert that no designer permission grants a fabric action and no fabric
role can mutate a spec; that the spec schema contains no tenant identifier;
that a compile is tenant-scoped and two tenants' artifacts never share a name,
volume, network or identity; and that the command centre's routes are
unreachable with designer-only credentials.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Designer, fabric and tenant planes, with generate-and-operate as the fabric's posture. |
