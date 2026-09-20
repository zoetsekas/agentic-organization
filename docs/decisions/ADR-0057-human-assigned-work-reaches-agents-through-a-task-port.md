---
id: ADR-0057
title: Human-assigned work reaches agents through a task port, and the backend is deferred
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Product]
consulted: [Security Engineering]
informed: [All engineering]
scope: [spec, runtime, security, docs]
workstreams: [WS-031]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0026, ADR-0030, ADR-0035, ADR-0039, ADR-0050, ADR-0056]
tags: [runtime, integration]
---

# ADR-0057: Human-assigned work reaches agents through a task port, and the backend is deferred

## Context
Every agent is paired with people — one accountable owner, plus approvers and
reviewers (ADR-0026). Those people have no way to *give it work*. The platform
has triggers (time and events), channels (conversation) and approval routing
(permission), and nothing that represents "here is a piece of work, track it
until it is done". That is how humans actually delegate, and its absence is why
an agent here can only be woken by a schedule or spoken to.

`docs/TASK_SERVICES.md` evaluated the candidates. The recommendation was Plane,
on the strength of an official MCP server and a bot principal that can be an
assignee without consuming a seat. The argument against it is not small: PR
`makeplane/plane#9399`, which would have made service accounts
API-provisionable, was closed unmerged with the maintainer stating that the
account and permission model is "kept on the commercial side". This platform
mints agents *by compilation* — a tenant publishes a spec and N identities
should appear — so the first thing we would automate is the thing that vendor
has declined to support in its free edition. Worse, the decisive question
(whether the community edition can create bot users without an interactive
OAuth install) could not be verified: `plane.so` is blocked from the
environment this was researched in.

Choosing a backend on evidence we could not obtain, for the sake of starting,
would be the expensive kind of decision.

## Decision
**Build the port; defer the backend.**

A **task** is a first-class thing in the runtime: work a human assigned to an
agent, with a lifecycle, that can be tracked to completion. How it is *stored*
is a binding concern, exactly like a model provider or a channel.

Six rules:

1. **The spec names no product.** It describes that an agent accepts assigned
   work and under what constraints. `plane`, `taiga` and the rest appear only
   in the binding (ADR-0002).
2. **An agent acts as itself or not at all.** A backend that cannot give a
   non-human principal an identity is refused **at bind time**, with the reason
   — it is not worked around with a human's borrowed credentials. An agent
   doing work under a person's account destroys the accountability the pairing
   model exists to create, and makes the audit trail a lie.
3. **A task maps to at most one agent run.** The run's session id is written
   back to the task, so a human can get from the work they assigned to what the
   agent actually did. One task, one traceable execution.
4. **Intent belongs to the task service; execution belongs to us.** When the
   two disagree — a task closed while its run is live, a run finished against a
   task somebody reopened — the disagreement is **surfaced, never silently
   reconciled**. A control plane that quietly makes its own state match
   somebody else's is a control plane nobody can trust.
5. **Only paired humans may task an agent.** Assignment is subject to the
   pairing model and to RBAC: being able to edit a board is not authority to
   direct an agent.
6. **A task backend instance belongs to one tenant** (ADR-0050). A shared
   instance across tenants is a cross-tenant channel, whatever its own
   permission model claims.

Incoming task content is **untrusted input**: a description is written by a
human in another system and crosses the input guardrail boundary like any
other external text (ADR-0035).

## Scope
The port, its lifecycle, its identity requirement and its conflict semantics,
plus a reference implementation good enough to exercise all of it. It does not
choose a product, and it does not build a task manager — if the port's local
implementation starts growing a board, we have taken a wrong turn.

## Implementation
A `TaskPort` protocol with a small, deliberately boring surface — list assigned
work, claim, report progress, complete, fail, comment — and a `TaskRecord`
carrying the fields a backend cannot be relied on to have: agent id, session
id, tenant, mission, approval state. A local reference backend over the
existing `Store`, so the port is exercised end to end without a vendor. A
conformance suite every future adapter must pass. Adapters for real products
are written only after their identity model is confirmed by standing the
product up.

## Timeline
Phase 5, WS-031.

## Advantages
- The decision that could not be made on evidence is not made at all, and
  costs nothing to defer.
- Humans get the delegation primitive that triggers and channels do not
  provide.
- The identity requirement is enforced at the boundary rather than discovered
  during an incident.
- A backend that turns out to be wrong is replaced behind a port instead of
  unpicked from the runtime.

## Disadvantages
- **A port with one local implementation proves less than it appears to.** The
  first real adapter will find something the port did not anticipate, and the
  shape will move. Ports written before their second implementation usually do.
- **The local backend is a temptation.** It is easier to add a field to it than
  to integrate a product, and if that continues we will have written a bad task
  manager by accident.
- **Deferring has a cost**: nobody can actually assign work to an agent from a
  tool they already use until an adapter exists, so the feature is invisible to
  its users for now.
- **Rule 2 may eliminate the best product.** If Plane's community edition
  cannot mint bot identities through an API, the candidate with the best MCP
  story fails our hardest requirement, and the options left are weaker.
- **Rule 4 creates work for a human.** Surfacing divergence rather than
  resolving it means somebody must look at it; an unattended deployment will
  accumulate conflicts nobody reads.

## Alternatives considered
- **Pick Plane now and integrate directly** — commits the runtime to a vendor
  on the strength of a capability we could not verify, with the vendor on
  record that the relevant subsystem is commercial.
- **Build our own task manager** — solves the integration problem by becoming
  the thing we are trying to integrate with; humans would have to leave the
  tools they use, so they would not use it.
- **Use channels as the task surface** — conversation has no lifecycle, so
  "what did you ask it to do and is it done" has no answer.
- **Use GitHub or GitLab issues** — good APIs and real service accounts, but it
  puts an organization's non-engineering work in an engineering tool, which is
  where most of this work is not.

## Verification
Tests assert no product name appears in the spec layer; that a backend without
a non-human principal is refused at bind time with its reason; that a task maps
to at most one run and the session id is written back; that divergence is
reported and never silently reconciled; that an unpaired human cannot assign
work; that a task's text crosses the input guardrail; and that a backend is
tenant-scoped. A conformance suite defines what any adapter must satisfy.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Build the task port, defer the product choice, require a real agent identity at bind time. |
