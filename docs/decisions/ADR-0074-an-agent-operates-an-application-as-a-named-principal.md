---
id: ADR-0074
title: An agent operates an application as a named principal
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Runtime Engineering]
informed: [All engineering]
scope: [spec, compiler, security]
workstreams: [WS-002, WS-009, WS-033]
supersedes: []
superseded_by: []
related: [ADR-0010, ADR-0015, ADR-0057, ADR-0065, ADR-0071, ADR-0073]
tags: [identity, enterprise-applications, controls]
---

# ADR-0074: An agent operates an application as a named principal

## Context
Agents use enterprise applications the way people do: they sign in, and the
application decides what that user may do. `IdentityIR` models the workload
identity this platform issues — our permissions, our secret references — and
says nothing about **who the agent is inside the ERP**. A capability binding
carries a server name and a credential reference, and a credential is not an
identity: two capabilities can share one, and nothing records which application
user it resolves to or what that user is allowed.

So there are two permission systems with nothing between them, and it fails in
both directions. Our spec can grant `post_journal_entry` to an agent whose ERP
account is read-only: the design validates, the call fails at the application,
and the first person to find out is whoever is on call. The other direction is
worse — the ERP account is broad and our mandate is narrow, so the agent can
reach more through that account than the design admits, and our refusal only
covers the paths we route.

ADR-0057 rule 2 already settled the principle for task backends: an agent acts
as itself or not at all, and a backend that cannot give a non-human principal
an identity is refused at bind time. The same reasoning applies to every
enterprise application an agent operates, and nothing currently applies it.

## Decision
**A capability binding names the application principal the agent acts as, and
that principal is the agent's alone.**

1. **The binding names the principal**, not merely a credential: the
   application, the user or service account, and — where the application can
   state it — the role that account holds.
2. **One principal per agent per application.** Two agents sharing an
   application account destroys the application's own audit trail, which is
   usually the only one an auditor will accept. ADR-0057 rule 2, extended from
   the task port to every system an agent operates.
3. **Our mandate may not exceed the principal's declared role.** Where the
   declaration exists, the phase gate checks it; where the application cannot
   state its grants, the gap is **reported, not assumed closed** (ADR-0073
   rule 7).
4. **A principal shared across a separation fails.** ADR-0071 rule 7 checks
   server and credential; this extends it to the application user, which is
   what the enterprise system actually keys its segregation on.
5. **The agent's own identity never disappears.** Where an application supports
   only a shared service account, the binding is refused rather than worked
   around, and the refusal names the application — the same answer ADR-0057
   gives, for the same reason.
6. **Credentials remain references.** Naming a principal adds an identity to
   the design; it adds no secret to it.

## Scope
The capability binding, the phase gate's implementation checks, and the
generated deployment artifacts. It does not change our own workload identities
(ADR-0015), the permission resolver, or what a mandate means.

## Implementation
`CapabilityBinding` gains the application principal and an optional declaration
of the role that principal holds. The phase gate checks rules 2, 3 and 4 and
reports rule 3's unverifiable cases. The generated artifacts list, per agent,
which application accounts it operates — which is the answer to a question an
auditor asks early and this platform currently cannot answer.

## Timeline
Phase 5, WS-009, after ADR-0073, whose `enforced_by` rule decides how rule 3's
unverifiable half is reported.

## Advantages
- The question "which application accounts does this agent hold?" gets an
  answer from the design rather than from a spreadsheet.
- A mismatch between our authority and the application's is caught at compile
  time instead of at the first call.
- Application-side audit trails stay meaningful, because each agent is its own
  user there.
- Segregation checks reach the thing the enterprise system keys on.

## Disadvantages
- **Rule 3 depends on a declaration we cannot verify.** An ERP role is a name
  somebody typed into a binding; what it actually grants lives in the ERP. The
  check compares our design against a claim, and the report has to say so.
- **One principal per agent multiplies accounts.** A finance function with
  forty agents across six systems is a couple of hundred service accounts to
  provision, rotate and decommission, and nothing here provisions them.
- **Rule 5 will block real deployments.** Plenty of enterprise systems offer
  only a shared integration account, and the honest answer — refuse — will be
  read as the platform being unable to integrate with software the company
  already owns.
- **It adds identity sprawl to the binding**, which was the document that
  stayed small.
- **A role name is not a permission set**, so rule 3's check is coarse: it can
  compare names, not grants, and two ERPs will spell the same authority
  differently.

## Alternatives considered
- **Keep credentials only.** No new concept, and it leaves two permission
  systems with nothing between them — the state this record exists to end.
- **Derive the principal from the credential reference.** Convenient, and a
  credential can be rotated to a different account without the design changing,
  which makes the derivation a guess.
- **Model the application's full permission set.** Would make rule 3 a real
  check, and requires maintaining a model of every ERP's role catalogue — the
  catalog problem (ADR-0041) at a scale nobody can staff.
- **Allow shared accounts with a declaration.** Pragmatic, widely done, and it
  gives up the application audit trail, which is the one an auditor trusts.

## Verification
Tests assert: a binding without a principal fails the implementation phase; two
agents bound to the same application principal fail; an agent whose mandate
exceeds its declared principal role fails; an undeclared role is reported as
unverified rather than passing silently; two capabilities on opposite sides of
a separation bound to one principal fail even when servers and credentials
differ; and the generated artifacts list each agent's application accounts.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. Bindings name an application principal, one per agent per application, with the mandate checked against its declared role and separations extended to the principal. |
