---
id: ADR-0092
title: Bring your own implementation — templates, and overlays that work
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering]
informed: [All engineering]
scope: [compiler, cli]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0011, ADR-0014, ADR-0050, ADR-0073, ADR-0091]
tags: [plugins, templates, overlays, extensibility]
---

# ADR-0092: Bring your own implementation — templates, and overlays that work

## Context
ADR-0091 opened the registries, so a new target is an installable Python
plugin. Two gaps remained either side of it.

**Below it**, a Python plugin is more ceremony than much of the demand
deserves. A Helm chart, a Nomad job, an internal YAML a platform team already
has a schema for, a one-page inventory for an auditor — none of those need a
`Target` class, and requiring one turns a ten-minute job into a package.

**Beside it**, every generated file carried the line *"Put customizations in
overlays/"*, and nothing implemented it. The engine created the directory and
never read it again. Worse, the advice was wrong per target in opposite
directions: Compose needs an explicit `-f` chain that the generated `Makefile`
did not build, while Terraform auto-loads `*.tf` from the **root module only**
and never descends into a subdirectory — so a `.tf` placed in `overlays/` was
silently inert, while a `.tf` placed at the root already worked and nothing
said so. A single sentence could not be true for both.

## Decision
**A template target.** `orgagents compile … --target template --template-dir
./my-templates` renders every `*.tmpl` under a directory against the IR. No
Python, no fork, no entry point. Substitution is `string.Template` (`$name`),
the standard library's — deliberately not a template engine. This codebase
builds all of its own output as strings, and taking a Jinja-shaped dependency
so users could have loops would be a large decision made on their behalf.
Iteration is expressed in the **filename** instead: a template whose path
contains `{agent}` renders once per agent, which covers the case that motivates
most of these. An unknown `$placeholder` is left exactly as written rather than
failing the compile — it is far more often a shell variable than a mistake —
and every one is listed in `CONFORMANCE.md` so a real typo stays findable. The
target ships `system.ir.json` beside the output, so a template is never the
ceiling, and its conformance report refuses to claim anything about what
somebody else's templates carry (ADR-0073). Wanting conditionals or loops is
the signal to write a Python target instead, and the report says so.

**Overlays, made real, per target's own idiom.** The generated `Makefile` now
builds the Compose `-f` chain from `sorted(overlays/*.yaml)`, so `make up`
includes them and `make config` shows the merged result. Each target emits an
`overlays/README.md` explaining how to extend *that* target: for Compose, drop
a YAML here; for Terraform, **put your `*.tf` in the directory above**, because
that is the root module and the compiler never touches a file it did not
generate (ADR-0014). Both say the same limit plainly: an overlay changes the
deployment, never the design. It cannot grant a permission, widen a sandbox or
remove an approval, because those resolve once from the spec into the IR long
before any HCL or YAML is written — a control you can edit away in a compose
file was never a control.

## Scope
A new `compiler/targets/template.py`, the `local` and `terraform` targets'
overlay guidance and Makefile, and a `--template-dir` flag on `compile` and
`targets`. No change to the spec, the IR, the phase gate, or what any existing
target generates beyond the Makefile's compose invocation.

## Implementation
`TemplateTarget` with `system_context()` / `agent_context()` (every value a
string, because `string.Template` substitutes into text), `_placeholders()` for
the unresolved report, and a conformance page. `local.py` gains
`_overlays_readme()` and a `COMPOSE` variable threaded through every recipe;
`terraform.py` gains `_overlays_readme()`. The target registers only when
`--template-dir` is given, since it is inert without one.

## Timeline
Accepted 2026-09-22.

## Advantages
- The cheap end of the extension spectrum exists: a Nomad job or Helm chart is
  a template, not a package.
- A promise printed on every generated file is now true, and true in the way
  each target actually works rather than in one sentence that fitted neither.
- No new dependency: `string.Template` is stdlib, and the limits are stated
  rather than papered over.
- An operator can extend a stack without editing generated files, and a
  regeneration cannot lose their work.

## Disadvantages
- `string.Template` has no loops or conditionals, so anything structural needs
  the filename convention or a Python target. That is a real ceiling and the
  conformance report names it.
- The template context is a hand-maintained flat dict of strings; a field the
  IR gains is not automatically exposed, though `system.ir.json` always is.
- `safe_substitute` cannot distinguish a typo from a shell variable, so the
  unresolved list needs reading rather than trusting.
- Overlay guidance now lives per target, which is more text to keep true than
  one shared sentence — but one shared sentence was the defect.

## Alternatives considered
- **Take a Jinja2 dependency.** Rejected for now: it is a large decision to
  make on a user's behalf, and the Python target seam already covers everything
  a template engine would. Worth revisiting if the filename convention proves
  too thin in practice.
- **Fail the compile on an unknown placeholder.** Rejected: `$HOME` in a shell
  fragment is legitimate and common, and dying on it would make ordinary
  templates unusable.
- **Make `overlays/` work uniformly for Terraform too** (a `module` block, or
  copying files up). Rejected: a module has its own scope and could not
  reference the generated resources, which is the whole point; and copying
  files into the root would blur whose file is whose.

## Verification
`tests/test_template_target.py`: a `{agent}` template renders once per agent
with the suffix stripped; a system template renders once; the design reaches
the template (description, team, what needs approval); an unknown placeholder
is left untouched *and* reported; the IR is shipped alongside; the conformance
report names roles, separations and approvals as somebody else's to enforce; a
missing directory fails clearly; the descriptor claims only the core it really
exposes. And for overlays: the Makefile builds the `-f` chain rather than
calling `docker compose` bare, each target explains its own idiom (Compose
merges, Terraform says "not in here"), and both state that an overlay can never
edit away a control.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. A `template` target rendering `*.tmpl` against the IR with `string.Template`; overlays implemented per target (Compose `-f` chain, Terraform root module) with an `overlays/README.md` each. |
