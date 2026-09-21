---
id: ADR-0063
title: Agent kind is derived, not declared, and service reach is encoded once
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture]
consulted: [Runtime Engineering]
informed: [All engineering]
scope: [spec, compiler, runtime]
workstreams: [WS-002, WS-009]
supersedes: []
superseded_by: []
related: [ADR-0027, ADR-0039, ADR-0057, ADR-0064]
tags: [vocabulary, org-model]
---

# ADR-0063: Agent kind is derived, not declared, and service reach is encoded once

## Context
We use the word *agent* for several different things, and `AgentKind` is where
that looseness became a data structure. It has five values —
`EXECUTIVE`, `MANAGER`, `INDIVIDUAL`, `SUBAGENT`, `SERVICE` — and exactly one
of them changes any behaviour.

The evidence is in the code:

* **Only `SERVICE` is load-bearing.** `org.py:143` reads it, once, to let any
  agent reach a shared-service agent. Nothing reads `EXECUTIVE`, `MANAGER` or
  `INDIVIDUAL`; they are set by `seed.py` and by the loader and then never
  consulted.
* **The three positional values are already derived.** `loader.py:358`
  computes them from the tree — leader with no manager is an executive, leader
  with one is a manager, otherwise an individual. They are a *view* of
  `reports_to` and `leader_of`, stored as if they were a fact somebody
  asserted.
* **The one working value is encoded twice.** The spec says
  `shared_service: bool` (`spec/model.py:890`), the IR carries it
  (`ir.py:327`, used at `ir.py:649`), and the runtime re-expresses the same
  thing as `AgentKind.SERVICE`. Two spellings of one property is two places to
  disagree, and the loader is the only thing keeping them in step.
* **`SUBAGENT` is vestigial.** ADR-0027 made sub-agents *tools* — a bounded
  call inside a parent's turn, not a member of the org chart. The enum value
  survives at `engine.py:652`, describing something that is no longer a peer
  of the other four.

The temptation, faced with "we use *agent* loosely", is to widen the
taxonomy — add `PERSONAL_ASSISTANT`, `WORKFLOW`, `BACKGROUND`, and so on. That
would make it worse. A flat enum of role labels invites every new distinction
to become a sixth value, and none of them would change behaviour either. The
distinctions people actually mean are not one axis: *where an agent sits* is
structure, *what starts it* is initiation, *whose authority it acts under* is
accountability. Collapsing three axes into one field is why the field stopped
meaning anything.

## Decision
**Structure is derived from the tree. Reach is declared once. Initiation is a
separate facet. Nothing else becomes a kind.**

1. **`shared_service` is the single encoding of service reach.** It is
   declared in the spec, carried in the IR, and read by `can_delegate`. The
   runtime stops asking `kind is AgentKind.SERVICE` and asks the property
   directly.
2. **Positional kind is a derived view, not stored state.** Executive,
   manager and individual are computed from `reports_to` and `leader_of`
   wherever they are needed for display. They are never written into a spec
   and never consulted for a permission decision — the tree is already the
   authority, and a stored label can contradict it.
3. **`AgentKind.SUBAGENT` is retired.** A sub-agent is a tool invocation
   (ADR-0027), not an org member; giving it a seat in the same enum implies a
   reporting line and a permission surface it does not have.
4. **Initiation is a distinct facet, with three values** — `assigned` (a human
   gives it work: ADR-0057), `triggered` (a workflow or a bus message starts
   it: ADR-0059), `scheduled` (it runs on its own clock). An agent may accept
   more than one. This facet exists because it *changes behaviour*: a
   scheduled agent has no requester to escalate to, and a triggered one's
   input crosses the guardrail boundary from a machine rather than a person.
5. **No facet may be added that does not change behaviour.** A label that only
   describes is documentation, and belongs in the agent's `summary`.
6. **Whose authority an agent acts under is out of scope here** and is put to
   a separate decision (ADR-0064). ADR-0057 rule 2 — an agent acts as itself
   or not at all — continues to hold until that decision is taken.

## Scope
The agent model in `models.py`, the spec field in `spec/model.py`, the IR in
`compiler/ir.py`, the delegation check in `org.py`, the loader and the
designer's agent editor. It does not change the org chart's shape, the
permission resolver, the pairing model, or sub-agent execution.

## Implementation
`AgentKind` loses `SUBAGENT`; the remaining values become a display-only enum
produced by a `positional_kind(agent, org)` helper rather than stored on
`Agent`. `Agent` gains `shared_service: bool`, mirroring the spec and IR
field, and `org.can_delegate` reads it. An `initiation` set is added to the
spec, the IR and the agent editor, defaulting to `{assigned}` so existing
specs are unchanged. The designer shows positional kind as computed text, not
as an editable field — a reader should not be offered a control that cannot
disagree with the tree.

## Timeline
Phase 5, alongside WS-009.

## Advantages
- One property, one encoding: the loader stops being the thing that keeps two
  spellings of *service* in agreement.
- A stored label can no longer contradict the tree it was derived from.
- The enum stops absorbing every new distinction, because the rule for adding
  one is now "it changes behaviour".
- `initiation` makes a real difference visible — an agent with no requester
  cannot escalate to one, and that is now a fact the compiler can check.

## Disadvantages
- **Deriving costs a lookup.** `positional_kind` needs the org tree, so any
  caller that had an `Agent` and wanted a label now needs both. A few call
  sites get more awkward to serve a correctness property they will not notice.
- **Retiring an enum value is a breaking change** for anything persisting
  `AgentKind` by name. Stored sessions carrying `subagent` need a read-side
  fallback, and we will have missed one.
- **`initiation` is a fourth thing to fill in**, and most agents will carry
  the default forever. A facet that is almost always the same value trains
  people to skip reading it — which is exactly how the old enum died.
- **This does not actually fix the loose language.** People will keep saying
  *agent* for a personal assistant, a workflow step and a background worker.
  The model is now honest about which distinctions it enforces; the vocabulary
  problem is a documentation problem and stays one.
- **`shared_service` as a boolean is itself a simplification** — reach is
  binary, when what operators eventually want is "callable by these units".
  We are entrenching the simple version.

## Alternatives considered
- **Expand `AgentKind` with the new categories** — the obvious move, and it
  multiplies decorative values. Five labels where one works becomes nine where
  one works.
- **Keep `AgentKind` and delete `shared_service`** — also collapses the
  duplicate, but keeps a permission decision riding on an enum whose other
  values are cosmetic, which is what made this confusing.
- **Leave it alone** — nothing is broken today. It costs nothing until
  somebody sets `kind=SERVICE` without `shared_service=True` and gets a
  delegation result the IR disagrees with.
- **Replace the enum with free-form tags** — maximally flexible and
  unenforceable; no compiler check can be written against a tag set.

## Verification
Tests assert: `can_delegate` permits reach to a shared-service agent declared
only in the spec, with no `kind` set anywhere; positional kind computed for a
leader with no manager reads executive, with a manager reads manager, and for
a non-leader reads individual; a spec that sets no `initiation` compiles with
`{assigned}`; a stored session carrying the retired `subagent` value loads
without error; and no permission decision anywhere reads a positional kind.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. Service reach encoded once, positional kind derived, `SUBAGENT` retired, initiation added as a behavioural facet. |
