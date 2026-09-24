---
id: ADR-0114
title: A running stack authenticates its callers and its approvers, publishes on loopback, and hardens its containers
status: Accepted
version: 1.2.0
date: 2026-09-23
updated: 2026-09-24
deciders: [Platform]
consulted: [Security]
informed: [Designer, Targets]
scope: [security, runtime, targets, ui]
workstreams: [WS-006, WS-008]
supersedes: []
superseded_by: []
related: [ADR-0047, ADR-0109, ADR-0015, ADR-0053]
tags: [authentication, approvals, network, hardening]
---

# ADR-0114: A running stack authenticates its callers and its approvers, publishes on loopback, and hardens its containers

## Context
A review of the running AYC stack (ADR-0109) found five gaps between what the
design enforces and what the deployment let anyone do:

1. `/approve` on a worker accepted any `approver` string the design named, and
   the chat forwarded one from the browser. Anyone who could reach the port
   could type "p_coo" and release a payment.
2. The designer defaulted to `trusted_proxy` auth, which believes `X-User`, and
   ran with no proxy in front: any caller was whoever they said.
3. A worker's `/run`, `/workflow` and `/approve` had no authentication at all.
4. Every port was published on every host interface; `control` had a gateway
   it did not need; the OTLP port was published.
5. Containers ran with a writable root filesystem, default capabilities and no
   process bound.

## Decision
1. **An approval is a signed release, not a name.** `/approve` takes a token:
   an **Ed25519** signature over agent, `aud` (the same agent id), tool, a
   hash of the canonical arguments, approver, expiry (5 minutes), a nonce and
   the `kid` of the signing key. **Only the issuer holds a private key;
   workers hold public keys** (`ORGAGENTS_APPROVAL_PUBLIC_KEYS`,
   `kid:key[,kid:key]`), so a worker -- or anything that can read its
   environment -- can verify a release and can never mint one, for itself or
   anyone else. Workers accept any listed key by `kid`, which is how the
   issuer's key rotates without a gap. The worker verifies the token, refuses
   a nonce it has seen, and still requires the approver to be one the design
   names; the grant keeps its single-use, TTL and exact-arguments semantics
   (ADR-0109). **Used nonces are persisted** on a per-agent state volume
   (`ORGAGENTS_STATE_DIR`, `/var/lib/orgagents/<agent>-nonces.jsonl`, pruned
   at expiry), so a restarted worker still refuses a replay within the token
   lifetime. Grants, consumptions and lapses go to the worker's audit log
   (`<state dir>/<agent>-audit.jsonl`, the structured log, and `GET /audit`).
2. **The chat is the issuer, only for a person it has authenticated.** It is a
   minimal local identity provider: pick yourself from the design's people and
   give a per-person passcode, generated into the git-ignored `.env` by
   `local_stack.py` and printed by `up` / `passcodes`. The approver is the
   signed-in person; the request carries no approver. Sessions are HttpOnly,
   SameSite=Strict cookies. **Production does not use this**: people sign in
   through the designer's OIDC (ADR-0047) and approvals are issued from that
   verified identity.
3. **Every worker needs its own service token** (`Authorization: Bearer`) on
   everything but `/healthz`; a worker with no token refuses to start. Tokens
   are per agent, generated into `.env`, given to the worker and to the
   platform components that call it (the chat). No agent holds another's.
4. **The designer defaults to `none`** (single-user local) when no mode is set,
   and **refuses to start `trusted_proxy`** unless `ORGAGENTS_PROXY_SECRET`
   (checked on every request against `X-Orgagents-Proxy-Secret`) or
   `ORGAGENTS_PROXY_SOURCES` (addresses/CIDRs) is configured. In
   `trusted_proxy` mode identity is read **only** from the headers named by
   `ORGAGENTS_PROXY_USER_HEADER` / `_NAME_HEADER` / `_EMAIL_HEADER` (default
   `X-User` / `X-User-Name` / `X-User-Email`); the proxy must set them from
   its own authentication and strip them from every client request. The
   repo's `docker-compose.yml` sets `none` explicitly and binds `127.0.0.1`.
   The fabric compose puts **forward auth** in front (oauth2-proxy against its
   Keycloak): Traefik's entrypoint middleware first strips every identity
   header any component reads (`X-User*`, `X-Auth-Request-*`,
   `X-Forwarded-User/Email/Groups/Preferred-Username`) and sets the proxy
   secret, then the `/api/` router's forward-auth copies back only
   `X-Auth-Request-*` from oauth2-proxy's answer, and the fabric reads
   `X-Auth-Request-User`. A client-sent `X-User` is removed at the proxy and
   would be ignored by the fabric if it were not.
5. **Loopback only, fewer gateways.** The local target publishes on
   `127.0.0.1`, does not publish telemetry, marks `control` `internal: true`, and
   adds an `ingress` network (masquerading off, no agents) for the services a
   person reaches. The AYC overlay follows.
6. **Hardening.** Agents and sandboxes: `read_only`, tmpfs `/tmp` (and
   `/workspace` for sandboxes), `cap_drop: [ALL]`, `no-new-privileges`, a pids
   bound (in `deploy.resources.limits.pids` for agents, since Compose refuses
   both). The designer compose, the AYC mocks and chat get the same.
   **The stack's own services too** (v1.2): the local target gives `state`
   (Postgres), `nats`, `artifacts` (SeaweedFS), `telemetry`, `bus-init` and the
   in-stack `designer` the same four settings, with **no capability added
   back** — none needs one, since every one listens above 1024 and none
   changes ownership or user:

   | Service | User | Writable |
   |---|---|---|
   | `state` | `70:70`, the image's `postgres` (so its entrypoint skips chown/gosu) | its volume; tmpfs `/tmp`, `/var/run/postgresql` |
   | `nats` | root, no capabilities (a fresh named volume at `/data` is root's) | its volume `/data`; tmpfs `/tmp`; config mounted read-only |
   | `artifacts` | root, no capabilities (same reason) | its volume `/data`; tmpfs `/tmp` |
   | `telemetry` | the image's 10001 | tmpfs `/tmp` |
   | `designer`, `bus-init` | the platform image's `agent` (10001) | the designer's database on a `designer-data` volume at `/var/lib/orgagents` (`--db`); tmpfs `/tmp` |

   Root without capabilities cannot override file permissions, bind a low
   port, change ownership or load anything into the kernel; it owns its own
   volume and nothing else.
7. **No default database password** (v1.2). The repository's
   `docker-compose.yml` reads the designer's Postgres password from a Compose
   secret (`.secrets/orgagents_postgres_password`, git-ignored, excluded from
   the build context) that `scripts/designer_secrets.py init` (`make
   secrets`) writes — `$ORGAGENTS_POSTGRES_PASSWORD` if set, random
   otherwise — and Compose refuses to start without it. Postgres reads it as
   `POSTGRES_PASSWORD_FILE`; the designer's entrypoint exports it as
   `PGPASSWORD`, so the URL carries none. **Existing deployments** (a
   `designer-postgres` volume initialised with the old default `orgagents`):
   `init` writes that old password so the database keeps working, warns, and
   `designer_secrets.py rotate --apply` changes it in the running database
   (`ALTER ROLE` over the local socket), writes the file (keeping
   `.previous`) and recreates the designer. `docs/DOCKER.md` has the steps.

## Scope
Binds the worker (`orgagents worker`), the local target, the designer's auth
mode, the repo and fabric compose files, and the AYC example. Does not cover
the cloud targets (their ingress and IAM are theirs). Since v1.2 it covers the
local target's infrastructure services and in-stack designer, and the repo
compose's database password. Agent-to-agent bus identity and delegation chains
are ADR-0118's (NKeys and signed hops, v1.1).

## Implementation
- `orgagents.security.service_auth`: Ed25519 token issue/verify (keys by
  `kid`), keypair generation, `NonceStore` (file-backed), constant-time
  bearer check. `cryptography>=42,<47` is a core dependency; the designer,
  fabric, command and runtime images and the AYC chat image install it.
- `runtime/worker.py`: token middleware, signed `/approve` against public keys
  with persisted nonce replay refusal, `/audit`, `audit_sink`.
- `harness/builder.py`: `ApprovalGrants(audit=...)` records
  `approval_granted` / `approval_consumed` / `approval_expired`.
- `designer/auth.py`, `api.py`: proxy guard, per-request proxy check,
  configurable identity headers, `none` default.
- `docker/compose/fabric.yml`: `strip-identity` on the entrypoint, `authn`
  forward-auth (oauth2-proxy) on the `/api/` and command routers.
- `compiler/targets/local.py`: loopback ports, internal `control`, `ingress`,
  hardening, per-worker `ORGAGENTS_WORKER_TOKEN`, the stack's
  `ORGAGENTS_APPROVAL_PUBLIC_KEYS`, a per-agent `agent-state-<id>` volume at
  `/var/lib/orgagents`.
- `examples/ayc`: `local_stack.py` generates tokens, the issuer keypair
  (private key to the chat only, public keys to workers) and passcodes,
  retires v1.0's HMAC secret and derived keys, and `rotate-approval-key`
  rotates (workers keep trusting the previous key); the chat signs in and
  issues; `end_to_end_local.py` signs in and checks the doors (step 11).
- v1.2: `compiler/targets/local.py` `INFRA_HARDENING` and `STATE_USER` on
  `state`, `nats`, `artifacts`, `telemetry`, `bus-init` and `designer` (its
  `--db` on the `designer-data` volume). `docker-compose.yml` secrets,
  `docker/entrypoint.sh` (`ORGAGENTS_DATABASE_PASSWORD_FILE` → `PGPASSWORD`),
  `scripts/designer_secrets.py` (`init`, `rotate [--apply]`, `status`),
  `make secrets` / `rotate-db-password`, `.gitignore` / `.dockerignore`.

## Timeline
Lands with ADR-0109's local stack, before it is shown to anyone else.

## Advantages
- A release proves who, what, and when; typing a name proves nothing.
- A compromised agent cannot drive a neighbour or approve for it.
- A laptop on a café network exposes nothing.

## Disadvantages
- Whoever holds the issuer's private key can approve anything a named
  approver could; it lives in the chat's environment only, and rotation is a
  manual `rotate-approval-key` + restart.
- The chat's passcode sign-in is a demo IdP, not an identity system.
- Used nonces are a file on the worker's own volume, not in the stack's
  `state` Postgres: wiring workers to Postgres (driver, credentials, schema)
  is larger than this change. Losing the volume (`down --volumes`) forgets
  them; the 5-minute expiry bounds that window.
- The fabric's forward-auth needs a Keycloak realm and client set up by hand
  before anyone can sign in.
- On Docker Desktop, disabling masquerade does not stop egress from `ingress`
  or `mocks-admin` (its VM networking routes it anyway); on a Linux engine it
  does. `control`'s `internal: true` holds on both.
- The chat's approval format is a copy of the platform's (the chat image
  does not carry the platform); a test holds them together.
- NATS and SeaweedFS still run as uid 0 inside their containers (v1.2): a
  fresh named volume mounted at a path the image does not create is root's,
  and making it someone else's needs a chown — a capability or an init step.
  With no capabilities, root there owns its volume and nothing else; a
  non-root user would need a pre-created, pre-owned volume per stack.
- The designer's database password is a file on the host
  (`.secrets/`, mode 0644 inside a 0700 directory, because Compose's
  non-Swarm file secrets are bind mounts that keep the host file's
  permissions and the container users are not the host user). Anyone who can
  read the checkout can read it; it is local-only, like `.env`.
- A deployment upgraded with `init` keeps the old default password until
  someone runs `rotate`; `init` and `status` say so, but do not force it,
  because rotating restarts the designer.

## Alternatives considered
- **Keep the approver field, check a shared password** — still a name plus a
  shared secret the browser holds.
- **One stack-wide approval key on every worker** — any worker could approve
  for any other.
- **Per-agent HMAC keys derived from a stack secret** (v1.0) — a worker could
  not approve for another agent, but could for itself: anything that read its
  environment could self-approve. Replaced by Ed25519 in v1.1.
- **Default the designer to `oidc`** — a local run would need an issuer.

## Verification
`tests/test_service_auth.py`: forged/unsigned/altered/expired/other-agent
releases refused (403 over HTTP), a token signed with a wrong key refused,
rotation trusts listed kids only, approver-in-a-field refused, replay refused
and still refused after a worker restart, audit events written; no worker's
environment names private key material and only the chat holds the signing
key; `/run` without or with the wrong token is 401;
`trusted_proxy` without a secret refuses to start and without the header is
401; generated ports are `127.0.0.1`, telemetry unpublished, `control`
internal; agents and sandboxes hardened; each worker gets only its own token
and key; chat and `local_stack.py` agree with the platform.
`examples/ayc/end_to_end_local.py` step 11 checks the same against the running
stack. `tests/test_fabric_proxy.py`: a client `X-User` delivered with the
proxy secret is not believed; fabric.yml strips every identity header
`designer/auth.py` reads, on the entrypoint, before the forward-auth router,
and copies back exactly the header the fabric reads.
v1.2: `tests/test_service_auth.py` holds the stack's own services hardened
with no capability added back (Postgres as 70:70, its socket on tmpfs, the
designer's database on a volume); `tests/test_docker_assets.py` holds the
repo compose free of any default password (no `:-orgagents`, no password in
the URL, both services on the secret, the entrypoint reading it, `.secrets/`
ignored) and `designer_secrets.py init` keeping an existing database's
password, generating one for a fresh install and never overwriting. The
generated stack is brought up with every service healthy and the AYC
end-to-end scenarios pass against it.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.2.0 | 2026-09-24 | The local target's infrastructure (Postgres as its own user, NATS, SeaweedFS, telemetry, bus-init) and in-stack designer are read-only, capability-free, no-new-privileges and pids-bounded, writable paths on tmpfs or volumes. The repo compose's Postgres password is a generated Compose secret with no default; `designer_secrets.py` keeps an existing deployment's password and rotates it. |
| 1.1.0 | 2026-09-23 | Approvals signed with Ed25519: issuer holds the private key, workers only public keys by `kid` (rotation), `aud` claim; used nonces persisted on a per-agent state volume. Trusted-proxy identity headers configurable; fabric strips all identity headers and takes identity only from oauth2-proxy forward auth. |
| 1.0.0 | 2026-09-23 | Accepted. |
