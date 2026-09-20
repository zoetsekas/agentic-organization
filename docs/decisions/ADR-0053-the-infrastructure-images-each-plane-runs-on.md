---
id: ADR-0053
title: The infrastructure images each plane runs on
status: Accepted
version: 1.1.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Product]
informed: [All engineering]
scope: [targets, security, docs]
workstreams: [WS-028, WS-029, WS-030]
supersedes: []
superseded_by: []
related: [ADR-0011, ADR-0048, ADR-0049, ADR-0050, ADR-0047]
tags: [deployment, tenancy]
---

# ADR-0053: The infrastructure images each plane runs on

## Context
ADR-0049 split the platform into three planes and ADR-0048 put the designer in
a container. Neither said what the rest of it runs on. "Use Docker" is not a
design until the images are named, because the image list *is* the trust
boundary: every one of them runs code we did not write, inside a boundary we
are claiming isolates tenants from each other.

Two failure modes to avoid. Picking images ad hoc per compose file means three
versions of Postgres and no idea which one holds what. And reaching for a
managed cloud service locally means the local plane stops resembling the
deployed one, which is exactly what ADR-0011 chose Docker to prevent.

## Decision
Each plane is a Compose project of its own, and every image is **pinned and
mirrored** — never `:latest`, never pulled straight from a public registry at
deploy time.

### Our own images, built from this repository

| Image | Plane | What it is |
|---|---|---|
| `orgagents-designer` | designer | The designer app: API, canvas UI, CLI (ADR-0048) |
| `orgagents-fabric` | fabric | Control plane API: tenants, deployments, quotas, health |
| `orgagents-command` | fabric | The command centre UI assets (ADR-0051) |
| `orgagents-runtime` | tenant | One tenant's agent runtime, one container per tenant |

All four build `FROM python:3.11-slim`. One base, one patch cadence.

### Third-party images

| Image | Plane | Why it is there | Alternative when deployed |
|---|---|---|---|
| `postgres:16-alpine` | designer, fabric, **per tenant** | The document store behind `store.py`. A tenant gets its **own** instance and volume — a shared database with a tenant column is one missing `WHERE` from a cross-tenant breach | Cloud SQL / RDS / Azure Database |
| `traefik:v3` | fabric | Reverse proxy and TLS. Routes `/ui/`, `/command/` and per-tenant hostnames; it is also what makes `trusted_proxy` identity trustworthy (ADR-0047) | Cloud load balancer + WAF |
| `quay.io/keycloak/keycloak:26` | fabric | An OIDC issuer to develop and test against locally | The company's real IdP |
| `hashicorp/vault:1.17` | fabric | Resolves the `secret_ref`s the compiler emits. Dev mode locally, never in a deployment | Secret Manager / Secrets Manager / Key Vault |
| `otel/opentelemetry-collector-contrib:0.110.0` | fabric | One collector; every plane exports to it. Tenant spans are tagged and routed, never merged into a shared view a tenant can read | Managed collector per provider |
| `prom/prometheus:v2.54.1` + `grafana/grafana:11` | fabric | Metrics and the operator dashboards behind the command centre | Cloud Monitoring / CloudWatch / Azure Monitor |
| `jaegertracing/all-in-one:1.60` | fabric | Session traces, which is how anyone debugs an agent run | Cloud Trace / X-Ray / App Insights |
| `minio/minio:RELEASE.2024-09-13T20-26-02Z` | **per tenant** | S3-compatible artifact workspace — the offload target for large tool output (ADR-0036) | GCS / S3 / Blob Storage |
| `redis:7-alpine` | fabric | Scheduler leases and rate limiting. Optional: the SQLite/Postgres path works without it | Memorystore / ElastiCache |

### Sandbox images, by toolchain

The environment classes in `docs/SANDBOX_TEMPLATES.md` map to images, not to
ad-hoc installs: `none` → `gcr.io/distroless/static-debian12:nonroot`;
`python` → `python:3.11-slim`; `node` → `node:22-alpine`; `data` →
`python:3.11-slim` plus the pinned analysis wheels.

**Amended in v1.1.0.** The first table named four images for eight toolchain
classes, and the three unnamed ones fell back to `python:3.11-slim` — which
meant a `browser` sandbox had no browser, silently. That is a capability a
design asked for and did not get, which is worse than a refusal. So:
`browser` → `mcr.microsoft.com/playwright/python` (the browsers and their
system libraries are the point of that image; pin the exact tag in the lock
file); `document` and `model_training` → `python:3.11-slim` **plus pinned
wheels**, named explicitly here rather than inherited by accident. A toolchain
with no image mapping must **fail the build**, not quietly resolve to a base
image that cannot do the job. A sandbox with `network: none` gets no network
in Compose; `allowlist` gets an egress proxy, because Docker cannot express a
destination allowlist on its own.

### Four rules

1. **Pin by digest, not by tag.** A tag moves; a digest is the thing that was
   reviewed. Tags appear in this table for readability and in the generated
   files as digests.

   **Not met today (v1.1.0).** Digests cannot be resolved in this environment —
   no daemon, no registry access — so `docker/images.lock` carries the literal
   token `UNRESOLVED` for every entry and the generated Compose files carry
   tags. `docker/resolve-images.sh` fills the lock in and rewrites the Compose
   files on a machine that can. Until somebody runs it, rules 1 and 2 are
   stated intent, not enforced practice, and `docs/DOCKER.md` says so.
2. **Mirror before use.** The fabric deploys from a registry it controls, so a
   deleted or re-pushed upstream tag cannot change what a tenant runs.
3. **A tenant's data stores are the tenant's own.** Postgres and MinIO are
   per-tenant instances with per-tenant volumes. Shared-instance-with-a-tenant-
   column is explicitly rejected.
4. **Shared infrastructure is fabric-owned and listed** as a common service
   (WS-030 M1), with what crosses the boundary stated. Today that is the
   collector, the proxy, the IdP and the secret store — and each is a
   cross-tenant channel if it is wrong.

## Scope
The images each plane runs and how they are pinned. It does not decide cluster
orchestration, registry choice, or the cloud equivalents beyond naming them.

## Implementation
`docker-compose.yml` (designer, exists), `docker/compose/fabric.yml`, and a
per-tenant stack generated by the local target — which already emits a
per-tenant Compose project with per-tenant networks and volumes (WS-028 M3).
A pinned `docker/images.lock` holds tag-to-digest for every third-party image.

## Timeline
Phase 5, alongside WS-029.

## Advantages
- The image list is reviewable in one place instead of implicit in four files.
- One base image for everything we build means one patch cadence.
- Local resembles deployed: the same topology, with managed services swapped in
  at the edges rather than the middle.
- Per-tenant data stores make the strongest isolation claim the cheapest option.

## Disadvantages
- **A per-tenant Postgres and MinIO is expensive** — tens of tenants means tens
  of databases. Isolation bought with money, and at some scale somebody will
  want the shared-instance version this ADR rejects.
- **Nine third-party images is a real supply chain.** Every one is code we did
  not write inside the boundary we claim isolates tenants; mirroring and
  digests reduce the risk and do not remove it. No SBOM, no scanning yet.
- **Pinned digests go stale**, and a stale pin is an unpatched CVE. Nothing
  here updates them; that is a job nobody has been given.
- **The versions in this table are indicative and unverified.** No Docker
  daemon exists in this environment, so not one of these images has been
  pulled, and no digest has been resolved. Treat the table as the decision and
  the lock file as the truth once somebody generates it.
- **Shared observability is a cross-tenant channel by construction.** Tagging
  and routing keep tenants apart; a misconfigured collector merges them.
- **Vault in dev mode is not a secret store.** It is a convenience that looks
  like one, which is worse if anyone forgets.

## Alternatives considered
- **Managed services locally too** — local stops resembling deployed, and
  developing requires a cloud account.
- **One shared Postgres with a tenant column** — cheap and one missing `WHERE`
  from a cross-tenant breach. Rejected on ADR-0050's terms.
- **Kubernetes for every plane** — better isolation primitives, far heavier for
  a single-machine story; WS-006 M5's question.
- **No third-party infrastructure; build it in** — a worse Postgres, a worse
  collector, and the same supply chain, only ours.

## Verification
Generation tests assert the per-tenant stack names its own Postgres and MinIO
volumes, that `network: none` sandboxes get no network, and that no generated
Compose file references a floating tag. `docker/images.lock` must resolve every
image this ADR names. Nothing here is verified against a running daemon.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-20 | Named images for all eight toolchain classes after `browser` silently resolved to a browserless base; recorded that digest pinning and mirroring are not met in this environment. |
| 1.0.0 | 2026-09-20 | Accepted. Four first-party images on one base, nine pinned third-party images, per-tenant data stores. |
