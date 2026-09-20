---
id: WS-007
title: Cloud deployment targets — Terraform for GCP, AWS and Azure
status: Proposed
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform SRE
contributors: [Platform Architecture, Security Engineering]
scope: [targets, security]
decisions: [ADR-0012, ADR-0015, ADR-0009, ADR-0016]
depends_on: [WS-005, WS-004]
tags: [targets, cloud, iac]
---

# WS-007: Cloud deployment targets — Terraform for GCP, AWS and Azure

## Objective
Let an enterprise deploy a designed agentic system into its own cloud account
through its existing change process: reviewed Terraform, planned and applied by
the customer, with permissions traceable to the design.

## Deliverables
- Provider-neutral resource set in the IR (compute service, job runner, state
  store, object store, secret, message bus, identity, policy binding, network
  boundary, observability sink).
- `terraform:gcp`, `terraform:aws`, `terraform:azure` targets emitting root and
  per-agent modules, `variables.tf`, `iam.tf`, backend stub and README.
- Per-agent workload identities and policy bindings derived from IR permissions.
- A **mapping report** per target naming every permission as mapped or coarsened.
- CI running `terraform validate` and `fmt -check` on generated output.

## Scope
In: infrastructure generation for three providers. Out: creating the customer's
landing zone, VPC backbone or org-level guardrails — those are module inputs.
Out: applying anything; the platform never holds cloud credentials.

## Approach
Map the neutral resource set onto each provider's serverless-first primitives,
GCP first because its primitive set is narrowest. Keep IAM derivation mechanical
from the IR, and treat fidelity loss as a first-class output: if a grant cannot
be expressed faithfully, emit the safe coarser binding and record it in the
report rather than silently widening or narrowing.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Neutral resource set in the IR | Phase 3 | Not started |
| M2 GCP target | Phase 3 | Not started |
| M3 AWS target | Phase 3 | Not started |
| M4 Azure target | Phase 3 | Not started |
| M5 Mapping reports and IAM gap review | Phase 3 | Not started |

## Dependencies
WS-005 (IR, plugin API) and WS-004 (resolved permissions; IAM cannot be emitted
without them). Blocked until both land.

## Advantages
- Fits enterprise change control and leaves customers in control of their cloud.
- One design deploys to three providers without being rewritten.
- Mapping reports make security fidelity auditable rather than assumed.

## Disadvantages
- The largest ongoing maintenance burden in the product: three provider
  mappings against continuously moving APIs and provider versions.
- Lossy IAM mapping is a security-relevant gap that documentation mitigates but
  does not close.
- Machine-generated HCL must be idiomatic enough for a platform team to own, and
  reviewing it is its own burden.
- Terraform and provider version skew becomes our support surface.

## Exit criteria
- The example spec compiles to all three providers; each output passes
  `terraform validate`.
- Every IR permission appears in the mapping report as mapped or coarsened.
- One end-to-end deployment per provider verified in a scratch account.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened as Proposed; blocked on WS-004 and WS-005. |
