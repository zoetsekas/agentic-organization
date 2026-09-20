---
id: ADR-0058
title: The agent-to-agent protocol is A2A, and the protocol is not the boundary
status: Accepted
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Security Engineering]
consulted: [Product]
informed: [All engineering]
scope: [spec, runtime, security]
workstreams: [WS-019]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0030, ADR-0035, ADR-0050, ADR-0056, ADR-0057]
tags: [runtime, security, integration]
---

# ADR-0058: The agent-to-agent protocol is A2A, and the protocol is not the boundary

## Context
ADR-0030 declared external agent endpoints — another team's agent, a partner's,
anyone's — as trust-classified boundaries with rules about what may be sent and
what an answer is worth. Those rules have been enforced at design time by the
validator and at call time by `runtime/endpoints.py` for a while. What has never
existed is the wire: no protocol is bound, so an endpoint is governed and
uncallable (WS-019 M4).

Writing our own protocol would be the wrong kind of work. The agentic workforce
reaches past one organization precisely so it can meet agents we did not build,
and a private protocol means nobody can meet us.

**A2A** (Agent2Agent) is the credible standard: an open protocol "enabling
communication and interoperability between opaque agentic applications", spec
version **1.0.0**, an open source project **under the Linux Foundation**,
contributed by Google, Apache-2.0. It defines JSON-RPC 2.0 over HTTP(S) with
gRPC and REST bindings that are functionally equivalent; discovery through an
**Agent Card** at `/.well-known/agent-card.json`; operations `SendMessage`,
`SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`,
`SubscribeToTask`, push-notification configuration and `GetExtendedAgentCard`;
a task lifecycle including `input-required` and `auth-required`; SSE streaming
and asynchronous push notifications; and security schemes covering API key,
HTTP auth, OAuth2 and OpenID Connect. There is a Python SDK (`a2a-sdk`).

The temptation that comes with adopting a standard is to treat it as the safety
model. It is not one. A2A describes how two agents talk; it says nothing about
whether *this* agent of ours should be talking to *that* one, with this
organization's data.

## Decision
**Bind A2A as the agent-to-agent protocol, and keep every existing endpoint
control in force around it.**

1. **The spec stays neutral.** An endpoint declares what it offers, its trust
   class and what data classes may be sent to it. `a2a` appears only in the
   binding, like a model provider or a channel (ADR-0002).
2. **A2A is a transport, not an exemption.** Every outbound call runs the
   existing endpoint path in order — tenant, egress allowlist, data
   classification, credential, approval, then transport, then the tool-output
   guardrail. A `network: none` sandbox cannot reach an A2A peer, and that is
   correct.
3. **An Agent Card is untrusted data, and fetching one is an egress event** —
   with one narrow waiver, added in v1.1.0 after the first implementation
   showed the rule as written was unworkable. Routing the card fetch through
   the full endpoint path meant an endpoint with no `secret_ref` could not
   fetch even a *public* card, and A2A public cards are unauthenticated by
   design — so no public peer could be discovered at all. The waiver covers
   **one check, on one body-less read**: the credential requirement is waived
   for discovery when the endpoint declares no credential of its own. Tenant
   scoping, the egress allowlist, approval and the tool-output guardrail all
   still run, the card is still untrusted data, a declared credential is still
   used, and the caller's own credential is still never borrowed. A call with a
   body is not discovery however it is labelled.
   The card is served by the remote party and describes itself: its skills,
   its security schemes, its extensions. Those are **claims**, not instructions
   and not permissions. Fetching a card goes through the allowlist like any
   other call, and nothing in a card may widen what we send, raise an
   endpoint's trust class, or select a credential we would not otherwise use.
4. **Trust classes survive the protocol.** An answer from anything but
   `internal` is untrusted input and never instructions (ADR-0030). A2A message
   parts and artifacts cross the tool-output guardrail, whatever the remote
   agent's card says about itself.
5. **`input-required` and `auth-required` route to humans, not around them.** A
   remote task asking for more input or a credential is an approval and routing
   question (ADR-0021), not something the runtime satisfies on its own
   initiative. A credential is never minted or forwarded to satisfy a remote
   prompt.
6. **Inbound is a separate decision, and the default is none.** Serving our own
   Agent Card — exposing our agents to other organizations — is WS-019 M5. No
   agent is exposed unless a deliberate decision exposes it, and a tenant's
   endpoint is that tenant's (ADR-0050).

Mapping: an A2A **Task** corresponds to one of our sessions, so a remote
collaboration is traceable the same way local work is.

## Scope
Outbound calls to external agents, and the discovery that precedes them. It
does not decide inbound exposure (M5), does not change the trust classes, and
does not make A2A the transport for delegation *inside* a system — internal
delegation stays a direct call under the org chart.

## Implementation
A protocol binding naming `a2a` and its transport variant; an A2A client over
the existing `call_endpoint` path, so governance is inherited rather than
re-implemented; Agent Card fetch and cache with the card treated as data;
mapping of A2A task states onto our session states, with `input-required` and
`auth-required` raised through human routing. The SDK is optional: it is not
installed in the development environment, so the client is written against the
documented JSON-RPC binding with the SDK as an optional accelerator.

## Timeline
Phase 5, WS-019 M4.

## Advantages
- Our agents can meet agents we did not build, over a protocol with a
  standards body behind it rather than one we invented.
- The governance that already exists keeps applying, because the protocol
  enters as a transport under it.
- One traceability model: a remote A2A task is a session, like everything else.
- Choosing the Linux Foundation's protocol rather than a vendor's reduces the
  chance of the wire changing under us for commercial reasons.

## Disadvantages
- **A waiver is a waiver.** v1.1.0 carves an exception into a check, and an
  exception that grows is worse than the flaw it fixed. It is held narrow by
  tests rather than by good intentions, and that is the only thing holding it.
- **A standard is a large surface.** Streaming, push notifications, extended
  cards, extensions and three functionally-equivalent bindings are a lot of
  protocol, and we will implement a subset — so "we support A2A" will be truer
  in the README than in the code unless we say which subset.
- **Agent Cards invite exactly the wrong reflex.** A card advertising skills
  and security schemes reads like configuration, and the pressure to let it
  select a credential or widen a data class will be constant. Rule 3 is a rule
  because the convenient behaviour is the unsafe one.
- **Untrusted-by-default makes remote agents less useful.** An answer that may
  not be treated as instructions cannot drive our agent's next step
  automatically, which is precisely what people will want it to do.
- **Push notifications mean inbound HTTP** to receive task updates, which is a
  listening surface in a platform that currently has none facing outward — and
  it arrives before the inbound decision in M5 is made.
- **Nothing here is verified against a real peer.** No A2A server exists in
  this environment, the SDK is not installed, and `a2a-protocol.org` is blocked
  by the egress proxy — the spec was read from the repository. The
  implementation will be tested against a fake peer.
- **Version 1.0.0 is recent.** A protocol at its first major version will move,
  and our subset will move with it.

## Alternatives considered
- **Our own HTTP protocol** — simpler to build, and nobody outside can speak
  it, which defeats the purpose of reaching past one organization.
- **MCP for agent-to-agent** — MCP connects an agent to tools and context, not
  two opaque agents as peers; using it here would flatten a peer into a tool
  and lose the task lifecycle.
- **Wait for consolidation** — the endpoint model has been governed and
  uncallable for several phases; waiting longer costs a capability and gains
  little now that a Linux Foundation standard exists.
- **Adopt A2A's security model as our boundary** — the error this ADR exists to
  prevent. A2A describes how agents talk, not whether these two should.

## Verification
Tests assert that no protocol name appears in the spec layer; that an A2A call
from a `network: none` sandbox is refused before transport; that a host off the
allowlist is refused; that a private-classified input is refused before egress;
that a fetched Agent Card cannot raise trust, widen data classes or select a
credential; that a non-`internal` response crosses the tool-output guardrail;
that `input-required` and `auth-required` raise human routing rather than being
answered automatically; that a remote task maps to exactly one session; and
that a card fetch is itself subject to the allowlist. All against a fake peer.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Waived the credential check for body-less agent-card discovery: as written, the rule made every public peer undiscoverable. All other checks still run. |
| 1.0.0 | 2026-09-20 | Accepted. A2A bound as the agent-to-agent protocol, entering as a transport beneath the existing endpoint governance. |
