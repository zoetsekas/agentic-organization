---
id: ADR-0015
title: Every agent gets its own workload identity; secrets appear only as references
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering]
consulted: [Platform Architecture, Platform SRE, Compliance]
informed: [All engineering]
scope: [spec, security, targets, runtime]
workstreams: [WS-004, WS-007]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0010, ADR-0012]
tags: [security, identity]
---

# ADR-0015: Every agent gets its own workload identity; secrets appear only as references

## Context
If a fleet of agents shares one service account, the effective permission of
every agent is the union of all their needs, least privilege is unachievable in
principle, and an audit log cannot attribute an action to an agent. Shared
identity also makes revocation all-or-nothing: containing one misbehaving agent
means breaking every other.

Separately, secrets have a habit of ending up in generated artifacts, which are
committed to version control by definition.

## Decision
The compiler assigns **one workload identity per agent** — a service account in
the cloud targets, a distinct principal locally — bound to exactly that agent's
effective permission set (ADR-0008). Sub-agents inherit their parent's identity
with permissions further narrowed, never widened.

Secrets are **never materialized** into the spec, the IR or any generated file.
They appear only as **references** (`secret_ref: WAREHOUSE_DSN`) resolved at
runtime from the platform's secret manager. Generated artifacts emit the
reference plus the IAM binding that lets the identity read it. A validator
rejects any spec, binding or generated file containing something that looks like
a credential.

## Scope
Identity and secret handling across spec, IR, all targets and the runtime.
Excludes human identity to the platform UI.

## Implementation
`ir.identities` derives one identity per agent with its policy bindings;
Terraform targets emit service accounts, workload identity bindings and secret
accessor roles; the local target emits `.env.example` with names only. The
runtime resolves references through `HarnessBuilder.dsn_resolver`.

## Timeline
Phase 1 for the reference discipline; per-provider identity mapping with the
cloud targets in phase 3.

## Advantages
- Least privilege becomes achievable per agent rather than per fleet.
- Audit logs attribute every action to one agent identity.
- Revocation and containment are per-agent, not all-or-nothing.
- Generated output is safe to commit, which the whole workflow depends on.

## Disadvantages
- Identity sprawl: a hundred agents means a hundred service accounts to create,
  rotate and clean up, and some providers have quotas.
- Per-identity IAM bindings make Terraform plans large and slow to review.
- Reference-only secrets add a resolution step that fails at runtime rather than
  compile time, which is a worse place to find out.
- Credential-shaped-string detection produces false positives and will annoy
  people.

## Alternatives considered
- **One identity per team** — fewer accounts, but the union problem returns at
  team scale and attribution is lost within a team.
- **One identity per system** — simplest, and indefensible for autonomous agents.
- **Inline encrypted secrets (SOPS-style)** — keeps everything in one artifact,
  but puts ciphertext in the design and key management in the compiler.

## Verification
A test scans all generated output for credential-shaped strings and fails on a
match. Terraform target tests assert one service account per agent and that no
binding exceeds that agent's IR permission set.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Per-agent identity, secrets by reference only. |
