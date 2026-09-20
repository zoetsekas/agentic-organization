---
id: ADR-0052
title: A hit quota degrades, an unentitled request refuses, and a stale belief is not health
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Product]
informed: [All engineering]
scope: [runtime, security, docs]
workstreams: [WS-030]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0021, ADR-0046, ADR-0049, ADR-0050]
tags: [tenancy, operations, quotas, health]
---

# ADR-0052: A hit quota degrades, an unentitled request refuses, and a stale belief is not health

## Context
WS-030 gives the fabric two limits and one belief, and leaves all three with an
unanswered question.

A **quota** bounds how much of the fabric a tenant may consume. WS-030 observes
that "refuse" is rarely the answer an operator wants at 3am: a quota number was
typed in by somebody, months ago, against a guess about load. Refusing at that
number converts an estimate into an outage, and the outage lands on the tenant
rather than on the person who typed the number.

An **entitlement** answers a different question — may this tenant use this
catalog entry at all — and it only looks similar because both are stored on the
same record. It is an authorization boundary, and ADR-0008 and ADR-0021 already
say what those do.

A **health check** compares what the fabric believes is deployed with what a
target reports. WS-030 notes that a stale control plane is worse than none if
anybody trusts it. The tempting implementation reports the last observation it
has, which is precisely how a control plane comes to assert that a dead tenant
is healthy.

## Decision
Three rules, one per question.

1. **A capacity quota degrades before it refuses.** Every `Quota` has a
   *soft limit* — the operator's number — and an optional *hard ceiling* — the
   number the fabric cannot pay past. Below the soft limit a request is allowed
   silently. Between soft and hard it is **allowed, recorded as a breach, and
   surfaced** (`ALLOW_DEGRADED`), so the decision is taken by a person in
   daylight rather than by a threshold at 3am. Above the hard ceiling it is
   refused. Both limits are inclusive: usage may reach a limit, not exceed it.
   A quota with no hard ceiling never refuses. An **unset** quota is not an
   infinite one — it is an unanswered question, so it is served and reported.
2. **An entitlement refuses.** `catalog_entries` is an allow-list; an entry not
   listed is not entitled, an empty list entitles nothing, and the request
   raises. Degrading here would mean running an unreviewed building block
   inside a tenant boundary because it was late, which is the failure the
   boundary exists to prevent.
3. **A stale belief is reported as `unknown`, never as health.** A
   `TargetObservation` carries `observed_at`; a `HealthCheck` carries the
   observation's age, the staleness horizon it was judged against, and a
   `Confidence` of `fresh`, `stale` or `unobserved`. Only a `fresh` observation
   may set a status of `healthy`, `degraded` or `unhealthy`; `stale` and
   `unobserved` force `unknown` and raise a `DriftSignal` in their own right.

## Scope
The fabric plane's quota, entitlement and health contracts —
`orgagents.fabric.quotas` and `orgagents.fabric.health`. It does not decide the
isolation mechanism (ADR-0050), what an operator is shown (ADR-0051), or any
limit a tenant's own agents impose on themselves (budgets in the spec are
unchanged).

## Implementation
`quotas.py` carries a pure `evaluate(quota, usage, amount) -> QuotaVerdict` so
the policy can be read without a store, plus `QuotaService.consume`, which
records usage and appends every breach to the tenant's entitlement record for
the command centre (WS-029) and the registry (WS-014). `require_entry` is the
refusing path. `health.py` compares a `Deployment` with a `TargetObservation`
from an injected `HealthBackend`; `StubBackend` is the only implementation that
exists today. The staleness horizon and the clock are both constructor
arguments, so freshness is testable rather than wall-clock-dependent.

## Timeline
Phase 5, with WS-030 M3 and M4. The horizon default (5 minutes) is a starting
number and is expected to change once M5 puts a real adapter behind it.

## Advantages
- A capacity guess cannot cause an outage on its own; it causes a signal.
- The distinction between "you may not" and "you have had a lot" is visible in
  the type system rather than in an operator's memory.
- A health status can never be more confident than the observation behind it,
  so the command centre cannot show a comforting stale green.
- An unset quota fails loud instead of quietly meaning "unlimited".

## Disadvantages
- **Degrading costs real money.** Between soft and hard, spend keeps being
  incurred; a tenant that ignores its breach log is subsidized by the fabric
  until an operator acts.
- Two numbers per quota is more configuration, and a soft limit set equal to
  the hard ceiling silently restores the refusing behaviour this ADR argues
  against — nothing stops an operator doing that.
- `unknown` is honest but unhelpful. A fabric with a flaky observer will show a
  wall of unknowns, and operators learn to ignore walls.
- The staleness horizon is itself a guessed number, and this ADR has no
  evidence for 5 minutes beyond it being short.
- **None of this has met a real target.** There is no cloud account and no
  container daemon in this environment, so the health contract is exercised
  against a stub only. It is a design, not an operation, until WS-030 M5.

## Alternatives considered
- **Refuse at the quota** — simplest and most defensible on paper; it makes an
  estimate authoritative and pages someone at 3am with an outage instead of a
  warning.
- **Never refuse, only report** — kind to tenants, unbounded for the fabric;
  one runaway tenant then spends the platform's budget with a clean audit
  trail of it happening.
- **Degrade entitlements too, with a grace period** — rejected: a grace period
  on an authorization boundary is a hole with a timer on it.
- **Report the last known status with an age field** — the age is present but
  the status still reads `healthy`, and every consumer that forgets to check
  the age gets a comfortable lie. Forcing `unknown` makes ignoring it harder.
- **Treat unobserved as unhealthy** — conflates "it is broken" with "we cannot
  see", which would make every observer outage look like a fleet outage.

## Verification
`tests/test_fabric_operations.py` asserts the soft limit is inclusive, that the
step past it allows-and-records, that the ceiling refuses and consumes nothing,
that a ceiling-free quota never refuses, that an unset quota is served and
reported, that an unentitled entry raises and an empty allow-list entitles
nothing, and that a stale or unobserved check reports `unknown` with a
`DriftSignal` even when the observation itself claimed `healthy`.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted alongside WS-030 M3 and M4. |
