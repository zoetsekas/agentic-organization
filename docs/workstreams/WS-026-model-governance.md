---
id: WS-026
title: Model governance — approved models per agent
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Security Engineering
contributors: [Platform Architecture, Compliance]
scope: [spec, compiler, security]
decisions: [ADR-0040]
depends_on: [WS-027, WS-005]
tags: [security, models]
---

# WS-026: Model governance — approved models per agent

## Objective
Make which model an agent runs on a reviewable policy decision rather than a
line in a config file — enforcing residency, training posture, context and cost
where they are actually decided.

## Deliverables
- `ModelPolicy` with capability classes, explicit allow/deny, and constraints.
- Catalog-backed resolution: `check_model`, `permitted_models`, `resolve_model`,
  deny by default, every refusal carrying a reason and alternatives.
- `apply_model_approvals` on the IR and a hard failure in `compile_system`.
- The same check in the phase gate, and a models table in the registry.

## Scope
In: which model serves an agent and its sub-agents. Out: prompt design, adapter
choice (WS-008), and model evaluation quality.

## Approach
Keep the spec neutral by asking for a class and constraints, let the catalog
hold the concrete facts, and put the check at compile time where a refusal
stops something rather than being noted.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Policy model and validation | Phase 4 | Done |
| M2 Catalog resolution with reasons and alternatives | Phase 4 | Done |
| M3 Compile-time refusal and phase gate | Phase 4 | Done |
| M4 Sub-agent model class enforcement | Phase 4 | Not started |
| M5 Automatic fallback to a permitted model | Phase 4 | Not started |
| M6 Catalog figures refreshed from provider data | Phase 4 | Not started |

## Dependencies
WS-027 for the catalog the policy resolves against.

## Advantages
- Residency, training posture and cost are enforced, not documented.
- The spec still names no vendor.
- Refusals list what would work, so the constraint teaches.
- Retiring a model in the catalog invalidates it everywhere at once.

## Disadvantages
- **Catalog figures go stale** — price, context and regions change without
  telling us, and a cost ceiling then checks a fiction (M6).
- Class tags are editorial; two organizations will disagree about what is
  `balanced`.
- `subagent_classes` is declared but not yet enforced (M4), so a sub-agent can
  inherit a more expensive model than the policy intends.
- `allow_fallback` is declared and not implemented (M5): today an unpermitted
  binding fails the build rather than falling back.
- A strict policy against a thin catalog permits nothing, and the failure is a
  build nobody can complete until an approval happens.

## Exit criteria
- Sub-agent models are checked against their own classes (M4).
- A permitted fallback is chosen automatically where the policy allows (M5).
- Catalog figures are refreshed from a source rather than typed (M6).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Policy, resolution and compile-time refusal landed. |
