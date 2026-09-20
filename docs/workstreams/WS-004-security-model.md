---
id: WS-004
title: Security model — RBAC, policies, least privilege, sandboxing and identity
status: Active
version: 1.2.0
date: 2026-09-20
updated: 2026-09-20
owner: Security Engineering
contributors: [Platform Architecture, Platform SRE]
scope: [spec, security, compiler, targets, runtime]
decisions: [ADR-0008, ADR-0009, ADR-0010, ADR-0015, ADR-0017, ADR-0007, ADR-0054]
depends_on: [WS-002, WS-003]
tags: [security, foundational]
---

# WS-004: Security model — RBAC, policies, least privilege, sandboxing and identity

## Objective
Make least privilege mechanical rather than aspirational: permissions declared
in business terms, resolved once, enforced identically by the runtime, the
sandbox and the generated cloud IAM, with violations failing the build.

## Deliverables
- `orgagents.security.rbac` — permissions, policy rules, deny-wins engine with
  auditable decisions.
- Effective-permission resolution in the IR, including team inheritance and
  narrow-only constraints.
- Environment-class model with narrow-only overrides (ADR-0009) and per-target
  bindings preserving network posture.
- Capability model bound to MCP servers, with server-side constraint enforcement.
- Per-agent workload identity derivation and secret-reference discipline.
- Least-privilege validators: wildcard resources, unused capabilities, agents
  exceeding their team, credential-shaped strings in output.
- `orgagents.sandboxes` — the `SandboxProvider` seam (ADR-0054) with the four
  named providers (`container`, `microvm_sbx`, `openshell`, `target_native`),
  loud degradation to `container`, per-provider boundary statements and
  environment-class mappings for the generated README and mapping reports.

## Scope
In: everything from declared permission to enforced permission, in every target.
Out: platform user authentication, corporate network security, and the
customer's landing-zone guardrails.

## Approach
Resolve permissions exactly once, in the IR, and make every enforcement point a
consumer of that result — runtime tool gate, sandbox mounts and egress, cloud
IAM bindings. Deny always wins and inheritance only narrows, so there is no
escalation path through the hierarchy, leaders included. Where a provider's IAM
cannot express a grant faithfully, emit the coarser binding *and* record the gap
in the target's mapping report rather than hiding it.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 RBAC/policy engine and IR resolution | Phase 1 | Done |
| M2 Least-privilege validators | Phase 1 | Done |
| M3 Environment-class bindings, local target | Phase 2 | Done |
| M6 Sandbox provider seam (ADR-0054) | Phase 5 | Module and tests done; not wired into the local target or runtime, and unverified against any real provider |
| M4 Per-provider IAM mapping and gap reports | Phase 3 | Not started |
| M5 External security review of the model | Phase 3 | Not started |

## Dependencies
WS-002 (spec) and WS-003 (hierarchy that permissions inherit along). Blocks the
cloud targets in WS-007, which cannot emit IAM without resolved permission sets.

## Advantages
- One resolved permission set means runtime and cloud enforcement cannot diverge.
- Violations are build failures, so compliance does not rest on review diligence.
- Per-agent identity makes containment and attribution possible.

## Disadvantages
- Deny-by-default is friction users feel on every new capability, and the
  pressure to add blanket grants will be constant.
- Provider IAM mapping is lossy; the gap between intended and enforced
  permission is real, documented, and will still surprise auditors.
- Condition evaluation costs latency on every tool call.
- This workstream gates two others, so its slippage is the programme's slippage.

## Exit criteria
- No path produces a permission absent from the union of assigned roles (tested).
- Every target enforces the IR's permission set, with documented gaps.
- No generated artifact contains a credential (tested).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.2.0 | 2026-09-20 | Built the `SandboxProvider` seam (ADR-0054) as a self-contained `orgagents.sandboxes` module: four named providers, `container` as the default floor, availability detection that degrades loudly and records the reason, per-provider boundary statements that state what is *not* enforced, tenant scoping per ADR-0050, and an OpenShell policy-domain mapping whose wire format is left as a marked adapter TODO because the schema is undocumented. No Docker daemon, `sbx` or `openshell` binary exists here, so nothing is verified against a running provider; the seam is not yet wired into the local target or the runtime. |
| 1.1.0 | 2026-09-20 | Milestone statuses reconciled with what has shipped. |
| 1.0.0 | 2026-09-20 | Opened. Engine, resolution and validators in progress. |
