# IAM and resource mapping — Google Cloud

Generated from the IR for **atlas** (spec_version 1.4.0).
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
| `group_ceo_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `consumer_head_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `loan_officer_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `loan_officer_agent` | `write:capability:loan_origination` | `roles/editor` |
| `loan_officer_agent` | `query:capability:customer_lookup` | `roles/bigquery.dataViewer` |
| `loan_officer_agent` | `query:capability:identity_verification` | `roles/bigquery.dataViewer` |
| `cib_head_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `markets_head_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `trader_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `trader_agent` | `write:capability:trade_execution` | `roles/editor` |
| `trader_agent` | `query:capability:position_query` | `roles/bigquery.dataViewer` |
| `research_analyst_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `research_analyst_agent` | `publish:capability:research_publishing` | `roles/pubsub.publisher` |
| `banker_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `banker_agent` | `write:capability:deal_advisory` | `roles/editor` |
| `banker_agent` | `write:capability:wall_crossing` | `roles/editor` |
| `cro_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `cro_agent` | `write:capability:limit_setting` | `roles/editor` |
| `cro_agent` | `query:capability:risk_analytics_run` | `roles/bigquery.dataViewer` |
| `aml_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `aml_agent` | `write:capability:aml_triage` | `roles/editor` |
| `aml_agent` | `query:capability:transaction_trace` | `roles/bigquery.dataViewer` |
| `aml_agent` | `read:data_class:aml_case` | `roles/viewer` |
| `audit_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `audit_agent` | `query:capability:audit_testing` | `roles/bigquery.dataViewer` |
| `platform_engineer_agent` | `read:data_class:public_knowledge` | `roles/viewer` |
| `platform_engineer_agent` | `administer:capability:production_deploy` | `roles/owner` |

## ⚠ Coarsened permissions

These grants are **broader** than the spec asked for. Review them before apply.

| Agent | Permission | Granted role | Why |
|---|---|---|---|
| `cro_agent` | `approve:capability:breach_override` | `roles/viewer` | Google Cloud IAM has no equivalent granularity; the binding is broader than the spec intends. |
| `cro_agent` | `approve:capability:credit_approval` | `roles/viewer` | Google Cloud IAM has no equivalent granularity; the binding is broader than the spec intends. |

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
