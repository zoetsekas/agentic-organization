---
id: WS-003
title: Organization modelling — recursive teams, leaders, roles and responsibilities
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Platform Architecture
contributors: [Product]
scope: [spec, runtime, ui]
decisions: [ADR-0006, ADR-0007]
depends_on: [WS-002]
tags: [organization]
---

# WS-003: Organization modelling — recursive teams, leaders, roles and responsibilities

## Objective
Let a user describe how their agents are organized the way they describe how
their company is organized: teams inside teams, one accountable leader each, and
explicit roles carrying both responsibilities and the permissions to meet them.

## Deliverables
- `spec.model.Team` / `Agent` / `Role` with recursive team nesting.
- Validation: leader is a member, acyclic tree, one home team per agent, child
  leaders are members of the parent.
- Role resolution: team role inheritance, assignment-site constraints,
  narrow-only semantics.
- Responsibility-to-prompt compilation with provenance.
- Org-chart rendering in the designer UI, teams as first-class nodes.

## Scope
In: team structure, leadership, membership, roles, responsibilities, delegation
paths. Out: permission evaluation semantics (WS-004) and the human org chart the
agents mirror.

## Approach
Model the tree in the spec, resolve it once in the IR, and keep the runtime a
consumer of that resolution. Delegation follows the tree — leaders to members
and child teams, declared peers laterally, escalation to the nearest common
leader. Roles compile into both the generated prompt and the permission set, so
accountability and authority cannot drift apart.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Team/role model and validators | Phase 1 | In progress |
| M2 Role resolution in the IR | Phase 1 | In progress |
| M3 Runtime consumes resolved org | Phase 2 | Not started |
| M4 UI team-aware org chart | Phase 2 | Not started |
| M5 Matrix/dotted-line investigation | Phase 3 | Not started |

## Dependencies
WS-002 for the spec. Feeds WS-004, which inherits permissions along this tree.

## Advantages
- Domain experts can author the structure without translation.
- A single accountable leader per team gives escalation an unambiguous target.
- Team-level mandate and shared data finally have somewhere to live.

## Disadvantages
- A strict tree cannot express matrix organizations, which are common; declared
  peer links are a manual and partial workaround that will not satisfy everyone.
- One home team per agent forces artificial choices for shared-service agents.
- Deep hierarchies add delegation hops, and each hop costs latency and tokens.
- Role proliferation is likely without catalog curation nobody has been assigned.

## Exit criteria
- A four-level organization with leaders at each level validates, compiles and
  runs, with delegation and escalation behaving per ADR-0006.
- Effective roles are explainable: for any agent, the UI can show which role and
  which team granted each responsibility and permission.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Team/role model and resolution in progress. |
