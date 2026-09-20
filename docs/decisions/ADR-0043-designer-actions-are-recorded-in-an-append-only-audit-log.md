---
id: ADR-0043
title: Designer actions are recorded in an append-only audit log
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering, Platform Architecture]
consulted: [Product]
informed: [All engineering]
scope: [designer, security]
workstreams: [WS-021, WS-020, WS-022]
supersedes: []
superseded_by: []
related: [ADR-0031, ADR-0032, ADR-0033]
tags: [designer, security, audit]
---

# ADR-0043: Designer actions are recorded in an append-only audit log

## Context
The designer is multi-user (ADR-0031), it has five roles that refuse things
(ADR-0032), and it has locks and a three-way merge that let one person's write
land on top of another's (ADR-0033). Between them that is a system in which
"who changed this, and when" is a question people will ask — and until now the
only answer available was revision authorship.

Revision history is a poor audit trail. It records accepted writes and nothing
else, so it is silent about exactly the events that matter when something has
gone wrong: a refused delete, a broken lock, a merge that quietly resolved
someone else's edit away, a settings change that turned merge-on-conflict on.
A denial in particular leaves no trace anywhere in the product — there is no
revision, no lock, no record of any kind — so unless it is written down at the
moment of refusal it is simply lost.

## Decision
Every designer action that changes state, and every action the RBAC layer
refuses, is written to an **append-only audit log** at the moment it happens.

Three properties are binding:

* **Append-only.** `AuditLog` exposes `record` and `query` and nothing else.
  There is no update and no delete anywhere in the module, the service or the
  HTTP API, so tampering means reaching past the product into the storage
  backend — which is the right place for that decision to be visible.
* **It rides the existing `Repository` protocol.** `append_audit` and
  `audit_events` join the protocol and are implemented by all three backends,
  so the log lives wherever the designs live rather than in a fourth store a
  deployment has to remember to back up.
* **Denials are first-class.** A refused action is recorded with the actor, the
  permission that was missing and the reason the decision gave, because that is
  the event an audit log exists for.

Auditing never breaks the operation it describes: a failure to persist an event
is caught and logged at ERROR with the lost event, and the operation proceeds.
Silently dropping it would make the log's gaps invisible; failing the write
would mean a full disk stops people saving their work.

Reading the log needs the new `audit.read` permission, held by **admin** and
**owner** only. An auditor's view names people and what they were refused, so
it belongs with membership and settings, not with `system.view`.

## Scope
Human actions in the designer: systems, locks, merges, revisions, membership
and settings. It does not cover agent runtime activity (that is observability,
WS-010), and it is not a security event pipeline — there is no export, no
alerting and no SIEM integration here.

## Implementation
`designer/audit.py` holds `AuditEvent`, `AuditAction`, `AuditOutcome` and
`AuditLog`. `designer/repository.py` gains `append_audit`/`audit_events` on the
protocol and in `MemoryRepository`, `FileSystemRepository` (one JSON file per
event under `audit/`, never reopened) and `SqlRepository` (a document
collection parented by system id). `DesignerService` records through a
`_require` helper that decides, writes the denial, then raises; the
`LockManager` gained an `on_expire` callback so an expiry, which has no request
behind it, still reaches the log. `GET /api/designer/audit` reads it, filtered
by system, actor, action and time range, newest first with a total order of
(timestamp, id).

## Timeline
Phase 3, WS-021 M5.

## Advantages
- "Who changed this system, and when" has an answer that includes attempts, not
  just accepted writes.
- Denials are visible, which turns a silent 403 into a reviewable signal — both
  for misconfigured access and for someone probing what they cannot reach.
- The log persists in every backend, so a filesystem deployment keeping its
  designs in git keeps its audit trail in git too.
- Append-only with no write API means the product cannot be used to rewrite its
  own history.

## Disadvantages
- **Retention is unbounded.** Nothing prunes the log, and unlike revisions it
  has no cap. A busy installation grows it forever, and neither rotation nor
  archival exists yet.
- **It contains personal data.** Actor ids, display names and denial reasons
  naming people are written to disk; the `detail` field can carry system and
  member names. A deployment subject to erasure requests has no tool here to
  satisfy one, and append-only is in direct tension with that obligation.
- **The actor is only as trustworthy as the proxy in front of it.** Identity
  still arrives in a request header (ADR-0032); anyone who can reach the API
  directly can write any name they like into the log. OIDC is WS-021 M3 and
  remains open, so until it lands the audit trail is attributable only under
  the assumption that an authenticating proxy is deployed — and nothing in the
  product enforces that.
- Append-only is a product-level promise, not a storage-level one: an operator
  with database or filesystem access can still edit history.
- Filtering by actor and time happens in the service rather than the store, so
  a very large log is read more broadly than it needs to be.

## Alternatives considered
- **Extend revisions with denied attempts** — cheap, but it corrupts the
  meaning of a revision (a snapshot of a design) and still has nowhere to put
  lock and settings events.
- **A separate append-only store (a log file, or a dedicated table)** — better
  tamper-resistance, but a fourth persistence mechanism that the filesystem and
  memory backends would have to grow independently, and one more thing to back
  up.
- **Emit to the platform's observability pipeline instead** — right for volume
  and retention, wrong for a feature that must work in a laptop install with no
  collector running, and it would put access control for the log outside the
  product.
- **Fail the operation when the audit write fails** — honest fail-closed
  behaviour, rejected because it lets a storage problem stop people working; the
  ERROR log leaves the choice visible to an operator who wants it.

## Verification
`tests/test_designer_audit.py` asserts an event for each audited action, a
recorded denial, identical behaviour across all three repository backends,
correct filtering by system, actor and time range, and — by inspecting the
public surface of `AuditLog`, `DesignerService` and the API routes — that no
mutation or deletion of an event is reachable.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Append-only designer audit log across all three backends. |
