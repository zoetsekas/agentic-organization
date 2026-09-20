---
id: ADR-0054
title: Sandbox execution is pluggable, and prefers a kernel boundary
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture, Security Engineering]
consulted: [Product]
informed: [All engineering]
scope: [targets, runtime, security]
workstreams: [WS-004, WS-028]
supersedes: []
superseded_by: []
related: [ADR-0009, ADR-0011, ADR-0050, ADR-0053]
tags: [security, deployment]
---

# ADR-0054: Sandbox execution is pluggable, and prefers a kernel boundary

## Context
ADR-0009 made the sandbox an environment *class* in the spec — tier, network
posture, egress allowlist, mount scopes, persistence — deliberately saying
nothing about what enforces it. The local target has been filling that in with
ordinary containers, and ADR-0050 already admits the consequence in the words
the mapping report uses: locally this is a Docker-object boundary, not a kernel
one. A shared host kernel, and anyone holding the Docker socket reaches every
tenant.

That is the weakest claim in the whole isolation story, and it is weakest
exactly where agents do the most dangerous thing: running code they wrote, with
tools they chose, unattended.

Docker Sandboxes (`sbx`) is a direct answer to that. It runs an agent inside a
**dedicated microVM** — Docker's own words are "hard security boundary from the
host" and "more isolation without paying the full cost of running a VM" — with
only the project workspace mounted, configurable network controls, and the
ability for the agent to start its own containers inside. It is driven by a CLI
(`sbx run`, `sbx exec`), it runs on a developer machine, it needs no Docker
Desktop, and sandbox kits are OCI references. Docker AI Governance is the
separate product for enforcing network and filesystem policy across an
organization.

## Decision
**Sandbox execution becomes a provider behind the existing environment class,
and where a provider offers a kernel boundary we prefer it.**

1. A `SandboxProvider` seam sits under the environment class. The spec keeps
   describing *what* the boundary must be; the provider decides *how*. No
   provider name enters `src/orgagents/spec/` — this is a binding-layer choice,
   exactly like a runtime adapter or a model.
2. Three providers are named: `container` (today's behaviour, the portable
   floor), `microvm_sbx` (Docker Sandboxes, preferred locally), and
   `target_native` (whatever the cloud target already isolates with — the
   generated infrastructure keeps owning that).
3. **Preference, not requirement.** `sbx` is a developer-machine tool driven by
   a CLI, not a server-side orchestrator with a documented API, so a deployment
   must still work without it. A provider that is unavailable degrades to
   `container` and **says so** — an environment that believes it has a kernel
   boundary and does not is worse than one that never claimed it.
4. The honest claim travels with the artifact. Whatever provider is in force is
   named in the generated README and the mapping report, alongside what it
   actually enforces.

## Scope
How a sandbox is executed locally and how that is reported. It does not change
the environment class in the spec, the egress model, or cloud isolation, which
the targets already own.

## Implementation
A provider protocol in the runtime with `container` as the default, an `sbx`
provider shelling out to `sbx run` / `sbx exec` against a sandbox kit built
from the toolchain images ADR-0053 names, and availability detection that
degrades loudly. The local target records the provider in the generated stack;
mapping reports gain the provider and its real boundary.

## Timeline
Phase 5, alongside WS-028's local isolation work.

## Advantages
- Upgrades the weakest isolation claim in the system from a shared kernel to a
  microVM, for the case that most deserves it.
- Costs nothing structurally: the seam is the one ADR-0009 already implied.
- Docker AI Governance could later enforce, organization-wide, the network and
  filesystem policy the spec already expresses — the policy would stop being
  advisory locally.
- The agent can still start containers inside its sandbox, so tool use does not
  have to be rewritten.

## Disadvantages
- **A dependency on a young third-party product** for the strongest local
  guarantee. Availability, licensing and pricing are not stated on the product
  page, and "New" is not a support commitment.
- **CLI, not an API.** Driving it means shelling out and parsing, which is
  fragile compared with a library, and nothing documents a programmatic
  interface.
- **A developer-machine tool doing a platform job.** It is built for one
  engineer running one coding agent; using it as the sandbox engine for a
  multi-tenant fabric is beyond what it advertises.
- **Two providers is two behaviours to test**, and the degraded path is the one
  that will be exercised in CI — so the preferred path is the *less* tested one.
- **Unverified here.** No Docker daemon and no `sbx` binary exist in this
  environment, so not a line of this has been run. Every claim about microVM
  isolation is Docker's, quoted, not measured by us.
- Platform coverage is macOS, Windows and Ubuntu; a Linux host outside that set
  falls back to containers.

## Alternatives considered
- **Keep containers only** — leaves the weakest claim where it is, and the
  mapping report keeps apologizing for it.
- **Require `sbx` locally** — makes a third-party tool mandatory for a local
  run, which ADR-0011 chose Docker specifically to avoid.
- **gVisor or Kata directly** — comparable isolation without the product
  dependency, at the cost of running the runtime ourselves; worth revisiting if
  the seam proves out.
- **Firecracker ourselves** — the same boundary and a great deal of work that
  is not this platform's job.

## Verification
Tests assert the provider seam exists with `container` as the default, that an
unavailable provider degrades to `container` and records that it did, that no
provider name appears in the spec layer, and that the generated README and
mapping report name the provider in force and what it enforces. Nothing is
verified against a running `sbx`.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Pluggable sandbox providers, microVM preferred locally, degradation must be loud. |
