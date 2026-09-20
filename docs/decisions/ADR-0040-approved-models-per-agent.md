---
id: ADR-0040
title: Every agent is limited to an approved set of models
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering, Platform Architecture]
consulted: [Compliance, Platform SRE, Product]
informed: [All engineering]
scope: [spec, compiler, security]
workstreams: [WS-026]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0013, ADR-0022, ADR-0041]
tags: [security, models]
---

# ADR-0040: Every agent is limited to an approved set of models

## Context
Which model an agent runs on is a governance decision, not a deployment detail.
It determines where inference happens (residency), whether the vendor trains on
what is sent, what a run costs, and whether the agent can do the job at all.
Until now the binding simply named a model and nothing checked it — so an agent
handling regulated data could be pointed at any endpoint by editing one line,
and the design would compile.

The tension is with neutrality (ADR-0004): a model name *is* an implementation
choice, and putting one in the spec would make every design a deployment
decision.

## Decision
Three layers, each answering one question.

**The spec** states a **model policy**: which capability *classes* the agent
needs (`frontier_reasoning`, `balanced`, `fast_cheap`, `long_context`,
`vision`, `code`, `on_premises`), plus constraints — a cost ceiling, a minimum
context window, required regions, and whether the vendor may train on submitted
data. No model names, so the design stays neutral. An organization that wants
to name models directly may use an explicit `allow` list, and a `deny` list
always wins.

**The catalog** (ADR-0041) says which concrete models exist here, their class
tags, context, price, regions and training posture, and whether they are
approved, restricted, deprecated or retired.

**The compiler** resolves the binding's model against the policy and the
catalog, and **refuses the build** if it is not permitted. An unapproved model
is not a warning. A refusal names the reason and lists the models that would
satisfy the policy, so the fix is obvious.

A model absent from the catalog is refused by definition: nobody has approved
it.

## Scope
Which model serves an agent, and the sub-agent model where one is declared. It
does not choose prompts, adapters (ADR-0013) or providers' features.

## Implementation
`spec.model.ModelPolicy` and `ModelClass`; per-agent policy narrowing a system
default. `catalogs/service.py` holds `check_model`, `permitted_models` and
`resolve_model` — deny by default, with every refusal carrying a reason.
`compiler.ir.apply_model_approvals` attaches the verdict to each agent;
`compile_system(catalog=…)` raises on any refusal; the phase gate reports the
same check; the registry has a models table naming the bound model, the
permitted classes, the ceiling, the regions and whether it is approved.

The check is skipped when no catalog is supplied, and the IR says so
(`"no catalog consulted"`) rather than implying approval.

## Timeline
Phase 4, with the catalog it depends on.

## Advantages
- Model choice becomes reviewable policy rather than a line in a config file.
- Residency, training posture and cost are enforced where they are decided.
- The spec stays neutral: it asks for a class, not a vendor.
- A refusal lists the alternatives, so the constraint teaches rather than blocks.
- Retiring a model in the catalog stops new designs using it immediately.

## Disadvantages
- Class tags are editorial: whether a model is `balanced` or `frontier` is
  someone's judgement, and two organizations will disagree.
- Catalog figures — price, context, regions — go stale the moment a vendor
  changes them, and a cost ceiling then checks a fiction.
- A strict policy plus a thin catalog means nothing is permitted, and the
  failure mode is a build that cannot proceed until someone approves a model.
- Blended cost (3:1 input to output) is a simplification that will misrank
  models with unusual pricing.
- Policies add another thing to get right before a design compiles.

## Alternatives considered
- **Name models in the spec** — simple, and makes every design a deployment
  decision, breaking ADR-0004.
- **Leave it to the binding, unchecked** — what we had; one line changes where
  regulated data is processed.
- **A global allow list with no per-agent policy** — cannot express that the
  clean-room agent needs stricter terms than the routing agent.
- **Warn instead of refuse** — a warning on an unapproved model is a warning
  everybody learns to ignore.

## Verification
Tests assert every agent in the example compiles onto an approved model, that a
model outside the policy stops the build, that an uncatalogued model is
refused, that each constraint (cost, context, region, training) is enforced
separately, that an explicit allow list overrides classes and a deny beats
everything, that refusals offer alternatives, and that the phase gate reports
the same verdict.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Policy in the spec, models in the catalog, refusal at compile time. |
