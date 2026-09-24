---
id: ADR-0110
title: The designer draws the governed workflow; an engine's own builder builds what happens inside a step
status: Accepted
version: 1.1.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Designer, Runtime, Security]
informed: [All engineering]
scope: [spec, ui, runtime, targets]
workstreams: [WS-032]
supersedes: []
superseded_by: []
related: [ADR-0030, ADR-0056, ADR-0075, ADR-0081, ADR-0096, ADR-0102, ADR-0109]
tags: [workflows, langflow, langgraph, langchain, designer, process, interface]
---

# ADR-0110: The designer draws the governed workflow; an engine's own builder builds what happens inside a step

## Context
The designer defines an agentic organisation: who exists, what each may do,
what is kept apart. Organisations also need *agentic workflows*, and the
question was whether to build our own workflow builder — a LangGraph/Langflow
equivalent — or hand workflows to Langflow and similar tools.

Neither extreme holds up:

- **Our own full builder** would re-implement a fast-moving product category
  (prompt nodes, retrievers, hundreds of connectors) that is not what this
  platform is for, and would tax every team that already has flows elsewhere
  with a rewrite. ADR-0056 already rejected importing other engines' graphs
  into ours as "a permanent translation problem".
- **Langflow as the workflow designer** knows nothing of what this platform
  exists to govern: who owns a step, the owner's mandate, separations of
  duties, approvals, data classes, environments, publication through an
  authority review. Work inside Langflow also runs outside the agent's
  sandbox (ADR-0056).

The pieces for a middle course exist. A `WorkflowSpec` is a UML Activity
(ADR-0102) whose steps are `agent`, `tool`, `human`, `branch`, `transform`
and `workflow` actions; ADR-0096 holds that a workflow reaches its target or
is declared absent; a `WorkflowBinding` chooses the engine per workflow and
treats an out-of-process engine as egress (ADR-0056); the local target ships
a pinned Langflow per tenant. What is missing is a way to *govern* a step
whose insides live in another tool, and a designer to draw the rest.

## Decision
**Two levels, one seam.**

1. **The governed workflow is ours.** Which steps happen, in what order, who
   owns each (an agent, a team, a person), where a human approves, where the
   flow branches or runs in parallel, what data passes between steps. It is
   drawn in the designer's process view, stored in the spec, and validated
   against mandates, separations and data classes like every other design
   fact. We do **not** build prompt, retrieval or connector nodes.

2. **What happens inside a step may belong to an engine.** A step that is
   built in Langflow, LangGraph, LangChain or code is a `workflow` step whose
   called workflow is bound to that engine. No new step kind is invented: the
   existing sub-workflow call plus the existing binding already say it.

3. **The seam is a declared interface, not an imported graph.** A workflow
   whose body lives in an engine declares its **interface** — inputs,
   outputs, the tools and endpoints it calls, the data classes it receives
   and returns — and is marked `body: external`. The spec never holds the
   engine's graph. The validator checks the interface as if it were the step:
   the calling step's owner must hold the capabilities the interface names,
   the data classes must be ones the owner may send, and the call is egress
   under ADR-0030 when the engine is out of process.

4. **Parallel work is drawn, not implied.** The activity graph gains a
   `fork` and a `join` (UML ForkNode/JoinNode), so "check stock and credit at
   the same time" is a picture and a validator rule, not a convention.

5. **The designer links out; it does not embed.** A step bound to an engine
   offers *Open in <engine>* (a URL in the binding) and *Refresh interface*
   (read the flow's inputs and outputs from the engine, where it can say).
   Our spec is the source of truth for the governed structure; the engine is
   the source of truth for the step's insides.

## Scope
The spec's `WorkflowSpec` and activity graph, the binding's workflow
section, the validator, the designer's process view, and the runtime's
engine registry. It does not make the platform an authoring tool for any
engine's graphs, does not translate between engine formats, and does not
govern what happens *inside* an external step beyond its declared interface.

## Implementation
**Milestone 1 (this ADR):**
- Spec: `ActivityNodeKind` gains `fork` and `join`; `WorkflowSpec` gains
  `body: "graph" | "external"` (default `graph`) and an `interface`
  (`inputs`, `outputs`, `tools`, `endpoints`, `receives_data_classes`,
  `returns_data_classes`). An external workflow has no graph; a graph
  workflow may declare an interface too.
- Validator (numbered in `issue_catalog.yaml`): an external workflow with no
  binding to an engine; an external workflow with an empty interface; a step
  whose owner lacks a capability the called interface names; data classes the
  owner may not send; a fork without a join, a join nothing forks into, and a
  human approval step with no named person or role.
- Designer: the process view draws every step kind including fork/join,
  shows the owner on each step, and edits an external workflow's interface;
  a bound step shows its engine and an *Open in* link from the binding.
- Runtime/local target: the existing in-process executor runs fork/join; an
  external step calls the bound engine through the ADR-0056 invoker, with
  the stub model and the tenant's Langflow in the local stack.
- AYC: the purchase-to-pay process drawn as a governed workflow, with one
  step bound to a Langflow flow, as the worked example.

**Later:** reading an interface from Langflow's API and LangGraph's schema;
compiling simple graph workflows to a LangGraph `StateGraph` per target.

## Timeline
Milestone 1 lands with WS-032 (designer completeness). The interface import
follows once Milestone 1 has been used on AYC.

## Advantages
- Governance applies to the structure people actually reason about — who
  does what, who approves — without re-implementing an LLM toolkit.
- Teams keep their Langflow and LangGraph flows and their builders.
- One seam, the interface, is small enough to validate and to keep honest.

## Disadvantages
- **An external step is governed at its edges only.** What it does inside is
  as safe as its declared interface is true; a flow that calls a tool it did
  not declare is caught at egress, not at design time.
- Two tools to open for one workflow when a step is external.
- An interface can drift from the flow it describes until *Refresh
  interface* exists for that engine.

## Alternatives considered
- **Build a full builder of our own** — rejected, above.
- **Adopt Langflow as the only workflow designer** — rejected: no notion of
  owners, mandates or separations, and its execution sits outside the
  sandbox.
- **Import engine graphs into the spec** — rejected in ADR-0056; still a
  permanent translation problem.
- **Embed Langflow's editor in an iframe** — deferred: it hides the seam this
  ADR makes explicit, and couples us to one engine's UI.

## Verification
Tests assert that fork/join validate and execute; that an external workflow
without a binding or an interface is an error; that interface capabilities
and data classes are checked against the calling step's owner; that no engine
name enters `src/orgagents/spec/`; and that the AYC purchase-to-pay workflow
validates, compiles for `local`, and runs its Langflow-bound step through the
egress path.

## Implementation notes (Milestone 1)
Milestone 1 is built. Where the implementation had to decide something the
text above left open, or deviated from it, it is said here.

- **Deviation — where the binding check lives.** The spec validator never
  sees a binding, so "an external workflow with no binding to an engine"
  (`OA-0507 workflow_external_unbound`) is raised by
  `spec.validate.workflow_binding_findings`, which the compiler calls per
  target and the designer calls when a design was saved with a binding. It is
  an **error** when the target has a binding that does not bind the workflow,
  and a **warning** when the target is compiled on the platform default with
  no binding document at all: nothing was chosen for that target, and its
  conformance report already names what it does not carry (ADR-0096).
- **Deviation — a human step with nobody named** (`OA-0514`) is a warning in
  development and an error in production, not always an error: shipped
  library workflows pause for "a person" without naming one, and the rule is
  one that tightens with the environment like ADR-0026's pairings.
- **Owners.** A step names its owner with `owner` (an agent, team or person);
  an `agent` step defaults to its agent. A `human` step names `person` or
  `role`. `OA-0509` refuses an owner or approver the design does not declare.
- **What the interface is checked against.** `interface.tools` must be held
  by the step's owner as a capability (its own, its roles' or its team's) or a
  declared tool, `interface.endpoints` as one of its endpoints (`OA-0510`);
  `receives_data_classes` must be data classes of the owner's capabilities,
  which is what the owner may send (`OA-0511`).
- **Fork/join at run time.** The in-process interpreter runs a fork's branches
  one after another from the state at the fork and merges their writes at the
  join; two branches writing the same key are refused rather than raced.
  `compile_langgraph` refuses a graph with a fork until the later milestone.
- **The external step at run time** calls the bound engine through the
  ADR-0056 invoker under the step owner's boundary, sending the interface's
  `receives_data_classes`. On the AYC workstation stack the tenant's flow
  engine is a mock of its run API (the overlay swaps the image), reachable as
  `flows.internal`, which `finance_ops` allowlists; a worker exposes
  `POST /workflow` to run it.
- **Designer.** The binding gains `editor_url` for *Open in*. The designer
  reads engines from the binding a design was saved with
  (`GET /api/designer/systems/{id}/workflow-engines`); a design saved without
  one shows the step as external and says the engine is unknown. There is
  still no way to attach a binding to a design in the UI; a shipped example
  loads with the binding it ships (its `*.local.binding.yaml`, else the one
  its deployment names). *Refresh interface* is not built (Later).
- **The process view** draws the workflow as a UML activity: an initial node
  at the entry, a final node wherever it ends, one swimlane per owner (the
  owner's team, else the agent or person), the flow left to right, loop-backs
  routed below the lanes and labelled with their condition, and each
  decision's arms labelled with their case. `lanes` is a server-side layout
  (`designer/layout.py`) and the default for process diagrams; on a process
  diagram the palette holds step kinds, and dropping one creates it in the
  workflow through the model's `create` operation.
- **In the profile (ADR-0112).** `fork`/`join` are literals of the declared
  `ActivityNodeKind` Enumeration (ForkNode/JoinNode), `WorkflowBody` is a
  declared Enumeration, `WorkflowInterface` a declared DataType, and every
  new field is a declared Property or relationship end: `action.owner` (to an
  abstract «StepOwner» realised by agent, team and person), `action.person`,
  `action.role`, and `workflow.interface.{tools,endpoints,
  receives_data_classes,returns_data_classes}`.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-23 | Milestone 1 built; implementation notes record where the binding check lives, the approver rule's severity and fork/join run-time semantics. |
| 1.0.0 | 2026-09-23 | Accepted. |
