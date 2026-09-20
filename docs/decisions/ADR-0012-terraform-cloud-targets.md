---
id: ADR-0012
title: Cloud deployment is generated as Terraform over a provider-neutral resource set
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Platform SRE]
consulted: [Security Engineering, Compliance]
informed: [All engineering]
scope: [targets, compiler, security]
workstreams: [WS-007]
supersedes: []
superseded_by: []
related: [ADR-0005, ADR-0008, ADR-0009, ADR-0015]
tags: [targets, cloud, iac]
---

# ADR-0012: Cloud deployment is generated as Terraform over a provider-neutral resource set

## Context
Enterprise deployment means the customer's own GCP, AWS or Azure account, under
their landing zone, their policy-as-code and their change process. Generating
imperative provisioning scripts, or calling cloud APIs directly from the
platform, fails all three: no plan step, no state, no review.

Terraform is what those teams already run. The question is how to generate it
without either writing three unrelated generators or inventing a lowest common
denominator so thin it deploys nothing useful.

## Decision
Cloud targets emit **Terraform (HCL)**, one target per provider
(`terraform:gcp`, `terraform:aws`, `terraform:azure`), from a shared
**provider-neutral resource set** in the IR:

`compute_service`, `job_runner`, `state_store`, `object_store`, `secret`,
`message_bus`, `identity`, `policy_binding`, `network_boundary`, `observability_sink`.

Each target maps that set to its provider's primitives (Cloud Run / ECS-Fargate /
Container Apps, and so on), emits root and per-agent modules, a `variables.tf`,
a backend stub for remote state, and a `README` naming exactly which permissions
were mapped and — critically — **where the mapping is coarser than the IR**.

Generated Terraform is never applied by the platform. The customer runs `plan`
and `apply` in their own pipeline.

## Scope
Cloud infrastructure generation for the three providers. Excludes the customer's
landing zone, networking backbone, and org-level guardrails, which the generated
modules consume as inputs rather than create.

## Implementation
`compiler.targets.terraform` with a provider mapping table per cloud;
`ir.resources` produces the neutral resource set including per-agent workload
identities and policy bindings derived from the effective permission sets
(ADR-0008). Output is `main.tf`, `variables.tf`, `iam.tf`, `agents/*.tf`,
`backend.tf.example` and a mapping report.

## Timeline
Phase 3. GCP first (narrowest primitive set), then AWS, then Azure.

## Advantages
- Fits enterprise change control: plan, review, apply, state, drift detection.
- Customers keep ownership; the platform is not in the provisioning path.
- The neutral resource set keeps one design deployable to three clouds.
- The mapping report makes fidelity loss explicit instead of implicit.

## Disadvantages
- Three provider mappings to write and keep current against moving APIs — the
  largest ongoing maintenance cost in the product.
- The neutral resource set is a real constraint: provider-specific features are
  unreachable without escape hatches that erode neutrality.
- IAM mapping is lossy in both directions; some generated bindings will be
  coarser than the IR intends, which is a security-relevant gap.
- Generated HCL must be idiomatic enough for a platform team to own, and
  reviewing machine-written Terraform is its own burden.
- Terraform version and provider-version skew becomes our support surface.

## Alternatives considered
- **Pulumi / CDK** — nicer authoring, but fewer enterprise platform teams have
  it approved and in their pipelines.
- **Crossplane / Kubernetes-native** — elegant where a cluster already exists;
  assumes Kubernetes, which many targets do not have.
- **Direct cloud API calls** — no plan, no state, no review; fails change control.
- **One "cloud" target with provider flags** — the three IAM models differ too
  much for a single mapping to stay honest.

## Verification
CI compiles the example spec to each provider target and runs `terraform
validate` and `fmt -check` where the binary is available; a test asserts every
IR permission appears in the mapping report as either mapped or explicitly
listed as coarsened.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Terraform per provider from one neutral resource set. |
