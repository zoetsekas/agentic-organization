---
id: ADR-0069
title: A sandbox environment is a placement scoped to an org unit
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Runtime Engineering, Product]
informed: [All engineering]
scope: [spec, targets, runtime, security]
workstreams: [WS-003, WS-004, WS-028]
supersedes: []
superseded_by: []
related: [ADR-0006, ADR-0008, ADR-0050, ADR-0054, ADR-0059, ADR-0065, ADR-0068]
tags: [sandbox, isolation, org-model]
---

# ADR-0069: A sandbox environment is a placement scoped to an org unit

## Context
ADR-0068 named two sandbox levels and then keyed the outer one by environment
class. An environment class is a **profile** — toolchain, tier, network
posture, egress — which says what an agent *needs*, not *whose work it is*. The
local target keys sandboxes the same way (`_used_environments`), so an HR agent
and a Finance agent that both need `analysis` land on one key. Grouping by kind
of work rather than by owner of the work is backwards for an enterprise, and it
is the shape that would quietly put two departments in one place.

The model an enterprise already has for this is the application platform. A
namespace gives an application a name scope, a network policy, storage and
resource limits; things inside it share freely, things outside reach it only
through declared services. Departments want exactly that: HR's agents in one
environment sharing a filesystem, Finance's somewhere else, and the two
reaching each other only over messaging.

Half of it is already built and unused at the runtime level. `Team.groups` is
documented as "protected-data reach", `Visibility.PROTECTED` means "shared with
every agent in one or more groups", and `OrgChart.unit_groups` inherits groups
down the unit chain. That is the department-shared-data model. Nothing
instantiates a *place* from it.

ADR-0068's rule 5 — every pair of co-resident agents must be connected by the
standing org chart — is a checkable proxy for a simpler fact: they are in the
same unit. The proxy admits combinations nobody would draw on purpose (a
shared-service agent connects to everyone, so it could justify almost any
pairing) and is harder to explain than the thing it approximates.

## Decision
**A placement is an org unit crossed with an environment class. A sandbox
environment is an instance of a placement. Placement is opt-in and inherits,
co-residency is membership, and only standing structure becomes a network
rule.**

1. **Placement = (org unit, environment class).** HR × `analysis` and
   Finance × `analysis` are different sandbox environments with the same shape.
   An agent is placed by its unit, never by its profile.
2. **Placement is opt-in and inherits.** A team may declare itself a placement
   boundary; a team that does not is placed in its nearest ancestor that did.
   The organization root is always a placement boundary, so every agent has
   exactly one answer. This is the inheritance shape mandates already use
   (ADR-0065 rule 5), and it stops a small organization from getting a
   namespace per team it never asked for.
3. **Co-residency is membership.** This **replaces ADR-0068 rule 5**: agents
   share a sandbox environment because they share a placement, not because a
   pairwise org-chart check passes. ADR-0068 rules 4 (one tenant), 6
   (filesystem scoping, and degradation where a provider cannot scope) and 7
   (the shared process namespace is declared, not fixed) are unchanged and
   still bind.
4. **The shared volume is the PROTECTED plane made concrete.** A placement's
   filesystem carries exactly the data classes the unit's groups already share.
   It is **not a new grant**: an agent that may not read a data class does not
   acquire it by sharing a disk with someone who may. Without this rule a
   department volume widens access by deployment topology, which is the
   weakness ADR-0068 already admitted rule 5 did not cover.
5. **A placement is not a security boundary, and the record says so.** The
   tenant remains the absolute boundary (ADR-0050). A placement is a naming and
   policy scope whose isolation strength is whatever the provider reports
   through its capabilities (ADR-0068). Borrowing the platform model means
   borrowing its caveat: namespaces on an application platform share a kernel
   too.
6. **Only standing structure becomes a network rule.** The manager chain,
   declared peers, shared services and the bus generate network policy.
   **Mission-lent reach never does** — a generated rule does not expire and a
   mission window does, so baking one in converts temporary reach into standing
   reach, the accident ADR-0065 rule 8 exists to prevent. Temporary reach
   travels over the bus, where the inbound worker already re-runs the org-chart
   check per message (ADR-0059).
7. **Default deny between placements**, permitted only over declared channels.
   Within a placement traffic is permitted, which is a widening, so the
   boundary statement says which agents that covers.
8. **Cross-tenant remains absolute.** No placement, group or channel makes it
   otherwise.

## Scope
How sandbox environments are named and populated, what their shared storage
carries, and what network policy the targets generate. It does not change the
permission resolver, delegation, mandates, the data planes' semantics, or what
an execution sandbox does.

## Implementation
**Not implemented.** A team gains a placement declaration naming an environment
class; the IR carries resolved placements and their members; the phase gate
checks tenancy and resolves each agent's placement by inheritance; the local
target emits one sandbox environment per placement with a volume scoped to the
unit's groups, plus network rules from standing structure only; and the
boundary statement names the co-resident agents. The `_used_environments`
keying in `compiler/targets/local.py` is what changes shape.

## Timeline
Phase 5, WS-028, after the alpha, and after ADR-0068's spec concepts land.

## Advantages
- The thing an enterprise already knows how to reason about: a department is a
  namespace, with storage, a network policy and a name scope.
- Two departments sharing a profile stop sharing a place, which is the defect
  in keying by environment class.
- Co-residency becomes a fact about the org chart rather than a pairwise check,
  so it is explainable to somebody who has not read the rules.
- The department volume reuses the PROTECTED plane instead of inventing a
  second sharing mechanism that could disagree with it.
- Mission reach keeps expiring, because it never becomes infrastructure.

## Disadvantages
- **The opt-in default is the widest one.** An organization that declares no
  placement gets a single namespace at the root: every agent in one sandbox,
  sharing one volume. That is the least isolated arrangement and the one a team
  ships by not deciding. Requiring an explicit declaration per team would be
  safer and more annoying; we chose annoying-for-nobody over safe-by-default,
  and this is where it will hurt.
- **Group inheritance accumulates.** `unit_groups` walks *up* the chain, so a
  deep team inherits its ancestors' groups, and a placement volume scoped to
  those groups may carry more data classes than the team needs. Rule 4 bounds
  the volume by what the unit already shares; it does not make what the unit
  shares minimal.
- **A shared process namespace goes from rare to routine.** ADR-0068 rule 7
  documents that co-resident agents can see each other's processes. Placement
  makes co-residency the normal case, so that unfixed hole is now the default
  rather than the exception.
- **Generated network policy and the org chart drift.** Policy is emitted at
  compile time; a reorganization changes reach immediately and the deployed
  rules only at the next apply. For that window the two disagree, and the
  disagreement is silent.
- **The analogy will be over-read.** People will expect namespace features we
  do not have — quotas, admission control, per-namespace RBAC — because the
  word brings them along.
- **A fourth noun.** Environment class, placement, sandbox environment,
  execution sandbox. The names are close and somebody will declare the wrong
  one.

## Alternatives considered
- **Keep keying by environment class.** The status quo, and it merges
  departments that happen to share a profile — the defect this record exists to
  fix.
- **Every team is a placement, no opt-in.** Predictable and safe by default,
  and it produces a sandbox per team including teams of two, plus a deep tree of
  near-empty environments in a large organization.
- **Placement declared per agent.** Maximum flexibility, and it lets an author
  place a Finance agent inside HR's namespace — precisely the thing the model
  exists to prevent.
- **Generate network policy from `can_delegate` wholesale.** One function,
  simplest generation, and it bakes expiring mission reach into a rule that
  does not expire.
- **A department volume as a new grant kind.** Direct, and it creates a second
  sharing mechanism beside the data planes that can disagree with them.

## Verification
Tests assert: two agents in different units with the same environment class
resolve to different placements; a team that declares no placement resolves to
its nearest declaring ancestor; the root always resolves; an agent's placement
volume carries no data class its unit's groups do not already share; an agent
without a grant on a data class cannot read it from a shared volume; generated
network policy contains no rule derived from a mission grant; cross-placement
traffic is denied by default and permitted over a declared channel; and the
boundary statement for a placement names the agents co-resident in it.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. Placement is (org unit × environment class), opt-in and inherited; co-residency is membership, replacing ADR-0068 rule 5; the shared volume is the PROTECTED plane made concrete; only standing structure becomes network policy. |
