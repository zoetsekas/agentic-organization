---
id: ADR-0086
title: A LangGraph Platform target, and runtime/target composition
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Security Engineering]
informed: [All engineering]
scope: [compiler, runtime]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0005, ADR-0050, ADR-0067, ADR-0073]
tags: [target, langgraph, langsmith, deepagents, deployment]
---

# ADR-0086: A LangGraph Platform target, and runtime/target composition

## Context
Two axes had quietly become one in people's heads. A binding names a **runtime
adapter** (`langchain_deepagents`, `openai_agents_sdk`, `langgraph_native`, …) —
*how* an agent's loop runs. A compile names a **target** (`local`,
`terraform:gcp`, `maf`, `adk`) — *where* the design is deployed and in whose
terms. These are independent: "langchain deep agents, on Google Cloud" is the
`langchain_deepagents` runtime under `terraform:gcp`, and it already worked.
"langchain deep agents, on the hosted LangChain platform" is the same runtime
under a target that did not exist — so the mix looked impossible when only one
of its two halves was missing.

The missing half is a platform target (ADR-0005) whose deployable unit is a
**LangGraph graph** served by LangGraph Platform, with tracing and evaluation
in LangSmith. The `langchain_deepagents` runtime already builds exactly such a
graph (`deepagents.create_deep_agent`), so the target packages the same shape
for the hosted platform instead of for our own cloud.

## Decision
Add a `langgraph` platform target. It emits a deepagents graph package: one
`create_deep_agent` graph module per agent, a `graphs/__init__.py` naming the
org `root`, a `langgraph.json` deployment manifest (an `org` graph plus one per
agent), a `.env` template for the LangSmith and model credentials,
`requirements.txt`, a `README.md`, and — like every platform target — a
`CONFORMANCE.md` (ADR-0073).

deepagents is the most faithful platform target so far, and the report says so
honestly: `subagents` carry the delegation hierarchy (one level, as deepagents
spawns them) and `interrupt_on` is a **real** human-in-the-loop gate on the
tools the design marks for approval — more than MAF or ADK carry. But the
authority model is still ours, not the framework's: no role, permission,
mandate, separation, autonomy posture or data-class egress rule survives, and
while the graph pauses for a human, *which* human — the paired approver with the
mandate — is this platform's model, not deepagents' (ADR-0067 rule 5).

We also state the composition plainly, in code and in the AYC example: the
runtime is a binding choice and the destination is a target, and the two
compose. AYC's binding carries the same `langchain_deepagents` runtime under
`terraform:gcp` (deepagents on its own Google Cloud) and under `langgraph`
(deepagents on the hosted LangChain platform), from one spec.

## Scope
The compiler's target plugin boundary (ADR-0005) and the binding's per-target
runtime choice. No change to the spec or the IR: the target reads the same IR
every other target reads.

## Implementation
`src/orgagents/compiler/targets/langgraph.py` (`LangGraphPlatformTarget`,
registered in `compiler/base.py`). It emits `graphs/<id>.py`
(`create_deep_agent`, with `subagents` from `delegates_to` and `interrupt_on`
from the design's approval-gated tools), `graphs/__init__.py` (the org `root`),
`langgraph.json`, `.env.example`, `requirements.txt`, `README.md` and
`CONFORMANCE.md`. The target never imports `orgagents.spec` (ADR-0005). AYC's
binding gains a `langgraph` section alongside its `terraform:gcp` one, both on
the `langchain_deepagents` runtime.

## Timeline
Accepted 2026-09-22, shipped with the AYC example generating all three targets.

## Advantages
- A design deploys to the hosted LangChain platform, held to the same
  conformance honesty as the other platform targets (ADR-0073).
- The runtime/target distinction is demonstrated, not just asserted: one spec,
  one `langchain_deepagents` runtime, two destinations (`terraform:gcp` and
  `langgraph`).
- deepagents carries more of a design than MAF or ADK — real `subagents` and a
  genuine `interrupt_on` gate — so less is lost, and the report says exactly how
  much.

## Disadvantages
- deepagents `subagents` nest one level, so a deep org tree is flattened to each
  agent's direct reports; the shape is approximate.
- The interrupt gate pauses for *a* human but cannot route to *the* paired
  approver, so a reader could over-read what the hosted platform enforces —
  which is why the conformance page is mandatory, not optional.
- A second platform target that emits Python graphs is more surface to keep
  runnable (a syntax test guards it).

## Alternatives considered
- **Only document that `langchain_deepagents` + `terraform:gcp` already exists.**
  Rejected: it answers "deepagents on my cloud" but not "deepagents on the
  hosted platform", which was half the ask.
- **Fold LangGraph output into the `local` target.** Rejected: `local` is an
  infrastructure target that runs our harness; a hosted-platform package is a
  platform target and belongs on that seam (ADR-0005).

## Verification
`tests/test_langgraph_target.py` compiles the target and asserts every emitted
Python file parses, `subagents` carry delegation, a gated tool becomes an
`interrupt_on` entry, the manifest is valid and names the org root, the `.env`
wires LangSmith, and the conformance page names roles, mandates, separation,
autonomy and data classes as not carried. `tests/test_compiler.py` pins the
registry to include `langgraph`. AYC compiles to all three targets from one
binding.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. A `langgraph` platform target emitting a deepagents graph package for LangGraph Platform / LangSmith; the runtime/target composition stated in code and in the AYC example. |
