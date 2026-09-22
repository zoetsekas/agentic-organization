---
id: ADR-0091
title: One extension seam for targets, runtimes and clouds
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Security Engineering]
informed: [All engineering]
scope: [compiler, runtime, cli]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0005, ADR-0067, ADR-0073]
tags: [plugins, extensibility, targets, runtime, designer]
---

# ADR-0091: One extension seam for targets, runtimes and clouds

## Context
A design is vendor-neutral by construction (ADR-0002); the implementation
phase is where vendors live. Three of those lists shipped closed:

* the target registry was a hardcoded block guarded by "is the registry
  empty?", which meant a caller who registered their own target *first*
  silently lost every built-in — a real defect, not just a limitation;
* `Runtime` is an enum, so a third-party agent framework could not even be
  *named*, let alone dispatched to;
* Terraform's `PROFILES` was a module dict with no public way in.

There was no entry-point discovery anywhere. Every integration was therefore a
fork of this package, and every fork a standing merge conflict.

A second problem sat underneath: frameworks are **not interchangeable**. Deep
agents has sub-agents, an interrupt gate, skills and a planning tool; the
OpenAI Agents SDK has handoffs and none of those. Anything that renders or
reasons about provider-specific fields — the designer above all — would have to
hardcode one vendor's list and be wrong for the next.

## Decision
One shared mechanism in `orgagents.plugins`, used by all three registries.

**`Registry`** holds implementations by id. It refuses to shadow an existing id
unless asked (`replace=True`), because a plugin that silently replaced a
built-in would change what a design compiles to with nobody deciding that. It
discovers third-party entries from Python **entry points**, so `pip install`
is the whole installation step. Loading built-ins is a one-shot flag rather
than an emptiness check, which fixes the defect above and makes registration
order irrelevant. A plugin that fails to import is recorded in `failures` and
skipped rather than taking the platform down, and `require()` fails naming
what *is* available instead of raising a bare `KeyError`.

**`ProviderDescriptor`** says what an implementation supports, drawn from one
shared `FEATURES` vocabulary (`subagents`, `handoffs`, `interrupt_on`,
`skills`, `long_term_memory`, `planning`, `streaming`, …). A descriptor that
claims a word the platform does not know is refused at construction, so the
vocabulary is a contract rather than a free-text tag. This is what keeps the
core common and the edges pluggable: the designer asks
`supporting("handoffs")` and offers the field for the runtimes that have it,
rather than hardcoding a vendor.

The three entry-point groups are `orgagents.targets`,
`orgagents.runtime_adapters` and `orgagents.provider_profiles`. The adapter
registry is keyed by the *string* a `Runtime` member carries, which leaves room
for an id the enum will never hold while every built-in keeps working. A new
cloud is a `ProviderProfile` plus `register_provider_profile()`.

`orgagents providers` prints what is installed and what each one claims, with
`--feature` to ask who supports one and `--format json` for the designer.

A descriptor is a claim about behaviour, not aspiration: a provider that says
`interrupt_on` must actually pause for a human, for the same reason a target
publishes a conformance report rather than pretending (ADR-0073).

## Scope
`orgagents.plugins` (new), the target registry, the runtime adapter registry,
Terraform provider profiles, the CLI, and the entry-point groups declared in
`pyproject.toml`. No change to the spec, the IR, the phase gate, or what any
existing target generates.

## Implementation
`src/orgagents/plugins.py` (`Registry`, `ProviderDescriptor`, `FEATURES`,
`PluginError`). `compiler/base.py` re-expresses `TargetRegistry` over it while
keeping `.targets`/`.register()`/`.ids()` for existing callers, and
`register_builtin_targets()` now loads built-ins once and then discovers.
`runtime/adapters.py` gains `ADAPTERS`, `runtime_key()`,
`register_builtin_adapters()` and per-runtime descriptors; `adapter_for()`
resolves through the registry. `compiler/targets/terraform.py` gains
`register_provider_profile()` and `discover_provider_profiles()`.
`cli.py` gains `providers`.

## Timeline
Accepted 2026-09-22.

## Advantages
- An integration is an installable distribution, not a fork.
- The built-in-wiping defect is fixed, and registration order no longer
  changes the result.
- Provider differences are data the designer can read, so vendor-specific
  fields stop being hardcoded anywhere.
- A broken or mistyped plugin produces a message naming the real ids rather
  than a `KeyError` from a dict the integrator cannot see.

## Disadvantages
- The `FEATURES` vocabulary is now a compatibility surface: adding a word is
  cheap, changing the meaning of one is not, and a provider claiming a feature
  it does not really have is a lie the platform cannot detect.
- Built-in target support sets live in a table in `compiler/base.py` rather
  than on each target, so a new built-in must be added in two places (a
  third-party target avoids this by publishing its own `descriptor`).
- Entry-point discovery imports third-party code at registry load, which is a
  trust decision an operator makes by installing the distribution.

## Alternatives considered
- **A config file listing plugin import paths.** Rejected: entry points are
  the packaging ecosystem's own answer and need no extra file to keep in sync.
- **Widening `Runtime` to a free string everywhere.** Rejected as the first
  move: keying the registry by the enum's *value* gets third-party ids in
  without touching every comparison, and the enum still documents what ships.
- **Letting a plugin override a built-in silently.** Rejected: it would change
  a compile's output with nobody deciding it. `replace=True` keeps that
  deliberate.

## Verification
`tests/test_plugins.py`: a descriptor cannot claim an unknown feature;
registering never silently shadows; loading built-ins is idempotent and
order-independent (the defect, held by a test); a missing id names what is
available; a broken plugin is reported rather than fatal; `supporting()`
answers the designer's question; a third-party target coexists with the
built-ins; a third-party runtime dispatches despite the closed enum; a new
cloud is a profile plus a call; and the built-in runtimes genuinely differ —
`handoffs` is OpenAI's alone, `planning` deep agents' alone, while all share
the portable core.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. One `Registry` + `ProviderDescriptor` behind targets, runtime adapters and Terraform provider profiles; entry-point discovery; `orgagents providers`; the built-in-wiping registry defect fixed. |
