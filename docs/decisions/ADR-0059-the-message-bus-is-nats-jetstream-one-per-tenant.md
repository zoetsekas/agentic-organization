---
id: ADR-0059
title: The message bus is NATS with JetStream, one per tenant
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Product]
informed: [All engineering]
scope: [targets, runtime, security]
workstreams: [WS-006, WS-013, WS-028]
supersedes: []
superseded_by: []
related: [ADR-0011, ADR-0050, ADR-0053, ADR-0058]
tags: [deployment, runtime]
---

# ADR-0059: The message bus is NATS with JetStream, one per tenant

## Context
`ChannelKind.INTERNAL_BUS` is how one agent leaves another a message without
tasking it — the lower-privilege path that the delegation refusal explicitly
points at ("route through a shared manager **or an enterprise channel**").
Today it is in-process: messages live in the same Python process as the agents,
so nothing survives a restart, nothing crosses a container, and the generated
Docker stack has no bus in it at all.

That is the gap. The local target runs each agent as its own service, and those
services currently have no way to reach each other asynchronously.

The choice is constrained by a decision already made. ADR-0050 treats the
tenant boundary as absolute, and ADR-0053 rule 3 rejected shared-instance-with-
a-tenant-column for data stores for the same reason it applies here: a broker
shared across tenants is one ACL mistake from a cross-tenant leak. So the
broker is **per tenant**, and footprint stops being a detail — it is multiplied
by the number of tenants on a host.

That reframes the candidates. Kafka and Pulsar are excellent at what they are
for, and both are wrong here: a Kafka broker (even KRaft, without ZooKeeper) or
a Pulsar broker with BookKeeper is hundreds of megabytes of resident memory
before a single message, per tenant, on a developer's laptop. Pulsar's native
tenant/namespace/topic hierarchy is the best multi-tenancy model of any
candidate — and our own rule means we do not get to use it, because we run one
instance per tenant anyway.

## Decision
**NATS with JetStream, one instance per tenant.**

1. **Per tenant, on the tenant's own network, with its own volume** — like
   Postgres and the artifact store (ADR-0053 rule 3). Subjects are
   tenant-prefixed too, so a misconfiguration that crossed instances would
   still not collide.
2. **Request/reply is built in**, which is what `requires_response` on a
   message already means. We do not have to build correlation on top of
   fire-and-forget.
3. **JetStream provides durability** where a message must outlive a restart.
   Plain core NATS is at-most-once; a channel that needs delivery guarantees
   declares it, rather than everything paying for persistence.
4. **The bus is a transport, not an authorization boundary.** A message from
   one agent to another passes the same `can_delegate`-style checks it passes
   today — subscribing to a subject is not permission to be reached. This is
   ADR-0058's rule restated: the wire is not the boundary.
5. **The fabric gets its own instance** for control-plane events, listed as a
   common service (WS-030 M1) with what crosses the boundary stated. It is not
   the tenants' bus and carries no tenant payloads.

Pinned: `nats:2.15.0-alpine`, tag and digest verified against the registry.

## Scope
The asynchronous message transport for the local Docker target and the fabric
plane. It does not change the messaging API, the permission model, or the
cloud targets, which should use the provider's managed equivalent through the
same seam.

## Implementation
A bus adapter behind the existing `messaging.py` interface, with the in-process
bus kept as the default for tests and single-process mode. The local target
emits a `nats` service per tenant with JetStream enabled, on the tenant's
network, with a tenant-prefixed subject namespace. `docker/compose/fabric.yml`
gains the fabric's own instance. The lock file gains the pinned digest.

## Timeline
Phase 5, alongside WS-006.

## Advantages
- Roughly 20 MB per instance, which makes per-tenant brokers affordable — the
  isolation rule stops being expensive to honour.
- Request/reply and durability in one dependency, matching the two message
  shapes we already have.
- A single static binary in the image: fast to start, little to operate, few
  moving parts to get wrong on a laptop.
- Accounts and subject-based authorization are available if we ever relax the
  one-instance-per-tenant rule.

## Disadvantages
- **Weaker replay than Kafka.** JetStream retains and replays, but this is not
  an event log for analytics, and if the platform later wants durable
  cross-tenant event history for audit, NATS is the wrong tool and we will have
  chosen twice.
- **Per-tenant brokers multiply operationally.** Twenty tenants is twenty
  brokers to monitor, upgrade and patch, and nothing here automates that.
- **Ecosystem is smaller.** Kafka connectors and tooling are everywhere; NATS
  bridges to enterprise systems are thinner, and the enterprise channels
  (Slack, Teams) still need their own clients regardless.
- **JetStream is a second mode to reason about.** At-most-once and persisted
  behave differently under failure, and a channel that quietly declares the
  wrong one will look fine until it loses a message.
- **Unverified.** No daemon here: the service is generated and parsed, never
  started. The tag and digest are checked against the registry; the image has
  not been pulled or run.
- We now pin an eleventh third-party image, which is more supply chain
  (ADR-0053's standing cost).

## Alternatives considered
- **Apache Kafka** — the right answer for a durable event log and the wrong one
  per tenant on one host: far heavier, and its strengths are for a workload we
  do not have.
- **Apache Pulsar** — the best multi-tenancy model of the candidates, which our
  own per-tenant rule makes unusable, at a footprint we cannot multiply.
- **RabbitMQ** — the closest call. Mature, `vhost`s are a real isolation
  primitive, excellent routing and a good management UI. Rejected on footprint
  and on having two moving parts where NATS has one; it remains the obvious
  substitute if routing needs outgrow subjects, and it is pinned-and-verified
  in case (`rabbitmq:4.3.6-alpine`).
- **Redis Streams** — Redis is already an optional fabric dependency, so this
  would add nothing to the image list; rejected because using a data store as a
  broker gives weak delivery semantics and no request/reply.
- **Keep the in-process bus** — leaves containerized agents unable to reach each
  other, which is the gap this closes.

## Verification
Tests assert the generated per-tenant stack contains a NATS service on the
tenant's network with its own volume and a tenant-prefixed subject namespace;
that no floating tag is used; that the in-process bus remains the default
outside the generated stack; and that bus delivery does not bypass the
delegation checks. Nothing is verified against a running broker.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. NATS with JetStream, one instance per tenant, chosen on footprint because the tenant rule multiplies it. |
