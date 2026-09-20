---
id: WS-014
title: Enterprise governance — registry, lifecycle gates, evaluations, budgets, compliance
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Security Engineering
contributors: [Platform Architecture, Compliance, Product]
scope: [spec, compiler, targets, security]
decisions: [ADR-0022]
depends_on: [WS-004, WS-005, WS-011]
tags: [governance, enterprise]
---

# WS-014: Enterprise governance — registry, lifecycle gates, evaluations, budgets, compliance

## Objective
Make a fleet of agents governable: a generated inventory an auditor can read,
promotion gates that say what must hold before production, a spend ceiling on
every agent, and compliance obligations that are checked rather than asserted.

## Deliverables
- `compiler/registry.py` → `REGISTRY.md` on every target: agents, owners,
  identities, permissions, environments, triggers, channels, budgets, declared
  flows, grounding sources, promotion gates and review flags.
- `Lifecycle`, `PromotionGate`, `EvaluationCase` in the spec.
- `Budget` resolution (tightest of agent, team, system) with a mandatory
  breach action, carried onto every `AgentIR`.
- `Compliance`: frameworks, residency, audit retention, trace redaction,
  always-approve actions, permission-review interval.
- Validators: unknown budget scopes, budgets without limits, production without
  a gate, data classes excluded from traces but not redacted.
- Phase-gate checks for owner, budget, production gate and residency.

## Scope
In: declaring and gating on governance facts, and generating the artifacts that
make them reviewable. Out: running evaluations (M3), reconciling the registry
against what is actually deployed (M4), and cost metering accuracy.

## Approach
Generate governance artifacts from the same IR that generates the IAM, so the
document and the deployment cannot disagree. Make the states that rot a fleet —
unowned agents, unbounded spend, stale permissions — visible as review flags
rather than requiring someone to notice their absence.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Registry generation on every target | Phase 2 | Done |
| M2 Lifecycle, gates, budgets, compliance in the spec | Phase 2 | Done |
| M3 Evaluation runner executing declared cases | Phase 3 | Not started |
| M4 Registry reconciliation against deployed state | Phase 3 | Not started |
| M5 Permission-review reporting on the declared interval | Phase 3 | Not started |

## Dependencies
WS-004 for resolved permissions and identities; WS-005 for the IR.

## Advantages
- The auditor's document is generated, versioned and diffable with the change.
- Review flags surface the fleet's rot without anyone having to look for it.
- Promotion is a gate with named requirements instead of a conversation.
- No agent has unbounded spend.

## Disadvantages
- **`evaluations_passed` is currently a promise, not a verdict** — cases are
  declared and gated on, but nothing runs them until M3. That is the most
  misleading gap in this workstream and the reason it stays open.
- A generated registry is a snapshot; between compiles it can be wrong, and
  nothing reconciles it with what is running (M4).
- Budget enforcement inherits provider pricing staleness, so `halt` may fire
  late.
- A heavier mandatory floor makes small systems feel over-governed.

## Exit criteria
- Declared evaluations execute and gate promotion on their real pass rate.
- The registry reconciles against deployed state, or states that it cannot.
- Every agent resolves to exactly one budget with a breach action. ✔

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Registry, lifecycle, budgets and compliance landed; runner pending. |
