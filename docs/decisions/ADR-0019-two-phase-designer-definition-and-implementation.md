---
id: ADR-0019
title: The designer has two explicit phases with a checkable gate between them
status: Accepted
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Product]
consulted: [Security Engineering, Developer Experience, Platform SRE]
informed: [All engineering]
scope: [spec, compiler, ui, sdk, process]
workstreams: [WS-011, WS-009, WS-005]
supersedes: []
superseded_by: []
related: [ADR-0003, ADR-0004, ADR-0005, ADR-0022, ADR-0026, ADR-0028]
tags: [foundational, product]
---

# ADR-0019: The designer has two explicit phases with a checkable gate between them

## Context
ADR-0004 separates the spec from the binding, and that separation is real in
the code. It is not, however, visible as a *process*. In practice a designer
does two different jobs: describing what the organization of agents should be,
and choosing how it gets built. Those jobs have different reviewers — a domain
owner and a security architect sign off the first, a platform team the second —
and different failure modes.

Without an explicit gate, the two collapse into one. People reach for the
binding to work around an incomplete definition (an image chosen to compensate
for an undeclared environment class), and a design gets deployed while nobody
has confirmed that every agent has an owner, a budget and a route to a human.
Comparable products do not help here: they configure a runtime, so there is no
"definition" artifact to sign off at all.

## Decision
The designer has two named phases with a gate between them:

**Definition phase** — the abstract description: teams, leaders, roles and
responsibilities, data classes, capabilities, environment classes, policies,
triggers, human channels, knowledge sources, lifecycle, budgets and compliance.
Nothing here names a vendor. It is reviewed and signed off on its own merits,
and it is deployable to nothing.

**Implementation phase** — the binding: which framework runs the loop, which
image backs an environment class, which MCP server serves a capability, which
workspace a channel lives in, which scheduler fires a trigger, which cloud
account it all lands in.

The gate is **mechanical**, not a meeting. `orgagents phase` runs both reviews
and returns a per-check verdict with the fix. A definition with failures cannot
be meaningfully bound; a binding that leaves an abstract thing unrealized
cannot be compiled. `compile` refuses on definition errors regardless.

## Scope
The designer's workflow, the UI, the SDK and the CLI. It does not change the
spec/binding split itself (ADR-0004) — it makes that split a visible,
enforceable process with named checks.

## Implementation
`orgagents/phases.py` holds the checks as data: each has an id, a phase, a
title, a detail and a **fix**. Definition checks cover ownership, roles, human
pairing (exactly one accountable owner, an approver for every gated action, a
fallback contact), classification, placement, trigger delivery, approval
routing, sub-agent scoping, external-endpoint gating, memory namespaces and
retention, lifecycle gates, budgets and residency. Implementation checks cover
binding completeness per target: capabilities, environments, channels,
scheduler, knowledge, secrets backend, region, state backend and residency
agreement. Implementation checks additionally cover the memory store binding.
`orgagents phase <spec> [--binding B --target T]` prints both; exit status is
non-zero on any failure.

Some checks are warnings in development and errors in production, mirroring
the least-privilege rules in ADR-0008.

## Timeline
Phase 1, alongside the spec work it gates. The checks grow with each new spec
block — a block with no phase check is an incomplete feature.

## Advantages
- The two reviews that actually happen get two artifacts and two verdicts.
- "Is this ready?" is answerable by a command rather than by argument.
- Each failure carries its fix, so the gate teaches the spec language.
- Incomplete definitions can no longer be papered over in the binding.
- A definition can be signed off long before any cloud account exists.

## Disadvantages
- A second gate to pass is friction, and people under deadline will ask to skip
  it; the checks must stay few enough to keep their credibility.
- Checks encode our opinion of what a complete design is, and that opinion will
  be wrong for some organizations — every false positive costs trust.
- The phase list and the validator overlap; keeping them from drifting is
  ongoing work, and a rule in the wrong place is invisible.
- A green gate implies a quality it cannot verify: nothing here checks that the
  design is *good*, only that it is complete.

## Alternatives considered
- **Leave the split implicit** — it already existed in the code and was still
  routinely worked around, which is what prompted this.
- **One combined readiness score** — hides which of the two reviews failed, and
  they have different owners.
- **Gate only at compile time** — too late: the definition review must be
  possible before anyone has a target in mind.

## Verification
`tests/test_phases.py` asserts the example passes both phases for all four
targets, that removing an owner, a budget, an approval channel, a capability
binding or a scheduler binding fails the right phase with the right check id,
and that every failure carries a fix.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Extended the check set for human pairing (ADR-0026), sub-agents (ADR-0027), memory (ADR-0028) and external endpoints (ADR-0030); `agents_have_humans` now means "paired with at least one human", with ownership checked separately. |
| 1.0.0 | 2026-09-20 | Accepted. Two phases, mechanical gate, per-check fixes. |
