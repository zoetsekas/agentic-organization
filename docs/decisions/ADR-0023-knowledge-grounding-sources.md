---
id: ADR-0023
title: Grounding knowledge is declared as sources, not embedded in prompts
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Data Platform, Security Engineering]
informed: [All engineering]
scope: [spec, compiler, targets, runtime]
workstreams: [WS-015]
supersedes: []
superseded_by: []
related: [ADR-0010, ADR-0017, ADR-0022]
tags: [knowledge, integration]
---

# ADR-0023: Grounding knowledge is declared as sources, not embedded in prompts

## Context
A declarative agent is instructions **plus grounding knowledge** plus actions —
that is the shape the market has settled on, and the grounding half was missing
from our spec. Without it, knowledge arrives one of two bad ways: pasted into
the system prompt, where it is unversioned, unattributed and stale on arrival;
or fetched through an ad-hoc capability, where nothing records that this agent
reads the finance handbook.

Classification makes it sharper. A grounding source carries data of some class,
and if that is not declared, the placement and egress rules of ADR-0017 cannot
apply to the material agents actually read most.

## Decision
A **knowledge source** is a spec object: an id, an abstract `kind` (document
store, wiki, ticketing, CRM, mailbox, code repository, data warehouse, web),
the `data_classes` it carries, a `freshness_seconds` bound, whether retrieved
material `require_citation`, and an optional secret reference.

Agents reference sources by id. The binding says which concrete system and
index backs each one, and supplies the credential reference. A source's secret
attaches to the **identity of the agent that reads it** — so who can read the
finance handbook is visible in the registry and in the generated IAM, not
implied by a prompt.

Citation is on by default: grounded answers name where the material came from.

## Scope
Read-only grounding material. Writing to a system of record stays a capability
(ADR-0010); a source that is also written to is declared as both.

## Implementation
`spec.model.KnowledgeSource`, `AgentSpec.knowledge`, `KnowledgeBinding`,
`compiler.ir.KnowledgeIR` (spec contract merged with binding), a
`knowledge_index` neutral resource, per-provider index resources in the
Terraform targets, and the source's secret folded into the reading agent's
`IdentityIR.secret_refs`.

## Timeline
Phase 2 for the declaration and binding. Retrieval quality — chunking, ranking,
freshness enforcement — is WS-015's phase 3 work and is not decided here.

## Advantages
- Who reads what is visible in the registry and enforced in IAM.
- Grounding material inherits data classification, so placement and egress
  rules apply to it.
- Citation by default makes grounded answers checkable by their reader.
- Swapping the backing system is a binding change, not a redesign.

## Disadvantages
- Declaring a source says nothing about retrieval quality; a well-governed
  index can still return the wrong passage, and the spec cannot express that.
- `freshness_seconds` is declared but not yet enforced anywhere, which risks
  reading as a guarantee it is not.
- Per-source secrets multiply the credentials an identity holds.
- Abstract `kind` values will not fit every source; the escape hatch is a
  binding option, which erodes neutrality slightly.

## Alternatives considered
- **Knowledge as just another capability** — loses the read-only, classified,
  citation-bearing semantics that make grounding reviewable.
- **Prompt-embedded knowledge** — unversioned, unattributed, stale.
- **A retrieval service outside the spec** — puts the most-read data outside
  the governance model entirely.

## Verification
Compiler tests assert a source resolves its provider and index from the
binding, and that its secret lands on the reading agent's identity and on no
other. Validator tests reject unknown data classes and unknown source
references.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Sources declared, bound per target, secrets on the reader's identity. |
