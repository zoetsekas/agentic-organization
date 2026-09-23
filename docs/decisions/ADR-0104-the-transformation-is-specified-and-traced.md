---
id: ADR-0104
title: The transformation to the physical representation is specified once and traced both ways
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Compiler]
informed: [All engineering]
scope: [compiler, metamodel]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0007, ADR-0027, ADR-0037, ADR-0065, ADR-0081, ADR-0101, ADR-0102, ADR-0103]
tags: [metamodel, compiler, ir, model-driven, traceability]
---

# ADR-0104: The transformation to the physical representation is specified once and traced both ways

## Context
The model is mature (ADR-0101, ADR-0102) and the designer is a view of it
(ADR-0103). What remained was the step that makes the model real: the
compiler's resolution of a spec into the IR, and each target's generation of
artifacts from the IR. That step was code — `build_ir` and seven targets — with
no statement of what it was *supposed* to do with each element and link of the
model, and so no way to say whether it did.

Model-driven development needs the transformation to be as explicit as the
model: every element and every link either arrives in the physical
representation where a rule says it does, or is named as not built and why.

## Decision

### The transformation is declared
`orgagents/metamodel/transformation.py` states, for every stereotype, what it
becomes in the IR:

- **collection** — one IR element per model element (agents, teams, knowledge,
  triggers…);
- **owned** — built inside its owner's image (a sub-agent in its agent, a
  step in its workflow);
- **per holder** — copied into each agent that holds it (a skill, a tool); one
  held by nobody is not built, and the trace says so;
- **resolved** — not an IR element but resolved into computed fields (a role
  into each player's capabilities and permissions; a decision into mandates; a
  plugin into its holders' skills and tools);
- **design only** — nothing is built (a note).

And for every relationship, where the IR carries it: under its own name on the
owner's image unless a rule says otherwise (`team.members` →
`TeamIR.member_ids`), or *resolved* with the check that stands for it
(a role's grants arrive in each player's effective capabilities). The IR also
holds collections *derived* from the model — placements, identities,
resources — and those are declared as such.

It is rendered as `docs/metamodel/transformation.md`
(`orgagents metamodel transformation`).

### The trace checks it, both ways
`trace(spec, ir)` checks a compiled IR against the declaration:
**forward**, every model element and link is in the IR where its rule says;
**backward**, every element of an IR collection has a model source or a
declared derivation. `trace_targets` checks the model-to-text step: every agent
and workflow reaches each target's artifacts. `orgagents metamodel trace
<spec>` runs both.

A structural test holds the declaration against the real IR classes: every
relationship is carried by a field the owner's IR class actually has, or is
declared resolved. A relationship with nowhere to go fails the build before any
example is compiled.

### What the trace found, and what changed
Run over the shipped examples, the trace found four defects in the existing
compiler and one in the model:

1. **Unit links were lost.** ADR-0081's oversight, escalation and service
   relationships were validated and then dropped: nothing in the IR carried
   them. `SystemIR.unit_links` now does.
2. **A sub-agent's output contract was lost.** Declared in the model, absent
   from `SubAgentIR`. It is carried now.
3. **A team's role grants never became capabilities.** They reached members
   as permissions but not as capabilities — and data access, tools and
   sub-agents all derive from capabilities, so anything resting on a
   team-granted capability was silently dropped. One definition,
   `SystemSpec.effective_capabilities` (own, roles', and the team's and its
   ancestors'), is now used by the IR, the validator and the model's
   constraints alike.
4. **The validator and the IR disagreed** on what a team grants (immediate
   team only, versus inherited); both use the one definition now.
5. **The model lacked «Team» plays «Role».** A team's role assignments existed
   in the spec and were not in the metamodel. They are declared, as an
   association class like an agent's.

Every example now traces completely, both ways, and reaches all seven targets;
so does the model left by every scenario of ADR-0102.

## Scope
The transformation declaration, the trace and its CLI; the four compiler fixes
and the model addition the trace required. Targets are unchanged beyond what
the IR now carries.

## Implementation
- `orgagents/metamodel/transformation.py`: `ELEMENTS`, `LINKS`,
  `RESOLVED_LINKS`, `DERIVED`, `image_class`, `carried_by`, `trace`,
  `trace_targets`, `describe`.
- `orgagents/compiler/ir.py`: `SystemIR.unit_links`, `SubAgentIR.output_contract`,
  team-granted capabilities in each agent's set.
- `orgagents/spec/model.py`: `SystemSpec.team_capabilities`,
  `SystemSpec.effective_capabilities`.
- `orgagents/spec/validate.py` and `metamodel/constraints.py`: use the effective set.
- `orgagents/metamodel/__init__.py`: «Team» plays «Role».
- `orgagents metamodel trace <spec>` and `orgagents metamodel transformation`.

## Timeline
Delivered with this ADR. The model, the designer and the transformation are now
each specified and tested; features from here start from the model.

## Advantages
- The compiler has a specification, and a failure of it names the element and
  the rule instead of surfacing as a wrong artifact.
- A relationship added to the model without somewhere for the IR to carry it
  fails a test at once.
- The same trace runs on every scenario, so the model's operations and the
  transformation are checked together.

## Disadvantages
- **Target traceability is by name.** `trace_targets` checks that each agent
  and workflow is named in some artifact, not that the artifact is right; each
  target's own tests remain what checks its content.
- **The IR still mixes shapes** — ids here, embedded spec objects there. The
  trace reads both; a uniform IR is a larger change this ADR does not make.

## Alternatives considered
- **Generate the IR from the metamodel.** Rejected for now: the IR resolves
  (permissions, mandates, placements) as much as it copies, and those
  resolutions are the compiler's substance. Declaring and checking them keeps
  that code and makes it accountable.
- **Trace only forwards.** Rejected: an IR element with no model source is as
  much a defect as a model element with no image.

## Verification
- `tests/test_transformation.py`: every stereotype has a rule; every built kind
  names a real IR class; every relationship is carried by a real IR field or
  declared resolved; every example traces completely and reaches every target;
  the transformation holds after every scenario; unit links, sub-agent output
  contracts and team-granted capabilities reach the IR; the specification is
  current.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. The transformation declared and traced both ways; four compiler defects and one model gap fixed. |
