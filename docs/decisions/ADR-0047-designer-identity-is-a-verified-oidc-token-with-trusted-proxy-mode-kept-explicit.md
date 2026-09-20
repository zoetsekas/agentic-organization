---
id: ADR-0047
title: Designer identity is a verified OIDC token, with trusted-proxy mode kept explicit
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Security Engineering, Platform Architecture]
consulted: [Product]
informed: [All engineering]
scope: [designer, security]
workstreams: [WS-021, WS-020]
supersedes: []
superseded_by: []
related: [ADR-0032, ADR-0043]
tags: [designer, security, identity, oidc]
---

# ADR-0047: Designer identity is a verified OIDC token, with trusted-proxy mode kept explicit

## Context
Designer RBAC (ADR-0032) has always decided what a principal may do; it has
never established who the principal *is*. The identity came from an `X-User`
request header, which anyone who can reach the port can set. That is defensible
behind an authenticating reverse proxy and indefensible anywhere else, and
nothing in the product distinguished the two situations — the header path was
the only path, so a deployment that forgot the proxy got no warning.

The audit log (ADR-0043) made the gap concrete rather than theoretical. It
records who did what and who was refused, and its own record of itself says the
actor is only as trustworthy as the proxy in front of it. An audit log whose
actor field is attacker-controlled is worse than none, because it invites
belief.

Meanwhile every organization that would run a shared designer already has an
identity provider and already expresses "who is a designer" as directory
groups. Re-entering that membership by hand as workspace members is duplicated
state that drifts the day someone changes team.

## Decision
The designer authenticates people itself, from an OIDC ID token, and maps the
token's groups onto workspace roles through configuration.

1. **`auth_mode` is explicit and is a setting, not an inference.** It is one of
   `oidc`, `trusted_proxy` or `none`, it is visible in
   `GET /api/designer/settings`, and the legacy value `header` loads as
   `trusted_proxy` so existing installations keep working while being renamed
   to what they actually are.
2. **In `oidc` mode a bare header authenticates nothing.** There is no fallback
   from a missing or bad token to the header, because a fallback makes the
   whole mechanism optional at the attacker's discretion.
3. **A token is verified before any claim in it is read for identity**:
   signature against the issuer's published JWKS, `iss`, `aud`, `exp`, `nbf`,
   `iat`, and `nonce` where the caller kept one. The algorithm is pinned to a
   configured asymmetric set, so `alg: none` and HMAC-over-the-public-key are
   refused before a key is even looked up.
4. **Group mapping is deny-by-default with a written precedence.** Highest
   wins within a tier; an unmapped group grants nothing at all.
   1. An explicit workspace membership beats every group claim — a role an
      admin chose is not silently rewritten by a directory change.
   2. Otherwise a mapping scoped to that workspace applies.
   3. Otherwise an installation-wide (`*`) mapping applies.
   4. Otherwise nothing: authenticated, and refused everything.
5. **Authentication failure is 401; authorization failure stays 403.** They are
   different answers to different questions and conflating them makes both
   harder to debug and easier to probe.
6. **Authentication failures are written to the designer audit log** with a
   stable failure code and the claimed user, and without the token or its
   claims — a failed token is attacker-controlled input and does not belong in
   an append-only store. Successes are not logged per request: they are every
   request, and the actions they precede are already recorded.

## Scope
Binds human access to the designer service and its HTTP API: `auth.py`,
`rbac.Principal`, `DesignerSettings` and the `principal` dependency in
`api.py`. It does not touch agent-facing RBAC (WS-004) — being an admin here
still says nothing about what any agent may reach — it does not make the
designer an identity provider, and it does not address per-system access within
a workspace (WS-021 M4). Only RSA-signed tokens (RS256/384/512) are verified;
EC and EdDSA are out of scope until an installation needs them.

## Implementation
`src/orgagents/designer/auth.py`:

- `AuthenticatedPrincipal`, a `Principal` that also carries subject, issuer,
  groups, mode and the verified claims, so everything downstream keeps taking a
  plain `Principal` and is unchanged by this decision.
- `TokenVerifier` over `OIDCConfig`, and `JWKSCache` over an **injectable**
  `JWKSSource` — the only part that touches the network — with a TTL, a
  rate-limited refresh on an unknown `kid` (key rotation), and cached keys that
  keep serving when a refresh fails.
- `GroupRoleMapping`, accepting both `{group: role}` and
  `{workspace_id: {group: role}}`, and the precedence above.
- `Authenticator`, which resolves the principal per `auth_mode` and records
  refusals to the audit log (`AuditAction.AUTH_FAILED`).

Verification is implemented directly against `hashlib`: RSASSA-PKCS1-v1_5 is a
modular exponentiation and a fixed-byte comparison. That removes a hard runtime
dependency on a JWT library — which is also what let this land in an
environment where `cryptography` cannot be imported — at the cost of supporting
RSA only. `rbac.Principal` gained a `granted_roles` mapping, consulted only
when there is no explicit membership; it is empty for header identities, so
deny-by-default is unchanged for every existing deployment.

## Timeline
Phase 3, WS-021 M3. `trusted_proxy` remains supported indefinitely; it is the
right answer for an install that already terminates identity at a mesh or
gateway.

## Advantages
- The audit log's actor field means something for the first time.
- Access follows the directory: adding someone to a group grants access, and
  removing them from it revokes it, without a second place to maintain.
- A forged header buys nothing once OIDC is on, and misconfiguration fails
  closed and loudly rather than quietly downgrading to headers.
- Group mapping cannot escalate: an unmapped group is not a default role, and
  algorithm confusion is refused by construction rather than by review.
- No new runtime dependency, so the designer still installs anywhere Python is.

## Disadvantages
- **Clock skew is a real weakening of expiry.** Verification needs a skew
  window or it rejects valid tokens from slightly fast clocks; that window is
  time during which an expired token is still accepted. It is configurable and
  defaults to 60 seconds, which is a compromise, not a correct answer.
- **JWKS availability becomes a new failure mode.** The designer previously
  depended on nothing external to say who you were. Now, for a token signed
  with a key it has not cached, it depends on the provider being reachable.
  Cached keys survive an outage and a cold cache does not, so a restart during
  a provider outage locks everyone out.
- **Token lifetime is the revocation window.** Nothing here consults the
  provider per request, so a person who is disabled or removed from a group
  keeps their access until their token expires. Introspection or a revocation
  list would close that and would reintroduce a per-request dependency on the
  provider; we chose availability and said so rather than implying immediacy.
- **Refresh on an unknown `kid` is rate-limited**, which means a genuine
  emergency rotation is picked up within that interval and not instantly.
- **Trusted-proxy mode still costs what it always cost.** Nothing in the
  product verifies the proxy is there. Anyone who can reach the port directly
  is whoever they say they are, and the audit log will faithfully record the
  name they chose. Renaming the mode makes that a decision someone took rather
  than a default they inherited; it does not make it safe.
- Only RSA is verified, so an issuer publishing EC keys cannot be used yet.
- Group mapping is workspace-coarse, and inherits WS-021 M4's gap.

## Alternatives considered
- **Keep header identity and document the proxy requirement** — free, and what
  we had. Rejected: the requirement was documented and still invisible at
  runtime, and ADR-0043 depends on the actor being real.
- **Depend on PyJWT or python-jose** — less code and well reviewed. Rejected as
  a *hard* dependency: it pulls `cryptography` into every install of a design
  tool, and it could not be imported in the environment this landed in. The
  verifier is small and the JWKS source is injectable, so an installation that
  wants a library can supply one; nothing here prevents that.
- **Validate tokens at the proxy and keep reading headers** — a legitimate
  architecture, and exactly what `trusted_proxy` mode still is. Rejected as the
  *only* option, because it makes every deployment's safety depend on a
  component the product cannot see.
- **Introspection (RFC 7662) on every request** — closes the revocation window.
  Rejected for now: it makes the provider a hard dependency of every single
  request rather than of key rotation.
- **Sync directory groups into workspace membership on a schedule** — keeps one
  RBAC path. Rejected: it writes derived state into the store that the audit
  log then records as membership changes nobody made.

## Verification
`tests/test_designer_oidc.py`, offline end to end: a locally generated RSA key
pair, a JWKS callable, and tokens signed in-process. It asserts that a valid
token authenticates and maps to the right role; that expired, wrong-issuer,
wrong-audience, tampered, wrong-key, unknown-`kid`, `alg: none` and HS256
tokens are all refused; that an unmapped group grants nothing over both the
service and HTTP; that an explicit membership outranks a group claim; that a
bare header is refused in `oidc` mode and accepted in `trusted_proxy` mode;
that key rotation is picked up without a restart and a retired key stops
verifying; that a provider outage does not invalidate cached keys but a cold
cache fails clearly; that a bogus `kid` cannot be amplified into provider
traffic; and that failures reach the audit log with a code and no token
material.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. OIDC verification, group mapping and explicit trusted-proxy mode (WS-021 M3). |
