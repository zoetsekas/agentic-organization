# Running the platform in Docker, plane by plane

The platform is three planes (ADR-0049), and each one is **its own Compose
project** (ADR-0053). That is deliberate: `docker compose down` on one plane
must not take another with it, and an operator should be able to say which
plane a container belongs to by looking at it.

| Plane | File | What it runs |
|---|---|---|
| Designer | `docker-compose.yml` (repository root) | The workshop: canvas, API, CLI (ADR-0048) |
| Fabric | `docker/compose/fabric.yml` | The control plane, the command centre, and the shared infrastructure every tenant draws on |
| Tenant | *generated* — `orgagents compile --target local` | One designed organization, one project per tenant, with its own data stores |

None of these are meant to be merged. The designer authors specs; the fabric
decides whether, where and for whom they run; a tenant project is somebody's
agents actually running.

## The designer plane

```bash
python scripts/designer_secrets.py init   # once: the database password (make secrets)
docker compose up --build
# UI  → http://localhost:8000/ui/
# API → http://localhost:8000/api/...
# health → http://localhost:8000/healthz

ORGAGENTS_SEED=1 docker compose up --build   # with the demo organization
```

Seeding is opt-in and only ever runs when the database file does not exist, so
a restart never overwrites or duplicates what is in the volume.

### The designer's database

Designs, their revisions, workspaces, locks and the audit log are stored in
PostgreSQL, the `postgres` service beside the designer (ADR-0113), in tables
generated from the UML profiles — one PostgreSQL schema per profile. The
platform's own records (catalog, fabric, sessions) stay in the SQLite file
`ORGAGENTS_DB` names.

| Choice | Why |
|---|---|
| `postgres:16-alpine` by digest | The digest in `docker/images.lock` (ADR-0053); `tests/test_docker_assets.py` holds the two equal. |
| Not published | Only the designer, on the Compose network, reaches it (ADR-0114). To look inside: `docker compose exec postgres psql -U orgagents orgagents`. |
| `user: "70:70"`, `cap_drop: [ALL]`, `read_only`, `no-new-privileges` | It runs as the image's own `postgres` user, so it needs no capability to switch users or chown; only the volume and two tmpfs (`/tmp`, `/var/run/postgresql`) are writable. |
| `designer-postgres` volume | The data outlives the container. `docker compose down -v` deletes it. |
| Health check over TCP | `pg_isready -h 127.0.0.1`: the temporary server `initdb` runs on a socket does not count as ready, so the designer does not start against it. |
| Migrations at start | The designer applies the committed forward-only migrations it lacks (`persistence/migrations`, under an advisory lock) before serving. |

**Existing designs.** On its first start with `ORGAGENTS_DATABASE_URL` set
and a SQLite store in the volume, the entrypoint runs
`orgagents db migrate-from-sqlite /data/designer.db --once`: it reads a copy
of the SQLite file (with its WAL), writes every workspace, design and
revision into PostgreSQL, reads each revision back and compares it with its
source, and records the import so it never runs twice. The SQLite file is
not changed and stays as a backup. A revision whose spec holds keys the
current model no longer reads is stored whole rather than normalised, so
nothing is lost. To run it by hand:

```bash
docker compose run --rm designer db migrate-from-sqlite /data/designer.db
docker compose run --rm designer db query q4 t=Organisation::Agent
docker compose run --rm designer export --system <id> --format json
```

### The database password (ADR-0114 v1.2)

There is no default password. Both services read it from the Compose secret
`orgagents_postgres_password`, a git-ignored file at
`.secrets/orgagents_postgres_password`; without it `docker compose up`
refuses to start. `python scripts/designer_secrets.py init` (`make secrets`,
which `make up` runs) writes it once: `$ORGAGENTS_POSTGRES_PASSWORD` if you
set it, a random password otherwise. Postgres takes it
(`POSTGRES_PASSWORD_FILE`) when the volume is first initialised; the designer's
entrypoint reads it into `PGPASSWORD` at every start
(`ORGAGENTS_DATABASE_PASSWORD_FILE`), so it is in no URL and no `docker
inspect` output.

**Upgrading a deployment started before this.** Its database was initialised
with the old default, `orgagents`, and still uses it. Nothing changes until
you recreate the containers; to move over without breaking it:

```bash
python scripts/designer_secrets.py init          # sees the existing designer-postgres
                                                 # volume, writes the OLD password
                                                 # ('orgagents') and warns
docker compose up -d                             # both services now read the secret;
                                                 # the database still works
python scripts/designer_secrets.py rotate --apply   # new random password: ALTER ROLE
                                                 # in the running database, then the
                                                 # file, then recreate the designer
```

`rotate` changes the password inside the running database first (over the
container's local socket, which the image trusts), then writes the file and
keeps the old one as `.previous`; without `--apply` it tells you to recreate
the designer (`docker compose up -d --no-deps --force-recreate designer`).
If you had set `ORGAGENTS_POSTGRES_PASSWORD` before, keep it set for `init`
and it is written instead. `rotate --container <name>` targets another
Postgres container; `--project` another Compose project.

| Choice | Why |
|---|---|
| `python:3.11-slim`, two stages | Dependencies resolve from `pyproject.toml` alone, so that layer rebuilds only when the metadata changes. |
| Non-root `designer` user (uid 10001), no login shell | The app writes to one directory; nothing needs an identity that can log in. |
| `PYTHONPATH=/app/src` with the source tree kept | `api.py` resolves the web assets relative to its own file (`parents[2]/web`), so the UI is only found when the on-disk layout matches the repo. |
| `/data` volume | The platform's SQLite database; the designs are in the `postgres` service's volume. |
| Healthcheck calls `/healthz` | A port check would call a process that is up but broken "healthy". |
| `scheduler` service behind a profile | It runs declared triggers unattended. That should be something you asked for. |

Configuration:

| Variable | Default | Notes |
|---|---|---|
| `ORGAGENTS_DB` | `/data/designer.db` | Keep it under `/data` or it lands on the container layer. |
| `ORGAGENTS_DATABASE_URL` | set by Compose to the `postgres` service | Where designs are stored (ADR-0113). Unset, the designer keeps them in the SQLite store instead. |
| `ORGAGENTS_POSTGRES_PASSWORD` | — (no default) | Read only by `scripts/designer_secrets.py init`, which writes it to the secret file; otherwise a random password is generated. |
| `ORGAGENTS_DATABASE_PASSWORD_FILE` | `/run/secrets/orgagents_postgres_password` | Set by Compose; the entrypoint exports its content as `PGPASSWORD`. |
| `ORGAGENTS_BASE_URL` | `http://localhost:8000` | Session URLs are handed to humans, so this must be the address *they* can reach. |
| `ORGAGENTS_PORT` / `ORGAGENTS_HOST` | `8000` / `0.0.0.0` | |
| `ORGAGENTS_SEED` | `0` | `1` seeds the demo org on first start only. |
| `ORGAGENTS_DESIGNER_AUTH` | `none` | `oidc`, `trusted_proxy` or `none` (ADR-0047). `none` is single-user local; `docker-compose.yml` sets it and binds 127.0.0.1 (ADR-0114). |
| `ORGAGENTS_PROXY_SECRET` | — | Required (or `ORGAGENTS_PROXY_SOURCES`) for `trusted_proxy`: the proxy sends it as `X-Orgagents-Proxy-Secret`; without either the designer refuses to start (ADR-0114). |
| `ORGAGENTS_PROXY_SOURCES` | — | Comma-separated proxy addresses/CIDRs whose identity headers are believed in `trusted_proxy` mode. |
| `ORGAGENTS_PROXY_USER_HEADER` / `_NAME_HEADER` / `_EMAIL_HEADER` | `X-User` / `X-User-Name` / `X-User-Email` | The only headers `trusted_proxy` mode reads identity from. Set them to what your proxy's *authentication* sets (the fabric uses `X-Auth-Request-*` from oauth2-proxy), and make the proxy strip them from client requests (ADR-0114). |
| `ORGAGENTS_OIDC_ISSUER` / `_AUDIENCE` / `_JWKS_URI` | — | Required when auth mode is `oidc`. |

Anything that is not `serve` is passed to the CLI unchanged, so there is no
second image to keep in step:

```bash
docker compose run --rm designer spec validate examples/acme/acme.system.yaml
docker compose run --rm designer compile examples/acme/acme.system.yaml --target local
docker compose run --rm designer records validate
```

## The fabric plane

```bash
cp docker/compose/fabric.env.example .env   # fill in the passwords first
docker compose -f docker/compose/fabric.yml up -d
docker compose -f docker/compose/fabric.yml --profile leases up -d   # + Redis
```

The file refuses to start without `FABRIC_DB_PASSWORD`, `KEYCLOAK_ADMIN_PASSWORD`
and `GRAFANA_ADMIN_PASSWORD` (`${VAR:?}`), because a default password on an
identity provider is worse than no identity provider. It also needs
`ORGAGENTS_PROXY_SECRET`, `OAUTH2_PROXY_CLIENT_SECRET` and
`OAUTH2_PROXY_COOKIE_SECRET`.

Identity comes from **forward auth** (ADR-0114). Traefik's entrypoint first
strips every identity header a client could send (`X-User*`,
`X-Auth-Request-*`, `X-Forwarded-User/Email/Groups/...`), then the `/api/` and
command routers ask `oauth2-proxy` (`/oauth2/auth`) and copy back only its
`X-Auth-Request-*` headers; the fabric reads `X-Auth-Request-User`. Before
anyone can sign in, create a realm `orgagents` and a confidential client
`orgagents` in Keycloak with redirect URI `<base url>/oauth2/callback`, and put
its secret in `OAUTH2_PROXY_CLIENT_SECRET`.

| Service | Image | What it is for |
|---|---|---|
| `fabric` | `orgagents-fabric` (built here) | Control plane API: tenants, deployments, quotas, health |
| `command` | `orgagents-command` (built here) | The command centre, a separate application over the same backend (ADR-0051) |
| `postgres` | `postgres:16-alpine` | The fabric's *own* store. No tenant data lives here. |
| `traefik` | `traefik:v3` | The only ingress. Routes `/ui/`, `/command/` and the API, strips client-supplied identity headers, runs forward auth, and is therefore what makes `trusted_proxy` mode trustworthy (ADR-0047, ADR-0114). |
| `oauth2-proxy` | `quay.io/oauth2-proxy/oauth2-proxy:v7.6.0` | Forward-auth service: an OIDC session against Keycloak; answers Traefik with the signed-in person's `X-Auth-Request-*`. |
| `keycloak` | `quay.io/keycloak/keycloak:26` | An OIDC issuer to develop against. `start-dev`: no TLS. |
| `vault` | `hashicorp/vault:1.17` | Dev mode. Resolves `secret_ref`s locally. **Not a secret store** — see below. |
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.110.0` | One collector; every plane exports to it |
| `prometheus` + `grafana` | `prom/prometheus:v2.54.1`, `grafana/grafana:11` | Metrics and the operator dashboards |
| `jaeger` | `jaegertracing/all-in-one:1.60` | Session traces, which is how anyone debugs an agent run |
| `redis` | `redis:7-alpine`, profile `leases` | Scheduler leases and rate limiting. Optional: the Postgres path works without it. |

`orgagents-fabric` and `orgagents-command` are separate images although they
run the same process over the same backend today. The reason is rollback: an
operator UI you cannot roll back without rolling back the control plane is not
a separate application, whatever ADR-0051 says.

## The tenant plane

A tenant plane is not in this repository as a file; it is *generated*:

```bash
orgagents compile examples/acme/acme.system.yaml --target local --out build
cd build/local && cp .env.example .env && make up
```

A worked example that is built, started and exercised end to end is AYC:
`make ayc-up` (or `python examples/ayc/local_stack.py up`) compiles
`examples/ayc` for the local target into `examples/ayc/generated/local`, with a
stub model and mock backing systems, and starts it as the Compose project
`ayc-local` beside the designer. See `examples/ayc/README.md` and ADR-0109,
which also records what the first real start of a generated stack had to fix.

`--tenant <id>` compiles for a tenant the fabric has registered (an unknown id
is refused, ADR-0050: a design does not get to name its own isolation domain).
Each tenant gets its own Compose project name, its own networks, its own named
volumes, its own `postgres:16-alpine` and its own
`chrislusf/seaweedfs` artifact workspace. A shared database with a tenant
column is rejected (ADR-0053 rule 3, ADR-0050): it is one missing `WHERE` from
a cross-tenant breach.

Sandbox images are per environment class, and every one of them is an image
ADR-0053 names (ADR-0055 explains the mapping and what it costs). They appear
in the generated Compose file as `sandbox-<class>` services behind the
`sandboxes` profile: they are built, not run, because a sandbox is something
code is executed *in* on demand, not a long-running process.

```bash
docker compose --profile sandboxes build
```

A class with `network: none` gets `network_mode: none` — no network at all,
not an internal one.

## Shared, per-tenant, and the line between them

| Shared, fabric-owned | Per tenant |
|---|---|
| Traefik (ingress and TLS) | Postgres instance and volume |
| Keycloak (identity) | MinIO instance and volume |
| Vault (secret resolution) | Compose project, networks, named volumes |
| The OTel collector, Prometheus, Grafana, Jaeger | Agent containers and sandbox images |
| The fabric's own Postgres | Identities and secret scope |

Everything in the left column is a **cross-tenant channel by construction**.
Tenant spans are tagged and routed in the collector rather than merged, and a
misconfigured collector merges them. That is the risk ADR-0053 accepts in
exchange for one observability stack instead of one per tenant.

## Pinning and the lock file

`docker/images.lock` holds a tag-to-digest pin for every third-party image
ADR-0053 names. A tag moves; a digest is the thing that was reviewed.

Right now **every digest in it is the literal token `UNRESOLVED`**. Nothing has
been pulled here, so writing digest-shaped strings would have produced a file
that reads as a supply-chain claim and is fiction. On a machine with a daemon
(or with `crane` or `skopeo`):

```bash
docker/resolve-images.sh                                      # fill in the lock
docker/resolve-images.sh --apply docker/compose/fabric.yml    # tag -> tag@digest
```

Review that diff like the security change it is.

## What this does not do yet

- **None of it has been built or run.** There is no Docker daemon in the
  environment these files were written in. `tests/test_docker_assets.py` and
  `tests/test_plane_compose.py` check that the assets do not drift from the
  application and from ADR-0053 — the services each plane names, no floating
  tags, per-tenant volumes, a zero-network sandbox with no network, entrypoints
  that only pass flags the CLI defines — and none of that is a substitute for
  `docker compose up`. Expect to fix something the first time.
- **No digests are resolved, so rule 1 of ADR-0053 is not yet met.** The
  Compose files reference tags. Some of those tags move by design (`traefik:v3`,
  `grafana/grafana:11`), which is exactly why the lock exists and exactly why
  an unresolved lock is not good enough for a deployment.
- **Nothing is mirrored.** Rule 2 says the fabric deploys from a registry it
  controls. These files reference upstream registries directly, so a deleted or
  re-pushed upstream tag can still change what runs.
- **Vault runs in dev mode.** In-memory storage, a fixed root token, no TLS,
  everything lost on restart. It looks like a secret store and is not one; that
  is worse than nothing if anyone forgets.
- **Keycloak runs `start-dev`.** No TLS, development realm defaults.
- **No TLS anywhere.** Traefik terminates plain HTTP on `:80`. In
  `trusted_proxy` auth mode that proxy is also what makes the identity header
  trustworthy at all, so putting real TLS on it is not cosmetic.
- **Traefik reads the Docker socket** read-only, which is still the strongest
  privilege in the fabric file: anything that can read it can reach every
  container on the host, including every tenant's.
- **Prometheus scrapes only the collector.** The control plane's
  `/api/ops/metrics` is JSON, not the Prometheus text format, so there is no
  scrape job for it and the Grafana dashboards ADR-0053 mentions do not exist.
- **The command centre's asset bundle is not built yet.** `web/command/` does
  not exist (WS-029), so `orgagents-command` currently ships the same `web/`
  tree as the designer and is routed at `/command/` by Traefik. The image and
  the routing are real; the application behind them is not finished.
- **`browser`, `document` and `model_training` sandboxes are degraded.** They
  resolve to `python:3.11-slim`, which has no browser, no LibreOffice and no
  GPU runtime (ADR-0055 records why and what it costs).
- **No SBOM, no image scanning, no digest refresh.** A stale pin is an
  unpatched CVE, and nothing here updates one.
- **No per-tenant lifecycle.** Standing up, tearing down and upgrading a
  tenant's project is a `make` target in generated output, not something the
  fabric performs.
