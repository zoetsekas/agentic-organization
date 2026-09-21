---
id: ADR-0066
title: Organizational prose becomes references and narrative stays narrative
status: Proposed
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture]
consulted: [Product]
informed: [All engineering]
scope: [spec, ui, compiler]
workstreams: [WS-002, WS-003]
supersedes: []
superseded_by: []
related: [ADR-0007, ADR-0041, ADR-0063, ADR-0065]
tags: [authoring, vocabulary, open-question]
---

# ADR-0066: Organizational prose becomes references and narrative stays narrative

## Context
Several fields that describe an organization are free text and decide nothing:
`Role.responsibilities`, `Team.mandate`, `shared_instructions`, and the
`description` on almost every model. The requirement raised against them was
to capture the information with a specific structure rather than as free-text
fields, with a markdown web editor in the designer.

Structured markdown is the obvious answer and the wrong one **for these
fields**, which is worth writing down because the repository contains a
precedent that argues the other way. ADRs and workstream records are markdown
with front matter and required sections, validated by `records.py`. That works
because what those records need is *completeness of argument* — did somebody
write the Disadvantages section.

Responsibilities and mandates need a different property: **referential
integrity**. A responsibility matters when it can be checked against something
else — does the role hold permission for the capability this responsibility
needs, is any evaluation case testing it, is there a mandate behind the
decision it implies. Required headings cannot express that. Building it on
markdown means writing a parser that lifts typed data back out of prose: the
structure would be real and the format could not enforce it.

Three existing properties would also get worse. The IR diff classifies changes
by consequence (WS-005 M4), and a markdown blob diffs as text, so
"responsibility added" and "responsibility reworded" become the same change.
The spec is already a structured authored format, so markdown adds a second
syntax, a second validator and a second editor. And a blob is harder to edit
concurrently than a field.

Most importantly it does not fix the defect. Today we have unchecked prose in a
small field; required headings would give us unchecked prose in a larger field,
with the heading proving only that somebody typed something underneath it.

The real axis is not *how rich is the text format*. It is **prose versus
references**.

## Decision
**Fields that should decide something become references to declared things.
Fields that are genuinely narrative stay prose, and get a plain markdown editor
with preview.**

1. **A responsibility becomes a structured object**: an id, a short statement,
   and links — the capabilities it requires, the decision classes it needs
   mandate for (ADR-0065), and the evaluation cases that verify it.
2. **The compiler checks the links, at the phase gate**, and reports: a role
   promising work it holds no permission for; a responsibility with no mandate
   behind it; a responsibility nothing evaluates. These are **findings, not
   refusals** — an organization mid-design is allowed to be incomplete, and a
   compiler that refuses a half-written org will be worked around.
3. **The designer edits references with pickers, not a text box.** The pattern
   already exists in the inspector, where a binding is an id from the open
   spec.
4. **Narrative fields stay markdown** — a team charter's rationale, onboarding
   notes, a system prompt. They reference nothing, they diff as text
   legitimately, and a plain editor with preview is enough.
5. **A vocabulary term is a catalog concern** (ADR-0041), not a constant in the
   codebase. Capabilities are already declared; decision classes will be.
6. **An organization may extend a vocabulary, and the extension is reviewed.**
   A term nobody approved is usable in a draft and blocks promotion, exactly as
   an unreviewed catalog entry does.

## Scope
`Role.responsibilities`, `Team.mandate` and the decision-class vocabulary, plus
the designer surfaces that edit them. It does not change permissions, the org
chart, the catalog's governance rules, or any `description` field.

## Implementation
Not implemented, and **deliberately not scheduled before the open question
below is answered.** When it is: a `Responsibility` model replaces the string
list, the phase gate gains the three checks above as findings, the designer
gains a responsibility editor built from reference pickers, and a narrative
markdown field with preview is added where prose genuinely belongs.

## Timeline
Blocked on the open question. Not before ADR-0065 is decided, since
responsibilities cannot reference decision classes that do not exist.

## Advantages
- The structure has teeth: a responsibility that promises what the role cannot
  do becomes visible instead of being read past.
- Consequence-classified diffs keep working, because a changed reference is a
  different change from a reworded sentence.
- One authored format, one validator, one editor.
- Narrative content still gets a good editing experience, in the places where
  narrative is the right answer.

## Disadvantages
- **A forced vocabulary is worse than prose.** If the declared terms do not fit
  an organization, people pick the nearest wrong one, and now the wrong term is
  machine-readable and trusted. Prose at least reads as approximate.
- **Authoring cost rises sharply.** Typing a sentence becomes choosing a term,
  linking capabilities, and finding an evaluation case. The first response will
  be to write a single vague responsibility per role, which passes every check
  and says nothing.
- **Findings get ignored.** Making the checks non-blocking is what keeps the
  compiler usable and also what lets a role ship promising work it cannot do,
  forever, with a warning nobody reads.
- **It splits one mental model in two.** Authors must learn which fields are
  referential and which are narrative, and the boundary is ours — somebody will
  reasonably think a charter is referential, or that a responsibility is prose.
- **Extensible vocabularies drift.** Per-organization terms mean two
  organizations' "approve_spend" may not mean the same thing, and nothing
  reconciles them. Closed vocabularies avoid that and fit nobody.

## Alternatives considered
- **Structured markdown with required sections.** The requirement as first
  stated. Enforces that sections exist, not that content refers to anything;
  breaks consequence-classified diffing; adds a second authored format.
- **Leave the fields as prose.** Free, and authority and responsibility stay
  decorative — see ADR-0065's context for what that costs.
- **A closed vocabulary with no extension.** Enforceable and rigid; the first
  customer needing a missing term has no route but a fork.
- **Free-form tags instead of declared terms.** Maximum flexibility, no check
  worth writing — this is the status quo with extra syntax.

## Verification
Not applicable while Proposed. If accepted: a responsibility naming a
capability the role lacks produces a finding and not a refusal; a responsibility
with no evaluation case produces a finding; a vocabulary term that is not
approved blocks promotion but not drafting; the IR diff distinguishes a changed
reference from a reworded statement; and a narrative field round-trips markdown
without being parsed for meaning.

## Open question
**Who owns a vocabulary, and what happens when an organization needs a term we
did not ship?** Rules 5 and 6 above propose catalog ownership with reviewed
extension, by analogy with how the catalog already governs models and servers.
The analogy may not hold: a catalog entry is a thing you bind to, while a
decision class is a word you reason with, and words drift in a way bindings do
not. Answering it wrongly is worse than leaving these fields as prose, which is
why this record is Proposed and its implementation is explicitly unscheduled.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Proposed. Referential fields become references; narrative stays markdown; vocabulary ownership left open. |
