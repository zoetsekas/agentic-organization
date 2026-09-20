---
id: ADR-0030
title: External agents are declared endpoints with a trust boundary
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering, Platform Architecture]
consulted: [Compliance, Product]
informed: [All engineering]
scope: [spec, compiler, targets, security]
workstreams: [WS-019]
supersedes: []
superseded_by: []
related: [ADR-0010, ADR-0017, ADR-0029, ADR-0015]
tags: [integration, security]
---

# ADR-0030: External agents are declared endpoints with a trust boundary

## Context
The agentic workforce does not stop at one organization. A finance agent may
want a contracted research desk's answer; a platform agent may call another
team's agent in a different cloud. Registries are already syncing agents across
platforms, and agent-to-agent protocols exist to make the calls.

Two risks arrive together, and they are not symmetric. Outbound: whatever we
send crosses a boundary we do not control, so data classification must gate it.
Inbound: whatever comes back was written by a system we do not run, and if an
agent treats that text as instructions, the external party is now steering our
organization. That is prompt injection with a contract attached.

## Decision
An external agent is a declared **endpoint** with an explicit trust level —
`internal` (another team here), `partner` (contracted), or `external`
(anything else) — and three enforced rules:

1. **Outbound is classified.** An endpoint declares the data classes it may be
   sent. A non-`internal` endpoint may be sent **public data only**; anything
   else fails validation as exfiltration.
2. **Inbound is data, never instruction.** Any non-`internal` endpoint must
   declare `treat_output_as_data`, and the composed prompt says so in as many
   words. An endpoint asking to be trusted as an instruction source fails
   validation.
3. **External calls are gated.** An `external` endpoint callable without
   approval is a warning in development and an error in production.

An endpoint's credential is a reference that lands on the **calling agent's**
identity (ADR-0015), so who may reach outside is visible in the registry and in
the generated IAM. Endpoints are reached through wrapper tools like anything
else (ADR-0029).

## Scope
Calls from our agents to agents we do not run. It does not cover inbound calls
*into* our system — exposing our agents as endpoints to others is a separate
decision we have not taken.

## Implementation
`spec.model.AgentEndpoint` with `EndpointTrust`; agent `endpoints` lists;
validator rules for exfiltration, output trust and ungated external calls; IR
carries endpoints onto the agent, folds their approval into the gated set and
their secret onto the identity; the composed prompt states the data rule; an
`agent_endpoint` neutral resource maps to a per-provider egress construct, and
the registry lists every endpoint with its trust, what it may be sent and
whether it is gated.

## Timeline
Phase 2 for declaration and enforcement. Protocol bindings — the actual wire
format for reaching another organization's agent — are phase 3, and the
abstraction is deliberately protocol-neutral until then.

## Advantages
- What may leave the organization is a classified, reviewable list.
- Untrusted output is marked as data in the prompt, not left to the model.
- Who may call outside is visible in the registry and enforced in IAM.
- Trust levels let internal federation stay cheap while external stays gated.

## Disadvantages
- "Treat output as data" is an instruction to a model, and models are
  imperfectly obedient; declaring it is necessary and not sufficient.
- Public-data-only outbound is strict enough that real partner integrations
  will want exceptions, and each exception is a negotiation.
- We model outbound only; being called *by* external agents is unaddressed and
  is the larger attack surface.
- No protocol binding yet, so an endpoint is currently a declaration the
  targets describe rather than a connection anything makes.
- Trust is declared by whoever writes the spec, and nothing verifies that a
  `partner` really is one.

## Alternatives considered
- **Treat external agents as ordinary capabilities** — loses the trust
  boundary, and the exfiltration and injection rules have nowhere to attach.
- **Forbid external agents entirely** — safe, and it declines the premise of an
  agentic workforce that crosses organizations.
- **Adopt a specific agent-to-agent protocol now** — premature; the protocols
  are moving, and the governance is the part that must be stable.

## Verification
Tests assert that a non-public class on a non-internal endpoint is rejected as
exfiltration, that disabling `treat_output_as_data` is rejected, that an
endpoint's approval reaches the agent's gated list, and that its secret lands
on the calling identity and no other.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Trust-classified endpoints; public-only outbound; output is data. |
