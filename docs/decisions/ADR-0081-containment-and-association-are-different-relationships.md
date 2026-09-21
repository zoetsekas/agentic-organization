---
id: ADR-0081
title: Containment and association are different relationships
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Designer]
consulted: [Security Engineering]
informed: [All engineering]
scope: [spec, designer]
workstreams: [WS-003, WS-032]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0024, ADR-0034, ADR-0065, ADR-0069, ADR-0070]
tags: [spec, designer, modelling, organisation]
---

# ADR-0081: Containment and association are different relationships

## Context
A line between two teams on our canvas means exactly one thing: the target is
inside the source. `team.teams` is a containment reference, authority narrows
through it (ADR-0065), placement inherits through it (ADR-0069), and dragging
one team onto another moves it.

That is the only relationship two units can have in this model, and real
organisations have more than one. Northwind's finance example carries this
comment:

> Internal Audit deliberately does **not** report to the CFO — its
> independence is the point, and an org model that cannot express that cannot
> model a finance function.

The comment is correct and nothing enforces it. Independence is expressed by
*absence* — Internal Audit is not nested under Finance — and an absence cannot
be checked, because every unit is not nested under most other units. Move
Internal Audit under Finance tomorrow and no rule fires. The one control the
example exists to demonstrate is, in the spec, a paragraph.

The same gap appears in every organisation we have modelled since. Meridian's
second and third lines supervise a first line they do not contain. Northbeam's
governance function rules on audiences that Growth wants and CRM builds.
Lumière's Regulatory function constrains Brand. In each case the relationship
is real, load-bearing, and unsayable.

Eclipse Sirius names the distinction we are missing. Its diagram mappings
separate *container* and *node* mappings — a child drawn inside its parent,
driven by a containment reference — from *edge* mappings, and it splits those
again: a **relation-based edge** is drawn from a plain reference and has no
object of its own, while an **element-based edge** exists because a model
element exists to represent the relationship, with its own identity and
properties. The question "is this team part of that one, or associated with
it?" is exactly that split.

We already have an element-based edge: `interaction_flows` between agents
(ADR-0024). It is directional, it carries a kind from a closed vocabulary, and
it means something — a `consult` lets the source ask without being able to
instruct. We have nothing equivalent between units.

## Decision
Two units may be related in two different ways, and the spec says which.

**Containment** stays what it is: `team.teams`. One parent, authority narrows
through it, placement inherits through it, and the designer re-parents on
drop. Nothing here changes.

**Association** is new: `unit_links`, a list of declared, directional
relationships between two units that are *not* containment. An association
grants nothing, narrows nothing, and inherits nothing. It carries a kind from
a closed vocabulary, and each kind has a stated consequence — because a
relationship with no consequence is decoration, and decoration is the defect
class this platform exists to refuse.

```yaml
unit_links:
  - source: internal_audit
    target: finance
    kind: oversees
    reason: >-
      Internal Audit tests the close. Its independence is the control, and
      this is what makes the independence checkable.
```

The vocabulary, and what each kind is checked against:

| kind | means | the rule |
|---|---|---|
| `oversees` | second- or third-line supervision | the source must not be contained by the target, at any depth, and the two must not share a leader. An overseer inside what it oversees is not independent |
| `escalates_to` | where this unit's escalations land | the target must not be contained by the source. Escalation goes outward or upward, never down into your own report |
| `serves` | a shared service one unit provides another | both must exist; no further rule. A service relationship constrains nothing by itself |
| `partners_with` | a working relationship with no authority in it | symmetric, and declared to be inert. Two of them in opposite directions is a duplicate, not two facts |

`oversees` is the one that earns the feature. It turns "Internal Audit does not
report to the CFO" from a comment into a statement the validator checks, and
moving Internal Audit under Finance now fails.

On the canvas the two are drawn differently and are not interchangeable:
containment is a solid line to a nested box, association is a dashed line
labelled with its kind. Linking two teams asks which is meant rather than
assuming, because assuming is how the canvas used to nest a team nobody asked
it to nest.

## Scope
The spec's organisation model, the validator, the designer's link rules and
canvas, and the generated documentation. It does not touch the permission
resolver, the mandate model, placement resolution, or any runtime target: an
association is a statement about the organisation, not a grant.

## Implementation
**Implemented.**

* `UnitLink` on the spec, with `source`, `target`, `kind` and `reason`, and
  `SystemSpec.unit_links`.
* Validation: both units exist, no self-link, no duplicate, and the per-kind
  rules in the Decision table — `oversees` may not run inward, `escalates_to`
  may not run downward.
* The designer's `LINK_RULES` gains an association rule for team-to-team, so
  linking two teams asks which relationship is meant instead of assuming
  containment.
* The canvas draws containment and association differently: containment solid,
  association dashed and labelled with its kind.
* The examples use it. Northwind's Internal Audit now *declares* that it
  oversees Finance, which is what makes its independence checkable.

## Timeline
Phase 5. Done with the designer's relationship work.

## Advantages
- It makes an existing, load-bearing fact sayable. Independence was expressed
  by absence, and an absence cannot be checked.
- It keeps the canvas honest. Two teams linked by a line no longer means one
  thing that the reader has to know; the line says which.
- Each kind carries a consequence, so no kind is decoration.

## Disadvantages
- A fourth relationship kind (`partners_with`) is deliberately inert, which
  invites the question of why it exists. It exists because the alternative is
  people expressing an inert relationship as `oversees`, which is worse.
- It is another list at the top level of the spec, and the spec is already
  large.

## Alternatives considered
- **A boolean on the team (`independent: true`).** Cheaper, and it says
  nothing about *from what*. Independence is a relationship, not a property.
- **Reusing `interaction_flows` for units.** Flows are between agents and are
  about what may be asked of whom at run time. Overlaying an organisational
  fact on a run-time mechanism would make both harder to read.
- **Inferring association from shared decisions or separations.** A guess, and
  guessing relationships is exactly the defect the canvas's proximity-parenting
  bug was.

## Verification
Tests assert: a link to an undeclared unit is refused; a self-link is refused;
an `oversees` whose source is contained by its target, at any depth, is
refused, and the finding names independence; an `escalates_to` that runs into
the source's own subtree is refused; a duplicate is refused; `partners_with`
grants nothing, so a principal's resolved permissions and mandate are byte
identical with and without it; and every shipped example validates with the
links it declares.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. Containment stays `team.teams`; association becomes `unit_links` with a closed vocabulary where every kind carries a consequence. `oversees` makes organisational independence checkable for the first time. |

## Consequences
An association is a declaration, so it can be wrong, and the validator says
so: an unknown unit, a self-link, an `oversees` that runs inward, an
`escalates_to` that runs down, a duplicate.

An association is **not** an access grant and not a mandate. Overseeing a unit
gives a principal nothing it did not already hold; the mandate model is
unchanged (ADR-0070), and the permission model is unchanged (ADR-0008). If
supervision needs to read something, that is a capability and a data class,
declared as one.

Compilation ignores `unit_links` for the runtime targets: nothing in a
generated container depends on who oversees whom. It reaches the generated
documentation, because that is where an organisation chart belongs.

The `partners_with` kind is deliberately inert and says so in its own
description. Declaring inertness is better than the alternative we rejected —
leaving it out and watching people express it as an `oversees` that is not
one.
