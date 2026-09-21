---
id: ADR-0064
title: Whether an agent may act under a human's delegated authority
status: Proposed
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture]
consulted: [Security Engineering, Product, Legal]
informed: [All engineering]
scope: [security, runtime, spec]
workstreams: [WS-009, WS-031]
supersedes: []
superseded_by: []
related: [ADR-0057, ADR-0061, ADR-0063, ADR-0079]
tags: [identity, authority, open-question]
---

# ADR-0064: Whether an agent may act under a human's delegated authority

## Context
ADR-0057 rule 2 is unambiguous: **an agent acts as itself or not at all.** A
backend that cannot give a non-human principal an identity is refused at bind
time; an agent never borrows a person's credentials. The reasoning holds
completely for the agents we have built so far. A workflow step and a
background worker have no person behind them, so acting "as" somebody would be
a fiction, and a fiction in an audit trail is worse than a refusal.

The personal assistant breaks the symmetry. When a human's counterpart agent
files their expense claim, replies on their behalf in a channel (ADR-0061), or
moves their ticket, the person genuinely *is* behind it — and the system they
are acting on usually has no concept of a non-human principal at all. Today
the platform's honest answer is to refuse the binding. That answer is correct
and it is also the reason a whole class of useful work cannot be done.

So the question is not "should agents impersonate people" — obviously not.
It is narrower and harder:

> Is there a form of **recorded, scoped, revocable delegation** in which an
> agent acts with a person's authority while the audit trail still says,
> unambiguously, that an agent did it?

This is the one gap the vocabulary work (ADR-0063) surfaced that is a genuine
design question rather than a defect. It needs a decision, not an
implementation, and it is not ours alone to take — it touches what we are
willing to tell an auditor.

## Decision
**Not yet taken.** This record exists to hold the question open with its
options stated, rather than to let it be settled by whoever next implements a
connector.

The shape of a decision, if taken, would need all four of:

1. **Delegation is a recorded grant, not a configuration flag.** A named
   human, a named agent, an explicit scope of actions, an expiry. Consent is
   captured as an authenticated act by the human, on the same footing as an
   approval click (ADR-0061) — not a checkbox in a spec somebody else wrote.
2. **The agent's own identity never disappears.** Every act carries both
   principals: the agent that did it and the human whose authority it carried.
   An audit record naming only the human is the failure mode this whole record
   exists to avoid.
3. **Scope is enumerated, never inherited.** A delegation grants named
   operations. "Everything I can do" is not a scope; it is a credential
   handover with paperwork.
4. **Revocation is immediate and unilateral**, by the human, without an
   operator in the loop, and in-flight work stops rather than drains.

Absent all four, **ADR-0057 rule 2 stands unchanged** and the binding is
refused. There is no partial version of this that is safe.

## Scope
Agents paired to a human acting on systems that cannot issue a non-human
principal. It would not touch workflow agents, scheduled agents, or any
agent-to-agent path (ADR-0058) — those act as themselves, always, and nothing
here relaxes that.

## Implementation
Not implemented. If accepted, it lands as a `DelegationGrant` in the fabric
plane — never the spec, since a spec author must not be able to grant away
somebody else's authority — with a human-facing consent flow, a dual-principal
field on every audit record, and a bind-time check that refuses any backend
that cannot represent the agent as distinct from the person.

## Timeline
Undecided. Blocked on a product and legal position, not on engineering.

## Advantages
- It closes the one real gap: a personal assistant that can act on the systems
  the person actually uses, instead of one that can only read and advise.
- A recorded grant with a scope and an expiry is strictly better than what
  teams do when refused — share a service account, or paste a token into a
  config.
- Dual-principal auditing would make delegated action *more* legible than the
  human doing it manually, not less.

## Disadvantages
- **It is impersonation with extra steps if the audit trail is weak**, and
  audit trails weaken quietly. A downstream system that records only the
  credential it saw will record the human, whatever we store on our side. We
  cannot fix somebody else's log format, and that is where an investigator
  will look first.
- **A second authority mode is a second thing to get wrong.** Every permission
  check, every escalation path and every guardrail now has two cases, and the
  rarely-taken one is the one that will be under-tested.
- **Consent decays.** A grant given once for a plausible reason persists until
  someone revokes it; nobody revokes things. Expiry mitigates and does not
  solve this, exactly as mission expiry (ADR-0039) mitigates and does not
  solve standing lateral reach.
- **It weakens a rule that is currently absolute**, and absolute rules are the
  ones people follow. "An agent acts as itself" needs no judgement; "an agent
  acts as itself unless there is a valid scoped grant" needs judgement at every
  call site.
- **Scope enumeration will be fought.** The first integration will want a
  broad grant because enumerating operations is tedious, and the pressure will
  be to add a wildcard.

## Alternatives considered
- **Keep ADR-0057 rule 2 absolute; refuse forever.** Safest, honest, and it
  permanently caps what a personal assistant can do. This is the status quo
  and the default if no decision is taken.
- **Agent proposes, human executes.** The agent prepares the action and the
  human commits it with their own credentials — no delegation needed, full
  accountability, and it removes most of the value for high-volume routine
  work, which is the value.
- **Per-system service accounts owned by the human.** Widely done, requires
  nothing from us, and produces exactly the untraceable shared-credential
  situation rule 2 was written to prevent.
- **Delegation declared in the spec.** Simple to build and wrong: it lets a
  design author grant away authority belonging to a person who never saw the
  document.

## Verification
Not applicable while Proposed. If accepted, tests would assert: an act under
delegation records both principals; a scope not enumerated in the grant is
refused; an expired grant is refused at the next act, not at the next compile;
revocation stops in-flight work; a backend that cannot distinguish the agent
from the human is refused at bind time even with a valid grant; and no
workflow, scheduled or agent-to-agent path can carry a delegation.

## Follow-up
ADR-0079 answers the half of this that is about a person's *own* authority: a person may hold a mandate, so a decision only a board can take has somewhere to land. Whether an agent may act *as* a person is still this record's question, and still open.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Proposed. Question stated, four necessary conditions named, decision deferred to product and legal. |
