---
id: ADR-0093
title: Delegation that returns a handle
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Security Engineering]
informed: [All engineering]
scope: [runtime, spec]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0027, ADR-0039, ADR-0058, ADR-0065, ADR-0067, ADR-0073]
tags: [delegation, sessions, runtime, missions]
---

# ADR-0093: Delegation that returns a handle

## Context
`delegate(to_agent_id, task)` runs the child inline and returns its output:

```python
child = self.run(to_agent_id, task, created_by=agent.id,
                 parent_session_id=session_id)
```

That shape is call-stack delegation, and it has carried the platform well: the
authority check (`Org.can_delegate`), the depth bound and the audit event all
sit on one code path, and the result is a value the caller can reason about
immediately. Three things it cannot express:

1. **Fan-out.** A leader with four reports cannot ask all four and reconcile
   afterwards. Each call blocks, so the work serialises and the wall-clock cost
   is the sum rather than the maximum.
2. **A durable view of assigned work.** A leader cannot ask "what have I got
   outstanding, and what is stuck?" Between turns there is nothing to ask: the
   child either has not started or has already returned.
3. **Work that outlives a turn.** A child that parks on a human approval
   (`SessionState.WAITING_HUMAN`) blocks its parent's whole run today. The
   leader is holding a budget and a turn open while a person reads an email.

The parts needed to fix this mostly exist. `AgentSession` already carries
`parent_session_id`, `created_by`, `state` — including `WAITING_HUMAN` — and
per-session `token_usage` and `cost_usd`. `SessionStore.trace()` already walks
the tree. What is missing is a way for a leader to *hold* a reference to work
it has commissioned without standing still.

Two adjacent facts shape the decision. `Harness.max_parallel_subagents` is
**declared and enforced nowhere** — harmless today, because serial delegation
cannot fan out, and load-bearing the moment it can. And the depth bound is a
process-local counter (`self._depth += 1 … finally: self._depth -= 1`), which
is meaningful only while delegation is a call stack.

## Decision
Add an **asynchronous delegation** alongside the synchronous one. It does not
replace it: a leader that wants an answer now should keep saying so, and most
delegation is that.

Three tools, and the session tree stays the only store:

| Tool | Returns |
|---|---|
| `assign(to_agent_id, task)` | a **handle** — the child session id — immediately |
| `check(handle)` | the child's state, and its output once terminal |
| `gather(handles, ...)` | the terminal outcome of each, blocking until all settle or a deadline passes |

`assign` performs the **same `Org.can_delegate` check, at assignment time**, and
writes the same `delegation` audit event. Nothing about who may hand work to
whom changes; only when the answer arrives does.

Six rules make it safe, and each exists because the asynchronous shape breaks
an assumption the synchronous one could take for granted.

**1. Authority is checked at assignment, and the result still returns.**
Mission grants are date-bounded (`open_peers(grants, on)`), so a grant can lapse
while work is in flight. The result is still delivered: the work was lawfully
commissioned, and stranding it would punish the leader for the calendar and
create a standing reason never to use a mission. `check` records that the grant
has since lapsed, so the audit trail shows the window the work was assigned
under. Re-checking at collection was considered and rejected below.

**2. Depth becomes a property of the tree, not the process.** The `self._depth`
counter cannot survive a handle that outlives the turn that created it. Depth is
derived by walking `parent_session_id` upward, which is the same number the
counter approximated and is correct under concurrency.

**3. `max_parallel_subagents` starts being enforced.** A leader may hold at most
that many handles that have not settled. Exceeding it refuses the `assign`
rather than queueing, because a queue nobody declared is a bound nobody
reviewed. This is a behaviour change for a field that currently does nothing,
and it is the point: a declared limit that nothing honours is the class of thing
this platform refuses to ship (ADR-0073).

**4. A child's spend counts against its parent.** Sessions already record
`token_usage` and `cost_usd`; `check`/`gather` charge a settled child's usage to
the parent's budget. Without this, fan-out is a way to leave a ceiling behind by
spending through other agents.

**5. A handle always settles.** Any handle that cannot be resolved — a lost
session, a process restart, a deadline — resolves to `FAILED` with a reason. A
handle that hangs forever is worse than one that fails, because a leader waiting
on it has no turn in which to notice.

**6. `WAITING_HUMAN` is reported, not waited on.** `check` returns it as a
distinct, non-terminal state. This is the case that most argues for the whole
change: an approval today freezes the delegating leader, and under this design
the leader learns the work is parked and gets on with something else.

Two things explicitly **do not** change:

- **No separation of duties is weakened.** A leader fanning out to two agents
  kept apart by a separation could already have asked both serially and seen
  both answers; reconciling them concurrently is the same reach at a different
  speed. The barrier is between the *agents*, and neither gains anything from
  the other. Stated here because a reader may assume a merge point is a breach.
- **No target gains the ability to enforce this.** Asynchronous delegation is a
  harness behaviour. A platform target that emits somebody else's agent
  definitions carries neither the handle nor the bound, and its conformance
  report must keep saying so.

## Scope
The runtime's delegation tools, the session store's view of outstanding
children, and `Harness.max_parallel_subagents` becoming enforced. A spec change
is likely needed only if a design should be able to *require* or *forbid*
asynchronous delegation for an agent; that is deliberately left out of this
proposal until somebody wants it.

No change to the phase gate, the IR shape, any target, or `Org.can_delegate`.

## Implementation
Built as described. What landed, and where it differs from the sketch:

- `SessionManager.outstanding(session_id)` returns children that have not
  reached one of `TERMINAL_STATES` (completed, failed, archived).
  `WAITING_HUMAN` is deliberately not terminal.
- `_delegation_tools` gains `assign` / `check` / `gather` beside `delegate`.
  **`delegate` was left exactly as it was** rather than re-expressed as
  `assign` + `gather`: the synchronous path runs the child on the calling
  thread, and routing it through a worker would have put a thread handoff
  under the common case to save a few lines.
- Depth comes from `AgentRuntime.delegation_depth`, which walks
  `parent_session_id`; the `self._depth` counter is gone.
- A settled child's tokens are charged once to the parent's `TurnBudget` by
  `_collect`, and the `delegation_collected` event records tokens and cost.
  **Cost is recorded, not enforced**: `TurnBudget` bounds tokens and wall
  clock, and inventing a money ceiling it does not have would be exactly the
  control that reads as enforced and is not.
- `settle_lost_handles` fails any handle no worker in this process holds, and
  `run` calls it whenever a session resumes. That is what closes the restart
  case in rule 5.
- The runtime grew **no honesty report**, because it has none to grow: the
  reports are per target, and no target carries this. That is now held by a
  test asserting no generated `CONFORMANCE.md` names `assign`, `gather` or
  `max_parallel_subagents`.

Work still open: a handle collected several turns later returns into a
conversation that has moved on, and nothing makes the leader re-read what it
asked for — see the disadvantage below. And a `gather` deadline abandons a
result whose worker may still be running; the `assignment_abandoned` event
records that it was a deadline rather than a failure of the work.

## Timeline
Delivered with this ADR. The eight Verification bullets below are
`tests/test_async_delegation.py`.

## Advantages
- A leader can fan out and reconcile, so wall-clock cost becomes the maximum
  rather than the sum.
- A human approval stops freezing the delegating leader — the case that most
  argues for the change.
- "What is outstanding, and what is stuck?" becomes answerable, from a store
  that already exists rather than a new one.
- A declared-but-dead bound (`max_parallel_subagents`) starts meaning
  something.
- Nothing about who may delegate to whom moves, so the authority model is
  reviewed once and still holds.

## Disadvantages
- **Concurrency in the harness.** The budget, the depth bound and the audit
  trail were all written against a call stack. Each is addressed above, but
  every one of them is a place a concurrency bug would be expensive and quiet.
- **Rule 1 is a real trade.** A result delivered under a lapsed mission grant is
  defensible, and it is still a grant the calendar had closed. The alternative
  strands work. Neither is free.
- **A leader can now hold stale context.** A handle collected several turns
  later returns into a conversation that has moved on, and nothing makes the
  leader re-read what it asked for.
- **More surface to explain.** Four delegation tools rather than one, and the
  difference between `delegate` and `assign`+`gather` is exactly the kind of
  thing an agent gets wrong in a prompt.
- **`max_parallel_subagents` becoming real is a behaviour change** for any
  design that set it low without meaning it.

## Alternatives considered
- **Re-check authority at collection.** Rejected: work lawfully assigned would
  be stranded when a mission ends, which both wastes the work and teaches
  people to avoid time-bounded grants — the opposite of what ADR-0039 wants.
  The lapse is recorded instead.
- **A durable task/work-item model, separate from sessions.** Rejected as the
  first move: `tasks/port.py` is deliberately inbound-only ("creating a task is
  not on the port"), and inventing a second, internal work-item store would
  duplicate the session tree, which already holds parent, state, spend and
  audit. If a leader later needs work items that outlive every session, that is
  a different ADR with a real backing store behind it.
- **Make all delegation asynchronous.** Rejected: most delegation genuinely
  wants an answer now, and forcing every caller through a handle would make the
  common case worse to read and to prompt.
- **Queue rather than refuse when the parallel bound is hit.** Rejected: an
  undeclared queue is an undeclared bound, with a latency nobody reviewed.

## Verification
What would have to be true, if this is built:

- `assign` refuses exactly what `delegate` refuses — same `can_delegate` gate,
  same message — proved against the four reach rules (reports, peers, mission
  grants, subtree, shared services).
- A handle assigned under a live mission grant still settles after the grant's
  window closes, and `check` reports the lapse.
- Holding `max_parallel_subagents` unsettled handles refuses the next `assign`,
  and the refusal names the bound.
- A child's tokens and cost land against the parent's budget at collection, so
  fan-out cannot exceed a ceiling that serial delegation respects.
- Depth computed from `parent_session_id` equals what the counter produced, for
  every synchronous case that exists today.
- A child parked on an approval reports `WAITING_HUMAN` and does not block the
  parent's turn.
- Every handle reaches a terminal state, including across a simulated restart.
- No target's conformance report claims to carry any of this.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted and implemented: `assign`/`check`/`gather`, depth from the session tree, `max_parallel_subagents` enforced, a child's tokens charged to the parent, lost handles settled on resume. |
| 0.1.0 | 2026-09-22 | Proposed. Asynchronous delegation returning a handle, backed by the existing session tree; authority checked at assignment; `max_parallel_subagents` enforced; children's spend charged to the parent. |
