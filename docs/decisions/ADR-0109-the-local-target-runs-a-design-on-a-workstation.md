---
id: ADR-0109
title: The local target runs a design on a workstation, with a stub model and mock systems, and its barriers are networks
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Runtime, Security]
informed: [All engineering]
scope: [targets, runtime, security]
workstreams: [WS-006, WS-008]
supersedes: []
superseded_by: []
related: [ADR-0011, ADR-0015, ADR-0059, ADR-0061, ADR-0065, ADR-0067, ADR-0069, ADR-0071, ADR-0085, ADR-0092]
tags: [local, docker, compose, stub-model, mocks, approvals, separation-of-duties]
---

# ADR-0109: The local target runs a design on a workstation, with a stub model and mock systems, and its barriers are networks

## Context
The local target (ADR-0011) generated a Compose stack that had been parsed and
never started. Starting AYC's on Docker Desktop found why it could not run, and
found that several controls the design depends on held on paper only:

- every agent container ran `orgagents worker <agent>`, and the CLI had no
  `worker` command;
- the runtime image installed `orgagents` from a package index, where it is not
  published;
- the artifact workspace was started with MinIO's flags on a SeaweedFS image,
  and exited;
- every backing-system service sat on `control`, which every agent joins, so
  any agent could route to any system — a buyer to the ledger;
- a server with five capabilities was written five times and kept the last
  one's label and credential;
- a capability bound through the servers catalog (ADR-0085) reached the runtime
  with no `server_name` or `url`, because the loader read the binding as
  written and never merged the catalog in;
- a mounted MCP server handed an agent *every* tool on it, so a server hosting
  both sides of a separation handed each side the other's, and the mandate
  check — keyed on the capability's name — never saw the qualified tool name a
  framework actually calls;
- deep agents received our tools with a `**kwargs` signature, from which
  LangChain builds an empty schema and drops every argument;
- the approval gate said "human approval required" and nothing could ever
  satisfy it, so a supervised capability could never be used at all.

A workstation also lacks the two things a design's real deployment has: a model
behind a key, and the business systems. Running a design locally needs a
stand-in for each that changes nothing else.

## Decision
1. **`orgagents worker <agent>` exists.** One agent per process: it loads the
   compiled IR into a platform of its own, mounts only that agent's backing
   systems with that agent's credentials, runs only that agent, and answers
   `/healthz`, `/agent`, `/run` and `/approve` on HTTP.
2. **The stub is a model, not a runtime.** `provider: stub` in a binding is a
   deterministic LangChain chat model (`orgagents.runtime.stub_model`). It
   makes the tool calls a message spells out (`call <tool> {json}`) and answers
   in canned words. The runtime the binding names — deep agents for AYC — runs
   unchanged, so the loop, the tool node, the harness, the mandate check, the
   approval gate and the MCP transport are the ones a real model would drive.
   No provider is called and no key is needed.
3. **Each backing system is on a network of its own.** The local target puts
   `mcp-<server>` on an internal `srv-<server>` network and nowhere else, and
   attaches an agent to `srv-<server>` exactly when it holds a capability bound
   to that server. Reach between placements is standing structure (ADR-0069);
   reach into a system is a grant, and only a grant. One service per server,
   carrying every capability's credential name.
4. **A capability mounts only its own tools, under its own credential.** A
   capability binding's `options.tools` narrows the mount; the qualified tool
   (`server__tool`) carries the capability's decision so the mandate check
   binds the call a framework makes; `dsn_secret_ref` on an MCP capability is
   the credential that call presents, so one server can tell two hands apart —
   the phase gate's "same server under different credentials" (ADR-0071), made
   real. The mock systems enforce it.
5. **An approval releases one call.** `ApprovalGrants` records a named
   approver's release of one tool call *with those arguments*; it is spent by
   that call, expires, and is consulted after the mandate and its conditions,
   so an approval never widens a mandate. Only a person the design names as the
   agent's approver may give one (the worker checks).
6. **The generated stack builds and says when it is ready.** A wheel in
   `wheels/` is installed before the index (the Dockerfiles copy `wheel[s]`, a
   pattern, so a checkout without the folder still builds); SeaweedFS gets its
   own flags; `state`, `nats` and `artifacts` carry health checks; an
   `internal` channel gets no bridge, having no workspace to bridge to.
7. **What is not built yet is parked, not faked.** `orgagents memory serve`
   does not exist, and `orgagents scheduler` runs a trigger's agent in its own
   process — outside that agent's container, networks and credentials. The AYC
   workstation overlay parks both behind a profile rather than leave them
   crash-looping or let the scheduler bypass rule 3.

## Scope
Binds the `local` target, the runtime's worker, the MCP HTTP client and the
harness's approval gate. It does not cover cross-container delegation (a
delegation made inside a worker is refused at the missing mount; carrying it
over NATS is ADR-0059's), the scheduler's and the memory service's commands,
or egress allow-lists, which Compose cannot express (an `allowlist` agent is
still on `egress`).

## Implementation
- `src/orgagents/runtime/worker.py`, `orgagents worker` in `cli.py`.
- `src/orgagents/runtime/stub_model.py`; `DeepAgentsAdapter`/`LangGraphAdapter`
  resolve `provider: stub` through `chat_model_for`, build tools with
  `langchain_tools` (open schemas from MCP `inputSchema` or the wrapped
  signature) and return `langchain_tool_calls`.
- `HttpMCPClient` and `MCPRegistry.mount_http` in `harness/mcp.py`
  (streamable HTTP, JSON-RPC, standard library, per-tool credential, the
  agent's id on every request).
- `ApprovalGrants` in `harness/builder.py`; `guarded` keeps the wrapped tool on
  `guarded_fn` (not `__wrapped__`, which `typing` follows into an MCP proxy
  with no globals).
- Loader: catalog references resolved, `options.tools` → `allowed_tools`,
  qualified `ToolBinding`s carrying the decision, credential → `secret_refs`.
- Local target: `_server_services`, per-server networks, per-agent credential
  names, `wheels/`, health checks, SeaweedFS flags, no bridge for `internal`.
- AYC: `examples/ayc/ayc.local.binding.yaml`, `mocks/` (five systems, one
  server, seeded), `chat/` (pick an agent and talk to it),
  `generated/local/overlays/20-ayc-workstation.yaml`, `local_stack.py`,
  `end_to_end_local.py`, Make targets `ayc-*`.

## Timeline
Lands with the first run of a generated stack on Docker Desktop.

## Advantages
- A design runs end to end on a laptop with no key and no real system touched,
  on the runtime its binding names.
- Separations are routing, credentials and application checks — three
  independent refusals — not only a gate verdict.
- Supervised capabilities are usable, under a named approver, one call at a
  time.

## Disadvantages
- Twelve agent containers are twelve copies of the runtime image's process;
  on a workstation that is memory, not isolation theatre, but it is memory.
- The stub model is scripted by the prompt: it proves the controls, not the
  judgement. Anything that depends on a model deciding *which* call to make is
  untested here.
- Mocks are ours. They implement the enforcement the spec names, which is the
  point, and they can disagree with the real Fishbowl or ledger.
- The telemetry collector image is distroless and has no health check.

## Alternatives considered
- **A stub runtime adapter** (an `echo` that acts) — rejected: it would replace
  deep agents as well as the model, so the local stack would stop exercising
  the runtime it is supposed to exercise.
- **Keep servers on `control` and rely on the harness** — rejected: a control
  enforced by one layer of our own code is one bug from not existing.
- **Run the agents in one container** (`run_local.py`) — kept as the no-Docker
  fallback, but it collapses every placement into one process.

## Verification
`tests/test_ayc_local_stack.py`: the binding passes the gate; each agent is on
exactly the system networks it holds capabilities on; each carries only its own
credentials; every agent's command exists; the committed stack is current;
Docker parses it with the overlay; the stub model drives deep agents through
the harness to a mock MCP server, stops for approval, is released by one
approval, is refused a tool it was never granted, and a system refuses a
credential that does not cover the tool. `examples/ayc/end_to_end_local.py`
checks the same against the running stack.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted with the first workstation run of AYC. |
