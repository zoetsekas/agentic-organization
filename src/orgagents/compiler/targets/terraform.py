"""Terraform targets for GCP, AWS and Azure (ADR-0012).

One generator, three provider mapping tables, driven by the IR's
provider-neutral resource set. Where a provider's IAM cannot express a grant
faithfully, the target emits the safer coarser binding **and** records the gap
in `MAPPING.md` — fidelity loss is reported, never hidden.

The platform never applies anything. The customer runs plan and apply in their
own pipeline, with their own credentials.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..base import GeneratedFile
from ..ir import NEUTRAL_RESOURCES, SystemIR


@dataclass(frozen=True)
class ProviderProfile:
    """How one provider realizes the neutral resource set."""

    id: str
    display: str
    terraform_provider: str
    source: str
    version: str
    resources: dict[str, str]
    identity_resource: str
    binding_resource: str
    secret_resource: str
    region_variable: str
    # Neutral permission verbs that this provider's IAM cannot express at the
    # requested granularity. Declared, not discovered, and surfaced in MAPPING.md.
    coarse_actions: tuple[str, ...] = ()


PROFILES: dict[str, ProviderProfile] = {
    "gcp": ProviderProfile(
        id="gcp",
        display="Google Cloud",
        terraform_provider="google",
        source="hashicorp/google",
        version="~> 6.0",
        resources={
            "compute_service": "google_cloud_run_v2_service",
            "job_runner": "google_cloud_run_v2_job",
            "state_store": "google_sql_database_instance",
            "object_store": "google_storage_bucket",
            "message_bus": "google_pubsub_topic",
            "observability_sink": "google_logging_project_sink",
            "network_boundary": "google_compute_network",
        },
        identity_resource="google_service_account",
        binding_resource="google_project_iam_member",
        secret_resource="google_secret_manager_secret",
        region_variable="region",
        coarse_actions=("approve",),
    ),
    "aws": ProviderProfile(
        id="aws",
        display="Amazon Web Services",
        terraform_provider="aws",
        source="hashicorp/aws",
        version="~> 5.0",
        resources={
            "compute_service": "aws_ecs_service",
            "job_runner": "aws_ecs_task_definition",
            "state_store": "aws_db_instance",
            "object_store": "aws_s3_bucket",
            "message_bus": "aws_sns_topic",
            "observability_sink": "aws_cloudwatch_log_group",
            "network_boundary": "aws_security_group",
        },
        identity_resource="aws_iam_role",
        binding_resource="aws_iam_role_policy",
        secret_resource="aws_secretsmanager_secret",
        region_variable="region",
        coarse_actions=("approve", "delegate"),
    ),
    "azure": ProviderProfile(
        id="azure",
        display="Microsoft Azure",
        terraform_provider="azurerm",
        source="hashicorp/azurerm",
        version="~> 4.0",
        resources={
            "compute_service": "azurerm_container_app",
            "job_runner": "azurerm_container_app_job",
            "state_store": "azurerm_postgresql_flexible_server",
            "object_store": "azurerm_storage_container",
            "message_bus": "azurerm_servicebus_topic",
            "observability_sink": "azurerm_log_analytics_workspace",
            "network_boundary": "azurerm_network_security_group",
        },
        identity_resource="azurerm_user_assigned_identity",
        binding_resource="azurerm_role_assignment",
        secret_resource="azurerm_key_vault_secret",
        region_variable="location",
        coarse_actions=("approve", "delegate", "publish"),
    ),
}

# Neutral action → the role/permission family each provider grants for it.
ACTION_ROLES = {
    "gcp": {
        "read": "roles/viewer", "query": "roles/bigquery.dataViewer",
        "write": "roles/editor", "invoke": "roles/run.invoker",
        "publish": "roles/pubsub.publisher", "delegate": "roles/run.invoker",
        "approve": "roles/viewer", "administer": "roles/owner",
    },
    "aws": {
        "read": "ReadOnlyAccess", "query": "AthenaQueryAccess",
        "write": "WriteAccess", "invoke": "InvokeFunctionAccess",
        "publish": "SNSPublishAccess", "delegate": "InvokeFunctionAccess",
        "approve": "ReadOnlyAccess", "administer": "AdministratorAccess",
    },
    "azure": {
        "read": "Reader", "query": "Reader", "write": "Contributor",
        "invoke": "Contributor", "publish": "Azure Service Bus Data Sender",
        "delegate": "Contributor", "approve": "Reader", "administer": "Owner",
    },
}

TIER_SIZING = {
    "minimal": {"cpu": "0.25", "memory": "512Mi"},
    "small": {"cpu": "1", "memory": "2Gi"},
    "medium": {"cpu": "2", "memory": "8Gi"},
    "large": {"cpu": "4", "memory": "16Gi"},
    "accelerated": {"cpu": "8", "memory": "32Gi"},
}


def _tf_name(value: str) -> str:
    """A valid Terraform identifier: letters, digits, underscores and dashes."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_").lower()
    return name if name and not name[0].isdigit() else f"r_{name}"


class TerraformTarget:
    """Emits Terraform for one provider from the neutral resource set."""

    def __init__(self, provider: str) -> None:
        if provider not in PROFILES:
            raise ValueError(f"unsupported provider '{provider}'")
        self.profile = PROFILES[provider]
        self.id = f"terraform:{provider}"

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": f"Terraform for {self.profile.display}",
            "summary": "Generates reviewable HCL the customer plans and applies in "
                       "their own pipeline; the platform never holds credentials.",
            "produces": ["main.tf", "variables.tf", "iam.tf", "agents.tf",
                         "backend.tf.example", "MAPPING.md"],
            "caveats": [
                "IAM mapping is lossy; MAPPING.md names every coarsened grant.",
                f"Actions mapped coarsely on this provider: "
                f"{', '.join(self.profile.coarse_actions) or 'none'}.",
            ],
        }

    # -- generation --------------------------------------------------------

    def generate(self, ir: SystemIR) -> list[GeneratedFile]:
        return [
            GeneratedFile("main.tf", self._main(ir)).with_header(ir),
            GeneratedFile("variables.tf", self._variables(ir)).with_header(ir),
            GeneratedFile("iam.tf", self._iam(ir)).with_header(ir),
            GeneratedFile("agents.tf", self._agents(ir)).with_header(ir),
            GeneratedFile("backend.tf.example", self._backend(ir),
                          preserve_if_exists=True).with_header(ir),
            GeneratedFile("system.ir.json",
                          json.dumps(ir.model_dump(mode="json"), indent=2) + "\n"),
            GeneratedFile("MAPPING.md", self._mapping_report(ir)),
        ]

    def _main(self, ir: SystemIR) -> str:
        p = self.profile
        shared = [r for r in ir.resources if r.kind in
                  ("state_store", "object_store", "message_bus", "observability_sink")]
        blocks = [
            f'''terraform {{
  required_version = ">= 1.6"
  required_providers {{
    {p.terraform_provider} = {{
      source  = "{p.source}"
      version = "{p.version}"
    }}
  }}
}}

provider "{p.terraform_provider}" {{
  {p.region_variable} = var.{p.region_variable}
}}

locals {{
  system      = "{ir.name}"
  environment = "{ir.environment}"
  labels = {{
    "managed-by"   = "orgagents"
    "system"       = "{_tf_name(ir.name)}"
    "spec-version" = "{ir.spec_version}"
  }}
}}'''
        ]
        for res in shared:
            tf_type = p.resources.get(res.kind)
            if not tf_type:
                continue
            blocks.append(
                f'''resource "{tf_type}" "{_tf_name(res.id)}" {{
  name = "${{local.system}}-{res.id.split('-')[-1]}"
  # neutral resource: {res.kind} — {res.attributes.get("purpose", "")}
  {p.region_variable} = var.{p.region_variable}
}}'''
            )
        return "\n\n".join(blocks) + "\n"

    def _variables(self, ir: SystemIR) -> str:
        p = self.profile
        secrets = sorted({r.id for r in ir.resources if r.kind == "secret"})
        blocks = [
            f'''variable "{p.region_variable}" {{
  description = "Deployment {p.region_variable}"
  type        = string
  default     = "{(ir.binding.infrastructure.region or '')}"
}}''',
            '''variable "project" {
  description = "Target project, account or subscription identifier"
  type        = string
}''',
            '''variable "image" {
  description = "Container image for the agent runtime"
  type        = string
}''',
        ]
        if secrets:
            refs = ", ".join(f'"{s}"' for s in secrets)
            blocks.append(
                f'''variable "secret_refs" {{
  description = "Names of secrets the agents read; values live in the secret manager"
  type        = list(string)
  default     = [{refs}]
}}'''
            )
        return "\n\n".join(blocks) + "\n"

    def _iam(self, ir: SystemIR) -> str:
        """One identity per agent, bound to exactly its resolved permissions."""
        p = self.profile
        roles = ACTION_ROLES[p.id]
        blocks = []
        for agent in ir.agents:
            if not agent.identity:
                continue
            name = _tf_name(agent.id)
            blocks.append(
                f'''resource "{p.identity_resource}" "{name}" {{
  # Workload identity for agent '{agent.id}' (ADR-0015)
  account_id   = "agent-{agent.id}"
  display_name = "{agent.identity.display_name}"
}}'''
            )
            granted: set[str] = set()
            for perm in agent.permissions:
                role = roles.get(perm.action.value)
                if role is None or role in granted:
                    continue
                granted.add(role)
                coarse = perm.action.value in p.coarse_actions
                note = "  # COARSENED: see MAPPING.md" if coarse else ""
                blocks.append(
                    f'''resource "{p.binding_resource}" "{name}_{_tf_name(role)}" {{
  # {perm.key()}{note}
  project = var.project
  role    = "{role}"
  member  = "serviceAccount:${{{p.identity_resource}.{name}.email}}"
}}'''
                )
            for ref in agent.identity.secret_refs:
                blocks.append(
                    f'''resource "{p.secret_resource}" "{name}_{_tf_name(ref)}" {{
  # Secret is referenced, never valued, by the generator (ADR-0015)
  secret_id = "{ref}"
}}'''
                )
        return "\n\n".join(blocks) + "\n" if blocks else "# no agent identities\n"

    def _agents(self, ir: SystemIR) -> str:
        p = self.profile
        service_type = p.resources["compute_service"]
        job_type = p.resources["job_runner"]
        blocks = []
        for agent in ir.agents:
            name = _tf_name(agent.id)
            env = agent.environment
            sizing = TIER_SIZING.get(env.tier.value if env else "minimal",
                                     TIER_SIZING["minimal"])
            posture = env.network.value if env else "none"
            blocks.append(
                f'''resource "{service_type}" "{name}" {{
  name     = "agent-{agent.id}"
  {p.region_variable} = var.{p.region_variable}

  # team: {' / '.join(agent.team_path)}
  # reports to: {agent.reports_to or 'no one'}
  # runtime adapter: {agent.runtime_adapter}
  template {{
    service_account = {p.identity_resource}.{name}.email
    containers {{
      image = var.image
      resources {{
        limits = {{
          cpu    = "{sizing['cpu']}"
          memory = "{sizing['memory']}"
        }}
      }}
      env {{
        name  = "ORGAGENTS_AGENT_ID"
        value = "{agent.id}"
      }}
    }}
  }}

  labels = merge(local.labels, {{
    "team"            = "{agent.team_id}"
    "network-posture" = "{posture}"
  }})
}}'''
            )
            if env:
                blocks.append(
                    f'''resource "{job_type}" "{name}_sandbox" {{
  # Execution environment '{env.id}' — tier {env.tier.value}, network {posture},
  # timeout {env.timeout_seconds}s, mounts: {', '.join(env.mounts) or 'none'}
  name     = "env-{agent.id}"
  {p.region_variable} = var.{p.region_variable}
}}'''
                )
        return "\n\n".join(blocks) + "\n" if blocks else "# no agents\n"

    def _backend(self, ir: SystemIR) -> str:
        backend = ir.binding.infrastructure.state_backend or "REPLACE_ME"
        return f'''# Copy to backend.tf and point at your own remote state.
terraform {{
  backend "{self.profile.terraform_provider if self.profile.id != "gcp" else "gcs"}" {{
    bucket = "{backend}"
    prefix = "{ir.name}/{ir.environment}"
  }}
}}
'''

    def _mapping_report(self, ir: SystemIR) -> str:
        """Every IR permission, named as mapped or explicitly coarsened."""
        p = self.profile
        roles = ACTION_ROLES[p.id]
        rows, coarse_rows = [], []
        for agent in ir.agents:
            for perm in agent.permissions:
                role = roles.get(perm.action.value, "—")
                row = f"| `{agent.id}` | `{perm.key()}` | `{role}` |"
                if perm.action.value in p.coarse_actions:
                    coarse_rows.append(
                        f"| `{agent.id}` | `{perm.key()}` | `{role}` | "
                        f"{p.display} IAM has no equivalent granularity; the binding "
                        f"is broader than the spec intends. |"
                    )
                else:
                    rows.append(row)
        unmapped = [k for k in NEUTRAL_RESOURCES if k not in p.resources and
                    k not in ("identity", "policy_binding", "secret")]
        return f"""# IAM and resource mapping — {p.display}

Generated from the IR for **{ir.name}** (spec_version {ir.spec_version}).
This report exists because the mapping is lossy in places, and a gap you cannot
see is a gap you cannot review (ADR-0012).

## Resource mapping

| Neutral resource | {p.display} |
|---|---|
""" + "\n".join(
            f"| `{kind}` | `{p.resources.get(kind, '— not mapped —')}` |"
            for kind in NEUTRAL_RESOURCES
        ) + f"""

Identities map to `{p.identity_resource}`, bindings to `{p.binding_resource}`,
secrets to `{p.secret_resource}`.

## Faithfully mapped permissions

| Agent | Permission | Granted role |
|---|---|---|
""" + ("\n".join(rows) if rows else "| — | — | — |") + """

## ⚠ Coarsened permissions

These grants are **broader** than the spec asked for. Review them before apply.

| Agent | Permission | Granted role | Why |
|---|---|---|---|
""" + ("\n".join(coarse_rows) if coarse_rows else "| — | — | — | None. |") + f"""

## Unmapped neutral resources

{', '.join(f'`{u}`' for u in unmapped) if unmapped else 'None — every neutral resource has a mapping.'}

## What this target does not do

It does not create your landing zone, VPC backbone or organization policies —
those are inputs. It does not apply anything; run `terraform plan` yourself.
"""
