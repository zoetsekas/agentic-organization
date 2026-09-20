---
id: ADR-0044
title: Pairings are reconciled against a people directory, which may know nothing
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Compliance]
consulted: [Product, Security Engineering]
informed: [All engineering]
scope: [spec, security, docs]
workstreams: [WS-016, WS-013]
supersedes: []
superseded_by: []
related: [ADR-0026, ADR-0021, ADR-0008]
tags: [human-in-the-loop, governance]
---

# ADR-0044: Pairings are reconciled against a people directory, which may know nothing

## Context
ADR-0026 requires every agent to have exactly one accountable owner, and lets
approvers, reviewers, escalation contacts, operators and stakeholders be named
alongside. All of these name individuals by contact, and individuals leave.

Nothing in the system noticed. A spec whose owner left the company last quarter
validates clean, compiles, and routes gated actions to a mailbox that nobody
reads — so the guarantee ADR-0026 states ("someone is accountable") quietly
degrades into "someone was accountable once". WS-016 recorded this as the
failure mode most likely to bite, and it is a governance hole rather than a
cosmetic one: an approval that lands nowhere is indistinguishable, from inside
the system, from an approval that was granted late.

The obvious fix — call the corporate directory — brings its own hazards. A
directory is an external system that goes down, does not hold everyone
(contractors, shared aliases, partners), and is joined to our specs on a
contact string that people change. A naive integration converts every one of
those into a build failure.

## Decision
Human pairings are **reconciled against a people directory**, and the directory
is allowed to have no opinion.

Three properties are binding:

* **Three answers, not two.** A lookup returns *active*, *departed*, or
  *unknown to me*. "Unknown" is never reported as a departure and never carries
  a departure's severity; they have different remedies (fix the record vs.
  reassign the agent) and reporting them as one would be wrong.
* **Silence by default.** The default `NullDirectory` knows nothing and says
  so. No directory configured, an empty directory, or a directory that could
  not be reached produces **zero** findings — never "everyone is present"
  (a lie) and never "everyone is unknown" (noise). A directory outage
  degrades reconciliation to no opinion rather than failing a build.
* **Severity follows accountability.** A confirmed departed *owner* (or mission
  sponsor) is a warning that is promoted to an error in production, exactly as
  the other mandatory pairing rules are (ADR-0008). Any other confirmed
  departure is a warning. An unknown contact is a warning always, even for an
  owner, even in production.

## Scope
Binds the spec validator and any caller that passes a directory: the CLI,
phases, the compiler and the designer all take the same optional argument and
behave identically without one. It covers `AgentSpec.humans` in every role and
`Mission.sponsor`.

It does **not** cover platform identity or SSO for the designer itself (that is
WS-021), it does not write to the directory, and it does not reassign anything:
reconciliation reports, a human decides.

## Implementation
`src/orgagents/directory.py`:

* `Directory` — a protocol with `lookup(contact) -> Person` and
  `knows_anyone()`. `Person` carries status, display name, groups and manager
  where the source exposes them.
* `NullDirectory` — the default; knows nothing, `knows_anyone()` is False.
* `StaticDirectory` — from a dict or a JSON/YAML file, for local runs and
  tests; authoritative about the people it holds.
* `FetchDirectory` — an adapter over an **injected** fetch callable, so an
  LDAP, SCIM or Graph client lives outside this package and is not a
  dependency. Payload spellings (`active`, `accountEnabled`, `displayName`)
  are mapped centrally; provider errors become `DirectoryUnavailable`.
* `reconcile(spec, directory)` — walks every pairing and returns a
  `ReconciliationReport` whose `consulted` flag records whether the directory
  had anything to say.

`spec/validate.py` gains `directory_findings(spec, directory)` and an optional
`directory` argument on `validate_spec`, emitting `departed_owner`,
`departed_human` and `human_unknown_to_directory` through the existing
`err`/`warn` conventions.

## Timeline
WS-016 M4, Phase 3, landed 2026-09-20. Wiring a real provider adapter and
surfacing reconciliation in the designer follow in WS-016 M5.

## Advantages
- The ADR-0026 guarantee becomes checkable over time instead of at one instant.
- Departures are found by a validator rather than during an incident.
- No network dependency enters the package: the transport is injected, so the
  same code path is exercised by a stub in CI and by LDAP in an enterprise.
- Installations without a directory are exactly as they were — silent.

## Disadvantages
- **A directory outage must not fail a build**, so the safe behavior is to
  report nothing; that means a reconciliation pass can silently check nothing
  at all, and a clean run is not proof that anyone was checked. The report's
  `consulted` flag exposes this, but a caller can ignore it.
- **Contact identifiers are a weak join key.** A person who changes surname,
  moves domain, or is reached on a shared alias will read as unknown while
  being perfectly present, and someone whose alias is recycled will read as
  active while being gone. Mitigated only by keeping unknown at warning
  severity — it is not solved.
- **Active is not the same as still responsible.** A person can be employed,
  present in the directory, and no longer have anything to do with the agent
  they own. This decision detects departures, not reassignments; the stale
  owner who is merely in another department remains invisible.
- One more external system to configure, and a second source of truth about
  people that can disagree with the spec in either direction.
- Groups and managers are read where available but unused so far, which invites
  scope creep into directory-driven ownership.

## Alternatives considered
- **Fail the build on any contact the directory cannot confirm** — turns an
  outage or a contractor into a stop-the-line event, and would have been
  switched off within a week.
- **Expiry dates on pairings instead of a directory** — needs no integration,
  but forces re-confirmation of correct records on a timer and still misses a
  departure the day after a renewal.
- **A concrete LDAP/SCIM client in this package** — there is no network in this
  environment to verify it against, and it would bind every installation to one
  provider's schema. Injection gives the same reach with a stub in CI.
- **Treat unknown and departed as one "not confirmed" finding** — simpler to
  implement, but it reports a contractor and a leaver identically, which makes
  the finding unactionable and therefore ignored.

## Verification
`tests/test_directory.py` covers all of it: a departed owner is reported, an
unknown contact is distinguished from a departed one, a null directory (and an
empty one, and an outage) produce nothing, reconciliation reaches owners,
approvers and every other paired role, and production promotes a departed owner
to an error while leaving unknown contacts at warning. `validate_spec` with a
`NullDirectory` is asserted to be identical to `validate_spec` without one.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Directory protocol, static/null/injected adapters, reconciliation and findings landed with WS-016 M4. |
