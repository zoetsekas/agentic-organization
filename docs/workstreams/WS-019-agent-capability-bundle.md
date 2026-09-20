---
id: WS-019
title: Agent capability bundle — skills, plugins, tools and external endpoints
status: Active
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Security Engineering, Developer Experience]
scope: [spec, compiler, targets, runtime, security]
decisions: [ADR-0029, ADR-0030, ADR-0058]
depends_on: [WS-005, WS-004]
tags: [capability, integration]
---

# WS-019: Agent capability bundle — skills, plugins, tools and external endpoints

## Objective
Make everything an agent is equipped with visible in the artifact we review:
the instructions it carries, the bundles installed on it, the named tools it
sees, and the agents outside this system it may call.

## Deliverables
- `SkillSpec`, `PluginSpec`, `ToolSpec` and per-agent lists.
- Effective resolution in the IR — direct plus plugin-provided — with skills
  folded into the composed prompt with provenance.
- Approval inheritance: a wrapper cannot remove its target's gate.
- `AgentEndpoint` with trust levels, classified outbound and data-not-
  instruction inbound; endpoint secrets on the calling identity.
- Validation for unheld capabilities, unmet plugin requirements, exfiltration,
  output trust and ungated external calls.
- Registry tables for tools, skills, plugins and endpoints.

## Scope
In: per-agent equipment and outbound calls to external agents. Out: exposing
our agents as endpoints to other organizations, marketplace distribution (the
catalog), and any specific agent-to-agent wire protocol.

## Approach
Keep the three concepts distinct so a reviewer can tell which lines widen
access: capabilities and endpoints grant, skills instruct, tools only wrap.
Enforce the trust boundary in the validator rather than in prose, and make the
prompt say plainly that external answers are data to check.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Skills, plugins, tools in the spec and IR | Phase 2 | Done |
| M2 Endpoints with trust rules and identity secrets | Phase 2 | Done |
| M3 Registry and target resources | Phase 2 | Done |
| M4 Agent-to-agent protocol binding | Phase 3 | Done |
| M5 Inbound endpoints — exposing our agents | Phase 3 | Not started |

## Dependencies
WS-005 for the IR; WS-004 for the identities endpoint secrets attach to.

## Advantages
- A reviewer can see at a glance which lines widen the blast radius.
- Plugins fail at validation rather than at runtime.
- Outbound data is classified and gated; inbound is marked as untrusted.

## Disadvantages
- **The bound protocol is a subset**: A2A's JSON-RPC binding with
  `SendMessage`, `GetTask`, `CancelTask` and Agent Card discovery only —
  streaming, push notifications, `ListTasks`, `SubscribeToTask` and extended
  cards are not implemented, and nothing is verified against a real peer.
- "Treat output as data" is an instruction to a model, and models are
  imperfectly obedient — necessary, not sufficient.
- Public-only outbound will be too strict for real partner integrations, and
  each exception becomes a negotiation.
- Inbound — other organizations calling *our* agents — is the larger attack
  surface and is entirely unaddressed (M5).
- Plugin-provided tools appear on agents that never declared them, making the
  effective toolset less obvious in the spec than in the registry.

## Exit criteria
- Every line that widens access is a capability or an endpoint, never a tool. ✔
- An external endpoint is actually callable over a bound protocol (M4). ✔
- A decision exists for inbound endpoints (M5).

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | M4 done: A2A bound as a transport beneath `runtime/endpoints`, JSON-RPC subset with card discovery, cards treated as untrusted claims, `input-required`/`auth-required` routed to humans (ADR-0058). |
| 1.0.0 | 2026-09-20 | Opened. Skills, plugins, tools and outbound endpoints landed. |
