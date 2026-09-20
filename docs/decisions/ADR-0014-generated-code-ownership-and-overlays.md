---
id: ADR-0014
title: Generated artifacts are owned by the compiler; customization goes in overlays
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Developer Experience]
consulted: [Platform SRE]
informed: [All engineering]
scope: [compiler, targets]
workstreams: [WS-005, WS-006, WS-007]
supersedes: []
superseded_by: []
related: [ADR-0003, ADR-0005, ADR-0012]
tags: [compiler, devex]
---

# ADR-0014: Generated artifacts are owned by the compiler; customization goes in overlays

## Context
Every code generator faces the same question the first time a user edits its
output: is regeneration safe? If generated files are hand-edited, the next
compile either destroys the edit or must merge it, and both outcomes erode trust
in the generator. But users *will* need changes the spec cannot express.

## Decision
Generated output is **compiler-owned and regenerated wholesale**. Every
generated file carries a header naming the spec, the spec version and the
compiler version, and a warning that edits are lost.

Customization has exactly three sanctioned routes:

1. **Spec change** — the preferred answer; if it cannot be expressed, that is
   product feedback.
2. **Overlay files** — user-owned files in `overlays/` merged over generated
   output at fixed extension points (extra Terraform in `overlays/*.tf`,
   subclasses and hooks in `overlays/python/`). Overlays are never overwritten.
3. **Fork** — take the output and own it, with the compiler out of the loop.
   Legitimate, and the generated README documents how.

The compiler writes a `manifest.json` of every file it produced with a content
hash, so it can detect and refuse to clobber a modified generated file unless
`--force` is given.

## Scope
All target output. Does not apply to the spec or binding, which are user-owned
by definition.

## Implementation
`compiler.engine` writes the manifest and performs hash checks;
`GeneratedFile` carries `path`, `content` and `overwrite` semantics; overlay
merging is target-specific but the directory convention and precedence are
shared.

## Timeline
Phase 2, with the first target — retrofitting ownership rules later is how
generators become unsafe.

## Advantages
- Regeneration is always safe and predictable, so users keep using it.
- Drift is detectable; the manifest makes "what changed" answerable.
- Spec-first pressure: the easy path is to improve the design, not patch output.
- Forking is explicit and documented rather than an accident.

## Disadvantages
- Overlays are a second mental model and a new source of merge surprises.
- The extension points we choose now constrain what users can customize later,
  and we will choose some of them wrong.
- Users who want "just this one line" find overlays heavyweight, and will edit
  generated files anyway; `--force` will be used routinely.
- Hash checking adds friction to legitimate manual experimentation.

## Alternatives considered
- **Protected regions in generated files** — familiar, but fragile under
  refactoring and makes generated files harder to read.
- **Generate once, then hand over** — simple, but abandons the design as the
  source of truth after the first deploy.
- **Full three-way merge** — powerful, and a substantial engineering effort with
  its own failure modes.

## Verification
Tests assert regeneration is byte-identical for an unchanged spec, that a
modified generated file blocks regeneration without `--force`, and that overlay
files survive regeneration untouched.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Compiler-owned output, overlays, manifest with hashes. |
