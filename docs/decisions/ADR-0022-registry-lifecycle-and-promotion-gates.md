---
id: ADR-0022
title: A generated registry, lifecycle stages, promotion gates, budgets and compliance
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Security Engineering]
consulted: [Compliance, Platform SRE, Product]
informed: [All engineering]
scope: [spec, compiler, targets, security]
workstreams: [WS-014]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0015, ADR-0019, ADR-0020, ADR-0021]
tags: [governance, enterprise]
---

# ADR-0022: A generated registry, lifecycle stages, promotion gates, budgets and compliance

## Context
The enterprise question is not "can you build an agent" but "how do you run a
hundred of them without losing track". The best-developed answer in the market
is a control plane: a registry as the single source of truth for every agent —
owner, platform, permissions, policy alignment — with per-agent identity whose
*lifecycle* is managed specifically so agents do not accumulate stale
permissions, and fleet-wide observability.

We had the pieces and not the answer. Permissions were resolved per agent, and
identities were generated, but nothing produced the document an auditor asks
for: *which agents exist, who owns them, what can they reach, what wakes them,
what do they cost, and when were their permissions last reviewed.* Nothing
stopped an agent reaching production without evaluation, and nothing bounded
what a fleet could spend overnight.

## Decision
Four related additions, enforced at the same points and therefore decided
together:

**Registry.** Every target generates `REGISTRY.md`: the fleet inventory —
agent, team, human owner, workload identity, permission count, environment,
triggers, channels, budget — plus what each agent may reach, what wakes it, its
human surfaces, its declared flows, its grounding sources, the promotion gates,
and a **review-flags** section naming agents with no owner, no budget, or that
never run unattended. It is a generated artifact the customer owns, not a
hosted console.

**Lifecycle and promotion gates.** A system has a `stage`
(draft → development → staging → production → retired), an owner, a review
cadence, a `retire_after_idle_days`, and `gates` stating what must hold before
each stage: evaluations passed, human approval, security review, cost within
budget, owner assigned, permissions reviewed. **Evaluations** are spec objects —
given/expect/must-not cases scoped to agents — so "it works" is a checkable
claim rather than an opinion.

**Budgets.** Every agent resolves to exactly one budget (tightest of agent,
team, system) with a **mandatory** action on breach: warn, throttle or halt.
There is no unbounded agent.

**Compliance.** Frameworks, data residency, audit retention, trace redaction,
actions always requiring approval, and a permission-review interval. Residency
is checked against the bound region in the implementation phase; a data class
excluded from traces must appear in the observability redaction list or the
spec fails.

## Scope
Fleet governance across the spec, the IR, every target and the phase gate. It
does not implement an evaluation *runner* — the cases are declared and gated on
here; executing them is WS-014's next milestone.

## Implementation
`spec.model` gains `Lifecycle`, `PromotionGate`, `EvaluationCase`, `Budget` and
`Compliance`. `compiler/registry.py` renders the registry for every target.
`compiler.ir` resolves the tightest budget per agent and carries lifecycle,
compliance and stage onto each `AgentIR`. `phases.py` gates on owner, budget,
production gate and residency; `spec/validate.py` rejects unknown budget
scopes, budgets without limits, production without a gate, and trace leaks.

## Timeline
Phase 2 for the declarations, the registry and the gates. The evaluation runner
and automated permission-review reporting follow in phase 3.

## Advantages
- The auditor's document is generated from the same IR that generates the IAM,
  so it cannot drift from what was deployed.
- Review flags surface exactly the states that rot a fleet: unowned agents,
  unbounded spend, stale permissions.
- Promotion is a gate with named requirements, not a conversation.
- Every agent has a spend ceiling and a defined behaviour at the ceiling.
- Residency and trace redaction are checked rather than asserted.

## Disadvantages
- Declared evaluations are not run here, so `evaluations_passed` is currently a
  promise the gate records rather than verifies — the most misleading gap in
  this decision, and the reason WS-014 stays open.
- A generated registry is a snapshot; a fleet changes between compiles, and
  nothing reconciles the document against what is actually running.
- Budget enforcement depends on cost accounting whose provider pricing goes
  stale, so `halt` may fire late.
- More mandatory blocks mean a heavier minimum spec; small teams will find the
  floor high, and the phase gate will feel bureaucratic before it feels useful.
- Lifecycle stages imply a promotion process we do not orchestrate.

## Alternatives considered
- **Hosted control plane** — better ergonomics, and it puts us in the middle of
  a customer's fleet, which ADR-0003 rules out.
- **Registry as a runtime API only** — invisible in review and impossible to
  diff; generating a document makes it reviewable in the same PR as the change.
- **Optional budgets** — every unbounded agent becomes someone's incident.
- **Compliance as documentation** — unenforced, and therefore untrue within a
  quarter.

## Verification
Compiler tests assert the registry names every agent, its owner, its triggers
and its promotion gates, and that the review-flags section reports no unowned
agents for the example. Validator tests cover unknown budget scopes, production
without a gate and trace leaks. Phase tests cover owner, budget and residency
gating.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Registry, lifecycle gates, evaluations, budgets, compliance. |
