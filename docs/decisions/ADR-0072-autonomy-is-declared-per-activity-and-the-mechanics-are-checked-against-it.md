---
id: ADR-0072
title: Autonomy is declared per activity and the mechanics are checked against it
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Product, Runtime Engineering]
informed: [All engineering]
scope: [spec, compiler, runtime, security]
workstreams: [WS-003, WS-009]
supersedes: []
superseded_by: []
related: [ADR-0026, ADR-0060, ADR-0064, ADR-0065, ADR-0070, ADR-0071]
tags: [autonomy, controls, human-in-the-loop]
---

# ADR-0072: Autonomy is declared per activity and the mechanics are checked against it

## Context
Some finance work should run unattended and some exists to support a person,
and the platform could not tell you which was which. Classifying every
agent-capability pair in the Northwind example showed why: **the posture was
emergent.** It fell out of four independent things — the capability's action,
whether it named a decision class, whether the agent held that class, and
whether approval was required — and nobody ever stated an intent.

That produced three concrete problems.

**Four writes were governed by nothing.** `cash_application` applies incoming
receipts against open invoices, mutating the receivables ledger, with no
decision class. So no mandate covered it, no condition bounded it, no
separation touched it and escalation could never fire. A first pass classified
it as "advisory" precisely because it named no decision — the absence of
governance read as the absence of consequence.

**Two of three autonomous activities were autonomous by omission.**
`invoice_entry` running alone is right: high-volume clerical entry is what you
want unattended. But `invoice_approval` is *the* accounts-payable control and
ran with no human confirmation solely because nobody had set
`requires_approval` on it. `budget_model` likewise. Neither was a decision
anybody took.

**Deleting one line silently changed posture.** Remove `requires_approval` from
`payment_release` and the treasurer becomes autonomous over instructing the
bank. Nothing in the spec, the gate or a review would say that an activity had
moved from supervised to autonomous.

The pattern this codebase already uses for exactly this is to declare the
intent and check the mechanics against it — a spec that disagrees with what it
does is refused. Autonomy had no declaration to disagree with.

## Decision
**Every activity declares how much of it an agent does alone, and the phase
gate refuses a spec whose mechanics do not match the declaration.**

Four postures, on the capability:

* **`advisory`** — reads and models; may not change a system of record.
* **`human_decides`** — prepares and recommends; the decision is not the
  agent's.
* **`supervised`** — decides, and a person confirms before it takes effect.
* **`autonomous`** — decides and acts alone.

Six rules:

1. **`advisory` may not mutate.** A capability whose action is anything but a
   read is refused under this posture. This is what closes an ungoverned write.
2. **`autonomous` requires a decision class the agent actually holds.** Without
   a class nothing governs it; without the mandate every call would escalate,
   so the declaration would be a lie either way.
3. **`supervised` requires approval, and an approver who is not the agent's own
   owner.** A control confirmed by the requester's own owner is not a second
   pair of eyes.
4. **`human_decides` requires that the agent does *not* hold the decision**, and
   warns when no agent holds it at all — that is work which cannot complete
   inside the organization, and saying so beats discovering it.
5. **`autonomous` requires evaluation evidence.** An agent running unattended
   carries cases that apply to it. The promotion gate already distinguishes
   never-run from failed (ADR-0060); autonomy is the one posture where
   never-checked must not read as safe.
6. **An assignment may tighten a posture and never loosen it** — the same
   narrowing discipline as roles, permissions and mandates.

**The default is `advisory`**, the most restrictive. A capability nobody has
thought about must not be the one running unattended, and because advisory may
not mutate, every write is a decision somebody makes explicitly.

## Scope
The capability model, per-agent tightening, and the validator. It does not add
runtime machinery: the mandate check and the approval gate in
`HarnessBuilder.refusal` already enforce these mechanics — the declaration is
what makes them checkable against intent.

## Implementation
`AutonomyPosture` and `AUTONOMY_ORDER` in the spec model; `Capability.autonomy`
defaulting to `advisory`; `AgentSpec.autonomy` as a per-capability tightening
map. The validator walks every agent-capability pair and applies the six rules.
Northwind declares a posture on all seventeen capabilities, gains
`revise_plan` and `apply_receipt` for the two writes that had no class, names
independent approvers for its supervised activities, and carries three
evaluation cases for its autonomous ones. Acme's `knowledge_contribute` and
`code_change` gain classes and postures — which incidentally closes the
public-plane contribution hole, since writing to what everyone reads is now a
decision a mandate governs.

## Timeline
Phase 5, WS-003. Implemented with this record.

## Advantages
- The question "what does this agent do without a person?" has an answer in the
  spec, per activity, rather than being reconstructed from four fields.
- Drift becomes a failed check. Deleting `requires_approval` under a
  `supervised` declaration now fails the gate instead of silently promoting the
  activity.
- The advisory default means an unconsidered mutation cannot ship.
- Autonomy is tied to evidence, so "we never tested it" stops being a way to
  reach production unattended.
- It does not supervise everything: invoice entry and cash application stay
  autonomous, which is the point.

## Disadvantages
- **A fifth thing to declare, and the one people will copy from a neighbour.**
  The posture is a judgement about risk, and the failure mode is a spec where
  everything is `supervised` because that felt safe — which trains reviewers to
  approve without reading, the same way an over-broad review queue always does.
- **Rule 3 checks identity, not independence.** It can see that the approver is
  not the agent's owner. It cannot see whether that person is senior enough,
  in a different reporting line, or the owner's direct report — because human
  org structure is not modelled (ADR-0064).
- **Rule 5 rests on evidence that is currently thin.** Evaluations run on the
  echo adapter, so coverage proves wiring rather than judgement, and a spec can
  satisfy this rule with one weak case.
- **The four postures will not fit everything.** "Autonomous below a threshold,
  supervised above it" is a real and common arrangement, and it is expressed
  here as one posture plus a mandate condition in a different place —
  discoverable only by reading both.
- **Rule 1 forces a decision class onto activities that are barely decisions.**
  Running a scenario model now needs `revise_plan`, which is machinery around
  something an analyst would call routine work.
- **A posture is a claim about behaviour and nothing verifies it at runtime.**
  An `autonomous` agent that should have asked, and a `supervised` one whose
  approver rubber-stamps, both look correct here.

## Alternatives considered
- **Leave it emergent and document the derivation.** Costs nothing, and leaves
  drift undetectable and two accidental autonomies in a nine-agent example.
- **Infer the posture and report it, without allowing a declaration.** A useful
  view, and it can only ever describe what the fields say — there is no intent
  to disagree with, so nothing can be wrong.
- **Put the posture on the agent rather than the capability.** Simpler to read,
  and wrong: the same agent should enter invoices alone and have its journal
  entries confirmed.
- **Make autonomy a property of the decision class.** Closer to the mark, and
  it cannot distinguish a clerk's use of a capability from an executive's.
- **Require approval for every mutation.** Safest, and it removes the reason to
  build any of this.

## Verification
Tests assert: the default posture is the most restrictive; an advisory activity
that mutates is refused and a read-only one is not; autonomy without a decision
class, or without the mandate behind it, is refused, and passes with both;
supervised without approval is refused; an approver who is the agent's own owner
is reported; `human_decides` where the agent holds the decision is refused; an
assignment may tighten and may not loosen; and, over the worked finance example,
every mutating capability declares a posture, invoice approval is supervised
with approval required, clerical entry stays autonomous with a decision class,
every autonomous agent carries evaluation evidence, and capital expenditure
still reports that no principal can take it.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. Four declared postures with an advisory default, six gate rules checking the mechanics against the declaration, and tightening-only assignment overrides. |
