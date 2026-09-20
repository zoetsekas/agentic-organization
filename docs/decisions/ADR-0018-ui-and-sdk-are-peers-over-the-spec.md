---
id: ADR-0018
title: The designer UI and the SDK are equal clients of the same spec and API
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Product, Platform Architecture]
consulted: [Developer Experience]
informed: [All engineering]
scope: [ui, sdk, spec]
workstreams: [WS-009]
supersedes: []
superseded_by: []
related: [ADR-0003, ADR-0004]
tags: [product, devex]
---

# ADR-0018: The designer UI and the SDK are equal clients of the same spec and API

## Context
Two audiences define agentic systems: architects and domain owners who want a
visual designer, and platform engineers who want code in version control with
review and CI. Products usually serve one and bolt on the other, and the bolted
-on path is always behind — a UI that can express things the SDK cannot, or an
export that produces something the UI can no longer open.

## Decision
The UI and the SDK are **peers**. Both produce and consume the same System Spec
document (ADR-0004) through the same API. Neither has private fields, and a
round trip through either must be lossless: a spec authored in the SDK opens in
the UI and vice versa, preserving comments and ordering where the format allows.

Feature work lands in the spec first; the UI and SDK then expose it. A capability
reachable from only one of them is a defect, not a roadmap item.

## Scope
The designer UI, the Python SDK and the spec API. Does not require UI parity for
*operational* views — traces, metrics and the marketplace are UI surfaces whose
SDK equivalent is the plain API.

## Implementation
The spec model is the shared schema; the SDK is a typed builder over it; the UI
edits the same document via the API and validates with the same validator.
Round-trip tests run in both directions over the example spec.

## Timeline
Phase 2 for SDK parity on the current spec; maintained as an invariant after.

## Advantages
- One authoring model; teams mix visual and code authoring on the same system.
- Specs live in git with review and CI regardless of how they were authored.
- No second-class export path to rot.
- Validation logic exists once, so both clients reject the same mistakes.

## Disadvantages
- Parity is a permanent tax: every spec feature needs UI work before it ships.
- The UI is constrained to what the spec can express, so genuinely visual
  affordances (layout, annotations) need somewhere neutral to live or are lost.
- Lossless round-tripping of YAML comments and ordering is fiddly and will have
  edge cases.
- Shipping is gated on the slower of the two clients.

## Alternatives considered
- **UI-first with export** — fastest to demo; export becomes lossy and
  untrusted.
- **SDK-first with a read-only viewer** — serves engineers, excludes the primary
  audience.
- **Separate models with a converter** — guarantees permanent drift.

## Verification
Round-trip tests (SDK → spec → UI payload → spec) assert byte-stable output for
the example spec, and a test asserts the UI's component palette is derived from
the spec schema rather than hand-listed.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. UI and SDK are peers over one spec. |
