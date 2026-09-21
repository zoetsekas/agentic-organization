---
id: ADR-0068
title: A sandbox environment hosts agents and co-residency is declared and scoped
status: Accepted
version: 1.1.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Runtime Engineering]
informed: [All engineering]
scope: [spec, targets, runtime, security]
workstreams: [WS-004, WS-028]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0009, ADR-0050, ADR-0054, ADR-0055, ADR-0065, ADR-0069]
tags: [sandbox, isolation, security]
---

# ADR-0068: A sandbox environment hosts agents and co-residency is declared and scoped

## Context
There are two sandboxes in this system and until now only one of them had a
name.

The **execution sandbox** is built and understood: an agent's harness runs code
inside an image resolved from its environment class, and the local target
generates a `sandbox-<class>` service for exactly that. A comment at
`compiler/targets/local.py:398` records why the distinction exists — conflating
the agent's own image with its execution image once gave an agent in a
`network: none` environment a distroless image with no interpreter to run on.

The **sandbox environment** is the level above, and we have been describing it
without modelling it. Today an `agent-<id>` service runs *beside* its sandbox,
on the platform runtime image, attached to a `control` network plus either
`isolated` or `egress`. Its boundary is therefore a Docker network, which
ADR-0054's own container boundary statement already concedes is a Docker-object
boundary and not a kernel one. The policy that governs the agent process is
ours, expressed in Compose; the policy that governs its code execution is the
provider's.

That is backwards from what a provider like OpenShell actually offers. ADR-0054
v1.1.0 describes it correctly — "a gateway control plane over sandbox
lifecycle", with a policy engine spanning filesystem, process, network and
providers, credentials injected as environment variables rather than written to
disk, and egress enforced at HTTP method and path level — and then uses it one
level too low, as a provider under a per-agent environment class. The right
description at the wrong level.

If the agent process runs *inside* the governed sandbox, that policy engine
covers the agent itself: its egress, its filesystem, its credentials. That is
strictly stronger than a Docker network, and it is what the platform has been
claiming in prose.

The moment a sandbox hosts agents, a second question follows that nothing above
answers: **may more than one agent share one?** Every control built so far
assumes the agent boundary is the process boundary. Two agents in one sandbox
share a filesystem and a process namespace, which is a lateral path that
`can_delegate` does not govern, that permissions cannot see, and that mandates
sit entirely above. Agent B reads agent A's workspace whatever the org chart
says. That assumption breaking quietly is the worst way for it to break.

## Decision
**An environment class instantiates as a sandbox environment that hosts one or
more agent processes and owns their policy and network. Each agent's harness
keeps its own execution sandbox. Co-residency is declared, scoped, and
permitted only where the standing org chart already connects the agents.**

1. **Two levels, named.** A *sandbox environment* is an instance of an
   environment class: it carries the policy domains and the network posture,
   and it hosts agent processes. An *execution sandbox* is where one agent's
   harness runs code, per invocation. They are not the same thing and neither
   substitutes for the other.
2. **The agent process runs inside its sandbox environment** where the provider
   can host it. Its egress is the sandbox's egress, enforced by the provider,
   not by a network the agent is attached to.
3. **Default is one agent per sandbox environment.** Co-residency is declared
   in the spec and never inferred from two agents sharing an environment class.
   Silence does not grant a shared boundary, for the same reason silence does
   not grant authority (ADR-0065 rule 5).
4. **Co-residency is within one tenant, absolutely** (ADR-0050). No policy,
   scoping or declaration makes a cross-tenant sandbox acceptable.
5. **Co-residency is membership of a placement (v1.1.0, ADR-0069).** This
   rule originally required every pair of co-resident agents to be connected by
   the standing org chart. That was a checkable proxy for a simpler fact — they
   are in the same org unit — and it admitted pairings nobody would draw on
   purpose, since a shared-service agent connects to everyone. A sandbox
   environment is now an instance of a *placement*, an org unit crossed with an
   environment class, and agents co-reside because they share one.

   What survives unchanged is the reasoning about time: **mission-lent reach
   never places an agent and never becomes a network rule**, because a mission
   window closes and neither a sandbox nor a generated rule does. Temporary
   reach travels over the bus, where it is re-checked per message.
6. **Each co-resident agent gets its own filesystem scope**, enforced by the
   provider. OpenShell locks filesystem policy at sandbox creation, which fits.
   **Where a provider cannot scope per agent, co-residency degrades to one
   agent per sandbox** — loudly, naming what was asked for and what was given,
   like every other degradation (ADR-0054 rule 3).
7. **The shared process namespace is not scoped by anything we can express.**
   Co-resident agents are therefore declared to share a process boundary, the
   boundary statement says so in those words, and it carries `verified=False`
   like every other boundary claim. We state what we configured, never what we
   measured.
8. **Where the provider cannot host the agent process** — the container floor,
   today, everywhere — the agent runs beside its sandbox exactly as now, and
   the generated artifacts record that the agent's own boundary is a Docker
   network rather than the provider's policy engine.

## Scope
The environment model in the spec, the IR, the generated targets, and the
sandbox provider seam. It does not change the permission resolver, the org
chart, delegation, mandates, or what an execution sandbox does.

## Implementation
**Not implemented.** The spec gains a sandbox-environment declaration naming an
environment class and the agents hosted in it; the IR carries the instance and
its members; the phase gate checks rules 4, 5 and 6; the local target places
co-resident agents in one service group and records rule 8's fallback; and the
provider seam gains a "can this host an agent process, and can it scope a
filesystem per agent" capability so degradation is decidable rather than
assumed. OpenShell's adapter still raises `NotImplementedError` because its SDK
publishes no policy schema, so this lands as a modelled shape ahead of an
implementable dependency — deliberately, and ADR-0054 rule 3 already requires a
deployment to work without it.

## Timeline
Phase 5, WS-028, after the alpha. Nothing here blocks ALPHA A1–A6.

## Advantages
- The agent process gets the boundary the platform has been describing: a
  policy engine covering its egress, filesystem and credentials, rather than a
  Docker network we admit is not a kernel boundary.
- OpenShell is used at the level it was built for, which is what ADR-0054 v1.1.0
  already describes it as.
- Co-residency becomes expressible instead of impossible, which is what a fabric
  operating many agents actually needs — one sandbox per agent does not scale to
  an organization of hundreds.
- The lateral channel co-residency creates is bounded by the org chart, so the
  sandbox cannot hand two agents a path the organization refuses them.
- The two levels stop being explained in conversation and start being checkable.

## Disadvantages
- **A shared process namespace is a real hole and rule 7 only documents it.**
  Two co-resident agents can see each other's processes, and probably each
  other's memory and environment. Filesystem scoping does not fix that, and
  nothing in our model can express a process-level partition. We are writing
  the weakness down rather than closing it.
- **Rule 5 is checkable and not sufficient.** "Could already reach each other"
  governs *delegation*, not *data*. Two agents in one team may legitimately
  delegate to each other while holding different data-class grants, and a shared
  sandbox lets the narrower one read what the wider one fetched. The rule
  prevents the obvious violation and not the subtle one.
- **Degradation will be common and silent-ish.** Rule 6 falls back to one agent
  per sandbox whenever a provider cannot scope filesystems, which today is every
  provider we can actually run. The loud report is honest and will also be
  routine enough to stop being read.
- **It adds a spec concept people must get right.** A sandbox environment is a
  third thing beside the environment class and the execution sandbox, and the
  names are close enough that somebody will declare the wrong one.
- **Rule 8 means two architectures in the field.** Deployments on the container
  floor keep the old shape; deployments on a hosting provider get the new one.
  The generated artifacts differ, and a reader has to check which they have.
- **It is designed against an alpha dependency.** OpenShell is pre-1.0 with no
  published policy schema. The shape may not survive contact with its wire
  format.

## Alternatives considered
- **One agent per sandbox, always.** Preserves the assumption that the agent
  boundary is the process boundary, needs no new rules, and costs a sandbox per
  agent — which does not scale, and leaves the level-mismatch with OpenShell
  unresolved.
- **Co-residency as a declared trust group, with no scoping.** Simple and
  honest: agents in one sandbox are declared to share a boundary and the
  statement says so. Rejected because it makes a spec author responsible for a
  security property they may not realise they are granting, with no check
  behind it.
- **Keep the agent beside the sandbox and strengthen the Docker networks.**
  Incremental and cheap, and it cannot reach what a policy engine reaches:
  method-and-path-level egress, credentials never touching disk, filesystem
  policy locked at creation.
- **Model the sandbox environment as a tenant sub-boundary.** Tempting, since
  tenancy is already absolute — and wrong, because tenancy is fabric-assigned
  and never spec-declared (ADR-0050), while which agents share a workbench is
  exactly a design-time decision.

## Verification
Tests assert: two agents sharing an environment class do not share a sandbox
environment unless declared; a declared co-residency across tenants is refused;
a co-residency between agents the standing org chart does not connect is
refused; a co-residency justified only by mission-lent reach is refused; a
provider that cannot scope filesystems per agent degrades to one agent per
sandbox and the degradation names what was asked and what was given; the
boundary statement for a co-resident sandbox states the shared process
namespace in words and carries `verified=False`; and on the container floor the
generated artifacts record that the agent's own boundary is a Docker network.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-21 | Rule 5 replaced: co-residency is membership of a placement (ADR-0069), not a pairwise org-chart check. Rules 4, 6, 7 and 8 unchanged. |
| 1.0.0 | 2026-09-21 | Accepted. Two named levels; the agent process runs inside its sandbox environment where the provider can host it; co-residency is declared, tenant-bound, org-chart-bound, filesystem-scoped, and honest about the shared process namespace. |
