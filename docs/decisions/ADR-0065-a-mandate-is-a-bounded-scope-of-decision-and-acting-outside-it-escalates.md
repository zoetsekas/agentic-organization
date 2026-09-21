---
id: ADR-0065
title: A mandate is a bounded scope of decision and acting outside it escalates
status: Accepted
version: 1.1.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture]
consulted: [Security Engineering, Product]
informed: [All engineering]
scope: [spec, compiler, runtime, ui]
workstreams: [WS-003, WS-009]
supersedes: []
superseded_by: []
related: [ADR-0006, ADR-0007, ADR-0026, ADR-0039, ADR-0063]
tags: [authority, org-model, governance]
---

# ADR-0065: A mandate is a bounded scope of decision and acting outside it escalates

## Context
The system exists to run organizations — fully agentic or hybrid — and an
organization is held together by four things: roles, responsibilities,
mandates and permissions. We model permissions rigorously. We model the other
three as prose.

`mandate` exists today on `Team` only (`spec/model.py:940`), as a free-form
`list[str]`. `validate.py:142` warns when it is missing, `ir.py:607` carries
it into the IR, `loader.py:265` parks it in `metadata` — and nothing reads it,
ever. Agents have no mandate field at all.

Permission and mandate are not the same question, and collapsing them is why
the second one went missing. **Permission is whether the door opens. Mandate
is whether you were the one who should have opened it.** An agent can hold a
perfectly legitimate permission to issue a refund and still have no business
deciding to issue this one. Today the platform has exactly one way to express
that: `requires_approval` on an individual capability or tool binding
(`spec/model.py:363`, `loader.py:160`), plus `requires_approval_for` per
agent. Those are per-action flags. They scale by action, not by unit, so
nobody can answer the question a real organization asks constantly — *what is
this team allowed to decide without asking?*

The consequence shows up as a shape problem. Authority in an organization is
hierarchical and bounded: a unit decides within a scope, and anything outside
it goes up. Our only tool is a boolean stapled to each action, so authority
gets re-litigated per tool binding, and the org chart — which already encodes
accountability — contributes nothing to it.

There is also an asymmetry with escalation. The runtime already knows how to
escalate: `org.escalation_target` walks the management chain, and
`engine.py:208` prefers the paired human, falling back to the manager. That
machinery is currently reachable only through failure and guardrail breaches.
Exceeding your authority is not a failure; it is the ordinary case that
escalation was invented for, and we do not route it there.

## Decision
**A mandate is a declared, bounded scope of decision held by a unit. An action
within permission but outside mandate escalates; it is not refused.**

1. **A mandate is structured, not prose.** `Mandate` carries
   `decisions: list[str]` — named decision classes drawn from the spec's
   declared vocabulary, the same way capabilities are named — plus
   `conditions` for the bounds that make a decision class finite (a value
   ceiling, a reversibility requirement, a data class).
   The free-form `list[str]` on `Team` is replaced, not supplemented.
2. **Teams and agents both hold mandates.** An agent's **effective mandate is
   the intersection of its own declared mandate with its team's**, recursively
   up the tree. Authority narrows downward and never widens — the same rule
   `RoleAssignment` already applies to permissions (ADR-0007). A unit cannot
   grant authority it does not itself hold.
3. **Permission is checked first, and refuses. Mandate is checked second, and
   escalates.** Both must pass for an agent to act alone. The order matters:
   escalating an action the agent could never perform wastes a human's
   attention on an impossible request, so a missing permission is a flat
   refusal with its reason, exactly as today.
4. **Escalation goes to the smallest unit whose mandate covers the decision**,
   found by walking up from the agent. It resolves to a person by the existing
   precedence (`engine.py:208`): the unit's paired human counterpart if it has
   one, otherwise its manager agent. If nothing up the chain holds the
   mandate, the action is **refused**, with the reason naming the decision
   class nobody in the organization is authorized to take. Silence does not
   promote.
5. **Silence never means authority.** An empty mandate on an agent or team
   means *inherit the parent's*, not *unlimited*. The organization root must
   declare a mandate explicitly; a root without one is a **spec error**,
   upgrading today's team-level warning. That is the one place authority
   enters the system, and it should be written down.
6. **Mandate never widens permission.** An agent with the mandate to decide
   something it has no permission to do is still refused at the permission
   check. Mandate is not a grant; it is a bound on a grant already held.
7. **`requires_approval` survives and keeps its meaning.** An action inside a
   unit's mandate may still require a second pair of eyes. Mandate answers
   *whose decision is this*; approval answers *should anyone check it*. The
   two compose; neither replaces the other.
8. **A mission may carry a mandate bounded by the mandate of the human
   accountable for it, and it expires with the mission window** (ADR-0039).
   Without this rule a mission is an authority hole: it already lends lateral
   reach, and a temporary team with a permanent decision right is how standing
   authority gets created by accident.
9. **Every escalation is recorded** with the decision class, the agent that
   reached its limit, the unit it went to, and the outcome. An organization
   that cannot see where its authority boundaries bind cannot tell whether it
   drew them in the right place.

## Scope
The `Team` and `AgentSpec` models, the mission model, the IR, the delegation
and escalation paths in `org.py` and `runtime/engine.py`, and the designer's
agent and team editors. It does not change permission resolution, the phase
gate, the pairing model, the org chart's shape, or what a role grants.

## Implementation
A `Mandate` model replaces `Team.mandate: list[str]` and is added to
`AgentSpec` and `Mission`. The compiler resolves an **effective mandate** per
agent at the phase gate, alongside permissions — intersected up the tree, once
— and writes it to the IR; the runtime never re-derives it. `org.py` gains
`mandate_holder(agent_id, decision)` returning the nearest unit up the chain
whose effective mandate covers the decision class. The engine consults it
after the permission check and before the act, routing an uncovered decision
through the existing escalation path rather than the failure path. The
designer shows **effective** mandate on an agent, not declared, with the unit
each line was inherited from — a reader must not have to walk the tree to
learn what an agent may decide.

## Timeline
Accepted and implemented in Phase 5, WS-003, ahead of the ADR-0063 refactor.

## Decisions taken while implementing
Four things the record left open, settled by building it:

1. **The decision vocabulary is declared in the spec**, as `decisions:`,
   beside capabilities — not in the catalog. That keeps ADR-0066's open
   question genuinely open: spec-declared terms can move to catalog ownership
   later, while catalog ownership could not be undone.
2. **Conditions are a chain, not a merge.** Every condition from every unit in
   the line applies. This makes narrowing correct by construction — a child
   tightens by adding one, and dropping a parent's is not an operation the
   structure has — and avoids inventing a general "is this condition narrower"
   comparison, which is not decidable over free-form maps.
3. **A prose mandate is refused, not coerced.** `Team.mandate` was a list of
   sentences; turning "Run the workforce safely" into a decision class would
   have manufactured exactly the machine-readable wrong term ADR-0066 warns
   about. The error names the replacement, and the prose moved to a new
   `Team.description`.
4. **A mission is bounded by its leader, not its sponsor.** Rule 8 says the
   accountable *human*, and people do not carry mandates — that is the hybrid
   gap ADR-0064 holds open. The leader's effective mandate is already narrowed
   by the standing tree, so it is the stricter available bound. This is a
   substitution, and it reverts to the sponsor if humans ever carry authority.

## Advantages
- Answers the question a real organization asks: *what may this team decide
  without asking?* — in one place, per unit, instead of scattered across
  per-action booleans.
- Turns the org chart into something load-bearing for authority, not just
  delegation reach.
- Routes exceeded authority to escalation, which is what escalation is for,
  rather than to refusal or to a silent success.
- Gives the free-form `responsibilities` something to anchor to: a
  responsibility with no matching decision class is visibly a promise the unit
  cannot keep.
- Missions cannot accumulate standing authority, because their mandate expires
  with the same window their lateral reach already does.

## Disadvantages
- **A too-narrow mandate turns the organization into a queue** at whichever
  human sits above it, and the pressure will be to widen mandates until they
  approve everything — the failure mode of every OAuth scope ever written. The
  escalation record makes this visible; it does not prevent it.
- **Two gates, two kinds of "no".** An action can now be refused (no
  permission) or escalated (no mandate), and people will conflate them. We are
  manufacturing a support question — *why did this stop?* — that has two
  answers with different remedies.
- **Decision classes are a new closed vocabulary** and every organization's
  real decision taxonomy is different. Ours will fit nobody exactly, and the
  first customer will want a class we did not think of.
- **Intersection with the parent means moving a team narrows its agents'
  authority silently.** A reorganization changes what an agent may decide with
  no edit to that agent and no diff on it. The IR diff (WS-005 M4) would show
  it only if effective mandate is diffed, which is one more thing to get right.
- **Inheritance makes the default invisible.** An agent declaring nothing has
  a real mandate somewhere up the tree, and the honest reading requires the
  designer to compute it. If that view is ever wrong or absent, the model is
  worse than the prose it replaced, because prose does not pretend to be
  enforced.
- **Escalation presumes a human is there** — and the stated purpose includes
  *fully* agentic organizations. Under rule 4 the chain ends at whichever unit
  holds the mandate, which may be an agent, so a fully agentic org can resolve
  its own escalations. That is either the feature or the hole, depending on
  how much you trust the root mandate, and it deserves to be decided
  deliberately rather than discovered.
- **It is a breaking spec change.** `Team.mandate` is a `list[str]` today, and
  every existing spec carries the old shape.

## Alternatives considered
- **Leave mandate as prose.** Costs nothing, changes nothing, and authority
  stays the one part of the org model that is decorative. This is the status
  quo and the default if this is rejected.
- **Model mandate as more permissions.** Simple and it destroys the
  distinction: "may decide" collapses into "may act", so exceeding your
  authority produces a refusal rather than an escalation, and nobody upstairs
  ever hears about the decision that needed making.
- **Keep per-action approval flags only.** Already works, already shipped, and
  scales by action rather than by unit — so the question "what may this team
  decide?" stays unanswerable no matter how many flags are set correctly.
- **Mandate as a spend budget.** Concrete, checkable, and covers money only.
  Most organizational authority is not denominated in currency.
- **Derive mandate from the role contract.** Attractive, since roles already
  bundle responsibilities and permissions — but a role travels with an agent
  while authority belongs to the position it occupies. The same role means
  different authority in a different unit, which is exactly the distinction
  this record is drawing.

## Verification
Tests assert: an action inside permission and inside mandate proceeds; an
action inside permission and outside mandate escalates, and the record names
the decision class and the unit reached; an action outside permission is
refused without escalating, whatever the mandate says; a mandate declaring
authority its parent does not hold is narrowed at the phase gate, not honoured;
an empty agent mandate resolves to the parent's rather than to unlimited; a
spec whose root declares no mandate fails validation; a decision no unit holds
is refused with that reason rather than promoted to the root; a mission mandate
is refused when it exceeds the accountable human's, and stops applying once the
mission window closes; effective mandate is resolved once at the phase gate and
the runtime never re-derives it; and every escalation is recorded.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-21 | **Accepted and implemented.** Vocabulary declared in the spec; conditions chain rather than merge; a prose mandate is refused rather than coerced; a mission is bounded by its leader until people carry mandates. |
| 1.0.0 | 2026-09-21 | Proposed. Mandate as a structured, inherited, narrowing scope of decision; outside it escalates rather than refuses; permission checked first. |
