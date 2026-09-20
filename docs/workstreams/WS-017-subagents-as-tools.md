---
id: WS-017
title: Sub-agents as tools
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Developer Experience]
scope: [spec, compiler, runtime]
decisions: [ADR-0027]
depends_on: [WS-005]
tags: [runtime]
---

# WS-017: Sub-agents as tools

## Objective
Let an agent hand a bounded task — research this, review that, check these
figures — to a task-scoped worker it calls like a tool, without adding an
ephemeral member to the organization or inventing an accountable human for it.

## Deliverables
- `SubAgentSpec` declared inline on the agent, with recognized kinds.
- `runtime/subagents.py`: narrow-only resolution and a prompt that states what
  a sub-agent is not.
- `SubAgentRunner` exposing each as `subagent_<id>`, with calls logged into the
  parent's session.
- Validation rejecting any widening of capabilities, tools, knowledge or
  environment.
- Registry table of every sub-agent with its purpose, access and budget.

## Scope
In: tool-shaped, synchronous, bounded workers. Out: delegation between real
agents (the org tree), and long-running background work.

## Approach
Make the safe default the lazy one: naming no capabilities means reaching
nothing, not inheriting the parent's. Ship instructions per kind so a useful
sub-agent is a few lines. Keep every call inside the parent's trace, because
that is where the work happened.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Spec model and narrow-only resolution | Phase 2 | Done |
| M2 Runtime tools and session logging | Phase 2 | Done |
| M3 Precise capability-to-tool mapping | Phase 3 | Not started |
| M4 Reusable sub-agent library | Phase 3 | Not started |
| M5 Parallel sub-agent execution | Phase 3 | Not started |

## Dependencies
WS-005 for the IR the parent's toolset is resolved from.

## Advantages
- The org chart stays a picture of the organization.
- Provably a subset of the parent, so no separate permission model is needed.
- Declared kinds make the common cases short and consistent.

## Disadvantages
- Inline declaration means no reuse; two agents wanting the same reviewer
  declare it twice and they will drift (M4).
- Toolset filtering is by name matching, which is coarse and will occasionally
  expose or hide the wrong tool (M3).
- Sub-agent work is invisible in the org chart by design, so effort is harder
  to see than with real delegation.
- `parallel_safe` is declared but nothing runs sub-agents in parallel yet (M5).

## Exit criteria
- A sub-agent cannot reach anything its parent cannot. ✔
- Every call appears in the parent's session trace. ✔
- Tool visibility is derived from capabilities rather than name matching.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Model, resolution and runtime tools landed. |
