# IAM and resource mapping — Google Cloud

Generated from the IR for **acme** (spec_version 1.3.0).
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
| `ceo` | `read:data_class:public_knowledge` | `roles/viewer` |
| `ceo` | `delegate:agent:*` | `roles/run.invoker` |
| `ceo` | `delegate:team:*` | `roles/run.invoker` |
| `cfo` | `read:data_class:public_knowledge` | `roles/viewer` |
| `cfo` | `read:data_class:finance_internal` | `roles/viewer` |
| `cfo` | `query:capability:warehouse_query` | `roles/bigquery.dataViewer` |
| `cfo` | `delegate:agent:*` | `roles/run.invoker` |
| `cfo` | `write:data_class:public_knowledge` | `roles/editor` |
| `analyst` | `read:data_class:public_knowledge` | `roles/viewer` |
| `analyst` | `read:data_class:finance_internal` | `roles/viewer` |
| `analyst` | `query:capability:warehouse_query` | `roles/bigquery.dataViewer` |
| `analyst` | `write:data_class:public_knowledge` | `roles/editor` |
| `analyst` | `invoke:workflow:governed_data_request` | `roles/run.invoker` |
| `reconciler` | `read:data_class:public_knowledge` | `roles/viewer` |
| `reconciler` | `read:data_class:finance_internal` | `roles/viewer` |
| `reconciler` | `query:capability:warehouse_query` | `roles/bigquery.dataViewer` |
| `reconciler` | `query:capability:pii_reconciliation` | `roles/bigquery.dataViewer` |
| `cto` | `read:data_class:public_knowledge` | `roles/viewer` |
| `cto` | `read:data_class:engineering_internal` | `roles/viewer` |
| `cto` | `read:capability:incident_signal` | `roles/viewer` |
| `cto` | `delegate:agent:*` | `roles/run.invoker` |
| `cto` | `write:data_class:public_knowledge` | `roles/editor` |
| `platform_lead` | `read:data_class:public_knowledge` | `roles/viewer` |
| `platform_lead` | `read:data_class:engineering_internal` | `roles/viewer` |
| `platform_lead` | `read:capability:incident_signal` | `roles/viewer` |
| `platform_lead` | `delegate:agent:*` | `roles/run.invoker` |
| `platform_lead` | `write:data_class:public_knowledge` | `roles/editor` |
| `platform_engineer` | `read:data_class:public_knowledge` | `roles/viewer` |
| `platform_engineer` | `read:data_class:engineering_internal` | `roles/viewer` |
| `platform_engineer` | `read:capability:incident_signal` | `roles/viewer` |
| `platform_engineer` | `write:capability:code_change` | `roles/editor` |
| `sre` | `read:data_class:public_knowledge` | `roles/viewer` |
| `sre` | `read:data_class:engineering_internal` | `roles/viewer` |
| `sre` | `read:capability:incident_signal` | `roles/viewer` |
| `sre` | `publish:channel:incidents` | `roles/pubsub.publisher` |
| `cro` | `read:data_class:public_knowledge` | `roles/viewer` |
| `cro` | `delegate:agent:*` | `roles/run.invoker` |
| `cro` | `write:data_class:public_knowledge` | `roles/editor` |

## ⚠ Coarsened permissions

These grants are **broader** than the spec asked for. Review them before apply.

| Agent | Permission | Granted role | Why |
|---|---|---|---|
| `cfo` | `approve:workflow:*` | `roles/viewer` | Google Cloud IAM has no equivalent granularity; the binding is broader than the spec intends. |
| `cto` | `approve:workflow:*` | `roles/viewer` | Google Cloud IAM has no equivalent granularity; the binding is broader than the spec intends. |
| `platform_lead` | `approve:workflow:*` | `roles/viewer` | Google Cloud IAM has no equivalent granularity; the binding is broader than the spec intends. |
| `cro` | `approve:workflow:*` | `roles/viewer` | Google Cloud IAM has no equivalent granularity; the binding is broader than the spec intends. |

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
