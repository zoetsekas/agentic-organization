---
id: ADR-0036
title: Context is managed by offloading and compaction, over a declared workspace
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Platform SRE, Data Platform]
informed: [All engineering]
scope: [spec, compiler, runtime]
workstreams: [WS-024]
supersedes: []
superseded_by: []
related: [ADR-0028, ADR-0009, ADR-0022, ADR-0025]
tags: [runtime, cost]
---

# ADR-0036: Context is managed by offloading and compaction, over a declared workspace

## Context
deepagents makes context management a named concern: summarize long threads,
offload tool outputs to disk, and give the agent a filesystem over pluggable
backends. We had neither, and the omission shows up as cost and failure.

A warehouse query returning 200 KB put 200 KB in the context window. A trigger
running an hour-long close pack grew its thread until it hit the model's limit
or became expensive enough to notice. And an agent had nowhere to put a working
file: memory is what it *learned* and a sandbox is where it *executes*, so
large intermediate material either sat in context or was thrown away.

## Decision
Two mechanisms and one new object, all declared in the spec.

**Artifact store.** A workspace agents read and write files in, with a sharing
scope, permitted data classes, retention, and size limits per file and in
total. It is deliberately a *third* thing beside memory and the sandbox:
conflating a scratch file with memory is how working material becomes permanent
knowledge nobody classified.

**Offloading.** A tool result larger than a declared threshold is written to the
artifact store and replaced in context by a reference plus the first 400
characters. The agent reads it back — whole or in part — only if it needs to.

**Compaction.** Past a token threshold, older turns are summarized and the most
recent kept verbatim, with the summary retained as a session memory so what was
compressed is recoverable. Compaction that would **not reduce** tokens is
refused: a summary longer than what it replaces is not a summary, and applying
one would cost tokens and lose detail simultaneously.

Both thresholds are per system with per-agent overrides, because an engineering
agent reading build logs and an executive agent routing requests have genuinely
different context profiles.

## Scope
Context window management and agent working files. It does not cover memory
(ADR-0028), sandbox execution (ADR-0009) or session tracing.

## Implementation
`spec.model.ArtifactStore` and `ContextPolicy`; `context.py` holds
`ArtifactWorkspace` (write, read with offset/limit, list, expire, limit
enforcement) and `ContextManager` (`should_offload`, `offload`, `compact`).
Artifacts carry a data class and inherit their store's scope, so reading one
follows the same sharing rules as anything else. A full store makes room
oldest-first rather than failing a run. The runtime exposes `artifact_write`,
`artifact_read` and `artifact_list`, and offloads oversized tool results,
logging a `context_offload` event.

## Timeline
Phase 3. A model-backed summarizer is pluggable and currently defaults to a
structural summary.

## Advantages
- Long runs stop growing without bound, so overnight work is affordable.
- Large results stay reachable without occupying the window.
- Working files have a classified, scoped, expiring home.
- Refusing unhelpful compaction avoids the worst outcome: paying more for less.
- Per-agent overrides fit genuinely different context profiles.

## Disadvantages
- **The default summarizer does not summarize.** Without a model it keeps the
  first and last turns and counts the rest, which is honest but lossy; real
  compaction quality depends on a summarizer the deployment supplies.
- Token estimation is four-characters-per-token, which is wrong per model and
  only good enough to decide *when* to act.
- Offloading makes a result one tool call away instead of present, and an agent
  that does not read it back will reason from a 400-character excerpt.
- Compaction loses detail by construction; the retained summary memory mitigates
  it but does not restore the original thread.
- A third storage concept alongside memory and sandboxes is more for a designer
  to hold in mind, and the boundary between "artifact" and "memory" will be
  argued about.

## Alternatives considered
- **Let the framework handle context** — each adapter does it differently, so
  identical designs would behave differently and cost differently per runtime.
- **Put working files in memory** — conflates learning with scratch space and
  drags classification and retention rules somewhere they do not fit.
- **Unbounded context, rely on model limits** — fails late, expensively, and
  usually during an unattended run.
- **Always compact on a fixed schedule** — predictable and wasteful on short
  threads.

## Verification
Tests cover offloading large results and reading them back, leaving small ones
alone, per-file and total limits (including making room oldest-first), refusing
to store an unreadable or undeclared data class, scope-respecting reads,
expiry, compaction that keeps recent turns, compaction refused when it would
not save tokens, and a supplied summarizer being used.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Declared artifact stores, offloading and compaction with a no-regression rule. |
