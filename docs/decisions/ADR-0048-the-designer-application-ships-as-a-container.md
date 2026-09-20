---
id: ADR-0048
title: The designer application ships as a container
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Product]
informed: [All engineering]
scope: [ui, docs]
workstreams: [WS-020]
supersedes: []
superseded_by: []
related: [ADR-0011, ADR-0020, ADR-0021, ADR-0047]
tags: [deployment]
---

# ADR-0048: The designer application ships as a container

## Context
ADR-0011 decided that Docker defines a *designed* system locally: the compiler
emits a Compose stack for the agents somebody builds. The designer itself had
no such story. Running it meant cloning the repository, arranging a Python
environment, knowing that the UI is served from `/ui/`, knowing that the
database is a file in the working directory, and remembering which flags
`serve` takes. That is a workshop nobody else can open.

It also leaves state in a bad place: the SQLite database sits wherever the
process happened to start, which is fine for one developer and wrong for a team
sharing an instance.

The confusion to avoid is real: two Compose files now exist in the same
repository and they describe different things.

## Decision
The designer ships as its own container image, with a Compose file that stands
up **one service** — the designer — and a second, **behind a profile**, that
runs declared triggers.

Four rules keep it honest:

1. **The designer's container and a designed system's container are separate
   artifacts.** `docker-compose.yml` at the root runs the designer;
   `orgagents compile --target local` emits a stack for somebody's agents. They
   are not merged, and the documentation says so where each appears.
2. **The only mutable state is `/data`**, declared as a volume, so even a bare
   `docker run` keeps the database off the image layer.
3. **Seeding is opt-in and first-start only.** A restart never overwrites or
   duplicates what is in the volume.
4. **Unattended execution is opt-in.** The scheduler runs declared triggers
   with no human in the loop, so it sits behind a Compose profile rather than
   starting with `up`.

The image runs as a non-root user with no login shell, and its healthcheck
calls the application's own `/healthz` rather than checking the port, so a
process that is up but broken reports unhealthy.

## Scope
The designer application: its API, its UI and its CLI. It does not cover
publishing images to a registry, TLS termination, horizontal scaling, or the
infrastructure the compiler generates for designed systems.

## Implementation
`Dockerfile` (two stages: dependency layer from `pyproject.toml`, then a slim
runtime), `docker/entrypoint.sh`, `docker-compose.yml`, `.dockerignore` and
`docs/DOCKER.md`. The entrypoint runs `python -m orgagents.cli` rather than an
installed console script, because the image installs dependencies and sets
`PYTHONPATH` instead of installing the project — the source tree has to stay on
disk in the repository's layout anyway, since `api.py` resolves the web assets
relative to its own file. Anything that is not `serve` is passed to the CLI
unchanged, so `spec validate`, `compile` and `records validate` run from the
same image with no second image to keep in step.

`tests/test_docker_assets.py` asserts the assets do not drift from the
application.

## Timeline
Phase 2, alongside the designer backend (WS-020).

## Advantages
- The designer can be opened by somebody who has not set up a Python
  environment, which is the difference between a tool and a repository.
- State has one home, on a volume, instead of wherever the process started.
- The same image runs the CLI, so a container cannot fall out of step with the
  commands documented next to it.
- Non-root, healthchecked, and unattended execution kept behind a profile — the
  defaults are the safe ones.

## Disadvantages
- **It has not been built or run.** No Docker daemon exists in the environment
  this was authored in, so the image is unbuilt and untested. The asset tests
  check coherence with the application, not that `docker build` succeeds.
  Expect to fix something the first time it is run for real.
- **`PYTHONPATH` plus a source tree, rather than an installed package**, is a
  workaround for `api.py` locating the UI relative to its own file. It works
  and it is documented, but it is a layout constraint the application should
  not be imposing.
- **SQLite in a single container** is not a scaled deployment. The repository
  layer abstracts persistence (ADR-0020), so a relational backend is
  configuration rather than a rewrite — but nobody has run it that way.
- **No TLS, and no image publishing.** A reverse proxy is assumed, and in
  `trusted_proxy` auth mode (ADR-0047) that proxy is also what makes the
  identity header trustworthy at all.
- **The base image is pinned by tag, not digest**, so a rebuild can pick up a
  different `python:3.11-slim`. No SBOM is produced.
- **Two Compose files in one repository** will be confused by somebody,
  however clearly it is documented.

## Alternatives considered
- **Leave it to a README section** — what we had; it assumes a Python
  environment and leaves state wherever the process started.
- **Reuse the generated local-target stack for the designer** — conflates the
  workshop with the work, and would make the compiler responsible for
  deploying its own designer.
- **Publish a prebuilt image** — better for consumers, but publishing an image
  nobody has built or scanned would be worse than not publishing one.
- **Kubernetes manifests instead of Compose** — the local story is one machine;
  a cluster is WS-006 M5's question, not this one's.

## Verification
`tests/test_docker_assets.py`: the healthcheck hits a route the app actually
serves, the entrypoint passes only flags the CLI defines, it does not assume a
console script the image never installs, the image layout keeps the web assets
resolvable, the container does not run as root, state lives on a volume,
seeding is opt-in and never overwrites, the scheduler is behind a profile, and
the build context excludes local databases. The entrypoint was additionally
executed directly against the application outside a container: `/healthz`,
`/ui/` and the API answered 200.

The open item is real verification: `docker compose up --build` on a machine
with a daemon, which nothing here can do.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. The designer ships as its own container, separate from the stack the compiler generates. |
