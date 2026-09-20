---
id: ADR-0021
title: Human contact is a declared channel contract with routing and escalation
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Product, Platform Architecture]
consulted: [Security Engineering, Platform SRE]
informed: [All engineering]
scope: [spec, compiler, targets, runtime]
workstreams: [WS-013]
supersedes: []
superseded_by: []
related: [ADR-0007, ADR-0017, ADR-0020, ADR-0022]
tags: [human-in-the-loop, channels]
---

# ADR-0021: Human contact is a declared channel contract with routing and escalation

## Context
Our model already had approval gates, and they were a boolean: a tool either
needs approval or it does not. That is not how approval works in an
organization. The questions that matter are *who* approves, *where* the request
lands, *how long* they have, and *what happens when nobody answers at 03:00*.
A gate with no answer to the last question is a system that stops.

The landscape is clear about the shape. OpenClaw treats the channel gateway as
the centre of the product. The 2026 enterprise pattern is a drafts channel
where an agent proposes, a human approves, and only then does it commit. An
Agent-to-Human protocol has been proposed specifically to make humans
participants rather than observers.

There is also a containment problem. If every agent holds a workspace
credential, a single compromised agent can post as the organization.

## Decision
A channel is a declared contract, abstract in the spec and bound per target:

* **`purposes`** — what the channel is for: notify, approve, handoff, report,
  ask. Routing a request to a channel that does not serve its purpose is an
  error, not a best guess.
* **`working_hours`** — when the people on it are actually contactable, with a
  timezone, days and holidays.
* **`response_sla_minutes`** — how long before the request is considered unmet.
* **`escalation`** — an ordered chain of who is tried next and when.
* **`out_of_hours`** — queue until the window opens, escalate immediately, or
  notify anyway. An incident channel chooses differently from an approvals one,
  and that choice belongs in the design.
* **`forbid_data_classes`** — content this surface may never carry.

Two structural rules follow. First, the **bridge holds the credential**: one
component per channel owns the workspace token, and agents talk to the bridge.
Second, enterprise surfaces only — Teams, Slack, mail, webhooks and an internal
bus. Consumer messengers are declined (see LANDSCAPE).

## Scope
All agent-to-human contact: approvals, notifications, handoffs, reports and
inbound questions. Agent-to-agent communication stays with the org model
(ADR-0006) and declared flows (ADR-0024).

## Implementation
`spec.model.ChannelSpec` gains the contract fields plus `WorkingHours` and
`EscalationStep`. `humans.py` answers the four questions — may we, when, how
long, then who — returning a `RoutingPlan` with a delivery time, an SLA expiry
and a timed escalation chain. `ChannelBinding` carries provider, workspace,
address and `bot_identity_ref` (a name, never a token). The local target emits
one bridge service per human channel; the Terraform targets emit a bridge
resource and the secret it reads. `messaging.channel_transport` records
delivery when no client is bound, so the whole path is exercisable without
workspace credentials.

## Timeline
Phase 2 with the trigger work — unattended runs are the main producer of
messages humans must answer.

## Advantages
- Approval becomes routable and answerable: who, where, by when, then whom.
- Out-of-hours behaviour is a design decision per channel, not an accident.
- The credential lives in one bridge, so a compromised agent cannot post as the
  organization.
- Channels can refuse content by data class, extending classification to egress.
- The same contract compiles to Slack, Teams or mail without redesign.

## Disadvantages
- Working hours and holidays are organizational data that goes stale; a wrong
  calendar silently delays approvals, and nothing detects that.
- SLAs the organization cannot meet are worse than no SLA: they generate
  escalation noise until people mute the channel, which is the failure mode
  this decision is trying to prevent.
- The bridge is a new single point of failure, and a busy one.
- Escalation chains encode named individuals, so they rot as people move roles.
- We cover a narrow set of surfaces by choice; teams on other tools will ask,
  and each addition widens the compliance surface.

## Alternatives considered
- **Keep approval as a boolean** — what we had; it has no answer for silence.
- **One notification service with per-message routing** — flexible, but routing
  logic ends up in prompts and becomes unreviewable.
- **Adopt consumer messengers too** — matches OpenClaw's reach, fails
  enterprise compliance for a population we do not serve.
- **Agents hold their own workspace tokens** — simpler, and hands every agent
  the ability to speak as the company.

## Verification
`tests/test_humans.py` covers availability (hours, days, holidays), queueing to
the next window, immediate out-of-hours escalation, purpose refusal, forbidden
data classes, SLA breach detection and channel selection. Compiler tests assert
the bridge holds the credential and the agent services do not.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Channels carry purpose, hours, SLA and escalation; bridges hold credentials. |
