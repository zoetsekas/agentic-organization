# The command centre API

The operator-facing surface of the fabric plane (ADR-0049, ADR-0051, WS-029).
One backend, two applications: the designer at `/ui/` over `/api/designer/*`,
the command centre at `/command/` over `/api/fabric/*`. Nothing in this
namespace can change what an organization *is* — editing a spec stays with the
tenant's own designers.

A front end can be built against this document alone; `docs/fixtures/command-centre.sample.json`
holds one response per route, shaped exactly as the API returns them.

---

## 1. Identity and roles

Authentication is the designer's (ADR-0047): OIDC bearer token in `oidc` mode,
`X-User` / `X-User-Name` / `X-User-Email` from a trusted proxy otherwise. The
same headers work on both applications; what differs is what they authorize.

**Authorization is separate.** A designer role grants nothing here, and an
operator role grants nothing in a design. An operator grant is a row in the
fabric's own registry, written by somebody holding `fabric.operator.grant` (or
seeded at boot from `ORGAGENTS_FABRIC_OPERATORS=alice=fabric_admin,bob=fabric_operator`).
Holding both role sets is two grants, never one inferred from the other.

| Role | Value | Grants |
|---|---|---|
| Automation | `fabric_automation` | all reads, `fabric.deployment.quarantine` |
| Operator | `fabric_operator` | all reads, deploy / stop / quarantine / redeploy, re-quota |
| Admin | `fabric_admin` | all of the above, plus `fabric.audit.read` and `fabric.operator.grant` |

Admin is not "operator plus" in the lifecycle: the transition table
(`src/orgagents/fabric/deployments.py`) decides each move on its own, and some
moves — leaving quarantine — are admin-only by design.

Permission names, all `fabric.`-prefixed and disjoint from the designer's:
`fabric.tenant.read`, `fabric.deployment.read`, `fabric.health.read`,
`fabric.quota.read`, `fabric.service.read`, `fabric.audit.read`,
`fabric.deployment.deploy`, `fabric.deployment.stop`,
`fabric.deployment.quarantine`, `fabric.deployment.redeploy`,
`fabric.quota.write`, `fabric.operator.grant`.

### Status codes

| Code | Meaning |
|---|---|
| 200 | Done. |
| 401 | Not authenticated (bad or missing token / header). |
| 403 | Authenticated, but the grant does not cover it — *or* the lifecycle table refuses this role this transition. |
| 404 | No such tenant, deployment or action name. |
| 409 | A legal-looking request that is off the lifecycle machine (an illegal transition). Refused, never coerced. |
| 400 | Malformed body (an unknown quota kind, a bad limit). |

The body of an error is FastAPI's `{"detail": "<message>"}`. The message names
the role and the states involved, and is safe to show to an operator.

---

## 2. Route table

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/fabric/whoami` | none (authentication only) | The caller's operator roles and permissions. |
| GET | `/api/fabric/tenants` | `fabric.tenant.read` | Every tenant, with deployment counts and states. |
| GET | `/api/fabric/tenants/{tenant_id}` | `fabric.tenant.read` | One tenant: deployments, health, drift, quotas. |
| GET | `/api/fabric/deployments?tenant_id=` | `fabric.deployment.read` | Deployments, all tenants or one. |
| GET | `/api/fabric/deployments/{deployment_id}` | `fabric.deployment.read` | One deployment with its last health check. |
| GET | `/api/fabric/deployments/{deployment_id}/history` | `fabric.deployment.read` | Its transition history. |
| GET | `/api/fabric/health?tenant_id=` | `fabric.health.read` | Health checks (belief vs observation). |
| GET | `/api/fabric/drift?tenant_id=` | `fabric.health.read` | Drift signals only. |
| GET | `/api/fabric/quotas?tenant_id=` | `fabric.quota.read` | Quota state, usage, breaches, entitlements. |
| GET | `/api/fabric/services` | `fabric.service.read` | The fabric's common services. |
| GET | `/api/fabric/services/boundary` | `fabric.service.read` | The boundary report: what is shared, and why. |
| GET | `/api/fabric/audit` | `fabric.audit.read` | The operator audit log. |
| POST | `/api/fabric/deployments/{deployment_id}/actions/{action}` | per action, below | Operator actions. |
| PUT | `/api/fabric/tenants/{tenant_id}/quotas` | `fabric.quota.write` | Re-quota. |
| GET | `/api/fabric/operators` | `fabric.operator.grant` | Who operates the fabric. |
| POST | `/api/fabric/operators` | `fabric.operator.grant` | Grant operator roles. |
| DELETE | `/api/fabric/operators/{user_id}` | `fabric.operator.grant` | Revoke them. |

These are the only writes in the namespace, and none of them touches a spec.

---

## 3. Reads

### GET `/api/fabric/whoami`

```json
{
  "user_id": "olive",
  "display_name": "Olive Operator",
  "operator_roles": ["fabric_operator"],
  "permissions": ["fabric.deployment.deploy", "…"],
  "is_operator": true
}
```

A designer with no fabric grant gets `operator_roles: []`, `permissions: []`,
`is_operator: false` — and 403 on everything else. The front end should treat
`is_operator: false` as "this person is not an operator", not as an error.

### GET `/api/fabric/tenants`

Array, sorted by tenant id:

```json
[{
  "id": "acme",
  "name": "Acme",
  "namespace_prefix": "acme",
  "isolation_domain": {
    "id": "acme-domain", "network": "acme-net",
    "identity_realm": "acme-identities", "secret_scope": "acme-secrets",
    "data_scope": "acme-data", "cloud_boundary": ""
  },
  "entitlements": ["catalog.reviewer"],
  "status": "active",
  "created_at": "2026-09-20T09:00:00+00:00",
  "deployment_count": 2,
  "states": ["running", "stopped"]
}]
```

`status` is one of `pending`, `active`, `suspended`, `retired`.

### GET `/api/fabric/tenants/{tenant_id}`

```json
{
  "tenant": { "…as above, without deployment_count/states…" },
  "deployments": [ "…deployment objects (§3.3)…" ],
  "health": [ "…health checks (§3.5)…" ],
  "drift": [ "…drift signals (§3.6)…" ],
  "quotas": { "…quota state (§3.7)…" }
}
```

404 if the tenant does not exist.

### Deployment object (§3.3)

Returned by `/api/fabric/deployments`, `/api/fabric/deployments/{id}`, every
action, and inside tenant detail.

```json
{
  "id": "dep_7c1f…",
  "tenant_id": "acme",
  "name": "acme-core",
  "system_id": "sys_a",
  "revision": "r1",
  "target": "local",
  "state": "deployed",
  "history": [{
    "at": "2026-09-20T09:05:00+00:00",
    "source": "generated", "target": "deployed",
    "actor": "olive", "role": "fabric_operator", "reason": "go live"
  }],
  "created_at": "2026-09-20T09:00:00+00:00",
  "updated_at": "2026-09-20T09:05:00+00:00",
  "allowed_transitions": {
    "running": ["fabric_automation", "fabric_operator"],
    "stopped": ["fabric_admin", "fabric_operator"],
    "quarantined": ["fabric_admin", "fabric_automation", "fabric_operator"]
  }
}
```

`state` is one of `requested`, `generated`, `deployed`, `running`, `stopped`,
`quarantined`, `retired`. `allowed_transitions` maps each *legal next state* to
the roles the table accepts for it — the front end should drive its buttons
from this rather than from a hard-coded table, so the UI cannot disagree with
the backend.

`GET /api/fabric/deployments/{id}` adds `"health"`: the last recorded check
(§3.5) or `null` if none has been taken.

`GET /api/fabric/deployments/{id}/history` returns just the `history` array.

### Health check (§3.5)

```json
{
  "id": "hc_dep_7c1f…",
  "deployment_id": "dep_7c1f…",
  "tenant_id": "acme",
  "believed_state": "deployed",
  "believed_revision": "r1",
  "observed_state": "running",
  "observed_revision": "r1",
  "status": "unknown",
  "confidence": "fresh",
  "observed_at": "2026-09-20T09:06:00+00:00",
  "observation_age_seconds": 12.0,
  "staleness_horizon_seconds": 300.0,
  "signals": [ "…drift signals…" ],
  "checked_at": "2026-09-20T09:06:12+00:00"
}
```

`status`: `healthy`, `degraded`, `unhealthy`, `unknown`. `confidence`: `fresh`,
`stale`, `unobserved`. **A stale or unobserved check reports `unknown`, never
`healthy`** (ADR-0052). The front end must render `unknown` distinctly from
`unhealthy`: they mean "we do not know" and "we know it is bad".

`GET /api/fabric/health` takes a fresh observation and returns an array of
these. Without `tenant_id` it covers every tenant, which is a cross-tenant
read and is audited as one.

### Drift signal (§3.6)

```json
{
  "deployment_id": "dep_7c1f…",
  "tenant_id": "acme",
  "kind": "state_mismatch",
  "severity": "critical",
  "believed": "deployed",
  "observed": "running",
  "detail": "the target is not in the state the fabric records",
  "raised_at": "2026-09-20T09:06:12+00:00"
}
```

`kind`: `state_mismatch`, `revision_mismatch`, `missing_on_target`,
`unknown_to_fabric`, `stale_belief`. `severity`: `info`, `warning`, `critical`.

### Quota state (§3.7)

```json
{
  "id": "acme",
  "tenant_id": "acme",
  "quotas": {
    "agents": {"kind": "agents", "soft_limit": 5, "hard_ceiling": 10, "unit": ""}
  },
  "usage": {"agents": 6},
  "breaches": [{"at": "…", "kind": "agents", "decision": "allow_degraded",
                "reason": "over the soft limit of 5; …"}],
  "catalog_entries": ["cat_reviewer"],
  "updated_at": "2026-09-20T09:07:00+00:00",
  "tenant_entitlements": ["catalog.reviewer"],
  "recorded": true
}
```

`recorded: false` means no entitlement record exists for that tenant yet; the
other fields are then empty rather than absent, so the front end can render
without branching. `hard_ceiling: -1` means unbounded: breaches are recorded,
nothing is ever refused. `catalog_entries` is an allow-list — an entry not
listed is not entitled.

Quota kinds: `deployments`, `agents`, `concurrent_sessions`,
`monthly_spend_usd`, `tokens_per_day`.

### GET `/api/fabric/services` and `/api/fabric/services/boundary`

Services:

```json
[{
  "id": "svc_catalog", "name": "Catalog of building blocks", "kind": "catalog",
  "summary": "…", "shared": true,
  "failure_impact": "No tenant can resolve a building block; …",
  "exposes": [{"what": "catalog entry metadata and version",
               "direction": "fabric_to_tenant", "why": "…",
               "visible_to_other_tenants": true}],
  "registered_at": "…"
}]
```

`kind`: `catalog`, `observability`, `record_layer`, `identity`.
`direction`: `fabric_to_tenant`, `tenant_to_fabric`, `tenant_to_tenant`.

The boundary report flattens every crossing into rows of **strings** (note
`visible_to_other_tenants` is `"true"` / `"false"`, not a boolean):

```json
[{"service": "Catalog of building blocks", "kind": "catalog",
  "what": "catalog entry metadata and version", "direction": "fabric_to_tenant",
  "why": "…", "visible_to_other_tenants": "true"}]
```

### GET `/api/fabric/audit`

Admin only. Query parameters: `tenant_id`, `actor`, `cross_tenant_only`
(boolean, default false), `limit` (default 200). Newest first.

```json
[{
  "id": "fab_9a2…", "timestamp": "2026-09-20T09:08:00+00:00",
  "actor": "olive", "actor_name": "Olive Operator",
  "action": "fabric.tenant.read", "outcome": "success",
  "tenant_id": "", "deployment_id": "", "cross_tenant": true,
  "route": "GET /api/fabric/tenants", "permission": "fabric.tenant.read",
  "operator_roles": ["fabric_operator"], "reason": "",
  "detail": {"tenant_count": 2, "tenant_ids": ["acme", "globex"],
             "truncated": false}
}]
```

`action` is one of the permission-shaped values plus
`fabric.operator.revoke`. `outcome`: `success`, `denied`, `conflict`,
`failed`. The log is append-only: there is no route that edits or deletes a
row.

---

## 4. Operator actions

`POST /api/fabric/deployments/{deployment_id}/actions/{action}`

Body: `{"reason": "why you did this"}` — optional but recorded, and worth
making the UI insist on.

| `action` | Permission | Lifecycle move |
|---|---|---|
| `generate` | `fabric.deployment.deploy` | `requested` → `generated` |
| `deploy` | `fabric.deployment.deploy` | `generated` → `deployed` |
| `stop` | `fabric.deployment.stop` | → `stopped` |
| `quarantine` | `fabric.deployment.quarantine` | → `quarantined` |
| `redeploy` | `fabric.deployment.redeploy` | → `running` (from `stopped` or `deployed`) |

The response is the updated deployment object (§3.3).

Two refusals, and they mean different things:

* **409** — the move is not on the machine at all (`deploy` on a `requested`
  deployment, `redeploy` straight out of `quarantined`). Nothing changed.
* **403** — the move is legal but not for this role. Leaving quarantine is
  `fabric_admin` only, and `quarantined` → `stopped` is the only way out:
  stop and investigate, never straight back to running.

Where the caller holds several roles, the backend acts as one the table
accepts for that specific move; the role it used appears in the audit row's
`detail.acted_as` and in the deployment's history.

### PUT `/api/fabric/tenants/{tenant_id}/quotas`

```json
{
  "quotas": {"agents": {"soft_limit": 5, "hard_ceiling": 10, "unit": ""}},
  "catalog_entries": ["cat_reviewer"],
  "reason": "growth plan approved"
}
```

Quota kinds not mentioned are left alone. `catalog_entries` omitted leaves the
allow-list untouched; `[]` empties it (which entitles the tenant to nothing —
the deny-by-default reading). Returns the quota state (§3.7). 400 on an
unknown quota kind, 404 on an unknown tenant.

### Operator grants

`POST /api/fabric/operators` with
`{"user_id": "nina", "roles": ["fabric_operator"], "reason": "joining the rota"}`
returns

```json
{"id": "nina", "user_id": "nina", "roles": ["fabric_operator"],
 "granted_by": "adam", "reason": "joining the rota", "granted_at": "…"}
```

A grant replaces that person's previous one. `DELETE
/api/fabric/operators/{user_id}` returns `{"revoked": true|false}`. Both are
admin-only and audited.

---

## 5. What is audited, and how much

Every request in this namespace produces exactly **one** audit row —
successes, refusals (`denied`) and illegal transitions (`conflict`) alike.

A read that covers more than one tenant is marked `cross_tenant: true` and
carries the scope it saw in `detail` (`tenant_count`, up to 50 `tenant_ids`,
`truncated`). A list over forty tenants is one row, not forty: the question a
reviewer asks is *who looked across the boundary*, not which record they read,
and one row per record would bury the answer. `GET /api/fabric/audit?cross_tenant_only=true`
is the review query.

A read that is refused is audited too, with `operator_roles: []` for a
designer who wandered in — a burst of those is the first sign of both a
misconfiguration and an attack.

---

## 6. Developing without a backend

`docs/fixtures/command-centre.sample.json` maps each route to a
representative response, in the exact shapes above:

```js
// copy or serve the file wherever the front end keeps its dev data
const fixtures = await (await fetch("command-centre.sample.json")).json();
fixtures.routes["GET /api/fabric/tenants"].response   // array of tenants
fixtures.routes["GET /api/fabric/audit"].response     // audit rows
fixtures.errors["409 illegal transition"]             // {"detail": "…"}
```

It was generated by calling the real API, not written by hand, so a shape
there is a shape the backend returns.

Every fixture is two tenants (`acme`, `globex`) at different points of the
lifecycle, including one stale health check, one drifted deployment, one
over-soft-limit quota and one denied audit row — the states worth designing
for rather than the happy path only.

---

## 7. Boundaries this API keeps

* **Reads cross tenants; nothing here authors inside one.** There is no route
  in `/api/fabric/*` that reaches a System Spec, a workspace, a revision or a
  lock, and a test asserts the complete set of write routes.
* **Two grants, never one.** No designer role is read on this path, and
  `whoami` will not report one.
* **The lifecycle table is the single authority** on what may move where and
  by whom; this namespace maps an action name to a target state and lets the
  table answer.
* **Nothing here has ever spoken to a target.** The health backend is a stub
  in this environment (WS-030 M5), so a health check is a contract, not a
  proven observation.
