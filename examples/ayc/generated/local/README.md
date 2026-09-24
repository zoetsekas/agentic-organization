# ayc — local deployment

Generated from the system spec (spec_version 1.4.0).
**Do not edit generated files**; put customizations in `overlays/`.

## Run it

```bash
cp .env.example .env     # fill in secret values from your secret manager
make up                  # start the stack
make seed                # load the compiled organization
open http://localhost:8000/ui/
```

No container runtime? `make single` runs everything in one process over SQLite.

## What was generated

| Agent | Team | Environment | Network | Permissions |
|---|---|---|---|---|
| `ceo_agent` | AYC | — | — | 1 |
| `coo_agent` | AYC / Operations | — | — | 1 |
| `buyer_agent` | AYC / Operations / Purchasing | warehouse_ops | allowlist | 4 |
| `warehouse_agent` | AYC / Operations / Warehouse and Shipping | warehouse_ops | allowlist | 3 |
| `inventory_agent` | AYC / Operations / Inventory Audit | warehouse_ops | allowlist | 3 |
| `ap_agent` | AYC / Operations / Finance | finance_ops | allowlist | 2 |
| `ar_agent` | AYC / Operations / Finance | finance_ops | allowlist | 2 |
| `software_agent` | AYC / Operations / IT | build | allowlist | 2 |
| `cgo_agent` | AYC / Growth | — | — | 1 |
| `ecommerce_agent` | AYC / Growth / E-Commerce and Web | storefront_ops, warehouse_ops | allowlist, allowlist | 4 |
| `cs_agent` | AYC / Growth / Customer Service | storefront_ops | allowlist | 3 |
| `marketing_agent` | AYC / Growth / Marketing | studio | allowlist | 3 |

## Scheduled and event-driven work

| Trigger | When | Runs | Delivers to |
|---|---|---|---|
| `nightly_stock_reconcile` | cron `0 2 * * *` (America/Los_Angeles) → listing_readiness | `ecommerce_agent` | ops_desk |

The scheduler holds no credentials of its own: it wakes the owning agent, which
runs under its own identity and its own permission set, exactly as it would for
interactive work.

## Human channels

| Channel | Provider | Purposes | SLA | Out of hours |
|---|---|---|---|---|
| `ops_desk` | internal | notify | — | queue |
| `growth_desk` | internal | ask, notify | — | queue |

## Placements

| Placement | Unit | Environment | Co-resident agents | Reaches |
|---|---|---|---|---|
| `customer_service--storefront_ops` | customer_service | `storefront_ops` | `cs_agent` | `finance--finance_ops` |
| `ecommerce--storefront_ops` | ecommerce | `storefront_ops` | `ecommerce_agent` | `inventory_audit--warehouse_ops` |
| `ecommerce--warehouse_ops` | ecommerce | `warehouse_ops` | `ecommerce_agent` | `inventory_audit--warehouse_ops` |
| `finance--finance_ops` | finance | `finance_ops` | `ap_agent`, `ar_agent` | — nothing |
| `inventory_audit--warehouse_ops` | inventory_audit | `warehouse_ops` | `inventory_agent` | — nothing |
| `it--build` | it | `build` | `software_agent` | — nothing |
| `marketing--studio` | marketing | `studio` | `marketing_agent` | `ecommerce--storefront_ops`, `ecommerce--warehouse_ops` |
| `purchasing--warehouse_ops` | purchasing | `warehouse_ops` | `buyer_agent` | `finance--finance_ops` |
| `warehouse--warehouse_ops` | warehouse | `warehouse_ops` | `warehouse_agent` | `inventory_audit--warehouse_ops` |

Agents inside one placement share a volume at `/srv/shared` and a process namespace, and reach each other without a rule. That is the widening; the table above is who it covers. The volume carries only the data classes the unit's groups already share and is **never a new grant** — an agent without a grant on a class does not acquire it by sharing a disk with somebody who has one.

## Tenant isolation

This system was compiled without a tenant, so nothing here is
namespaced: it is safe on a host that runs one system and nothing else.

## Sandbox execution

Provider in force: **container** (stable). Ordinary containers on a shared host kernel. A Docker-object boundary, not a kernel or account one.

- **Enforces:** separate filesystems, networks and named volumes per sandbox; declared mounts and resource limits, as container configuration
- **Does not enforce:** a kernel boundary — a kernel escape reaches the host and every other tenant on it; protection from anyone who can reach the Docker socket, which reaches every tenant on this host; egress at HTTP method or path level; host-level rules only
- **Kernel boundary:** no
- **Tenant isolation:** name_scoped_only — tenants are separated by generated resource names and networks only; a host-level actor sees all of them
- **Verified here:** no. No sandbox provider was run when this stack was generated (ADR-0054).

What the environment class cannot express here:

- egress is applied per host, not per HTTP method and path (every environment class)
- isolation is a Docker-object boundary, so the mapping cannot claim a kernel boundary however strict the class is (every environment class)
- no tenant id was assigned, so generated names carry no tenant prefix (ADR-0050) (every environment class)


## Workflow engines

| Engine | Image | Credential | May be sent |
|---|---|---|---|
| `langflow` | `langflowai/langflow:1.12.2` | `LANGFLOW_SECRET_KEY` | financial_records |

**A flow that runs in one of these services runs
outside the agent's sandbox.** Its CPU and memory limits, its network posture and its filesystem
are the engine's, not the ones the agent's environment class declares. What
this platform governs is the **call**: the agent's egress allowlist decides
whether the engine is reachable at all (an agent whose environment is
`network: none` is on the isolated network and cannot reach it), what may be
sent is checked against the caller's data classification before anything
leaves, the engine authenticates with its own `secret_ref` and never with the
agent's, and the reply comes back as untrusted input through the tool-output
guardrail (ADR-0035, ADR-0056).

Flows are authored in the engine, not in the spec, and are mounted read-only
from `./flows`. That means a flow is a dependency the system spec cannot see
or version: it can change under a system that was already reviewed.

## Caveats

A placement is **not** a security boundary. The tenant is. Borrowing the
namespace model means borrowing its caveat: namespaces on an application
platform share a kernel, and so do these.

Generated network policy is a snapshot. A reorganisation changes who may
delegate to whom immediately and these rules only at the next apply; for that
window the two disagree and nothing at run time says so.

Compose approximates network posture with attached networks and cannot
represent cloud IAM at all. Isolated (`none`) environments are placed on an
internal network with no gateway, which is close — but a local run does **not**
verify the IAM bindings the cloud targets generate. Use a cloud target to test
those.
