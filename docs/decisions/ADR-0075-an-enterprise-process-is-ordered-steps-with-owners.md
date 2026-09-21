---
id: ADR-0075
title: An enterprise process is ordered steps with owners
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture]
consulted: [Security Engineering, Product]
informed: [All engineering]
scope: [spec, compiler, runtime]
workstreams: [WS-003, WS-009, WS-033]
supersedes: []
superseded_by: []
related: [ADR-0018, ADR-0057, ADR-0069, ADR-0070, ADR-0072, ADR-0073, ADR-0074]
tags: [process, controls, org-model]
---

# ADR-0075: An enterprise process is ordered steps with owners

## Context
People in a finance function do not exercise capabilities; they follow
processes. Procure-to-pay is a purchase order, then a receipt, then an invoice,
then a three-way match, then an approval, then a payment, then a
reconciliation — spanning the ERP and the banking platform, with different
people at different steps and a handoff between each.

The platform has no such concept. `WorkflowSpec.graph` is an opaque dictionary
handed to an execution engine: a graph to run, not a procedure with steps,
owners and an order. Capabilities describe single reaches. So a process exists
in this system only as a set of unrelated permissions that happen to be held by
agents who happen to be in the same team.

That matters most for segregation of duties, because **the real control is per
document, and ours is per role.** `separations` (ADR-0070) forbid one agent
from holding two decision classes. The control an auditor tests is narrower and
stricter at once: the same clerk may raise invoice A and approve invoice B, but
not both on invoice A. Role-level separation is therefore both too strict — it
forbids a small team from covering two roles across different transactions,
which real organizations do with compensating controls — and too weak, because
it says nothing about a single instance.

It is also why enterprise systems enforce segregation themselves: the ERP holds
the document. We do not, and no amount of modelling here will change that.

## Decision
**A process is a declared sequence of steps, each with an owner, a capability
and an autonomy posture. Instance-level separation is declared here and
enforced by the application.**

1. **A `process` declares ordered steps.** Each step names the role or agent
   that owns it, the capability it exercises, and the application it touches.
   A process may span applications; that is the normal case, not an exception.
2. **Autonomy is per step** (ADR-0072). Entering an invoice and approving it
   are different postures in the same process, which is exactly why the posture
   does not belong on the agent.
3. **Separation may be declared between steps**, not only between decision
   classes. "The owner of `approve` must not be the owner of `enter`" is a
   statement about the process, and it is the one a control framework is
   written in.
4. **Instance-level separation is declared here and enforced by the
   application** (ADR-0073). The platform states the requirement — same
   document, different principal — names the system that enforces it, and does
   **not** claim to evaluate it. We do not hold the document, and a control we
   cannot check must not read as one we can.
5. **A handoff that crosses a placement goes over a channel** (ADR-0069). Two
   steps in different departments do not share a filesystem; the work moves as
   a message, re-checked on arrival.
6. **A process is a design artifact, not an execution engine.** It describes
   what the organization does and who owns each part. Whether a step is run by
   an agent loop, a workflow engine or a person is a binding concern, exactly
   as a runtime adapter is.

## Scope
A new spec construct and the checks over it. It does not replace
`WorkflowSpec`, which stays what it is — a graph handed to an engine — and it
does not add a process runtime.

## Implementation
A `processes` list in the spec: id, description, and ordered steps carrying an
owner, a capability, an application, an autonomy posture and optional
step-level separations. The validator checks that every step's owner holds the
capability, that step separations are satisfiable by the org chart, and that a
cross-placement handoff has a channel. Instance-level requirements are carried
into the phase-gate report and the generated README under ADR-0073's ownership
rule, naming the enforcing system.

## Timeline
Phase 5, WS-003, after ADR-0073 and ADR-0074. Rule 4 depends on the first and
rule 1's application naming on the second.

## Advantages
- The organization's actual work becomes visible in the design, rather than
  being reconstructed from a scatter of capabilities.
- Segregation can be expressed the way control frameworks write it — between
  steps of a named process — instead of as a pairwise decision constraint that
  approximates it.
- Per-step autonomy puts the posture where the risk is: entry unattended,
  approval supervised, in one readable sequence.
- Rule 4 stops us pretending to a control we cannot hold, and records who does.
- Cross-placement handoffs inherit the boundary rules already decided rather
  than inventing a second path.

## Disadvantages
- **A process model is a swamp.** Every organization's variant has exceptions,
  reversals, partial receipts, credit notes and emergency paths, and a
  straight-line sequence of steps describes none of them. The first real
  procure-to-pay will not fit, and the pressure will be to add branches until
  this becomes the workflow engine rule 6 says it is not.
- **Two things called a process.** `WorkflowSpec` is an executable graph and
  this is a described procedure. The names are close, the concepts are not, and
  somebody will put the wrong one in the wrong place.
- **Rule 4 is a documented gap wearing a rule's clothes.** The strongest
  segregation control in a finance function is the one we explicitly decline to
  enforce, and the honest report does not make the risk smaller.
- **Step separations can be satisfied on paper.** The check is that different
  roles own different steps; whether the same *person* ends up behind two
  agents is exactly the human question ADR-0064 leaves open.
- **It adds a fourth place authority appears** — mandate, autonomy posture,
  separation, and now process step — and a reader has to hold all four to know
  what an agent may do.

## Alternatives considered
- **Extend `WorkflowSpec` instead.** One concept rather than two, and it
  conflates a description of the organization with an executable artifact,
  which is the mistake the spec/binding split exists to prevent.
- **Model processes in the catalog.** Reusable and governed, and a process is
  an organization's own shape rather than a building block it selects.
- **Claim instance-level separation here.** We would hold a copy of the
  document identity per call and enforce four-eyes ourselves — a partial
  duplicate of the ERP's control, diverging at the first exception.
- **Leave processes unmodelled and rely on role separation.** The status quo,
  which is both too strict and too weak, and makes the platform unable to
  describe what the organization actually does.

## Verification
Tests assert: a step whose owner does not hold its capability is refused; a
step separation that no org-chart arrangement can satisfy is refused; a
cross-placement handoff without a channel is refused; per-step autonomy is
applied rather than the capability's default; an instance-level requirement
appears in the report with its enforcing system named and is not evaluated
here; and a process spanning two applications compiles.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. Processes are ordered steps with owners, per-step autonomy and step-level separation; instance-level separation is declared and left to the application. |
