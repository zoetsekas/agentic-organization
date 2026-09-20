# Architecture Decision Records

An ADR captures **one architectural decision**: why it was needed, who made it,
what was decided, where it applies, how it is implemented, when it lands, and
what it costs us. Records are markdown with YAML front matter, validated by
`orgagents records validate` in CI — a broken cross-reference or a changelog
that disagrees with the version fails the build.

## Identifiers

`ADR-nnnn`, zero-padded, allocated in order and **never reused**. The filename
is `ADR-nnnn-kebab-title.md`. A decision that changes materially does not get a
new number — it gets a new *version*. A decision that is *replaced* gets a new
number and supersedes the old one.

## Lifecycle

```
Proposed ──accept──> Accepted ──replace──> Superseded
    │                   │
    └──reject──> Rejected└──retire──> Deprecated
```

| Status | Means |
|---|---|
| `Proposed` | Written, under review. Do not build on it yet. |
| `Accepted` | In force. Code and infrastructure must comply. |
| `Rejected` | Considered and declined. Kept so the reasoning survives. |
| `Deprecated` | Still true of existing systems, not for new work. |
| `Superseded` | Replaced by a newer ADR, named in `superseded_by`. |

## Versioning and changelog

`version` is semver over the *decision*, not the software:

- **patch** — wording, links, clarifications that change no behaviour;
- **minor** — the decision is extended (new scope, an added constraint) without
  invalidating anything already built against it;
- **major** — a binding term changes; anything built on the old terms must be
  revisited. A major bump usually means the decision should be superseded by a
  new ADR instead — prefer that when the *reasoning* changed, not just the terms.

Every record ends with a changelog table whose newest row must equal the
front-matter `version`. That is what the validator checks.

## Supersession

Supersession is symmetric and machine-checked:

```yaml
# ADR-0012
supersedes: [ADR-0004]
# ADR-0004
status: Superseded
superseded_by: [ADR-0012]
```

The superseded record stays in the tree, unedited apart from its status,
`superseded_by` and a changelog row. Deleting history is how teams re-litigate
the same decision every eighteen months.

## Front matter

```yaml
---
id: ADR-0007                      # unique, matches filename
title: Deny-by-default RBAC …     # one line, imperative or declarative
status: Accepted                  # see lifecycle
version: 1.0.0                    # semver; matches newest changelog row
date: 2026-09-20                  # first written
updated: 2026-09-20               # last substantive change
deciders: [Platform Architecture] # WHO decided — accountable
consulted: [Security Engineering] # WHO was asked
informed: [All engineering]       # WHO needs to know
scope: [spec, compiler, runtime]  # WHERE it binds
workstreams: [WS-004]             # WS records delivering it
supersedes: []
superseded_by: []
related: [ADR-0005]
tags: [security, foundational]
---
```

## Required sections

`Context` (why) · `Decision` (what) · `Scope` (where) · `Implementation` (how) ·
`Timeline` (when) · `Advantages` · `Disadvantages` · `Alternatives considered` ·
`Verification` · `Changelog`.

`Advantages` and `Disadvantages` are both mandatory and neither may be empty. A
decision with no stated cost has not been thought through; reviewers are asked
to reject records whose disadvantages section is decorative.

## Working with records

```bash
orgagents records validate      # governance rules; exits non-zero on violation
orgagents records index         # regenerate index.md for both record sets
orgagents records graph         # JSON node/edge graph of the record set
orgagents records new adr "Title of the decision"
```

Index: [index.md](index.md) · Template: [_template.md](_template.md) ·
Workstreams: [../workstreams/index.md](../workstreams/index.md)
