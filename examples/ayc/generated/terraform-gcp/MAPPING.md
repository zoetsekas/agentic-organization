# IAM and resource mapping — Google Cloud

Generated from the IR for **ayc** (spec_version 1.4.0).
This report exists because the mapping is lossy in places, and a gap you cannot
see is a gap you cannot review (ADR-0012).

## Tenant boundary

This configuration was compiled **without a tenant**, so nothing enforces a
tenant boundary and nothing is namespaced. Apply it only into a
project that hosts this system alone.

## Sandbox execution

Provider in force: **target_native** (stable). Whatever the generated cloud infrastructure already isolates with. This seam neither configures nor inspects it.

- **Enforces:** nothing added by this seam; the generated infrastructure keeps owning the boundary
- **Does not enforce:** any boundary this platform can state on the target's behalf — read the target's own mapping report
- **Tenant isolation:** delegated_to_target — tenant isolation is the generated infrastructure's to enforce and to report (ADR-0050)
- **Verified here:** no. No sandbox provider was run in this environment; this restates the target's documentation (ADR-0054).

What the environment class cannot express here:

- the environment class is handed to the target unchanged; this seam cannot state what the target enforces (every environment class)
- no tenant id was assigned (ADR-0050) (every environment class)

## Scale

Every workload above carries an explicit bound. Where the design declared none, the platform default was emitted and marked as such — an absent bound is not an absent ceiling, it is a ceiling chosen by whoever wrote this provider's defaults (ADR-0095).

| Agent | min | max | per instance | concurrent ceiling | source |
|---|---|---|---|---|---|
| `ceo_agent` | 0 | 3 | 1 | 3 | platform default |
| `coo_agent` | 0 | 3 | 1 | 3 | platform default |
| `buyer_agent` | 0 | 3 | 1 | 3 | platform default |
| `warehouse_agent` | 0 | 3 | 1 | 3 | platform default |
| `inventory_agent` | 0 | 3 | 1 | 3 | platform default |
| `ap_agent` | 0 | 3 | 1 | 3 | platform default |
| `ar_agent` | 0 | 3 | 1 | 3 | platform default |
| `software_agent` | 0 | 3 | 1 | 3 | platform default |
| `cgo_agent` | 0 | 3 | 1 | 3 | platform default |
| `ecommerce_agent` | 0 | 3 | 1 | 3 | platform default |
| `cs_agent` | 0 | 3 | 1 | 3 | platform default |
| `marketing_agent` | 0 | 3 | 1 | 3 | platform default |

The concurrent ceiling is `max × per instance`. It is **not** `max_parallel_subagents`, which bounds one leader's fan-out inside a single process. Both are real and they bound different things.

### Agents that scale to zero

`ap_agent`, `ar_agent`, `buyer_agent`, `ceo_agent`, `cgo_agent`, `coo_agent`, `cs_agent`, `ecommerce_agent`, `inventory_agent`, `marketing_agent`, `software_agent`, `warehouse_agent`

An instance that goes away takes with it any asynchronous handles that agent was holding (ADR-0093) and any standing-in it was doing for a failed leader (ADR-0094). Those handles are not lost silently — they settle as failed with a reason on the next run — but at zero that stops being an exceptional path and becomes the normal one. Whoever chose zero to save money is usually not whoever reads the failed handles.

## Resource mapping

| Neutral resource | Google Cloud |
|---|---|
| `compute_service` | `google_cloud_run_v2_service` |
| `job_runner` | `google_cloud_run_v2_job` |
| `state_store` | `google_sql_database_instance` |
| `object_store` | `google_storage_bucket` |
| `secret` | `— not mapped —` |
| `message_bus` | `google_pubsub_topic` |
| `identity` | `— not mapped —` |
| `policy_binding` | `— not mapped —` |
| `network_boundary` | `google_compute_network` |
| `observability_sink` | `google_logging_project_sink` |
| `scheduler` | `google_cloud_scheduler_job` |
| `event_subscription` | `google_eventarc_trigger` |
| `channel_bridge` | `google_cloud_run_v2_service` |
| `knowledge_index` | `google_discovery_engine_data_store` |
| `memory_store` | `google_firestore_database` |
| `artifact_store` | `— not mapped —` |
| `agent_endpoint` | `google_service_networking_connection` |

Identities map to `google_service_account`, bindings to `google_project_iam_member`,
secrets to `google_secret_manager_secret`.

## Faithfully mapped permissions

| Agent | Permission | Granted role |
|---|---|---|
| `ceo_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `coo_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `buyer_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `buyer_agent` | `write:capability:purchase_ordering` | `roles/editor` |
| `buyer_agent` | `write:capability:goods_receiving` | `roles/editor` |
| `buyer_agent` | `query:capability:stock_check` | `roles/bigquery.dataViewer` |
| `warehouse_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `warehouse_agent` | `write:capability:order_fulfillment` | `roles/editor` |
| `warehouse_agent` | `query:capability:order_query` | `roles/bigquery.dataViewer` |
| `inventory_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `inventory_agent` | `write:capability:inventory_adjustment` | `roles/editor` |
| `inventory_agent` | `query:capability:stock_check` | `roles/bigquery.dataViewer` |
| `ap_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `ap_agent` | `write:capability:invoice_payment` | `roles/editor` |
| `ar_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `ar_agent` | `write:capability:receivable_management` | `roles/editor` |
| `software_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `software_agent` | `administer:capability:software_deploy` | `roles/owner` |
| `cgo_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `ecommerce_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `ecommerce_agent` | `write:capability:product_publishing` | `roles/editor` |
| `ecommerce_agent` | `query:capability:order_query` | `roles/bigquery.dataViewer` |
| `ecommerce_agent` | `query:capability:stock_check` | `roles/bigquery.dataViewer` |
| `cs_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `cs_agent` | `write:capability:refund_processing` | `roles/editor` |
| `cs_agent` | `query:capability:order_query` | `roles/bigquery.dataViewer` |
| `marketing_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `marketing_agent` | `write:capability:promotion_run` | `roles/editor` |
| `marketing_agent` | `publish:capability:content_publishing` | `roles/pubsub.publisher` |

## ⚠ Coarsened permissions

These grants are **broader** than the spec asked for. Review them before apply.

| Agent | Permission | Granted role | Why |
|---|---|---|---|
| — | — | — | None. |

## Unmapped neutral resources

`artifact_store`

## Network isolation

When this provider profile has a network mapping, `network.tf` translates the
placement model (ADR-0069) into real isolation (ADR-0084):

- one VPC for the system, and **one subnet per placement** (unit × sandbox);
- a **default-deny** firewall between placements, then one **allow** rule per
  placement rule the IR resolved — scoped to the agents' own service accounts,
  so the network boundary and the workload identity are one fact;
- an **egress-deny** for every `none`-posture sandbox, so PII, MNPI or a live
  sample cannot call home.

What a firewall rule cannot do is a **hostname** egress allowlist: it works on
IP ranges and service accounts, not FQDNs. An `allowlist`-posture sandbox
therefore needs an egress proxy or Cloud NAT with an FQDN policy, wired in
`overlays/`; `network.tf` names which sandboxes that applies to.

## What this target does not do

It does not create your **organization-level** landing zone (the folder
hierarchy, org policies, or a shared-VPC host project) — those are inputs it
attaches into. It does not resolve the hostname allowlists above into proxy
config. And it does not apply anything; run `terraform plan` yourself.
