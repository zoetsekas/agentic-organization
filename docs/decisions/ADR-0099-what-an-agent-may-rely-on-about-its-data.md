---
id: ADR-0099
title: What an agent may rely on about its data
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Data Engineering, Runtime Engineering, Security Engineering]
informed: [All engineering]
scope: [spec, compiler, runtime]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0005, ADR-0017, ADR-0023, ADR-0073, ADR-0085, ADR-0097]
tags: [data, contracts, lineage, semantics]
---

# ADR-0099: What an agent may rely on about its data

## Context
The question put to the platform was whether it should model physical data
models, serializations, semantic models, knowledge graphs and data contracts —
and what a complete system looks like from a data perspective.

What exists today is an **access and placement** model, and it is good at that
job. `DataClass` carries scope, allowed environments, region egress, trace
visibility and retention. `CapabilityConstraint` carries row caps, masked
fields and operation allowlists. `RelationalGrant` names a connection, its
reachable schemas and tables, its statement classes and its row limit.
`KnowledgeSource` carries `freshness_seconds` and a citation requirement.

Between them they answer *who may touch what, where it may live, and how much
of it*. Three things they do not answer:

1. **What the data means.** A data class is an id with policy attached.
   `customer_pii` cannot say it identifies a person, and cannot say that a
   `refund` relates to an `order`. So a rule that should follow the meaning —
   an egress restriction that travels across a join, a separation expressed
   over data rather than decisions — cannot be written.
2. **What an agent may rely on.** An agent is granted `select` on a table with
   a thousand-row cap. Nothing states that the column it reads will still be
   there next week, still mean what it meant, or was refreshed this morning. An
   agent acting on a silently reshaped or stale table is exactly the failure
   this platform is organised around: a control that reads as enforced and is
   not (ADR-0073).
3. **Who breaks when data changes.** The diff engine can say which agents an
   authority change affects. Nothing can say it for a data change.

`KnowledgeSource.freshness_seconds` is the shape of the right answer, and it
exists for exactly one of the several ways an agent gets data.

## Decision
Three of the five things asked about are **declined**, and the reasoning is the
same one that runs through the rest of the platform. Two are adopted.

### Not in the spec: physical data models and serializations
A table's columns and types, an Avro or Protobuf schema, a Parquet layout —
these are owned by the system that holds the data, and modelling them here
would make this platform keep a **copy of somebody else's truth**. The copy is
correct until their first migration and silently wrong afterwards, and a
design that is silently wrong about a schema is worse than one that never
claimed to know it. This is the same reasoning that kept task priority out of
the port (ADR-0097): a second source of truth for something another system
owns is a liability, not a feature.

The binding already has the right shape — `RelationalGrant` *references* the
physical thing by name and constrains access to it without restating it — and
that is where physical detail stays.

### Not as a new store: a knowledge graph
The IR is already a graph, and the records index already proves the pattern
over ADRs. What is missing is not a graph engine; it is **edges between data
concepts**, which is the semantic gap below. Building a knowledge-graph product
inside this would duplicate the IR the way a second work-item store would have
duplicated the session tree (ADR-0093), and the duplicate is the thing that
goes stale.

### Adopted: data classes gain meaning and relationships
A `DataClass` may declare what it *is* — subject, event, reference, derived,
aggregate — and how it relates to others (`identifies`, `derived_from`,
`part_of`, `references`). Modest, and it pays for itself immediately: an
egress restriction can follow `derived_from`, so a class derived from
restricted data inherits the restriction instead of relying on somebody to
remember. That is a rule the platform can check, which is the bar a semantic
addition has to clear.

### Adopted: a data contract, on the dependency rather than on the schema
The load-bearing addition. A `DataDependency` on an agent states what that
agent **relies on**: the data class, the fields it actually uses, how fresh it
needs them, who produces them where anyone does, and — the part that makes it
a control rather than documentation — **what to do when the promise does not
hold**: refuse, degrade, or escalate.

Four rules:

1. **A contract is a promise between two parties, so the producer is named
   where there is one.** An agent that produces a class, and an agent that
   depends on it, are an edge the platform can see and a change it can route.
   A dependency on data nobody in the design produces is legal and reported,
   because most organizations' data comes from outside.
2. **Staleness is a state, not an error.** A dependency past its freshness
   window does not crash: the declared `on_stale` decides, and the default is
   to refuse. Defaulting to "carry on" would make a freshness declaration
   decorative, which is the one thing it must not be.
3. **A contract names fields, and the fields are what lineage is about.**
   "This agent uses `order.total` and `order.currency`" is what makes a
   producer's change answerable. A dependency that names no fields is accepted
   and says it is a whole-class dependency, because pretending to a precision
   nobody supplied is worse than admitting the coarseness.
4. **Nothing here enforces a schema.** This platform does not read the
   producer's tables and cannot verify a column exists. What it does is make
   the reliance **declared, checkable against the design, and visible in the
   diff**. The conformance reports must keep saying that the promise is
   carried by the producing system, not by this one.

### Adopted: impact answers the question the diff already answers for authority
`data_impact(class_id)` names every agent that depends on a class, with the
fields each uses. The diff engine gains a change kind for a dependency, so
"this contract moved" reads like every other governance change rather than
being invisible.

## Scope
`DataClass` gains semantics and relations; agents gain `data_dependencies`;
the IR carries both; validation checks them; the runtime can answer impact and
enforce `on_stale`. No change to placement, to grants, to the servers catalog,
or to how classification drives access today.

## Implementation
- `DataSemantics` and `DataRelation` on `DataClass`.
- `DataDependency` on the spec agent, carried into `AgentIR`.
- Validation: unknown class, unknown producer, a producer that does not
  declare it produces, a cycle in `derived_from`, and a restriction a derived
  class drops.
- `AgentRuntime.data_impact()` and a diff change kind.

## Timeline
Delivered with this ADR.

## Advantages
- The reliance an agent places on its data becomes reviewable at the same gate
  as its authority, instead of living in whoever wrote the prompt.
- "What breaks if this changes" becomes answerable, which is the question that
  actually gets asked in an incident.
- Staleness gets a declared behaviour rather than an implicit one.
- A derived class cannot quietly drop the restriction it inherited.
- Nothing is copied from a system that owns it, so nothing drifts.

## Disadvantages
- **A declared contract is not a verified one.** The platform cannot read the
  producer's schema, so a design can declare a field that does not exist and
  nothing here will notice. The reports must keep saying so, and somebody will
  still read a green gate as a verified pipeline.
- **Field-level dependencies rot.** They are written once and the code changes,
  so they will drift towards being wrong in the direction of listing too much.
- **Five semantic kinds and four relations will not fit everybody**, and the
  designs that need more will either force a fit or leave it empty.
- **Impact is only as good as the declarations**, so an organization that
  declares nothing gets an impact report saying nothing is affected — which
  reads like safety and is silence.

## Alternatives considered
- **Model physical schemas and generate DDL.** Rejected above: a copy of
  another system's truth, wrong after their first migration.
- **Adopt an existing data-contract standard wholesale.** Rejected for now:
  those standards describe a *dataset's* shape, and what is missing here is the
  *consumer's reliance* on it. The two compose, and a binding could map one to
  the other, which is where a standard belongs.
- **Put freshness only on the knowledge source, as today.** Rejected: an agent
  gets data through capabilities, relational grants and MCP servers as well,
  and a freshness rule that covers one of four paths reads as covering all of
  them.
- **Enforce staleness by failing the run.** Rejected as the only option: some
  work legitimately proceeds on stale data and says so. The declaration picks,
  and the default refuses.

## Verification
- A data class may declare what it is and what it relates to, and an unknown
  relation target is refused.
- A class derived from one that may not leave its region may not itself
  declare that it may.
- An agent may declare a dependency; an unknown class is refused, and so is a
  producer that does not declare it produces that class.
- A dependency naming no fields is accepted and reported as whole-class.
- A dependency on a class nobody in the design produces is accepted and
  reported as externally produced.
- A `derived_from` cycle is refused.
- `data_impact` names every dependent agent and the fields each relies on.
- A stale dependency takes its declared action, and the default is to refuse.
- The IR diff reports a changed dependency as a change.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. Physical models, serializations and a knowledge-graph store declined as copies of other systems' truth; data classes gain semantics and relations; agents declare what they rely on, how fresh, and what to do when it does not hold; impact becomes answerable. |
