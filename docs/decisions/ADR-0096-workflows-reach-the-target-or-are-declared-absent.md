---
id: ADR-0096
title: A workflow reaches the target, or the target says it did not
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Compiler]
informed: [All engineering]
scope: [spec, compiler, targets]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0005, ADR-0012, ADR-0056, ADR-0073, ADR-0086]
tags: [workflows, targets, conformance, validation]
---

# ADR-0096: A workflow reaches the target, or the target says it did not

## Context
A design can declare workflows: process graphs of tool calls, agent calls,
branches, nested workflows and human interrupts. They validate, they resolve
into the IR, `AgentIR.workflows` names the ones each agent may invoke, and a
trigger can schedule one.

Then they stop. `grep -c workflow` over the agent-framework targets:

```
adk.py        0
langgraph.py  0
maf.py        0
```

Not "partially emitted" or "emitted approximately" — **absent, and unmentioned**.
An agent whose IR says `workflows: [listing_readiness]` compiles to a module
with no trace of it, and a `CONFORMANCE.md` whose whole purpose is to name what
did not survive the translation does not mention workflows at all. A reader
comparing the design to the generated stack has to notice an absence, which is
the one thing readers reliably do not do.

That is a straightforward breach of ADR-0073: the design reads as carrying an
encoded, auditable process, and the deployed stack carries none of it.

Writing this ADR also surfaced two smaller things. The example workflow in
`examples/ayc` declares two nodes and **no edges**, so its second node was
unreachable and never ran — and nothing said so. And `WorkflowSpec.graph` is a
free-form dict whose shape nothing checks, so an edge to a node that does not
exist, or an entry that is not a node, is found at run time or not at all.

## Decision
**Every target that emits agents must account for every workflow those agents
may invoke.** Account for means one of exactly two things: emit it, or name it
in the conformance report as not carried, with the reason. Silence is no longer
an option, and this is the whole rule.

Three consequences.

**1. LangGraph emits a real graph.** The semantics already exist — the runtime
compiles the same declaration onto a `StateGraph` when LangGraph is installed —
so the target emits one module per workflow building that graph, and a
`CONFORMANCE.md` row saying so. This is the one platform where a declared
workflow survives translation, because it is the one whose primitives are the
same primitives.

**2. ADK and MAF declare them absent, by name, rather than approximating.**
Both frameworks have workflow constructs — ADK's `SequentialAgent`,
`ParallelAgent` and `LoopAgent`, MAF's own — and it is tempting to map onto
them. They express a different thing: an ordered composition of *agents*,
where our graph's nodes are tool calls, pure transforms, branch predicates and
human interrupts. A `tool` node is not an agent, and a `transform` node runs a
Python expression over workflow state that no other framework will evaluate.
Mapping a five-node graph onto a `SequentialAgent` of two would produce
something that runs, is not the declared process, and reads as if it were.
So each workflow is listed by id in the conformance report as carried by the
design and not by this stack.

**3. The graph's shape is validated.** A node unreachable from the entry, an
edge to a node that does not exist, a branch case pointing nowhere, and an
entry that is not a node are all refused. This is the same failure the dropped
edge was: a declared step that cannot run, and nothing saying so.

## Scope
The LangGraph target's output; the conformance reports of every agent-framework
target; graph-shape validation. No change to the IR, to the runtime interpreter
(beyond what ADR-0093's edge fix already did), or to how workflows resolve.

## Implementation
- `workflow_conformance(ir)` in a shared place, so a target cannot forget the
  row — the absent case is one call, and a target that emits nothing still
  reports every workflow.
- The LangGraph target gains `graphs/workflows/<id>.py` per workflow.
- Graph validation in `validate_spec`: reachability, dangling edges and
  branch targets, entry membership.
- The AYC example gains the edge it was missing.

## Timeline
Delivered with this ADR.

## Advantages
- A reader of a generated stack can tell what happened to every declared
  workflow without diffing it against the spec.
- The one platform that can carry them, does.
- A declared step that cannot run is refused at the gate instead of being
  discovered from a workflow that silently did half its work.
- The rule is mechanical: emit or name. A future target cannot pass review by
  saying nothing, because saying nothing is now the thing that is refused.

## Disadvantages
- **Most designs' workflows still do not deploy.** Naming an absence is
  honest and is not the same as closing it; two of three agent targets carry
  none of this, and the report makes that more visible rather than less true.
- **The refusal to approximate will read as unhelpful** to somebody who would
  have accepted a `SequentialAgent` that was roughly right, and who now has to
  write it themselves.
- **Graph validation will break specs that were quietly broken**, which is the
  point and is still a migration cost.
- **One more thing every target must do**, and the shared helper makes it cheap
  rather than automatic.

## Alternatives considered
- **Map workflows onto each framework's workflow agents.** Rejected above: it
  produces something that runs and is not the declared process, which is worse
  than nothing because it reads as if it were.
- **Emit workflows only where they fit, silently skipping the rest.** Rejected:
  a partial emission with no report is the situation this ADR exists to end.
- **Emit the graph as data (JSON) for the host to interpret.** Considered
  seriously. Rejected for now because it invents a second execution contract —
  the host would need our interpreter's exact semantics, including
  `transform`'s expression rules — and a contract nobody implements is another
  thing that reads as carried. It is the obvious next move if hosts ask.
- **Leave it as it is.** Rejected: an unmentioned absence in a document whose
  job is naming absences is the failure ADR-0073 was written about.

## Verification
- Every agent-framework target's conformance report names every workflow in
  the IR, whether or not it emits it.
- The LangGraph target emits a module per workflow, and the module builds a
  graph whose nodes and edges are the declared ones.
- ADK and MAF name each workflow as not carried and say why, and neither emits
  a workflow construct.
- A workflow with a node unreachable from its entry is refused, naming the node.
- An edge or branch case pointing at a node that does not exist is refused.
- An entry that is not one of the graph's nodes is refused.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. Targets must emit a declared workflow or name it as not carried; LangGraph emits real graphs; graph shape is validated. |
