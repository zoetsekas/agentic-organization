---
id: ADR-0102
title: Model first — abstractions, constraints in OCL, operations with UML semantics, and scenarios as the specification
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Design, Compiler]
informed: [All engineering]
scope: [spec, metamodel]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0024, ADR-0027, ADR-0069, ADR-0070, ADR-0081, ADR-0082, ADR-0096, ADR-0101]
tags: [metamodel, uml, ocl, model-driven, scenarios]
---

# ADR-0102: Model first — abstractions, constraints in OCL, operations with UML semantics, and scenarios as the specification

## Context
ADR-0101 made UML the metamodel and the platform's kinds a profile over it. That
settled the *vocabulary*. It did not settle the model's *behaviour*: what may
hold, what an edit does to the rest of the organisation, and what is refused.
Those rules lived in three places that did not agree — the validator's prose
checks, the designer's canvas code, and the compiler's assumptions — and each
new feature added a fourth.

The project is model-driven: the model is the product, the designer is a view
of it, and every physical output is a transformation of it. That order only
holds if the model is mature **before** the designer and the compiler depend on
it. Three gaps in ADR-0101's model had to close first, and its behaviour had to
be stated somewhere it could be run.

## Decision
The model is completed and made executable in four parts, in
`orgagents/metamodel/`, with no change to the designer or the compiler.

### 1. The abstractions the model was missing
- **A sub-agent is not an agent.** Specialising Agent would make every rule
  about agents — a team, a mandate, a human, a successor — true of sub-agents,
  and none is. Both specialise an abstract **«Worker» {isAbstract}** holding
  what they share: identity, instructions, capabilities, knowledge, tools and an
  output contract. A SubAgent is a composite part of its Agent and deploys only
  where its parent does. It is called like a tool by being *wrapped* by one —
  a Tool wraps a **«Wrappable»** (Capability, SubAgent, Workflow, Endpoint) —
  not by being one: substitutability would put sub-agents wherever tools are
  held, and `agent.tools` never holds them. The
  spec has the same shape: `Worker`, `AgentSpec(Worker)`, `SubAgentSpec(Worker)`.
- **What a policy is about.** `subjects` and `resources` were strings. They are
  now typed ends: a Policy *applies to* **«Principal»** (realised by Agent, Team
  and Role) and *governs* **«Resource»** (DataClass, Capability, Agent, Team,
  Workflow, Environment, Channel), with `'*'` as a declared wildcard. No YAML
  change: the ids were always ids.
- **A workflow is an Activity.** Its graph was a free-form dict. It is now typed
  (`ActivityGraph`, `ActivityNode`, `ControlFlow`): steps are UML actions — a
  tool call, an agent delegation, a sub-workflow, a transform, a decision, a
  wait for a person — and each one that calls something references the model.
  A tool step calls a **«Callable»** (a Tool, or a Capability and its
  operations, `capability__operation`). The typed graph serialises to exactly
  the document it was read from, so the runtime and the compiler read it
  unchanged.

Substitutability is computed once (`specialisations`): an abstract end stands
for its specialisations and realisers, and a concrete one also for its
subclasses — an Organization may stand wherever a Team may.

### 2. Constraints, stated in OCL and executable
`metamodel/constraints.py` holds every rule the model must satisfy, each with a
context stereotype, its rule in OCL, and a check that names the instance that
breaks it:

- **Generic, derived from the profile**, so no relationship can be added
  without them applying: *references resolve* (to an instance of the declared
  kind, or any realiser of an abstract one), *multiplicities hold*, *ids are
  unique* per kind.
- **Domain, from the ADRs**: a leader is a member; a sub-agent is within its
  parent's effective reach, roles included; unit links respect containment; a
  flow joins two agents; no agent is its own successor; the root always places;
  separations hold; a step that calls something names it; edges join steps.

`orgagents metamodel check <spec>` runs them. Every shipped example satisfies
them — which, on first run, it did not: the acme example's workflow called two
tools the model never declared. The existing validator had not noticed.

### 3. Operations, with UML's semantics
`metamodel/operations.py` defines the edits — `create`, `link`, `unlink`,
`delete`, `set_leader` — generically from the profile, and with UML's rules for
their consequences:

- linking a composite part **moves** it (a part has one whole); a part cannot be
  unlinked from its whole, only moved or deleted;
- deleting an instance destroys its parts and **every link to it**, and says so
  as effects — except a link whose other end requires at least one, which is
  kept so the constraint refuses the delete and names who still needs it;
- a team whose leader leaves has no leader, said as an effect, never dangling;
- every operation is a **transaction**: applied to a copy, checked, and
  refused with the violations it would introduce, leaving the model untouched.

An ambiguous link — a team *has member* and is *led by* an agent — must say
which relationship it means.

### 4. Scenarios as the specification
`metamodel/scenarios.py` plays 23 short stories on one small base organisation:
hire, hire a duplicate, move an agent, move a leader, share a knowledge source,
deploy and undeploy, sub-agents within and beyond their parent (and through a
role), oversight and escalation against containment, leadership, separation of
duties, deletions that cascade and deletions that are refused, policies,
successors, flows and workflow edges. Each step declares the model's answer —
accepted with its effects, or refused by a named constraint — and each scenario
ends by asserting what must be true.

Every constraint is shown refusing at least one scenario; a test fails if one is
not. `orgagents metamodel scenarios` writes the catalogue
(`docs/metamodel/scenarios.md`) and a UML object diagram of the model after
each scenario; tests fail when either falls behind the code.

### The order of work from here
1. **The model** — this ADR. Its scenarios are the behavioural specification.
2. **The designer** is specified against the model: every gesture is one of the
   operations above, with a test, before any canvas change. It adds no rules.
3. **The physical representation** — IR, sandboxes, IAM, generated code — is a
   transformation of the model, specified and tested as a mapping.

## Scope
The metamodel's abstractions, constraints, operations and scenarios; the spec
classes `Worker`, `ActivityGraph`, `ActivityNode`, `ControlFlow`; a
`metamodel check|scenarios` CLI. No designer or compiler change.

## Implementation
- `orgagents/metamodel/__init__.py`: the Worker, Principal, Resource, Callable,
  Action and ControlFlow stereotypes; `specialisations`.
- `orgagents/metamodel/instances.py`: every instance of every stereotype in a
  spec, with its owner.
- `orgagents/metamodel/constraints.py`: `CONSTRAINTS` and `check`.
- `orgagents/metamodel/operations.py`: `create`, `link`, `unlink`, `delete`,
  `set_leader`.
- `orgagents/metamodel/scenarios.py`: `base`, `SCENARIOS`, `play`, the object
  diagrams and the catalogue.
- `orgagents/spec/model.py`: `Worker`, `ActivityGraph`, `ActivityNode`,
  `ControlFlow`.

## Timeline
Delivered with this ADR. The designer specification follows it; the
transformation to the physical representation follows that.

## Advantages
- The model's behaviour is written once, runnable, and reviewable as pictures.
- A new relationship gets resolution, multiplicity, linking and deletion
  semantics without new code.
- The designer and compiler will have one source of rules to conform to, and a
  test suite that says whether they do.

## Disadvantages
- **Two rule sets for a while.** The validator's checks still run; many overlap
  with the constraints. They are retired into the model as the designer and
  compiler move onto it, not before.
- **OCL as documentation.** The rules are stated in OCL but executed in Python;
  a reader must trust that the two say the same thing. The scenarios are what
  keeps them honest.

## Alternatives considered
- **Make SubAgent a subclass of Agent.** Rejected: it fails substitutability on
  most of Agent's features.
- **An OCL engine.** Rejected for now: the constraints are few, and a checker
  that names the offending instance in the platform's words matters more than
  evaluating OCL text.
- **Specify the designer first.** Rejected: a designer specified before its
  model is the designer defining the model, which is how the rules came to live
  in three places.

## Verification
- `tests/test_model_scenarios.py`: the base is valid; every scenario holds;
  every shipped example is valid; every constraint refuses something; a refused
  operation leaves the model untouched; the catalogue and diagrams are current.
- `tests/test_metamodel.py`: a sub-agent is not an agent and both are workers;
  inherited relationships reach both; policy ends and workflow actions are
  typed.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. Worker, Principal, Resource, Callable and the Activity; constraints in OCL; operations with UML semantics; 23 scenarios as the model's specification. |
