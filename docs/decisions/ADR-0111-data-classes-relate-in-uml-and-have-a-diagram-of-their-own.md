---
id: ADR-0111
title: Data classes relate to each other in UML, and the conceptual data model has a diagram of its own
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Designer, Data Governance, Security]
informed: [All engineering]
scope: [spec, ui]
workstreams: [WS-032]
supersedes: []
superseded_by: []
related: [ADR-0017, ADR-0080, ADR-0081, ADR-0099, ADR-0100, ADR-0101, ADR-0105, ADR-0112]
tags: [data, uml, metamodel, lineage, classification, diagram]
---

# ADR-0111: Data classes relate to each other in UML, and the conceptual data model has a diagram of its own

## Context
ADR-0101 made UML the metamodel: every palette kind is a stereotype, and every
relationship the spec can hold is declared once in the profile, from which
`LINK_RULES`, the canvas, the relationship filter and the tests are derived.
«DataClass» is a stereotype of DataType, and its links to the rest of the
organisation — an agent *produces* it, *relies on* it (the `DataDependency`
association class), a capability *reaches* it, knowledge *contains* it, a
memory namespace *stores* it — are declared there.

ADR-0099 then gave data classes relations to **each other** —
`derived_from`, `identifies`, `part_of`, `references` — and made
`derived_from` carry restrictions: a class derived from data that may not
leave its region may not say that it may. Those relations were added to the
spec model (`DataClass.relations`) but **not to the UML profile**. The
consequences:

- the canvas cannot draw them, and the relationship filter does not list them,
  so the one relationship that moves a restriction is the one nobody can see;
- the metamodel's constraints (references resolve, multiplicities hold) do not
  cover them, so they are checked by hand-written rules instead of the
  declared ones;
- there is no view in which a person can see the conceptual data model: what
  classes exist, how they derive from one another, how they are classified,
  and which agents produce and rely on them.

ADR-0099's line on physical models stands and is not reopened: tables,
columns, types, keys and serialization schemas belong to the system that
holds the data, and a copy here is silently wrong after its first migration.

## Decision
1. **Data-class relations are UML relationships in the profile**, each
   declared once with its multiplicities and its spec field
   (`data_classes[].relations`, keyed by `kind`):

   | Relation | UML relationship | Reading |
   |---|---|---|
   | `derived_from` | Dependency «derive» | the target is computed from the source; restrictions flow along it |
   | `part_of` | Composition | the source is a component of the target |
   | `identifies` | Association «identifies» | the source names the subject the target is about |
   | `references` | Association | a plain pointer; carries no restriction |

   `LINK_RULES`, the relationship filter, drawing a link on the canvas and the
   metamodel constraints all pick these up from the profile, as every other
   relationship does. The hand-written reference checks for relations are
   replaced by the declared ones; the ADR-0099 restriction rule stays.

2. **The conceptual data model gets a diagram: *Data*.** A diagram aspect
   (ADR-0100) alongside Organisation and the process diagrams, drawn as a UML
   class diagram:
   - every data class is a node, its classification shown the same way an
     agent's is (the stripe ADR-0080 already uses) and its semantics
     (subject, event, reference, derived, aggregate) as a stereotype label;
   - data-class relations are drawn with their UML notation — «derive» as a
     dashed arrow, `part_of` as a filled diamond — and filtered with the
     existing relationship filter (ADR-0105);
   - producers and consumers are shown as the agents that produce or rely on
     each class, with a `DataDependency` drawn as an association class whose
     label names the fields used and the freshness window;
   - a restriction that travels along `derived_from` is **drawn**: a derived
     class that inherits "may not leave region" or a stricter classification
     shows where it came from.

3. **Still not in the spec:** physical or logical schemas (entities,
   attributes, types, keys, Avro/Protobuf/Parquet). A data class may carry a
   **reference** to where its schema lives (a URL or a catalogue id), which
   the diagram shows as a link out. The platform does not read it, copy it or
   check it.

## Scope
The metamodel profile, `LINK_RULES`, the metamodel constraints, the
validator's data section, the canvas (a Data diagram aspect, relationship
filter entries, drawing relation links), the issue catalog and the User
guide. It does not change classification, grants, egress checks, the servers
catalog or the ADR-0099 contract semantics, and it adds no physical model.

## Implementation
- `metamodel`: four `R(...)` declarations for data-class → data-class
  relations; a «derive» dependency flavour if the profile has none.
- `DataClass` gains an optional `schema_ref: str` (a pointer only).
- Validator: relation findings come from the metamodel constraints; the
  derived-restriction rule is unchanged. New codes are catalogued.
- Designer: a *Data* diagram aspect with a layered layout (derivation flows
  left to right), classification stripes, stereotype labels, association-class
  labels, and a legend row for the Data diagram.
- AYC: data classes related (for example `order_data` part of `customer_order`,
  `sales_report` derived from `order_data`) so the example exercises it.

## Timeline
After ADR-0110 Milestone 1 lands, within WS-032.

## Advantages
- The relationship that moves a restriction becomes visible and checkable in
  the same way as every other relationship.
- A real conceptual data model in UML, without claiming to own anybody's
  schema.
- Lineage and classification can be reviewed in one picture, which is what
  a data-governance reviewer actually needs.

## Disadvantages
- One more diagram aspect to lay out and keep legible on large designs.
- `schema_ref` can go stale like any link; it is labelled as a pointer, not
  a promise.

## Alternatives considered
- **Model physical schemas in the spec** — rejected in ADR-0099; unchanged.
- **Leave data-class relations outside the profile** — rejected: the one
  relationship carrying a restriction would stay the one nobody can see.
- **Draw data classes on the Organisation diagram only** — rejected: mixing
  the org chart with lineage makes both unreadable at AYC's size.

## Verification
Tests assert that every `DataRelationKind` is declared in the profile with a
UML relationship kind; that `LINK_RULES` and the relationship filter include
them; that a dangling relation target is reported by the metamodel
constraint; that the Data diagram renders every data class and relation of the
AYC example; and that a restriction inherited along `derived_from` is shown
on the derived class.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. |
