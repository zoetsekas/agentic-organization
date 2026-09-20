---
id: ADR-0017
title: Data is classified in the spec, and classification drives access and placement
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering, Platform Architecture]
consulted: [Compliance, Data Platform]
informed: [All engineering]
scope: [spec, security, runtime, targets]
workstreams: [WS-004, WS-002]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0009, ADR-0010]
tags: [security, data]
---

# ADR-0017: Data is classified in the spec, and classification drives access and placement

## Context
Agents accumulate and share knowledge. Without classification, everything an
agent learns is either siloed — making collaboration useless — or global,
making the first regulated record a compliance incident. Access decisions,
sandbox mounts and storage placement all need the same answer to one question:
what class of data is this?

## Decision
The spec declares **data classes**, each with a sharing scope and a handling
policy. Three scopes are built in:

* **private** — the owning agent and its human counterpart;
* **protected** — the teams or groups named on the record;
* **public** — readable organization-wide; any agent may contribute.

Additional classes (e.g. `regulated`) are declared with their own constraints:
which environment classes may mount them, whether they may leave a region,
whether they may appear in traces.

Classification drives three things mechanically: **access** (permissions are
written against data classes, ADR-0008), **placement** (which environment class
may mount it, ADR-0009), and **egress** (whether a capability may return it).
Writing to a protected class requires membership in that class's group — the
check is on the writer, not merely on the grant.

## Scope
Data handled by agents: memory, knowledge, capability results. Excludes the
platform's own operational data.

## Implementation
`spec.model.DataClass` → IR grants → `data/planes.py` enforcement, which raises
on denial rather than returning empty so refusals appear in the trace. Targets
map classes onto storage with the placement constraints applied.

## Timeline
Phase 1; the three built-in scopes already exist in the runtime.

## Advantages
- One classification answers access, placement and egress consistently.
- Public knowledge can be genuinely shared without weakening confidentiality.
- Regulated data gets a declarative home instead of a convention.
- Denials are visible in traces, so over-restriction is diagnosable.

## Disadvantages
- Classification is manual and will be wrong; mislabelled data is a silent
  failure with no automatic detection.
- Three built-in scopes are coarse — real organizations have more nuance, and
  custom classes reintroduce complexity.
- Group-based protection depends on group hygiene, which decays.
- Reclassifying existing records is a migration nobody will want to run.

## Alternatives considered
- **Per-record ACLs** — precise and unmanageable at agent scale.
- **No classification, one shared store** — simple, and fails the first audit.
- **Inferred classification from content** — attractive, unreliable, and a
  false negative is a breach.

## Verification
Tests cover private isolation, protected group checks in both directions,
public contribution, and rejection of writes to groups the writer is not in.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Declared classes drive access, placement and egress. |
