# Running the designer in Docker

This is the **designer application** — the thing you build agentic systems
in. It is not the Compose stack the compiler *generates* for a system you
design (`orgagents compile --target local`, ADR-0011). The two are separate
artifacts and are not meant to be merged: one runs the workshop, the other
runs somebody's agents.

## Quick start

```bash
docker compose up --build
# UI  → http://localhost:8000/ui/
# API → http://localhost:8000/api/...
# health → http://localhost:8000/healthz
```

With the demo organization already populated:

```bash
ORGAGENTS_SEED=1 docker compose up --build
```

Seeding is opt-in and only ever runs when the database file does not exist, so
a restart never overwrites or duplicates what is in the volume.

## What the image contains

| Choice | Why |
|---|---|
| `python:3.11-slim`, two stages | Dependencies resolve from `pyproject.toml` alone, so that layer rebuilds only when the metadata changes. |
| Non-root `designer` user (uid 10001), no login shell | The app writes to one directory; nothing needs an identity that can log in. |
| `PYTHONPATH=/app/src` with the source tree kept | `api.py` resolves the web assets relative to its own file (`parents[2]/web`), so the UI is only found when the on-disk layout matches the repo. Changing that resolution belongs in the application, not in a Dockerfile workaround. |
| `/data` volume | The SQLite database is the only mutable state. Declared as a volume so even a bare `docker run` keeps it off the image layer. |
| Healthcheck calls `/healthz` | A port check would call a process that is up but broken "healthy". |
| `scheduler` service behind a profile | It runs declared triggers unattended. That should be something you asked for, not a side effect of `up`. |

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `ORGAGENTS_DB` | `/data/designer.db` | Keep it under `/data` or it lands on the container layer and is lost on recreate. |
| `ORGAGENTS_BASE_URL` | `http://localhost:8000` | Session URLs are handed to humans, so this must be the address *they* can reach, not the container's. |
| `ORGAGENTS_PORT` / `ORGAGENTS_HOST` | `8000` / `0.0.0.0` | |
| `ORGAGENTS_SEED` | `0` | `1` seeds the demo org on first start only. |
| `ORGAGENTS_DESIGNER_AUTH` | `trusted_proxy` | `oidc`, `trusted_proxy` or `none` (ADR-0047). |
| `ORGAGENTS_OIDC_ISSUER` / `_AUDIENCE` / `_JWKS_URI` | — | Required when auth mode is `oidc`. |

## Other commands from the same image

Anything that is not `serve` is passed to the CLI unchanged, so there is no
second image to keep in step:

```bash
docker compose run --rm designer spec validate examples/acme.system.yaml
docker compose run --rm designer compile examples/acme.system.yaml --target local
docker compose run --rm designer records validate
```

## What this does not do yet

- **It has not been built or run here.** There is no Docker daemon in the
  development environment this was written in, so the Dockerfile, Compose file
  and entrypoint are unbuilt. `tests/test_docker_assets.py` checks that they do
  not drift from the application — that the entrypoint only passes flags the
  CLI defines, that the healthcheck hits a route that exists, that the image
  layout keeps the UI resolvable — but none of that is a substitute for
  `docker compose up` on a machine with a daemon. Expect to fix something the
  first time.
- **No TLS.** Run it behind a reverse proxy. In `trusted_proxy` auth mode that
  proxy is also what makes the identity header trustworthy at all.
- **SQLite, single container.** Fine for a workshop and for a team sharing one
  instance; it is not a horizontally scaled deployment. The designer's
  repository layer already abstracts persistence (ADR-0020), so a relational
  backend is a configuration change rather than a rewrite — but nobody has run
  it that way.
- **No image publishing, no pinned base digest, no SBOM.** The base image is
  pinned by tag, not by digest, so a rebuild can pick up a different
  `python:3.11-slim`.
