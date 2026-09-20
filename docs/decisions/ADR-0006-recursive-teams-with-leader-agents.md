---
id: ADR-0006
title: Organization is a recursive tree of teams, each with one leader agent
status: Accepted
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Product, Security Engineering]
informed: [All engineering]
scope: [spec, runtime, ui]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0007, ADR-0008]
tags: [organization, foundational]
---

# ADR-0006: Organization is a recursive tree of teams, each with one leader agent

## Context
Enterprise work is organized as teams inside teams: a squad inside a department
inside a division. A flat manager-to-report edge between agents can express the
reporting line but not the *team* — and it is the team that owns a mandate,
holds shared data, and has a single accountable lead. Without teams there is
nowhere to attach a collective responsibility, and permissions have to be
restated on every individual agent.

A leader is also an agent, and a leader is a member of its parent team. That
recursion is what makes the structure scale to an actual company.

## Decision
The organization is a tree of **teams**. Each team has:

* exactly one **leader agent**, which must also be a member of the team;
* zero or more **member agents**;
* zero or more **child teams**, whose leaders participate in this team
  *implicitly* through their leadership — they are not listed twice;
* a team-level **mandate** (responsibilities) and a set of team roles.

An agent belongs to exactly one team as its home: the team whose `members` list
it appears in, which for a leader is the team it leads. Participation in the
parent team is **derived** from leading a child team, not declared — declaring
it would give the agent two home teams and make permission inheritance
ambiguous. That derived participation is the mechanism by which authority and
information flow between levels.

Delegation follows the tree: a leader may delegate to its team's members and
into its child teams; peers are reachable laterally only when declared; anything
else escalates to the nearest common leader.

## Scope
The spec's `organization` block, the IR, the runtime's org model, and the UI's
org view. It does not model dotted-line reporting or matrix management beyond
explicit peer links — see Disadvantages.

## Implementation
`spec.model.Team` (`leader`, `members`, `teams`, `mandate`, `roles`) with
validation: the leader must be a member of the team it leads, the tree must be
acyclic and connected, every agent must have exactly one home team, and a child
team's leader must **not** also be listed in the parent's members. `compiler.ir` flattens the tree into per-agent reporting
chains and effective team membership. The existing runtime `OrgChart` becomes a
consumer of that IR.

## Timeline
Phase 1, alongside the spec — permissions inherit through this structure, so it
blocks the security model.

## Advantages
- Mirrors how companies actually organize, so a domain expert can author it.
- Gives collective responsibility and shared data a place to attach.
- Permission inheritance has an obvious, explainable path.
- One leader per team means escalation always has an unambiguous target.

## Disadvantages
- Strict trees cannot express matrix or dotted-line organizations, which are
  common; declared peer links are a partial and manual workaround.
- One home team per agent forces an artificial choice for genuinely shared
  agents; shared-service agents need a special case.
- A leader that participates in two levels complicates permission resolution —
  its own grants and its team's grants must not be silently merged.
- Implicit parent participation is derived rather than written down, so the
  spec does not show it and readers must know the rule.
- Deep trees make delegation paths long, and latency accumulates per hop.

## Alternatives considered
- **Flat agent graph with manager edges** — what the prototype had; no place for
  team-level mandate or shared data.
- **Arbitrary DAG of teams** — expresses matrix structures, but makes permission
  inheritance ambiguous and escalation targets non-unique.
- **Multiple leaders per team** — reflects co-leadership, removes the single
  escalation target that makes the model tractable.

## Verification
Validator tests cover leader-not-a-member, cycles, orphan agents and
multi-home agents. The IR test asserts that a leader's effective permissions are
the union of its own role and its team role, resolved once.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Parent-team participation is implicit via leadership, not a second membership; writing the spec surfaced that the original wording conflicted with the one-home-team rule. |
| 1.0.0 | 2026-09-20 | Accepted. Recursive team tree with a single leader per team. |
