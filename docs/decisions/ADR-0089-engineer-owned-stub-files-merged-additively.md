---
id: ADR-0089
title: Engineer-owned stub files, merged additively on regeneration
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture]
consulted: [Runtime Engineering]
informed: [All engineering]
scope: [compiler]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0014, ADR-0073, ADR-0087]
tags: [target, tools, regeneration, codegen]
---

# ADR-0089: Engineer-owned stub files, merged additively on regeneration

## Context
The platform targets emit typed function stubs for tools whose code is the
host's (ADR-0087), for an engineer to implement. But those stubs sat in a file
carrying the standard generated header — "Do not edit: regenerated on every
compile" — and were rewritten on every compile. The moment an engineer began
implementing them, the next `compile` would overwrite the work. That is the
opposite of what the file is for, and it is a trap.

The engine already had two protections for generated files: a manifest that
detects a hand-edited generated file and refuses to overwrite it unless
`--force` (ADR-0014), and `preserve_if_exists` for write-once samples. Neither
fits a file that must both accept new stubs over time *and* never lose an
implementation: the manifest guard blocks the whole compile on any edit, and
`preserve_if_exists` would freeze the file so a newly-designed tool never gets a
stub.

## Decision
The stub module (`graphs/tools.py`, `agents/tools.py`) is **engineer-owned and
merged additively**. A new `GeneratedFile.merge_additive` mode, honoured by the
engine's writer, means: on regeneration the file's existing top-level functions
are left exactly as the engineer left them — implemented, half-implemented or
still a stub — and only a function the file is *missing* (a tool the design
newly requires) is appended, under a banner that says so. A regeneration never
rewrites or removes an implementation, and it never duplicates an existing
function. If the file does not parse (an engineer mid-edit), nothing is
appended rather than risk corrupting it.

The file's header changes accordingly: it says the file is the engineer's, that
regeneration is additive-only, and to implement each body in place. The real
generated clients (`_backends.py`) and the agent modules keep the ordinary "do
not edit" contract and the manifest guard, because nothing there is the
engineer's to change.

The completeness check is free: each agent module does
`from .tools import <the tools it has>`, so a design that adds a tool the
engineer has not yet implemented fails loudly at import, naming the missing
function, rather than running with a silent gap.

## Scope
The compiler's file writer (`GeneratedFile.merge_additive`, `_merge_additive`
in `engine.py`) and the two platform targets' stub module. No spec or IR
change.

## Implementation
`GeneratedFile.merge_additive` (base.py); `_merge_additive` and
`_top_level_defs` in `engine.py`, invoked from `_write` before the manifest
guard; `CompileResult.merged` records which stubs were appended, and the
summary line reports them. `stub_module` (in `targets/_wiring.py`) carries the
engineer-owned header, and both targets emit `tools.py` with
`merge_additive=True` instead of the generic "do not edit" header.

## Timeline
Accepted 2026-09-22.

## Advantages
- An engineer's implementation is never lost to a re-compile — the central
  worry when generated code and hand-written code share a tree.
- A newly-designed tool still arrives as a stub automatically, appended rather
  than requiring a manual diff.
- The "do not edit" vs "this is yours" distinction is now truthful per file,
  and a missing implementation fails at import rather than silently.

## Disadvantages
- Additive merge keys on the top-level function name, so renaming a tool in the
  design appends a new stub and leaves the old function as dead code (harmless,
  but the engineer must delete it).
- A removed tool's function is not cleaned up automatically.
- Merge is by function name, not signature; if the design changes a tool's
  input schema, the engineer's existing function keeps the old signature and
  must be updated by hand (the agent import still works; the change is visible
  in the regenerated reference).

## Alternatives considered
- **`preserve_if_exists` on the stub file.** Rejected: it freezes the file, so a
  tool added later never gets a stub.
- **Rely on the manifest guard (error unless `--force`).** Rejected: it blocks
  the entire compile on any edit and pushes engineers toward `--force`, which
  discards their work — the very outcome to avoid.
- **Full AST merge that also updates changed signatures.** Rejected for now as
  more machinery and more ways to surprise the engineer than the additive rule;
  the regenerated reference plus the import-time failure cover signature drift.

## Verification
`tests/test_stub_regeneration.py`: an implemented function is left byte-for-byte
unchanged; a new tool is appended without touching or duplicating the old; an
unparseable file is never corrupted; a second `compile_system` into the same
directory preserves an implementation and reports the stub file skipped; and
`_backends.py` keeps the "do not edit" header and manifest guard.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. `GeneratedFile.merge_additive`: the stub module is engineer-owned and only ever gains new stubs on regeneration; existing implementations are never rewritten. |
