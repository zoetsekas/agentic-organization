---
id: ADR-0101
title: UML is the metamodel, and the platform's kinds are a UML profile
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Design, Compiler]
informed: [All engineering]
scope: [designer, spec]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0024, ADR-0027, ADR-0034, ADR-0069, ADR-0081, ADR-0082, ADR-0100]
tags: [metamodel, uml, designer, canvas, relationships]
---

# ADR-0101: UML is the metamodel, and the platform's kinds are a UML profile

## Context
The designer knows what may be linked to what through a table of nine rules
(`LINK_RULES`): a team contains teams, has members, an agent uses sub-agents,
holds skills, plugins and tools, delegates to other agents, and a trigger
fires an agent. Each rule names the spec field it writes.

That table is a metamodel in all but name, and it has the problems of one that
grew by accretion:

- **Most relationships in the spec are missing from it.** An agent has
  `knowledge`, `capabilities`, `roles`, `endpoints`, `workflows`,
  `environments` and `produces_data`; a role grants capabilities; a capability
  reaches data classes; an evaluation applies to agents. None can be drawn. A
  user asked for the obvious one — associate a knowledge source with one or
  more agents — and the canvas had no edge for it.
- **Its relationship words are its own.** `contains`, `member`, `uses`,
  `holds` and `flow` each mean something, but not something anybody outside
  this codebase can read, and nothing says which of them are ownership (the
  child's lifetime is the parent's) and which are reference (the target exists
  on its own and many may point at it). That distinction decides how a
  relationship should be drawn, what deleting one end does, and whether
  dropping one box into another should link them.
- **Environments cannot be drawn as what they are.** An agent runs in one or
  more sandboxes (ADR-0082). The canvas shows placements as dashed regions it
  computes, which cannot be resized, dropped into, or edited — so the one
  relationship most naturally drawn as "this box is inside that box" is the one
  the canvas will not let you draw that way.

The shape of the answer is not in doubt: this is what UML's metamodel exists
for, and a UML **profile** is its standard mechanism for a domain's own kinds.

## Decision

### The metamodel is a subset of UML
`orgagents.metamodel` declares the UML metaclasses the platform uses — Class
(and active Class, which is what an agent is: an object with its own thread of
control), Component, Interface, Actor, Artifact, Node, ExecutionEnvironment,
DataType, Activity, Event and Constraint — and UML's relationship kinds:
**composition**, **aggregation**, **association**, **dependency** (with
**usage**), **deployment**, **realization** and **generalization**, with
multiplicities at both ends.

A subset, deliberately. The platform uses perhaps a fifth of UML, and
declaring the rest would be declaring things nothing reads.

### The platform's kinds are a profile
Every palette kind is a **stereotype** extending one metaclass: «Agent» extends
active Class, «Team» extends Component, «Capability» extends Interface,
«Person» extends Actor, «Knowledge» extends Artifact, «Environment» extends
ExecutionEnvironment, «Workflow» extends Activity, «Trigger» extends Event,
«Policy» and «Separation» extend Constraint, and so on.

Every relationship the spec can hold is declared once, as a UML relationship
between two stereotypes, with its multiplicities, the end that owns it, and the
spec field it lives in. `LINK_RULES` is **derived** from the profile rather
than written by hand, so the canvas, the API and the tests read one
declaration. A test checks every declared relationship against the spec model:
a relationship that names a field the spec does not have fails the build, so
the metamodel cannot describe a spec that does not exist.

### What UML's kinds decide in the canvas
The kind is not decoration; it decides behaviour, which is the reason to adopt
it rather than to label the existing rules:

| UML kind | Meaning here | Drawn as | Dropping one box into the other |
|---|---|---|---|
| Composition | the part lives in the whole (team members, sub-agents) | a solid edge from the whole | moves the part there |
| Deployment | the agent runs in that environment | the agent **inside** the environment box | deploys it; dragging it out undeploys it |
| Association | reference; the target stands on its own (knowledge, capabilities, roles) | an edge, from either end | no — associations are drawn, not nested |
| Dependency | one relies on the other (delegation, triggers) | a dashed edge with its stereotype | no |

An **ExecutionEnvironment** box is resizable, and is the first container the
canvas draws: the one relationship where "inside" is the truthful picture.
Teams stay drawn as a tree — composition is shown by an edge from the whole,
because an organisation of twelve teams drawn as nested boxes is unreadable —
but dragging an existing agent onto a team box now re-parents it, as dropping
a new one already did.

An association can be drawn from either end. Knowledge is associated with an
agent whether the line starts at the knowledge or at the agent, because the
spec field it writes (`agent.knowledge`) is the same either way.

## Scope
The metamodel and profile; `LINK_RULES` derived from them; the canvas's
linking, edges and containment driven by the kind; a `metamodel` API route.
No change to the spec's shape: every relationship writes a field that already
exists.

## Implementation
- `orgagents/metamodel/`: the UML subset, the profile, and `link_rules()`.
- `GET /api/designer/metamodel` returning the profile.
- The canvas applies a link by the relationship's shape — move an owned part,
  append a reference, set a single reference — and draws edges for every
  reference relationship with both ends on the diagram.
- Environment nodes are resizable containers; dragging an agent into one
  deploys it, and out of one undeploys it, undoably.

## Timeline
Delivered with this ADR.

## Advantages
- Every relationship the spec can hold can be drawn, and each is declared once.
- The words are UML's, so the model can be read, reviewed and eventually
  exchanged by people and tools that have never seen this codebase.
- Whether a relationship is ownership or reference now decides how it is drawn
  and what dropping does, instead of being implied by which of nine words was
  used.
- The metamodel is tested against the spec model, so it cannot drift from it.

## Disadvantages
- **A profile is a claim of conformance it does not fully make.** This is a
  UML subset with a profile over it, not a UML tool; there is no XMI export
  yet, and a reader expecting full UML semantics (templates, state machines,
  redefinition) will not find them.
- **Deployment by position is a new way to change the model by moving a box.**
  Dragging an agent out of an environment removes the deployment. It is
  undoable and the status line says so, and it will still surprise somebody
  who was only tidying the diagram.
- **More edges.** Drawing every association makes a busy diagram busier; a
  design with thirty capabilities will want filtering by relationship kind,
  which is not in this change.

## Alternatives considered
- **Keep the hand-written rules and add the missing ones.** Rejected: it fixes
  today's gap and repeats the cause, and it leaves ownership versus reference
  implicit.
- **Adopt a full UML implementation (an EMF/XMI stack).** Rejected for now as
  far more machinery than the relationships need; the profile is written so
  that an XMI export is a projection of it rather than a rewrite.
- **Draw teams as nested containers too.** Rejected: composition is the right
  kind for team membership, but nesting is the wrong picture at organisation
  scale. The kind is shared; the presentation differs, which UML itself allows.

## Verification
- Every palette kind is a stereotype extending exactly one UML metaclass.
- Every declared relationship names a field that exists on the spec model.
- `LINK_RULES` is derived from the profile and still contains every rule the
  canvas relied on.
- An association can be drawn from either end, and writes the same field.
- Knowledge linked to two agents appears in both agents' `knowledge`, with an
  edge to each.
- Dragging an agent into an environment box deploys it; out of it, undeploys it.
- An environment box can be resized, and the size is kept.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. A UML subset is the metamodel; the platform's kinds are a profile of stereotypes; relationships are UML kinds that decide drawing and dropping; environments become resizable deployment containers. |
