---
id: WS-013
title: Human channels, approval routing and declared interaction flows
status: Active
version: 1.2.0
date: 2026-09-20
updated: 2026-09-20
owner: Product
contributors: [Platform Architecture, Security Engineering]
scope: [spec, compiler, targets, runtime]
decisions: [ADR-0021, ADR-0024, ADR-0059, ADR-0061]
depends_on: [WS-005, WS-011]
tags: [human-in-the-loop, channels]
---

# WS-013: Human channels, approval routing and declared interaction flows

## Objective
Make human involvement designable: approvals that reach a named person on a
real surface within a stated time, escalation when they do not answer, and
lateral agent-to-agent contact that is declared rather than assumed.

## Deliverables
- Channel contract in the spec: purposes, working hours, response SLA,
  escalation chain, out-of-hours policy, forbidden data classes.
- `humans.py`: availability, routing plans, SLA expiry, escalation timing,
  channel selection.
- `ChannelBinding` with provider, workspace, address and `bot_identity_ref`.
- Local target channel-bridge services; Terraform channel resources and their
  secrets.
- `channel_transport` for Slack, Teams, mail, webhook and the internal bus,
  recording delivery when no client is bound.
- Typed directional `InteractionFlow`s, resolved per agent in the IR.
- `channels/`: a `ChannelBridge` port (identify, post, reply in thread, open an
  approval, resolve one) with declared capabilities checked at bind time — a
  bridge that cannot mint a non-human principal does not bind, and one that
  cannot deliver an authenticated callback may not carry `approve` (ADR-0061).
  An approval ledger correlates each click to its request and refuses a stale,
  duplicate, cross-tenant or unexpected-approver callback. A Mattermost adapter
  over an injected transport, a conformance suite in `src/` for the next
  adapter, and a per-tenant `mattermost` service in the local target on the
  Postgres this stack already pins. Nothing has been run against a real chat
  server: there is no daemon here and every vendor documentation site is
  blocked by the egress proxy, so the wire shapes sit behind
  `TODO(mattermost-wire)`.
- `bus.py`: a NATS/JetStream backend behind the same `MessageBus` transport
  seam (ADR-0059). Subjects are tenant-prefixed, `requires_response` maps onto
  request/reply rather than a new concept, durability is declared per channel,
  and an addressed message is refused when the org chart refuses it — the bus
  is a transport, not an authorization boundary. The client is injected, so
  `nats-py` is not a dependency.

## Scope
In: agent-to-human contact and declared agent-to-agent lateral contact. Out:
real Slack and Teams app implementations (bridge clients), and any consumer
messaging surface — explicitly declined in LANDSCAPE.

## Approach
Treat approval as routing, not as a boolean: decide *may we*, *when*, *how
long*, *then who*, and make each answer part of the design. Keep the workspace
credential in one bridge per channel so a compromised agent cannot post as the
organization. Type lateral flows so that permitting a question does not permit
an instruction.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Channel contract and routing engine | Phase 2 | Done |
| M2 Bindings and target generation | Phase 2 | Done |
| M3 Typed interaction flows in spec and IR | Phase 2 | Done |
| M4 Channel bridge port and the Mattermost adapter | Phase 5 | Done |
| M5 Runtime approval flow wired through routing plans | Phase 3 | In progress |

## Dependencies
WS-005 for the IR; WS-012, whose triggered runs generate most of the messages
humans must answer.

## Advantages
- Approvals have an owner, a surface, a deadline and a fallback.
- Out-of-hours behaviour is chosen per channel rather than by accident.
- One bridge per channel contains the workspace credential.
- Channels can refuse content by data class, extending classification to egress.

## Disadvantages
- Working hours, holidays and escalation names are organizational data that
  rots, and nothing detects a stale calendar or a person who changed role.
- An SLA the organization cannot meet generates noise until people mute the
  channel — the exact failure this workstream exists to prevent.
- Bridges are new single points of failure on the human path.
- Delivery to Slack and Teams is still *recorded*, not performed: the first
  real adapter is Mattermost, and an OpenClaw gateway is what would put a
  message in somebody's existing workspace.
- The Mattermost adapter is unverified against a server. The riskiest guess is
  that an agent-initiated approval cannot obtain a `trigger_id`, so it is posted
  as buttons rather than a dialog; if that is wrong the shape changes.
- Team Edition has no SSO, so humans have local chat accounts while the
  platform knows them by OIDC, and nothing reconciles the two (ADR-0061).

## Exit criteria
- An approval requested out of hours queues, escalates on schedule and is
  visible as a breach when unmet. ✔ (routing; delivery through a bridge that
  has never been pointed at a running server)
- No agent service holds a workspace credential. ✔
- An approval is a click that proves who made it, or it is refused. ✔ (against a
  fake server)
- Consult flows never confer delegation. ✔
- Subscribing to a subject is not permission to be reached. ✔ (tested against a
  fake client; no broker has been run here)

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.2.0 | 2026-09-20 | M4 done: the channel bridge port, bind-time identity and approval-callback refusals, correlated approvals with stale/duplicate/cross-tenant callbacks refused, the Mattermost adapter over an injected transport, a conformance suite for the next adapter, and a per-tenant chat surface in the local target (ADR-0061). Nothing verified against a real server. |
| 1.1.0 | 2026-09-20 | Added the NATS/JetStream bus backend behind the existing transport seam: tenant-prefixed subjects, request/reply from `requires_response`, per-channel durability, and delegation checks that the wire does not bypass (ADR-0059). |
| 1.0.0 | 2026-09-20 | Opened. Contract, routing, bindings, flows landed; real bridges pending. |
