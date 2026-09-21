---
id: WS-033
title: Enterprise application integration — principals and processes
status: Proposed
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
owner: Platform Architecture
contributors: [Security Engineering]
scope: [spec, compiler, security, runtime]
decisions: [ADR-0073, ADR-0074, ADR-0075]
depends_on: [WS-002, WS-004, WS-019]
tags: [integration, controls]
---

# WS-033: Enterprise application integration — principals and processes

## Objective
Treat agents as what they are — users of the enterprise systems that already
implement an organization's functions — so the platform governs *which agent
operates which application, as which principal, following which process*,
without pretending to be the system of record.

## Deliverables
- An application principal on a capability binding: the account an agent signs
  in as, and the role that account is declared to hold (ADR-0074).
- A phase-gate check that our mandate does not exceed that declared role, and
  a report where the application cannot state its grants.
- Separation extended from server-and-credential to the principal, which is
  what an enterprise system actually keys segregation on.
- A `processes` block: ordered steps, each with an owner, a capability and an
  application, with per-step autonomy and step-level separation (ADR-0075).
- Instance-level segregation declared here and attributed to the system that
  enforces it (ADR-0073).

## Scope
In: how a design says who an agent is inside an application and what procedure
it follows. Out: implementing any control the application already enforces, and
any adapter for a specific product — a product name belongs in a binding
(ADR-0002).

## Approach
ADR-0073 landed first and decides what the other two may assume: a control
declares its enforcer, a `platform` control must be evaluable here, and an
`application` control may be described and not claimed. Everything below sits
inside that rule.

Principals come before processes. A process step names an application, and a
step whose application has no principal is a step nobody can be held to, so
ADR-0074 is the floor.

The hardest judgement is already made and should not be relitigated in
implementation: **instance-level segregation is not ours**. The ERP holds the
document and we do not, so the platform declares the requirement — same
document, different principal — names the enforcing system, and does not claim
to evaluate it.

## Milestones
| Milestone | Target | Status |
|---|---|---|
| M1 Application principal on a capability binding | Phase 6 | Not started |
| M2 Mandate checked against the declared principal role | Phase 6 | Not started |
| M3 Separation extended to the principal | Phase 6 | Not started |
| M4 The `processes` block and its validators | Phase 6 | Not started |
| M5 Per-step autonomy and step-level separation | Phase 6 | Not started |
| M6 Instance-level requirements in the gate report and the README | Phase 6 | Not started |
| M7 A worked procure-to-pay in the finance example | Phase 6 | Not started |

## Dependencies
WS-002 for the spec language the new blocks join, WS-004 for the separation
machinery this extends, WS-019 for the capability bundle a principal binds.

## Advantages
- "Which application accounts does this agent hold?" gets an answer from the
  design rather than from a spreadsheet.
- A mismatch between our authority and the application's is caught at compile
  rather than at the first call.
- The organization's actual work becomes visible, instead of being
  reconstructed from a scatter of capabilities.
- Segregation reaches the layer where it is really enforced.

## Disadvantages
- **One principal per agent per application multiplies service accounts**, and
  nothing here provisions, rotates or decommissions them. A finance function of
  forty agents across six systems is a couple of hundred accounts somebody has
  to run.
- **Refusing shared integration accounts will block real deployments.** Plenty
  of enterprise software offers only one, and the honest answer — refuse — will
  read as the platform being unable to integrate with software the company
  already owns.
- **A role name is not a permission set**, so M2's check compares names rather
  than grants, and two products spell the same authority differently.
- **The process model is a swamp.** Reversals, partial receipts, credit notes
  and emergency paths fit no straight-line sequence, and the pressure at M4
  will be to add branches until it becomes the workflow engine ADR-0075 rule 6
  says it is not.
- **M6 ships a documented gap.** The strongest segregation control in a finance
  function is the one we explicitly decline to enforce, and reporting it
  honestly does not make the risk smaller.

## Exit criteria
Every capability binding names a principal or records why it cannot; a design
whose mandate exceeds a declared application role fails the implementation
phase; two capabilities on opposite sides of a separation cannot share a
principal; a procure-to-pay process spanning the ERP and the bank compiles in
the worked finance example; and every instance-level requirement in that
example names the system that enforces it.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Proposed. ADR-0074 and ADR-0075 had no implementation milestone anywhere. |
