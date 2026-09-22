---
id: ADR-0094
title: Leader continuity and the successor who inherits a mandate
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Security Engineering, Governance]
informed: [All engineering]
scope: [spec, runtime, governance]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0039, ADR-0065, ADR-0070, ADR-0073, ADR-0079, ADR-0093]
tags: [continuity, failover, mandates, separation-of-duties, delegation]
---

# ADR-0094: Leader continuity and the successor who inherits a mandate

## Context
A leader agent's **authority** is modelled with some care — `mandate`,
`mandate_holder()` walking up the line, hierarchical narrowing (ADR-0065),
separation of duties proved at validation (ADR-0070). Its **continuity** is
modelled not at all. There is no successor, standby, deputy or acting-for
anywhere in the platform.

What happens today when a team leader agent fails: its session goes `FAILED`,
`raise_alert` fires, `escalation_target()` names its manager, and the run
returns. Three things then have no owner:

1. **Its outstanding handles.** Since ADR-0093 a leader can be holding work it
   commissioned from four agents. When the leader dies those handles settle —
   rule 5 guarantees that much — but nobody collects them and nothing acts on
   what came back.
2. **Its mandate.** Every decision that was this leader's to settle now needs a
   human, including the routine ones.
3. **Its reports.** Nobody may hand them work: `can_delegate` is a property of
   the chart, and the chart still points at an agent that is not running.

The question this ADR answers is whether a successor inherits the mandate or
only the work. It was put to the decision-maker as exactly that choice, and
the answer was **inherit the mandate**.

The sharpest fact in the room is one the platform already establishes. Under
ADR-0065 authority narrows downward, so **a manager's effective mandate is
already a superset of its reports'**. The manager does not need to be granted
anything to decide what a failed report could decide. It already holds it.

That reframes the whole problem. "Successor inherits the mandate" is free and
riskless when the successor is the manager, and is a genuine grant of new
authority only when the successor is somebody lateral. So the design turns on
which of those it is.

## Decision
A leader that cannot run is **stood in for**, not replaced. Six rules.

**1. The default successor is the manager, and it inherits nothing.**
When no successor is declared, the manager stands in. Under ADR-0065 it
already holds a superset of the failed agent's mandate, so nothing is granted,
no separation can be newly breached, and there is nothing to time-bound. This
is the case that needs no ceremony, and it is the default precisely because
the safe answer should be the one you get by saying nothing.

**2. A declared successor is for standing in laterally, and that is a grant.**
`successor_agent_id` names a peer — another team's leader, a deputy, a shared
service — for when routing everything to the manager is the wrong answer:
the manager is two units wide, or is the approver of what this team proposes.
A lateral successor does **not** already hold the mandate, so acting for a
failed leader hands it authority the org chart never gave it. Everything below
exists because of that sentence.

**3. An inherited mandate is time-bounded and recorded, like a mission grant.**
Not a promotion. The grant carries the failed agent, the reason, a window, and
lapses at its end (ADR-0039's machinery, which already expires date-bounded
lateral reach). Renewal is a decision somebody makes again, in daylight. A
standing-in that never ends is a reorganisation nobody approved.

**4. No separation of duties is ever inherited, and the gate proves it early.**
A successor that already holds `approve_payment`, standing in for a leader that
holds `raise_payment`, would hold both — collapsing a control the spec
validator had *proved* impossible. So: decisions that would breach a
separation are **withheld** from the grant and escalate to a human, while the
rest of the succession proceeds. And the same check runs at **validation**, over
the union of each declared successor's mandate and the leader's, so a
succession that cannot be safe is refused before deployment rather than
discovered during an outage. A control found at 3am is a control that failed.

**5. Standing in does not chain.** A successor's successor does not act for the
original. Chaining is how one agent quietly ends up holding three mandates
nobody granted together, and each hop is a step further from anyone who
reviewed it.

**6. Standing in is visible in every session it touches.** A run under an
inherited mandate logs `acting_for`, so the trace says who decided and under
whose authority. A decision that reads as the successor's own is a decision
nobody can audit.

**What triggers it.** Not one failed run. A leader stands down after
`escalate_to_human_after_failures` consecutive failed sessions — the threshold
already declared on every harness for exactly this judgement — or when a human
declares it. One failure is a bad turn; the platform should not reorganise
around a bad turn.

**What the successor gets**, in order of how much it costs to be wrong:
the failed leader's **outstanding handles** (otherwise nobody collects work
already commissioned), **delegation reach to its reports** (otherwise it cannot
lead them), and its **mandate**, minus anything rule 4 withholds.

**What no target enforces.** All of this is harness behaviour. A target that
emits somebody else's agent definitions carries no successor and no lapse, and
its conformance report must keep saying so (ADR-0073).

## Scope
`Agent.successor_agent_id` in the spec and IR; an acting-assignment record and
its expiry; `Org.can_delegate` and `mandate_holder` consulting a live standing-in;
the runtime's failure path; one new validation rule. No change to the phase
gate's shape, to any target's output, or to how mandates resolve normally.

## Implementation
- `successor_agent_id` on the spec agent, carried to the IR and the runtime
  `Agent`.
- `ActingAssignment`: failed agent, successor, decisions granted, decisions
  **withheld** and why, started/ends, reason. Stored like any document.
- `Org.acting_for()` resolves a live assignment; `can_delegate` and
  `mandate_holder` consult it. Expiry is evaluated per call, as mission grants
  are, so a lapsed standing-in confers nothing whether or not anybody swept it.
- The runtime opens an assignment when the consecutive-failure threshold is
  crossed, adopts the failed leader's unsettled handles, and logs `acting_for`
  on every session run under it.
- A validation rule refusing a declared successor whose union with the
  leader's mandate breaches a separation.

## Timeline
Delivered with this ADR.

## Advantages
- The safe case is the default: saying nothing gets you the manager, who was
  already entitled to decide this.
- A lateral stand-in becomes possible without it being an untracked promotion:
  bounded, recorded, and lapsing on its own.
- Work already commissioned is collected rather than stranded — the gap
  ADR-0093 opened when it let a leader hold work across turns.
- A succession that cannot be safe is refused at validation, not discovered
  during the incident it was meant to survive.
- Separation of duties survives the one situation designed to pressure it.

## Disadvantages
- **A lateral successor holds authority the chart did not give it.** Bounded,
  recorded and lapsing, but real, and for the duration the org chart is not a
  complete description of who may decide what. This is the cost of the choice
  made here, and the alternative below is not free either.
- **The trigger is a heuristic.** Consecutive failures cannot distinguish a
  dead leader from one being handed impossible work, and the second case
  reorganises around a problem that standing in will not fix.
- **Withheld decisions make a partial successor.** A stand-in that holds most
  of a mandate is harder to reason about than one that holds all or none, and
  the escalations it produces arrive during an incident.
- **More governance surface.** A second way to hold a mandate is a second
  thing every separation review has to think about.

## Alternatives considered
- **Inherit the work but not the mandate.** The option not taken. Nothing
  stalls, nobody gains authority, and every mandated decision escalates —
  which during an outage converts an agent failure into a human queue, exactly
  when the humans are busiest. Recorded because the trade is close, and if
  standing-in is later found to be over-used this is what it reverts to.
- **Promote a report.** Rejected: authority nobody declared, arriving upward,
  which is the direction ADR-0065 spends its whole argument forbidding.
- **Always route to the manager, with no declared successor at all.** Nearly
  right — it is rule 1 — but it makes the manager a bottleneck for every team
  it owns, and it is wrong outright where the manager approves what this team
  proposes: the failover would breach a separation by construction.
- **Let succession chain.** Rejected by rule 5.
- **Trigger on a single failure.** Rejected: one bad turn is not an outage.

## Verification
- With no declared successor, the manager stands in and is granted nothing:
  its effective mandate is unchanged, because it already held it.
- A declared lateral successor's grant carries the leader's decisions, minus
  any that would breach a separation, and names what it withheld and why.
- A spec whose declared successor would breach a separation is refused by
  validation, with the rule and both mandates named.
- A lapsed standing-in confers nothing, evaluated per call, with no sweep.
- Standing in does not chain: the successor's own successor may not act for the
  original.
- The failed leader's unsettled handles are adopted, and settle against the
  successor rather than being stranded.
- One failed session does not trigger anything; the declared threshold does.
- Every session run under an inherited mandate carries `acting_for`.
- No target's conformance report claims to carry any of this.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. A failed leader is stood in for: the manager by default (already holding the mandate under ADR-0065, so nothing is granted), or a declared lateral successor under a time-bounded, recorded grant that never carries a decision a separation forbids. |
