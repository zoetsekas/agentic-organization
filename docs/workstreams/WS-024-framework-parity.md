---
id: WS-024
title: Framework parity — guardrails, context, contracts and shared instructions
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Security Engineering, Developer Experience]
scope: [spec, compiler, runtime, security]
decisions: [ADR-0035, ADR-0036, ADR-0037, ADR-0038]
depends_on: [WS-011, WS-005]
tags: [research, runtime]
---

# WS-024: Framework parity — guardrails, context, contracts and shared instructions

## Objective
Close the gaps a direct read of the OpenAI Agents SDK, deepagents and Agency
Swarm exposed, and record what we deliberately left out.

## Deliverables
- Guardrails: four boundaries, named checks, four actions, enforced outside the
  agent loop, with system-wide application an agent cannot narrow.
- Context management: declared artifact stores, tool-output offloading, thread
  compaction with a no-regression rule.
- Output contracts: reusable checkable shapes for agents, sub-agents and tools.
- Shared operating instructions at organization and team level, composed with
  attribution.
- Validation, IR resolution, runtime enforcement and tests for all four.

## Scope
In: the four gaps above. Out, and recorded as backlog rather than decided:
a lightweight planning/todo primitive, OpenAPI-derived tools, and
realtime/voice as a channel modality.

## Approach
Read the frameworks for their primitives rather than their features, and ask
what a *compiler* must express. Anything a target could not generate is a
structural gap; anything that only affects one runtime is not.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Guardrails in spec, IR and runtime | Phase 3 | Done |
| M2 Artifact stores, offloading, compaction | Phase 3 | Done |
| M3 Output contracts and validation | Phase 3 | Done |
| M4 Shared instructions with attribution | Phase 3 | Done |
| M5 Model-backed summarizer and classifier guardrails | Phase 4 | Not started |
| M6 Retry-on-contract-violation in the adapters | Phase 4 | Not started |
| M7 Planning primitive and OpenAPI-derived tools | Phase 4 | Not started |

## Dependencies
WS-011 for the landscape method; WS-005 for the IR everything resolves through.

## Advantages
- Closes the gap between what an agent may reach and what may leave it.
- Long and unattended runs stop growing without bound.
- Callers can rely on a shape instead of parsing prose.
- "How we work here" has one home instead of a copy per role.

## Disadvantages
- **Pattern-based guardrails are crude** and will both miss and over-fire;
  model-backed classifiers are M5, and until then `flag` is doing work that
  `block` cannot be trusted with.
- **The default summarizer does not summarize** — it keeps the first and last
  turns and counts the rest. Compaction quality depends on a summarizer the
  deployment supplies (M5).
- **Contract violations are recorded, not corrected**: retry-with-errors is M6.
- Injection detection by phrase list is trivially evaded and is a trace signal,
  not a defence — saying otherwise would be worse than not having it.
- Four new spec blocks raise the floor for a small system, and the phase gate
  now has more to complain about.

## Exit criteria
- Guardrail recall is measured rather than assumed (M5).
- A contract violation is corrected automatically where the policy says retry (M6).
- The backlog items are either decided or explicitly declined (M7).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Four gaps closed; classifier guardrails, real summarization and retry pending. |
