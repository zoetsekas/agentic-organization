---
id: ADR-0061
title: Humans reach agents through a channel bridge port, with Mattermost as the first adapter
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Product]
consulted: [Security Engineering]
informed: [All engineering]
scope: [runtime, targets, security, docs]
workstreams: [WS-013]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0021, ADR-0026, ADR-0035, ADR-0050, ADR-0053, ADR-0057]
tags: [integration, runtime]
---

# ADR-0061: Humans reach agents through a channel bridge port, with Mattermost as the first adapter

## Context
`ChannelClass` and `ChannelPurpose` — notify, approve, handoff, report, ask —
have been in the spec since ADR-0021, and Slack and Teams have been named in
the binding with **nothing behind either**: routing plans are computed against
an in-process stub (WS-013 M4). So an agent cannot actually reach the people
it is paired with, which makes approval routing a diagram.

`docs/CHAT_PLATFORMS.md` evaluated the self-hosted options. Two criteria
separated them, and they are the same two that decided the task-service pass:

1. **Can an agent hold its own principal** — post as itself, be @-mentioned and
   DM'd — without a paywall?
2. **Can a human approve by clicking**, with the click arriving as an
   authenticated callback?

The second is not a nicety. `ChannelPurpose.APPROVE` means a human decides.
A platform without interactive elements turns that into parsing chat text for
the word "approve", which is both unreliable and forgeable by anyone who can
type in the channel. Zulip's buttons send a chat message; Matrix has no
buttons, dialogs or forms in the spec; Rocket.Chat's UIKit needs a private app,
reserved for premium; Nextcloud Talk adds bots only from the command line.
Mattermost Team Edition has API-mintable bot accounts that "don't count as a
registered user", interactive dialogs with a `trigger_id`, and the only
vendor-owned MCP server of the set.

There is also a second, different answer: bind to an **OpenClaw gateway** and
let people stay in Slack or Teams. That is not a competing product so much as a
competing *shape* — and both shapes are wanted, at different times.

## Decision
**Build a channel bridge port. Mattermost is the first adapter; OpenClaw is the
second.**

1. **The port, not the product, is the commitment.** The spec keeps describing
   channel *classes* and purposes; `mattermost` and `openclaw` appear only in
   the binding (ADR-0002). Same reasoning as the task port (ADR-0057): the
   decision that will age is the product, so it goes behind a seam.
2. **An agent posts as itself or not at all.** A bridge that cannot mint a
   non-human principal is refused **at bind time**, with the reason. An agent
   speaking through a human's account destroys the accountability ADR-0026
   exists to create, and makes every message in the channel a lie about who
   said it.
3. **An approval is a click, not a word.** A bridge that cannot deliver an
   authenticated callback may not carry `ChannelPurpose.APPROVE` — it may
   still notify, report and ask. Refusing the *binding* for that purpose is
   the enforcement; degrading to text-matching is not an option, because a
   forged approval is worse than an unreachable one.
4. **A callback is verified and correlated.** The callback proves which human
   clicked and which request they answered. An approval for a request that has
   expired, already been answered, or belongs to another tenant is **refused**,
   not honoured — a stale approval is a decision nobody made today.
5. **Inbound is untrusted** (ADR-0035): a human's message is external text and
   crosses the input boundary like any other.
6. **One instance per tenant** (ADR-0050), on the tenant's own network, with
   the tenant's own bot tokens.

For the Docker alpha we run **Mattermost Team Edition**,
`mattermost/mattermost-team-edition:11.11.0` (digest verified against the
registry, not taken from documentation), against the Postgres this stack
already pins.

## Scope
The bridge port, its identity and approval requirements, and the first adapter.
It does not change `ChannelClass`, does not choose the *enterprise* answer —
which is the OpenClaw binding — and does not cover voice or video.

## Implementation
A `ChannelBridge` protocol — post, reply in thread, open an approval, resolve
an approval, identify the bot principal — plus declared capabilities the
binding checks at bind time. A Mattermost adapter over an injected transport
(no SDK dependency; nothing here can reach a server anyway). The local target
gains a per-tenant `mattermost` service and its database. The OpenClaw adapter
follows behind the same port.

## Timeline
Phase 5, WS-013 M4.

## Advantages
- Approval routing stops being a diagram: a human can actually be asked, and
  actually answer, with the answer attributable.
- Each agent appears in the channel as itself, which is what the pairing model
  claims on the org chart.
- A self-contained alpha: the whole path runs on one machine with no
  third-party account, so it is exercisable in CI.
- The product choice stays replaceable, and the enterprise answer — people
  keeping Slack and Teams — is a second adapter rather than a rewrite.

## Disadvantages
- **SSO is not in Team Edition.** SAML, Entra, Okta and OpenID are outside the
  free tier, and the Enterprise binary's free "Entry" mode carries a message-
  history cap. So in the alpha, humans authenticate to Mattermost with local
  accounts while the platform authenticates them by OIDC — **two identity
  systems for the same people**, and nothing reconciles them. That is the
  strongest argument against this decision and it is not a small one.
- **A chat server is a large dependency** to run per tenant: an application and
  a database each, which is heavier than everything else we made per-tenant.
- **We now have two answers to "where do humans talk"**, and somebody will run
  both and be surprised when an approval arrives in one of them.
- **The verification is thin.** Every vendor documentation site was blocked by
  this environment's egress proxy, so the capability claims come from in-tree
  repository docs and search extracts. The tags and digests are registry-
  verified; the *behaviour* is not first-hand, and the Rocket.Chat premium
  restriction that eliminated it is the weakest-sourced claim in the pass.
- **Nothing has been run.** No daemon, no server, no bot token.
- Rule 3 will be argued with: a deployment that only has a notify-capable
  bridge will want to approve through it anyway.

## Alternatives considered
- **OpenClaw gateway first** — the better long-run answer, since people keep
  the tools they use and its per-tenant footprint is one Node process. Second
  rather than first because a gateway needs a workspace to point at, and its
  Fleet mode is documented as experimental and free to change between releases.
- **Zulip, Matrix/Element, Rocket.Chat, Nextcloud Talk** — each fails criterion
  1 or 2, as recorded above and in `docs/CHAT_PLATFORMS.md`.
- **Our own chat UI in the designer** — no adoption problem and no dependency,
  but people do not watch a second inbox, so approvals would rot there.
- **Email** — universal and terrible for this: no identity for an agent, no
  authenticated click, and threading nobody can rely on.

## Verification
Tests assert no product name in the spec layer; that a bridge without a
non-human principal is refused at bind time; that a bridge without interactive
callbacks cannot bind `ChannelPurpose.APPROVE` while still binding notify; that
an approval callback is correlated and that a stale, duplicate or cross-tenant
callback is refused; that inbound text crosses the input guardrail; and that
the generated per-tenant stack carries a pinned Mattermost on the tenant's own
network. All against a fake server.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Channel bridge port; Mattermost Team Edition first, OpenClaw second; approval requires an authenticated click. |
