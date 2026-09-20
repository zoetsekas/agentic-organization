---
id: ADR-0062
title: A catalog entry is editorially mutable and substantively versioned
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Product]
informed: [All engineering]
scope: [runtime, ui, security]
workstreams: [WS-027]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0040, ADR-0041, ADR-0052]
tags: [catalog, governance]
---

# ADR-0062: A catalog entry is editorially mutable and substantively versioned

## Context
The platform catalog is the list of building blocks a design may choose from —
models, MCP servers, plugins, tools, environment templates, permission sets,
guardrails and the rest (ADR-0041). Entries can be published, reviewed,
entitled and retired, and retirement already refuses while a design still
references the entry.

What has never been decided is **editing**. Operators need it: a summary is
wrong, an owner left, a server gained a tool, a model's price changed. Today
the only honest route is to publish a new entry, which loses the review
history and leaves the old one lying around.

Editing is not one thing, and treating it as one is where this gets dangerous.
Changing a summary is housekeeping. Changing an MCP server's tool list, a
permission set's grants, an environment's network posture or a model's cost is
**changing what every design bound to that entry resolves to** — after it was
reviewed, and without anybody reviewing it again. A catalog whose approved
entries can be quietly rewritten is not a control; it is a list that looks
like one.

"CRUD" is therefore the wrong shape to copy wholesale. The C, the R and a
bounded U belong here. The D is already answered by retirement, and giving the
catalog a delete that erases a reviewed decision would remove the only record
of why something was allowed.

## Decision
**Editorial fields may be edited in place. Substantive fields may not be
edited on an approved entry — they make a new version. Delete exists only for
an entry nobody has approved and nothing references.**

1. **Editorial fields** — name, summary, description, owner, tags,
   documentation URL — may be edited at any status. They describe the entry to
   people; they do not decide anything.
2. **Substantive fields** — kind, version, and `attributes` (the tool list, the
   transport, the grants, the network posture, the cost, the context window) —
   decide what a design gets. Editing one on an entry that is **approved or
   restricted** is **refused**, with the reason and the two ways forward:
   publish the next version as a new entry and supersede this one, or send this
   one back for review.
3. **Sending back for review is explicit.** An operator may move an approved
   entry to `proposed` deliberately, which makes it unselectable until somebody
   approves it again. That is a decision with a consequence, not a side effect
   of saving a form.
4. **A version is immutable.** `jira 1.0.0` always means one thing. A new
   version is a new entry that supersedes the old, so a design pinned to the
   old one keeps resolving to what was reviewed.
5. **Retirement, not deletion, is how an entry leaves.** It already refuses
   while designs reference it, and `force` makes breaking them a decision
   somebody takes rather than discovers.
6. **Delete is narrow**: permitted only for an entry that has never been
   approved and that nothing references — a typo in a draft. Anything else is
   retired, because the review history is the point.
7. **Every mutation is attributed and recorded** — who, when, what changed —
   on the entry's own history, the same way a review already is.

## Scope
The lifecycle of a catalog entry and the operations exposed over the API, the
CLI and the designer UI. It does not change what a kind means, how entitlement
works, or how the compiler resolves a binding.

## Implementation
`CatalogService` gains `update` (editorial), `amend` (substantive, refused per
rule 2), `send_back`, and a narrow `delete`; each records an entry in the
history with the actor. The API exposes them; the CLI gets `catalogs add`,
`edit`, `review`, `retire` and `delete`; the designer's Catalog tab gets a
form per kind, driven by the kind's declared attributes rather than a
hand-written form per kind.

## Timeline
Phase 5, WS-027.

## Advantages
- The common case — fixing a description, correcting an owner — stops
  requiring a new entry.
- What a design resolves to cannot change after review without somebody
  deciding that it should.
- The review history survives, because entries retire rather than vanish.
- A pinned version keeps meaning what it meant.

## Disadvantages
- **Two classes of field is a rule people must learn**, and the boundary is
  ours, not obvious. Somebody will consider `tags` substantive because they
  drive discovery, or `summary` substantive because it is what a designer
  reads before choosing.
- **The refusal will be worked around** by whoever is in a hurry: retire and
  republish achieves the same end with more clicks and a worse history. The
  rule slows a bad edit; it does not prevent one.
- **`attributes` is a free-form map**, so "substantive" is enforced on the
  whole field rather than per key. Correcting a typo inside `attributes` on an
  approved entry costs a version, which will feel disproportionate.
- **Versions accumulate.** Immutability means the catalog grows monotonically,
  and nothing here prunes it; a heavily-maintained server will leave a trail of
  superseded entries somebody has to look past.
- **Send-back is a foot-gun.** Moving an approved entry to `proposed` makes
  every design using it unselectable at the next compile, and the operator
  doing it may not know who that hits — the usage index makes it visible, but
  only if they look.

## Alternatives considered
- **Full CRUD, no distinction** — simple, and it makes approval meaningless:
  anything reviewed can be rewritten afterwards.
- **Immutable entries, no editing at all** — safest, and it makes a typo cost
  a version. Operators would stop curating the catalog rather than pay that.
- **Edit anything, but re-review on every change** — defensible, and it turns
  a corrected owner into a review queue item, which trains reviewers to
  approve without reading.
- **Hard delete with an audit record** — the audit says something was removed
  but not what it allowed; retirement keeps the entry readable, which is what
  an auditor actually needs.

## Verification
Tests assert an editorial edit succeeds at any status; a substantive edit is
refused on an approved or restricted entry, with both remedies named; the same
edit succeeds on a proposed entry; send-back makes an entry unselectable;
delete is refused for an approved entry and for a referenced one, and permitted
for an unapproved unreferenced draft; retirement still refuses while designs
reference the entry; a superseded version remains readable and resolvable; and
every mutation records its actor.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Editorial fields mutable, substantive fields versioned, delete narrow, retirement preserved. |
