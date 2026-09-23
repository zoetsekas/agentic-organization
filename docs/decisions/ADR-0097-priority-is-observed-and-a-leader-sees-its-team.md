---
id: ADR-0097
title: Priority is observed, not invented, and a leader can see its team
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Integrations]
informed: [All engineering]
scope: [runtime, tasks]
workstreams: [WS-003, WS-031]
supersedes: []
superseded_by: []
related: [ADR-0026, ADR-0050, ADR-0057, ADR-0073, ADR-0093, ADR-0094]
tags: [tasks, priority, coordination, delegation]
---

# ADR-0097: Priority is observed, not invented, and a leader can see its team

## Context
An agentic organization is supposed to manage and prioritise work. Two things
are missing, and they are less alike than they look.

**Nothing anywhere carries a priority.** `TaskRecord` has state, approval,
tenant, mission, assigner and a run — and no notion of what matters more. The
only `priority` in the codebase is on Terraform firewall rules.

**A leader cannot see its team.** Since ADR-0093 a leader can ask
`unsettled_handles` what *it* has outstanding. That is one session's view.
A team leader asking "what is my team doing right now" — the question that was
put to this platform directly — has nothing to call. The data is all there:
every run is a session, every session has an agent, and the org chart says who
reports to whom. Nothing joins them.

The first gap comes with a trap. A task lives in Jira or Linear, where it
already has a priority that humans set and look at. Inventing a second one
here would create two answers to "what matters most", diverging the moment
anybody edits either, and the one people trust is not ours.

An earlier framing of this work said the task port's inbound-only rule —
"creating a task is not on the port" — would need revisiting. **That was
wrong.** The rule is right, and it is the reason this ADR is shaped the way it
is: intent belongs to the people, execution belongs to the agent (ADR-0057
rule 4). Priority is intent.

## Decision

### Priority is observed, and never written
`TaskRecord` gains a neutral `priority` and the backend's own raw value beside
it. Both are **read**. There is no `set_priority` on the port, and there will
not be one: a priority this platform could write is a second source of truth
for something the assigning humans already own.

The neutral vocabulary is four buckets — `urgent`, `high`, `normal`, `low` —
plus **`unknown`**, which is the load-bearing one. A backend that does not
report priority yields `unknown`, never `normal`, because "nobody said" and
"somebody said it is ordinary" are different facts and a leader sorting by the
second when it has the first is sorting by an assumption. `BackendCapabilities`
gains `reports_priority`, declared at bind time like everything else there, so
the difference is answerable before the first call rather than inferred from a
column of `unknown`s.

The raw value is kept because four buckets cannot hold five Jira levels, and a
report that has lost `P2` to say `high` cannot be checked against the tool the
humans use.

### Priority on delegation orders, and does not schedule
`assign` takes a priority. It does exactly three things, and a leader is told
all three:

1. `outstanding` and `gather` return in priority order, so "what have I got,
   and what matters most" is one call.
2. When the parallel bound refuses an `assign`, the refusal **names the
   lowest-priority handle currently held**, so it is actionable rather than
   merely correct.
3. It is recorded on the delegation event, so the audit trail says what the
   leader thought mattered at the time.

It does **not** preempt, queue or schedule. ADR-0093 refuses rather than
queues, deliberately, and priority does not change that: a queue nobody
declared is still a bound nobody reviewed. This is stated in the refusal and
in the docstring, because a field called `priority` that silently did nothing
would be exactly the dead control this platform keeps finding and removing —
`max_parallel_subagents`, `escalate_to_human_after_failures`, the absent
scaling bound. Three is the honest list, so three is what it claims.

### A leader can see its team
`team_workload(agent_id)` returns every unsettled session across that agent's
subtree, with who is running it, how long it has been going, whether it is
parked on a human, and whether the agent is being stood in for (ADR-0094). It
is a read over the session tree and the org chart, joining two things that both
already existed.

It is bounded by the org chart, not by convenience: a leader sees its own
subtree and nothing else. Delegation reach and visibility are the same
question, and a view that reached further would be a quiet widening of it.

## Scope
`TaskRecord`, `BackendCapabilities` and the adapter conformance suite; the
runtime's delegation tools and session views. No change to the port's surface,
to the transition table, or to who may delegate to whom.

## Implementation
- `TaskPriority` with `unknown`, and `priority_raw` beside it.
- `BackendCapabilities.reports_priority`; the conformance suite requires a
  backend that declares it to report one, and one that does not to say
  `unknown` rather than guessing.
- `assign(..., priority=)`; ordering in `unsettled_handles` and `gather`; the
  bound refusal names the lowest-priority handle.
- `AgentRuntime.team_workload(agent_id)` over `OrgChart.subtree`.

## Timeline
Delivered with this ADR.

## Advantages
- One answer to "what matters most", and it is the humans'.
- `unknown` keeps "nobody said" distinguishable from "ordinary", which is the
  difference a leader actually needs.
- A leader can answer what its team is doing, from data that already existed.
- Priority does three specific things and claims exactly those three.

## Disadvantages
- **Four buckets lose detail.** A five-level backend collapses, and the raw
  value beside it is a mitigation rather than a fix: anything sorting by the
  neutral bucket has still lost the distinction.
- **Ordering without scheduling will disappoint.** Somebody will set `urgent`
  and expect it to jump a bound. It will not, and the refusal saying so is a
  worse experience than the scheduling they wanted.
- **`team_workload` is a point-in-time read**, not a subscription, so a leader
  polling it is doing what a queue would have done, less well.
- **Visibility bounded by subtree cuts both ways.** A peer leader coordinating
  laterally under a mission grant can delegate but cannot see, which is
  defensible and will be felt.

## Alternatives considered
- **Own a priority and write it back to the backend.** Rejected: two sources
  of truth for intent, and the port exists precisely to keep intent on the
  humans' side.
- **Default an unreported priority to `normal`.** Rejected: it makes a leader's
  sort look meaningful when it is an assumption, which is a worse failure than
  an obviously empty column.
- **Make priority schedule work — a real queue with preemption.** Rejected for
  now. It is a genuine design with real weight (fairness, starvation,
  preemption semantics mid-tool-call) and it is not a field added to `assign`;
  it is its own ADR with a scheduler behind it.
- **Give a leader visibility over everyone it may delegate to**, including
  mission peers and shared services. Rejected: a mission lends the right to
  hand over work, not the right to watch, and conflating them would widen
  every mission grant ever written.

## Verification
- A backend that does not declare `reports_priority` yields `unknown` for
  every task, and never `normal`.
- A backend's own raw value survives beside the neutral bucket.
- The port has no way to write a priority.
- `outstanding` and `gather` return in priority order.
- A refusal at the parallel bound names the lowest-priority handle held.
- Priority is on the delegation audit event.
- `team_workload` returns unsettled sessions for the whole subtree and nothing
  outside it, and marks those parked on a human and those being stood in for.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. Priority read from the assigning backend and never written, with `unknown` distinguished from `normal`; priority orders delegation views without scheduling; a leader can read its subtree's unsettled work. |
