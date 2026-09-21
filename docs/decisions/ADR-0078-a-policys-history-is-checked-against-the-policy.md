---
id: ADR-0078
title: A policy's history is checked against the policy
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: []
informed: [All engineering]
scope: [compiler, security]
workstreams: [WS-028]
supersedes: []
superseded_by: []
related: [ADR-0062, ADR-0076, ADR-0077]
tags: [governance, lifecycle, audit]
---

# ADR-0078: A policy's history is checked against the policy

## Context
ADR-0077 gave the platform policy states, a signed approval and a fingerprint.
Its own Disadvantages named what remained:

> **Still no history.** There is no record of who changed what when, only the
> current signature — the attribution half of ADR-0062 is not here, and
> reconstructing a policy's past means reading git.

The obvious response is to add a `history` list and be done. That would be the
decorative field this session has spent its time removing: a policy is a
document, nothing watches it being edited, and a self-reported log is exactly
as trustworthy as the person editing the file — which is to say, it records
what somebody chose to write down.

What makes a history worth having here is different. The fingerprint already
knows what the document contains. So the history can be **held to the
document**: if the newest recorded change does not name this version and this
fingerprint, something substantive changed and nobody recorded it, and that is
refusable. The log stops being a claim and becomes a constraint.

## Decision
**A policy records its history, and the history must agree with the policy.**

1. **Every entry is attributed and dated.** Version, date, who, what act —
   `drafted`, `reviewed`, `amended`, `approved`, `retired` — and a note. An
   entry with no `by` is refused: an unattributed change is the thing this
   exists to prevent.
2. **A substantive entry records the fingerprint** as of that act, because the
   fingerprint is the half a version cannot prove.
3. **The newest entry must describe this document.** Its version must be this
   version, and where it carries a fingerprint, that fingerprint must be this
   one. A substantive edit nobody recorded is **refused at load**.
4. **A version is immutable.** Two entries naming one version may not disagree
   about what it contained — ADR-0062's rule, one layer up.
5. **History is append-only, oldest first**, and out-of-order dates are
   refused.
6. **An approved policy must record its approval**, and the recorded act must
   match the signature: `approved_by` and `approved_on` are the same fact as
   the newest `approved` entry for this version, not a second one that can
   drift from it.
7. **A draft needs no history.** A rule being tried out is not yet a thing
   anybody is accountable for, and requiring an audit trail to experiment would
   just mean drafts stop being written down at all.
8. **The verdict carries the last act.** The IR stamp says who last touched
   these rules and when, so a build's provenance does not require opening the
   policy.

## Scope
The policy document and its load-time checks. It adds no store, no workflow and
no runtime behaviour.

## Implementation
`PolicyChange` with validation on each entry, and `_check_history` on the
policy holding the list to the document. The document's own fields are checked
first, so "approved by nobody" is the complaint when both are true rather than
the more distant one about history. `SystemIR.platform_policy.last_change`
carries the act. The worked house policy records being drafted, amended after
the finance example showed invoice approval running unattended, and approved.

## Timeline
Phase 5, WS-028. Implemented with this record.

## Advantages
- A recorded history that can be wrong is now a history that is checked: the
  common failure — edit the rules, keep the version, leave the log alone — is
  refused rather than discovered later.
- The approval stops being two loose fields and becomes a recorded act with the
  signature derived from it.
- Version immutability is enforced where it matters: in the record of what a
  version contained.
- A build's provenance names a person without opening the policy.
- Drafts stay cheap to write, so the lifecycle does not push experimentation
  out of the document.

## Disadvantages
- **It is still self-reported.** Nothing stops somebody editing the rules,
  updating the fingerprint in the head entry and signing it as whoever they
  like. The checks catch carelessness and inconsistency; they catch dishonesty
  only when it is sloppy. Git catches more, and is not what an auditor is
  handed.
- **Consistency is not truth.** A history that agrees with its document proves
  the document is coherent, not that any of the named people did any of it.
  ADR-0077 already said the approval is a string in a file; this makes the
  string load-bearing without making it true.
- **Editing a policy now takes two correct edits.** Change a rule and the
  fingerprint moves, so the load fails until the history is updated — with the
  new fingerprint, which the author has to obtain by running the loader and
  reading the error. That is a poor authoring experience and the most likely
  reason somebody works around this.
- **Only the head is checked.** Entries before it can say anything at all;
  nothing verifies that the fingerprint recorded against version 0.1.0 was ever
  that version's fingerprint. The history is checked at its boundary and
  trusted in its interior.
- **Old entries cannot be corrected.** Append-only is the right rule and it
  means a typo in a note from three months ago is permanent, or is fixed by an
  edit this model cannot distinguish from a rewrite of the past.

## Alternatives considered
- **A plain unchecked log.** One field, no constraints, and it records what
  somebody chose to write — the decorative-field defect with a paper trail.
- **Derive history from version control.** Genuinely more trustworthy, and it
  makes the policy unreadable without its repository, which is not how the
  document travels to whoever is asking.
- **A signed chain, each entry over the previous.** Would make tampering
  detectable rather than merely inconsistent, and needs key management this
  platform does not have — the same wall ADR-0077 hit.
- **Keep policies in a store with a real audit trail.** The right end state,
  and it needs a store, an API and a UI before the first rule can be enforced.
- **Check every entry, not just the head.** Impossible as stated: verifying a
  past fingerprint means having that past document, which the file does not
  carry.

## Verification
Tests assert: a draft needs no history; an approved policy without one is
refused; a substantive edit nobody recorded is refused and one that was
recorded passes; a version recorded twice with different fingerprints is
refused; out-of-order dates are refused; an entry attributed to nobody is
refused; a signature disagreeing with the recorded approval is refused; a head
entry naming another version is refused; the stamp carries the last act; and
the worked house policy records being drafted, amended and approved.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted and implemented. Attributed, append-only history held to the document by version and fingerprint, with the approval derived from a recorded act. |
