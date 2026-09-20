---
id: WS-016
title: Human pairing and accountability
status: Active
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
owner: Product
contributors: [Platform Architecture, Compliance]
scope: [spec, compiler, runtime, ui]
decisions: [ADR-0026]
depends_on: [WS-013]
tags: [human-in-the-loop]
---

# WS-016: Human pairing and accountability

## Objective
Record who every agent answers to, in what capacity, so accountability,
approval, review and notification stop sharing one field — and so a person can
see what they are on the hook for across the fleet.

## Deliverables
- Role-typed `HumanCounterpart` with per-pairing channel and working hours.
- `AgentSpec.humans` with backwards-compatible lifting of the old `human:`.
- `owner`, `humans_with()` and `approvers_for()` resolution.
- Validation: exactly one owner, approver coverage for every gated action,
  no duplicate pairing, known channels.
- Phase checks for pairing, ownership, approver coverage and a fallback human.
- Registry tables: per-agent pairings and a person-centric view.

## Scope
In: who an agent answers to and how they are reached. Out: what an agent may
do (roles and permissions), and human identity/SSO for the platform itself.

## Approach
Make the roles explicit and few, enforce exactly one owner so accountability is
never ambiguous, and let approval be its own role so a holiday does not stop
the organization. Generate the person-centric view, because the question
"what am I responsible for" is asked by people, not by agents.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Role-typed pairing model and resolution | Phase 2 | Done |
| M2 Validation and phase gates | Phase 2 | Done |
| M3 Registry pairing and person views | Phase 2 | Done |
| M4 Directory integration to detect departed people | Phase 3 | Not started |
| M5 UI pairing editor | Phase 3 | Not started |

## Dependencies
WS-013 for the channels these people are reached on.

## Advantages
- Approval survives one person's absence.
- Reviewers and stakeholders are recorded in the design, not in a channel.
- The person-centric registry view answers a real management question.

## Disadvantages
- Pairings name individuals and rot as people move; until M4 nothing detects a
  departed employee still listed as an approver, which is the failure mode most
  likely to bite.
- More to declare per agent, and the lazy path — one person in every role —
  recreates the single-point-of-failure with extra syntax.
- Exactly-one-owner will be resisted by teams with genuine co-ownership.

## Exit criteria
- Every agent has exactly one owner and an approver for each gated action. ✔
- A person's pairings across the fleet are answerable from the registry. ✔
- Departed people are detected rather than discovered during an incident.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Opened. Model, validation, gates and registry views landed. |
