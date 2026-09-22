---
id: ADR-0095
title: Scale is declared, not inherited from a provider default
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Platform Operations]
informed: [All engineering]
scope: [spec, compiler, targets]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0069, ADR-0073, ADR-0084, ADR-0093, ADR-0094]
tags: [scaling, capacity, terraform, conformance]
---

# ADR-0095: Scale is declared, not inherited from a provider default

## Context
An agentic organization is supposed to scale with its workload. Today the
platform expresses no opinion about that whatsoever. `min_instances`,
`max_instances`, replicas and concurrency appear **nowhere** — not in the spec,
not in the IR, not in any target.

The consequence is not that scaling is absent. It is that scaling is decided
by whoever wrote the provider's defaults. The Terraform target emits a
`google_cloud_run_v2_service` with a `template` that carries CPU, memory,
service account and network attachment, and **no `scaling` block at all**. The
service still scales. It scales to whatever Cloud Run defaults to, which is a
ceiling nobody in the organization chose, nobody reviewed, and nothing in the
generated output names. A reader of that Terraform cannot tell whether the
bound is 1 or 100, and neither can the person paying for it.

That is precisely the shape this platform refuses elsewhere: a control that
reads as governed and is not (ADR-0073). `max_parallel_subagents` was the same
class of thing until ADR-0093 enforced it, and
`escalate_to_human_after_failures` until ADR-0094 counted it. This is the
third, and it is the one with a bill attached.

Two other decisions have made it more urgent. Since ADR-0093 a leader can hold
work across turns in process memory, and since ADR-0094 a stand-in can be
holding somebody else's. Both assume an instance that is still there.

## Decision
Scale becomes a **declared, vendor-neutral property of an agent**, carried
through the IR, and emitted by every target that deploys workloads. Five rules.

**1. The vocabulary is neutral, because the spec never names a vendor.**
A `ScalingPolicy` on an agent: `min_instances`, `max_instances` and
`concurrent_sessions_per_instance`. These are statements about expected load
and tolerated latency, which is an organizational fact, not a cloud one
(ADR-0002). Scale-to-zero is not a fourth field: it is `min_instances == 0`,
and a flag that could disagree with the number beside it is a bug waiting.

**2. Nothing is ever emitted without a bound.** An agent that declares no
policy still gets an explicit one in the generated output, carrying the
platform default **and a comment saying that is what it is**. The point is not
the number; it is that a reviewer reading the Terraform sees a ceiling and
knows whether anyone chose it. Inheriting silently is the only outcome ruled
out.

**3. A cloud that cannot express a bound says so.** `ProviderProfile` gains a
`scaling` descriptor naming the fields it can express. Anything the design
declared and the provider cannot carry is listed in `MAPPING.md` and in the
agent's `CONFORMANCE.md` entry, in the same voice as a coarsened permission or
an unmapped resource. A target that quietly dropped a declared ceiling would be
worse than one that never offered the field.

**4. `min_instances: 0` carries a consequence, stated where it is chosen.**
An agent that scales to zero loses the process that held its asynchronous
handles (ADR-0093) and any standing-in it was doing (ADR-0094). Those handles
are not silently lost — `settle_lost_handles` fails them with a reason on the
next run, which is exactly the case it was written for — but with
`min_instances: 0` that stops being an exceptional path and becomes the normal
one. The generated conformance report says so per agent, because the person
choosing zero to save money is usually not the person who will read the failed
handles.

**5. The real concurrency ceiling is stated once, because it is a product.**
`max_instances × concurrent_sessions_per_instance` is how much work this agent
can have in flight, and it is not `max_parallel_subagents` — that bounds one
leader's fan-out inside one process. Both are real, they bound different
things, and the generated output names both rather than letting a reader
assume the smaller one is the answer.

## Scope
`ScalingPolicy` in the spec and IR; the Terraform target's workload emission
for every provider profile; validation of the bounds; the conformance and
mapping reports. No change to the phase gate's shape, to placement, or to how
sizing tiers work — tier decides how big one instance is, this decides how many.

## Implementation
- `ScalingPolicy` on the spec agent and `AgentIR.scaling`, with a declared
  platform default rather than an implicit one.
- `ProviderProfile.scaling`: which of the three fields this cloud expresses,
  and the resource arguments to express them with.
- The Terraform target emits the bound for every workload, marks a defaulted
  policy as defaulted, and reports anything the provider cannot carry.
- Validation refuses `min > max`, `max < 1` and `concurrency < 1`.

## Timeline
Delivered with this ADR.

## Advantages
- The bill has a ceiling somebody chose, and a reviewer can see it in the diff.
- A declared bound that a cloud cannot carry is visible rather than dropped.
- The interaction between scale-to-zero and work held in memory is stated
  where the choice is made, instead of being discovered from failed handles.
- Capacity becomes reviewable at the same gate as authority and permission,
  rather than being a deployment detail nobody in the design ever sees.

## Disadvantages
- **A default is still a default.** Writing it down makes it visible, not
  correct, and a visible wrong number can be more persuasive than an absent
  one.
- **Three fields cannot describe every scaling model.** Queue-depth targets,
  scheduled floors and per-region minimums all exist and none of them fit;
  designs that need those will fight this vocabulary or bypass it in an
  overlay.
- **Per-agent granularity is a lot of knobs** for an organization with fifty
  agents, most of which want the same answer.
- **It states a ceiling it cannot enforce.** Nothing here stops a cloud
  operator raising the bound by hand afterwards; the generated Terraform is the
  claim, and drift against it is somebody else's control.

## Alternatives considered
- **Put scaling in the binding, not the spec.** Tempting, since the binding is
  where vendors live. Rejected: expected load and tolerated latency are facts
  about the organization's work, not about the cloud chosen to run it, and
  putting them in the binding would mean two bindings of one design disagree
  about how much work an agent does.
- **Derive scale from the sizing tier.** Rejected: tier is how big one instance
  is, and conflating "this agent needs a lot of memory" with "this agent needs
  a lot of instances" gets both wrong for the common cases of a large
  single-threaded analyst and a tiny high-fan-out router.
- **Leave it to overlays.** Rejected: that is today, and today the ceiling is
  invisible. An overlay is the right place to *tune* a bound and the wrong
  place to *first learn one exists*.
- **Refuse to compile an agent with no declared policy.** Rejected as too
  sharp: it would block every existing design on a field they have never had,
  to fix a problem the explicit default already fixes.

## Verification
- No generated workload is emitted without an explicit bound, for any provider
  profile.
- An agent that declared no policy is emitted with the platform default, and
  the output says it is a default.
- A declared bound a provider cannot express appears in `MAPPING.md` and the
  agent's conformance entry, and is not silently dropped.
- `min > max`, `max < 1` and `concurrency < 1` are refused at validation.
- An agent with `min_instances: 0` carries the handle-loss consequence in its
  conformance entry.
- The generated output states both the instance-level ceiling and
  `max_parallel_subagents`, and does not present either as the other.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. Scale declared as a neutral `ScalingPolicy` on the agent, carried to the IR, emitted with an explicit bound by every provider profile, and reported where a cloud cannot express it. |
