---
id: ADR-0056
title: Workflow engines are pluggable, and an out-of-process engine is an egress event
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Security Engineering]
consulted: [Product]
informed: [All engineering]
scope: [spec, compiler, targets, runtime, security]
workstreams: [WS-006, WS-008]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0013, ADR-0019, ADR-0035, ADR-0050, ADR-0053]
tags: [runtime, security]
---

# ADR-0056: Workflow engines are pluggable, and an out-of-process engine is an egress event

## Context
An agent can already call a declarative workflow: the spec holds a
`WorkflowSpec` graph and the runtime executes it, with a LangGraph adapter for
real execution. That assumed one engine, in our own process.

Real organizations will not have one engine. A team that has built its flows in
**LangGraph** should not rewrite them; a team using **LangChain** chains, one
using **Langflow**'s visual builder, and one on **Gemini ADK** are all bringing
working assets. Forcing a single engine means either rewriting other people's
work or leaving it outside the governed path — where it still runs, just
without permissions, guardrails or a trace.

The engines are not the same shape, and that is the part that matters.
LangGraph, LangChain and Gemini ADK are **libraries**: they execute inside our
process, under our identity, inside the sandbox we already assigned. Langflow
is a **service**: flows live in its own server and are invoked over HTTP. That
is not a smaller version of the same thing. Handing a task to another process
means data leaves the agent's boundary, the engine holds its own credentials,
and its output arrives as untrusted input.

## Decision
**The engine is a binding choice; the invocation mode decides the governance.**

1. The spec keeps describing a workflow neutrally — what it is for, its shape,
   its interrupt points, its inputs — and never names an engine. `langflow`
   does not appear in `src/orgagents/spec/`, exactly as no model vendor does
   (ADR-0002).
2. The binding names the engine and how to reach it. Four are recognized:
   `langgraph`, `langchain`, `gemini_adk` and `langflow`, plus the existing
   `native` executor.
3. **In-process engines** (`langgraph`, `langchain`, `gemini_adk`) run under
   the calling agent's identity, permissions and sandbox. Nothing new is
   granted; a workflow cannot do what its caller could not.
4. **An out-of-process engine is an external endpoint** (`langflow`, and any
   future service engine), and inherits the endpoint rules already in force:
   - it is subject to the agent's **egress allowlist** — a sandbox with
     `network: none` cannot call it, and that is not a bug to work around;
   - what is sent is checked against the caller's **data classification**, so a
     private-scoped input does not silently leave the boundary;
   - its response crosses a **guardrail boundary** as tool output (ADR-0035),
     because it is a reply from a system we do not control;
   - it holds its **own credentials**, referenced as a `secret_ref`, never
     inherited from the agent;
   - it is **tenant-scoped** (ADR-0050): a tenant's engine is that tenant's,
     and a shared engine instance is a cross-tenant channel.
5. **The local Docker target ships Langflow** as the worked example of a
   service engine, so the out-of-process path is exercised rather than
   theorized.

## Scope
How a workflow is executed and what governs it. It does not change what a
workflow *is* in the spec, and it does not make the platform responsible for
authoring flows in anybody's builder.

## Implementation
A `WorkflowBinding` naming engine, mode and endpoint; an engine registry in the
runtime with the existing native executor as the default; an out-of-process
invoker that routes through the endpoint path already built for external agent
endpoints, so egress, classification, guardrails and approval are reused rather
than reimplemented. The local target gains a `langflow` service — pinned per
ADR-0053, on the tenant's own network, with its flows mounted and its own
secret — and the generated README states that a flow running there is outside
the agent's sandbox.

## Timeline
Phase 5, alongside the local target work in WS-006.

## Advantages
- Teams keep the flows they have already built, inside the governed path
  instead of beside it.
- The dangerous case is named as dangerous: calling another process is egress,
  and is treated like every other egress.
- One worked service engine in the local target means the out-of-process path
  is tested, not assumed.
- Adding an engine is a binding change, not a spec change.

## Disadvantages
- **Four engines is four failure modes**, and only the ones with a local
  container will be exercised here. The Gemini ADK and LangChain paths will be
  thin bindings needing credentials nobody has in this environment.
- **A service engine breaks the sandbox story.** Work done inside Langflow is
  not inside the agent's sandbox: its resource limits, its network posture and
  its filesystem are the engine's, not ours. The boundary we govern is the
  *call*, not the execution — and that is a weaker guarantee than the
  environment class implies.
- **Flows are authored outside the spec**, so a Langflow flow is a dependency
  the designer cannot see, validate or version. A flow can change under a
  system that was reviewed and approved.
- **Egress allowlisting is coarse.** Allowing the engine's host allows
  everything that host can be persuaded to do.
- **Untested here.** No daemon, no credentials: the Langflow service is
  generated and validated as configuration, never started.

## Alternatives considered
- **One engine only** — makes the platform a rewrite tax, and pushes other
  engines outside the governed path where they still run.
- **Treat a service engine as a library** — the convenient lie: it would
  inherit the agent's identity and skip egress and classification checks on a
  call that genuinely leaves the boundary.
- **Import flows into our own graph model** — a permanent translation problem
  against four moving formats, and it would still be running somebody else's
  semantics.
- **Sidecar the engine inside the agent's sandbox** — recovers the sandbox
  story and is worth revisiting; it means one engine instance per agent, which
  is heavy, and Langflow is not built for it.

## Verification
Tests assert no engine name appears in the spec layer; that an in-process
engine gains no permission its caller lacks; that a `network: none` sandbox
cannot reach a service engine; that a private-classified input is refused
before leaving the boundary; that a service engine's response passes through
the tool-output guardrail; that its credential is a `secret_ref` and not the
agent's; and that the generated local stack contains a pinned, tenant-scoped
Langflow service whose README states the sandbox caveat.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Engine is a binding choice; out-of-process engines are governed as endpoints; Langflow is the worked example in the local target. |
