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
from typing import Any, Optional

from ..base import GeneratedFile
from ..ir import NEUTRAL_RESOURCES, SystemIR
from ..registry import registry_report


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
    # How this provider's scheduler expresses a cadence.
    schedule_field: str = "schedule"
    timezone_field: str = "time_zone"
    # Neutral permission verbs that this provider's IAM cannot express at the
    # requested granularity. Declared, not discovered, and surfaced in MAPPING.md.
    coarse_actions: tuple[str, ...] = ()
    # The per-tenant deployment boundary (ADR-0050): what the tenant's
    # resources live inside, what Terraform pins it with, and where that is
    # coarser than one tenant per blast radius.
    boundary_kind: str = "project"
    boundary_argument: str = "project"
    boundary_enforcement: str = ""
    boundary_coarser_than_model: str = ""
    # Network isolation (ADR-0084). When `network` is set, the target emits a
    # VPC, one subnet per placement, and firewall rules that implement the
    # placement rules with the agents' own identities. Providers that do not
    # set it emit a network.tf that says the isolation was NOT generated,
    # rather than letting a reader assume it was.
    network: Optional["NetworkProfile"] = None


@dataclass(frozen=True)
class NetworkProfile:
    """How one provider realizes the placement network model (ADR-0069/0084).

    Firewall rules are scoped by *service account*, not by IP range: the
    agents' workload identities are what the placement rules are really about,
    and GCP firewall can target a rule at a source and destination service
    account directly. So the network isolation and the IAM identities are the
    same fact, expressed once.
    """

    vpc_resource: str
    subnet_resource: str
    firewall_resource: str
    # Whether firewall rules can be scoped to service accounts (GCP) rather
    # than only to IP ranges/tags. When true, a placement rule becomes a rule
    # from the source agents' SAs to the target agents' SAs.
    firewall_by_service_account: bool = True


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
            "scheduler": "google_cloud_scheduler_job",
            "event_subscription": "google_eventarc_trigger",
            "channel_bridge": "google_cloud_run_v2_service",
            "knowledge_index": "google_discovery_engine_data_store",
            "memory_store": "google_firestore_database",
            "agent_endpoint": "google_service_networking_connection",
        },
        schedule_field="schedule",
        timezone_field="time_zone",
        identity_resource="google_service_account",
        binding_resource="google_project_iam_member",
        secret_resource="google_secret_manager_secret",
        region_variable="region",
        coarse_actions=("approve",),
        network=NetworkProfile(
            vpc_resource="google_compute_network",
            subnet_resource="google_compute_subnetwork",
            firewall_resource="google_compute_firewall",
            firewall_by_service_account=True,
        ),
        boundary_kind="project",
        boundary_argument="project",
        boundary_enforcement=(
            "one Google Cloud **project** per tenant, pinned by the provider's "
            "`project` argument; every IAM binding is a project-level binding "
            "inside it"
        ),
        boundary_coarser_than_model=(
            "IAM bindings are granted at the project level rather than per "
            "resource, so an identity reaches every resource of that kind in "
            "its own tenant's project. Organization-level policy and shared "
            "VPC attachments are inputs this target does not create, and a "
            "misconfigured one would reach across projects."
        ),
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
            "scheduler": "aws_scheduler_schedule",
            "event_subscription": "aws_cloudwatch_event_rule",
            "channel_bridge": "aws_ecs_service",
            "knowledge_index": "aws_kendra_index",
            "memory_store": "aws_dynamodb_table",
            "agent_endpoint": "aws_vpc_endpoint",
        },
        schedule_field="schedule_expression",
        timezone_field="schedule_expression_timezone",
        identity_resource="aws_iam_role",
        binding_resource="aws_iam_role_policy",
        secret_resource="aws_secretsmanager_secret",
        region_variable="region",
        coarse_actions=("approve", "delegate"),
        boundary_kind="account",
        boundary_argument="allowed_account_ids",
        boundary_enforcement=(
            "one AWS **account** per tenant, pinned by the provider's "
            "`allowed_account_ids`, so an apply aimed at the wrong account "
            "fails before it creates anything"
        ),
        boundary_coarser_than_model=(
            "Inline role policies are written per role, but the account is the "
            "only boundary this target creates; cross-account trust, SCPs and "
            "resource policies live in the landing zone and are not generated "
            "here."
        ),
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
            "scheduler": "azurerm_logic_app_workflow",
            "event_subscription": "azurerm_eventgrid_event_subscription",
            "channel_bridge": "azurerm_container_app",
            "knowledge_index": "azurerm_search_service",
            "memory_store": "azurerm_cosmosdb_account",
            "agent_endpoint": "azurerm_private_endpoint",
        },
        schedule_field="schedule",
        timezone_field="time_zone",
        identity_resource="azurerm_user_assigned_identity",
        binding_resource="azurerm_role_assignment",
        secret_resource="azurerm_key_vault_secret",
        region_variable="location",
        coarse_actions=("approve", "delegate", "publish"),
        boundary_kind="subscription",
        boundary_argument="subscription_id",
        boundary_enforcement=(
            "one Azure **subscription** per tenant, pinned by the provider's "
            "`subscription_id`; role assignments are scoped to that "
            "subscription"
        ),
        boundary_coarser_than_model=(
            "Role assignments are subscription-scoped rather than "
            "resource-scoped, and Azure's built-in roles are broad, so a "
            "tenant's identity holds more inside its own subscription than "
            "the spec asked for. This is coarser than the model."
        ),
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

#: Ordered widest-last, so "the widest sandbox an agent has" is a max().
_TIER_ORDER = ("minimal", "small", "medium", "large", "accelerated")
_POSTURE_ORDER = ("none", "allowlist", "internal", "open")


def _widest(environments: list) -> object | None:
    """The sandbox with the widest reach, for a decision that admits one.

    A deployed workload is one shape. Where a target can only express one,
    it takes the widest rather than the first, because under-sizing or
    under-permitting a service makes the design undeployable — and says so
    in its conformance notes rather than pretending the others do not exist.
    """
    if not environments:
        return None
    return max(environments, key=lambda e: (
        _POSTURE_ORDER.index(e.network.value) if e.network.value in _POSTURE_ORDER else 0,
        _TIER_ORDER.index(e.tier.value) if e.tier.value in _TIER_ORDER else 0,
    ))


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
                         "triggers.tf", "channels.tf", "backend.tf.example",
                         "REGISTRY.md", "MAPPING.md"],
            "caveats": [
                "IAM mapping is lossy; MAPPING.md names every coarsened grant.",
                f"Actions mapped coarsely on this provider: "
                f"{', '.join(self.profile.coarse_actions) or 'none'}.",
                f"The tenant boundary is one {self.profile.boundary_kind} per "
                f"tenant, which is coarser than ADR-0050's per-resource model; "
                f"MAPPING.md says where.",
            ],
        }

    # -- generation --------------------------------------------------------

    def generate(self, ir: SystemIR) -> list[GeneratedFile]:
        return [
            GeneratedFile("main.tf", self._main(ir)).with_header(ir),
            GeneratedFile("variables.tf", self._variables(ir)).with_header(ir),
            GeneratedFile("iam.tf", self._iam(ir)).with_header(ir),
            GeneratedFile("agents.tf", self._agents(ir)).with_header(ir),
            GeneratedFile("network.tf", self._network(ir)).with_header(ir),
            GeneratedFile("triggers.tf", self._triggers(ir)).with_header(ir),
            GeneratedFile("channels.tf", self._channels(ir)).with_header(ir),
            GeneratedFile("REGISTRY.md", registry_report(ir)),
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
  # The tenant boundary on this provider: everything below is created inside
  # this one {p.boundary_kind}, and an apply pointed elsewhere fails (ADR-0050).
  {p.boundary_argument} = {"[var.project]" if p.boundary_argument.endswith("ids") else "var.project"}
}}

locals {{
  system      = "{ir.name}"
  environment = "{ir.environment}"
  tenant      = "{ir.tenant.id if ir.tenant else ""}"
  tenant_prefix = "{ir.tenant.namespace_prefix if ir.tenant else ""}"
  labels = {{
    "managed-by"   = "orgagents"
    "system"       = "{_tf_name(ir.name)}"
    "tenant"       = "{_tf_name(ir.tenant.id) if ir.tenant else ""}"
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
        # When the fabric has already assigned the tenant a boundary, pin it
        # here so a plan against somebody else's account cannot even start.
        boundary = ir.tenant.cloud_boundary if ir.tenant else ""
        tenant_validation = (
            f'''
  validation {{
    condition     = var.project == "{boundary}"
    error_message = "This configuration belongs to tenant {ir.tenant.id}, whose {p.boundary_kind} is {boundary}."
  }}
'''
            if boundary
            else ""
        )
        blocks = [
            f'''variable "{p.region_variable}" {{
  description = "Deployment {p.region_variable}"
  type        = string
  default     = "{(ir.binding.infrastructure.region or '')}"
}}''',
            f'''variable "project" {{
  description = "The {p.boundary_kind} this tenant deploys into. One {p.boundary_kind} per tenant: it is the boundary, not a label (ADR-0050)."
  type        = string
{tenant_validation}}}''',
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
  account_id   = "{ir.qualified(f'agent-{agent.id}')}"
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
            # An agent with several sandboxes gets one service sized for the
            # widest of them: a single deployed workload cannot be two shapes,
            # and under-sizing it would make the design undeployable. The
            # narrower sandboxes still bound what its *calls* may reach, which
            # is where the isolation actually lives.
            env = _widest(agent.environments)
            sizing = TIER_SIZING.get(env.tier.value if env else "minimal",
                                     TIER_SIZING["minimal"])
            posture = env.network.value if env else "none"
            blocks.append(
                f'''resource "{service_type}" "{name}" {{
  name     = "{ir.qualified(f'agent-{agent.id}')}"
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
                net = p.network
                vpc_block = ""
                if net is not None:
                    # Attach the sandbox to the subnet of its placement, so the
                    # firewall rules in network.tf actually govern this
                    # workload (ADR-0084). A `none` sandbox keeps to private
                    # ranges; the belt-and-braces egress-deny is in network.tf.
                    subnet = self._subnet_for(ir, agent.id, env.id)
                    egress = ("PRIVATE_RANGES_ONLY" if posture == "none"
                              else "ALL_TRAFFIC")
                    if subnet:
                        vpc_block = f'''
  template {{
    template {{
      vpc_access {{
        network_interfaces {{
          network    = {net.vpc_resource}.{_tf_name(ir.name)}_vpc.id
          subnetwork = {net.subnet_resource}.{subnet}.id
        }}
        egress = "{egress}"
      }}
    }}
  }}'''
                blocks.append(
                    f'''resource "{job_type}" "{name}_sandbox" {{
  # Execution environment '{env.id}' — tier {env.tier.value}, network {posture},
  # timeout {env.timeout_seconds}s, mounts: {', '.join(env.mounts) or 'none'}
  name     = "{ir.qualified(f'env-{agent.id}')}"
  {p.region_variable} = var.{p.region_variable}{vpc_block}
}}'''
                )
        return "\n\n".join(blocks) + "\n" if blocks else "# no agents\n"

    def _subnet_for(self, ir: SystemIR, agent_id: str, env_id: str) -> str:
        """The subnet resource name for the placement an agent runs in for an
        environment, or "" if there is none (ADR-0084)."""
        for pl in ir.placements:
            if pl.environment == env_id and agent_id in pl.agents:
                return _tf_name(pl.id)
        return ""

    def _network(self, ir: SystemIR) -> str:
        """Translate the placement model into real network isolation (ADR-0084).

        A placement is a unit crossed with a sandbox environment (ADR-0069):
        two units in the same sandbox are two placements, two blast radii, and
        by default they cannot reach each other. That maps onto cloud
        networking directly: one VPC for the system, one subnet per placement,
        a default-deny firewall, and one allow rule per placement rule the IR
        resolved -- and only those. The placement rules are the declared
        channels, flows and the manager chain (ADR-0069 rules 6/7), so "teams
        talk only through official channels" becomes exactly the set of allow
        rules and everything else is denied.

        Allow rules are scoped to the agents' service accounts, not IP ranges:
        the workload identities in iam.tf are what a placement rule is about,
        so the network boundary and the identity are one fact. Egress follows
        the sandbox's posture: a `none` sandbox gets an all-egress deny.
        """
        net = self.profile.network
        if net is None:
            return (
                "# Network isolation is NOT generated for '"
                + self.profile.display + "' yet.\n"
                "# The placement model (ADR-0069) is resolved in the IR, but\n"
                "# this provider profile has no network mapping -- so team\n"
                "# isolation here is whatever your landing zone already does.\n"
            )
        if not ir.placements:
            return ("# No placements: no agent declares a sandbox, so there "
                    "is nothing to isolate.\n")

        rv = self.profile.region_variable
        vpc = _tf_name(ir.name) + "_vpc"
        blocks = [
            'resource "' + net.vpc_resource + '" "' + vpc + '" {\n'
            '  name                    = "' + ir.qualified("vpc") + '"\n'
            '  auto_create_subnetworks = false\n'
            '  # One VPC for the whole system. Every placement gets its own\n'
            '  # subnet below, and nothing crosses without a firewall rule the\n'
            '  # design asked for (ADR-0084).\n'
            '}'
        ]

        posture = {e.id: e.network.value for e in ir.environments}
        order = sorted(ir.placements, key=lambda pl: pl.id)
        cidr = {pl.id: "10." + str(8 + i) + ".0.0/24"
                for i, pl in enumerate(order)}
        sub = {pl.id: _tf_name(pl.id) for pl in order}
        by_id = {pl.id: pl for pl in order}

        def sas(agent_ids):
            ids = [self.profile.identity_resource + "." + _tf_name(a) + ".email"
                   for a in agent_ids]
            return "[" + ", ".join(ids) + "]"

        for pl in order:
            blocks.append(
                'resource "' + net.subnet_resource + '" "' + sub[pl.id]
                + '" {\n'
                '  name          = "' + ir.qualified("subnet-" + pl.id) + '"\n'
                '  ' + rv + ' = var.' + rv + '\n'
                '  network       = ' + net.vpc_resource + '.' + vpc + '.id\n'
                '  ip_cidr_range = "' + cidr[pl.id] + '"\n'
                "  # placement '" + pl.id + "' -- unit '" + pl.unit
                + "', sandbox '" + pl.environment + "',\n"
                "  # posture '" + posture.get(pl.environment, "none")
                + "', agents: " + (", ".join(pl.agents) or "none") + "\n"
                '}'
            )

        blocks.append(
            'resource "' + net.firewall_resource + '" "deny_cross_placement" {\n'
            '  name      = "' + ir.qualified("deny-cross-placement") + '"\n'
            '  network   = ' + net.vpc_resource + '.' + vpc + '.id\n'
            '  priority  = 65534\n'
            '  direction = "INGRESS"\n'
            '  # Default-deny between placements (ADR-0069 rule 7). Only the\n'
            '  # allow rules below open a path.\n'
            '  deny { protocol = "all" }\n'
            '  source_ranges = ["10.0.0.0/8"]\n'
            '}'
        )

        for pl in order:
            if not pl.agents:
                continue
            blocks.append(
                'resource "' + net.firewall_resource + '" "allow_within_'
                + sub[pl.id] + '" {\n'
                '  name      = "' + ir.qualified("allow-within-" + pl.id) + '"\n'
                '  network   = ' + net.vpc_resource + '.' + vpc + '.id\n'
                '  priority  = 1000\n'
                '  direction = "INGRESS"\n'
                "  # Traffic inside placement '" + pl.id + "' is permitted "
                "and unlisted (ADR-0069).\n"
                '  allow { protocol = "tcp" }\n'
                '  source_service_accounts = ' + sas(pl.agents) + '\n'
                '  target_service_accounts = ' + sas(pl.agents) + '\n'
                '}'
            )

        for i, rule in enumerate(ir.placement_rules):
            src, dst = by_id.get(rule.source), by_id.get(rule.target)
            if not src or not dst or not src.agents or not dst.agents:
                continue
            blocks.append(
                'resource "' + net.firewall_resource + '" "allow_' + str(i)
                + '_' + sub[rule.source] + '_to_' + sub[rule.target] + '" {\n'
                '  name      = "' + ir.qualified("allow-" + str(i)) + '"\n'
                '  network   = ' + net.vpc_resource + '.' + vpc + '.id\n'
                '  priority  = 900\n'
                '  direction = "INGRESS"\n'
                "  # " + rule.reason + " (via " + rule.via + ").\n"
                "  # A declared, official path (ADR-0069 rule 7); without it\n"
                "  # the default-deny above stands.\n"
                '  allow { protocol = "tcp" }\n'
                '  source_service_accounts = ' + sas(src.agents) + '\n'
                '  target_service_accounts = ' + sas(dst.agents) + '\n'
                '}'
            )

        for pl in order:
            if posture.get(pl.environment) != "none" or not pl.agents:
                continue
            blocks.append(
                'resource "' + net.firewall_resource + '" "deny_egress_'
                + sub[pl.id] + '" {\n'
                '  name      = "' + ir.qualified("deny-egress-" + pl.id) + '"\n'
                '  network   = ' + net.vpc_resource + '.' + vpc + '.id\n'
                '  priority  = 900\n'
                '  direction = "EGRESS"\n'
                "  # sandbox '" + pl.environment + "' posture 'none': its "
                "agents reach\n"
                "  # nothing outbound -- PII, MNPI or a live sample never "
                "calls home.\n"
                '  deny { protocol = "all" }\n'
                '  destination_ranges = ["0.0.0.0/0"]\n'
                '  target_service_accounts = ' + sas(pl.agents) + '\n'
                '}'
            )

        allowlisted = sorted({e.id for e in ir.environments
                              if e.network.value == "allowlist"})
        note = ""
        if allowlisted:
            note = (
                "\n# NOTE: sandboxes " + ", ".join(allowlisted) + " declare a\n"
                "# hostname egress allowlist. A firewall rule works on IP\n"
                "# ranges and service accounts, not FQDNs, so the allowlist is\n"
                "# enforced by an egress proxy / Cloud NAT with an FQDN policy,\n"
                "# wired in overlays/ -- not by these rules (ADR-0084).\n"
            )
        return "\n\n".join(blocks) + "\n" + note

    def _triggers(self, ir: SystemIR) -> str:
        """Schedulers and event subscriptions (ADR-0020).

        A trigger wakes the owning agent's service; it never carries its own
        identity, so a scheduled run has exactly the agent's permissions.
        """
        p = self.profile
        blocks = []
        for trigger in ir.triggers:
            if not trigger.enabled:
                blocks.append(f"# trigger '{trigger.id}' is disabled in the spec")
                continue
            name = _tf_name(trigger.id)
            agent = _tf_name(trigger.agent_id)
            if trigger.cron or trigger.interval_seconds:
                expression = (
                    trigger.cron
                    if trigger.cron
                    else f"rate({trigger.interval_seconds // 60} minutes)"
                )
                blocks.append(
                    f'''resource "{p.resources["scheduler"]}" "{name}" {{
  # {trigger.description or trigger.id}
  # {trigger.schedule}
  # overlap={trigger.overlap} catch_up={trigger.catch_up} retries={trigger.retries}
  name             = "{ir.qualified(f'trigger-{trigger.id}')}"
  {p.region_variable} = var.{p.region_variable}
  {p.schedule_field} = "{expression}"
  {p.timezone_field} = "{trigger.timezone}"
  attempt_deadline = "{trigger.max_runtime_seconds}s"

  # Runs as the agent, not as the scheduler (ADR-0015).
  service_account = {p.identity_resource}.{agent}.email
}}'''
                )
            else:
                blocks.append(
                    f'''resource "{p.resources["event_subscription"]}" "{name}" {{
  # {trigger.description or trigger.id}
  # fires on event class '{trigger.event_class}' filtered by {trigger.filters or "{}"}
  name             = "{ir.qualified(f'trigger-{trigger.id}')}"
  {p.region_variable} = var.{p.region_variable}
  service_account = {p.identity_resource}.{agent}.email
}}'''
                )
        if ir.binding.scheduler and ir.binding.scheduler.dead_letter:
            blocks.append(
                f'''resource "{p.resources["message_bus"]}" "dead_letter" {{
  # Runs that exhaust their retries land here (ADR-0025).
  name = "{ir.qualified(ir.binding.scheduler.dead_letter)}"
}}'''
            )
        return "\n\n".join(blocks) + "\n" if blocks else "# no triggers\n"

    def _channels(self, ir: SystemIR) -> str:
        """Channel bridges and their credentials (ADR-0021).

        The bridge is the only component holding a workspace credential, so a
        compromised agent cannot post as the organization.
        """
        p = self.profile
        blocks = []
        for channel in ir.channels:
            if not channel.human_facing:
                continue
            name = _tf_name(channel.id)
            escalation = " → ".join(
                f"{step['notify']} after {step['after_minutes']}m"
                for step in channel.escalation
            ) or "none"
            blocks.append(
                f'''resource "{p.resources["channel_bridge"]}" "{name}" {{
  # {channel.description or channel.id}
  # provider={channel.provider} purposes={", ".join(channel.purposes) or "-"}
  # sla={channel.response_sla_minutes or "none"} out_of_hours={channel.out_of_hours}
  # escalation: {escalation}
  name             = "{ir.qualified(f'channel-{channel.id}')}"
  {p.region_variable} = var.{p.region_variable}
}}'''
            )
            if channel.bot_identity_ref:
                blocks.append(
                    f'''resource "{p.secret_resource}" "{name}_credential" {{
  # Workspace credential for '{channel.id}', referenced by name only.
  secret_id = "{channel.bot_identity_ref}"
}}'''
                )
        if ir.memory.long_term.enabled:
            namespaces = ", ".join(n.id for n in ir.memory.namespaces) or "none"
            blocks.append(
                f'''resource "{p.resources["memory_store"]}" "long_term_memory" {{
  # Long-term agent memory (ADR-0028); namespaces: {namespaces}
  # retention: {ir.memory.long_term.retention_days or "unbounded"} days,
  # promotion from a session: {ir.memory.long_term.promotion_allowed}
  name             = "{ir.name}-memory"
  {p.region_variable} = var.{p.region_variable}
}}'''
            )
        for endpoint in {e.id: e for a in ir.agents for e in a.endpoints}.values():
            blocks.append(
                f'''resource "{p.resources["agent_endpoint"]}" "{_tf_name(endpoint.id)}" {{
  # External agent '{endpoint.id}' — trust: {endpoint.trust.value} (ADR-0030)
  # may be sent: {", ".join(endpoint.send_data_classes) or "nothing"}
  # answers are data, never instructions: {endpoint.treat_output_as_data}
  name             = "{ir.qualified(f'endpoint-{endpoint.id}')}"
  {p.region_variable} = var.{p.region_variable}
}}'''
            )
        for source in ir.knowledge:
            blocks.append(
                f'''resource "{p.resources["knowledge_index"]}" "{_tf_name(source.id)}" {{
  # Grounding source '{source.id}' ({source.kind}); citation required: {source.require_citation}
  # data classes: {", ".join(source.data_classes) or "none"}
  name             = "{ir.qualified(f'knowledge-{source.id}')}"
  {p.region_variable} = var.{p.region_variable}
}}'''
            )
        return "\n\n".join(blocks) + "\n" if blocks else "# no human channels\n"

    def _backend(self, ir: SystemIR) -> str:
        backend = ir.binding.infrastructure.state_backend or "REPLACE_ME"
        return f'''# Copy to backend.tf and point at your own remote state.
terraform {{
  backend "{self.profile.terraform_provider if self.profile.id != "gcp" else "gcs"}" {{
    bucket = "{backend}"
    prefix = "{ir.tenant.id + '/' if ir.tenant else ''}{ir.name}/{ir.environment}"
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
        tenant_section = (
            f"""## Tenant boundary

Tenant: **{ir.tenant.id}** · namespace prefix `{ir.tenant.namespace_prefix}-` ·
isolation domain `{ir.tenant.isolation_domain}`.

**What enforces the boundary here:** {p.boundary_enforcement}. Every resource
name, service identity and secret reference in this configuration carries the
tenant's namespace prefix, so two tenants' configurations collide on nothing
even if they are applied into the same {p.boundary_kind} by mistake.

**Where this is coarser than the model:** ADR-0050 treats the tenant boundary as
absolute and per-resource. Here it is not. {p.boundary_coarser_than_model}
The namespace prefix prevents collisions; it is not an access control, and it
does not stop a principal with {p.boundary_kind}-wide credentials from reading
across it. Read this before you rely on the boundary."""
            if ir.tenant
            else """## Tenant boundary

This configuration was compiled **without a tenant**, so nothing enforces a
tenant boundary and nothing is namespaced. Apply it only into a
{kind} that hosts this system alone.""".replace("{kind}", p.boundary_kind)
        )
        # The sandbox boundary is a separate claim from the tenant boundary, and
        # a reader who trusts one will trust the other unless we say which is
        # which (ADR-0054).
        from ...sandboxes import EnvironmentFacts, detect_context, resolve_provider

        context = detect_context(target=f"terraform:{p.id}" if hasattr(p, "id")
                                 else "terraform")
        # One statement for the provider, then only what differs per class:
        # repeating the same paragraph per environment trains readers to skip
        # the section, and this is a section that must be read.
        statement = None
        gaps: dict[str, list[str]] = {}
        for env in ir.environments:
            facts = EnvironmentFacts.from_environment_class(
                env, tenant_id=ir.tenant.id if ir.tenant else None
            )
            resolution = resolve_provider("target_native", facts, context)
            statement = statement or resolution.boundary
            unexpressible = list(resolution.mapping.unexpressible)
            if unexpressible:
                gaps[env.id] = unexpressible
        if statement is None:
            sandbox_section = (
                "## Sandbox execution\n\n"
                "This system declares no environment classes.\n"
            )
        else:
            lines = [
                "## Sandbox execution",
                "",
                f"Provider in force: **{statement.provider}** "
                f"({statement.maturity}). {statement.summary}",
                "",
                f"- **Enforces:** {'; '.join(statement.enforces)}",
                f"- **Does not enforce:** "
                f"{'; '.join(statement.does_not_enforce)}",
                f"- **Tenant isolation:** {statement.tenant_scoping.value} — "
                f"{statement.tenant_note}",
                "- **Verified here:** no. No sandbox provider was run in this "
                "environment; this restates the target's documentation "
                "(ADR-0054).",
            ]
            if gaps:
                lines += ["", "What the environment class cannot express here:", ""]
                # Identical gaps across every class is the usual case; listing
                # it once per class buries the one class that differs.
                distinct = {tuple(v) for v in gaps.values()}
                if len(distinct) == 1 and len(gaps) == len(ir.environments):
                    for item in next(iter(distinct)):
                        lines.append(f"- {item} (every environment class)")
                else:
                    for env_id, items in gaps.items():
                        lines.append(f"- `{env_id}`: " + "; ".join(items))
            sandbox_section = "\n".join(lines) + "\n"

        return f"""# IAM and resource mapping — {p.display}

Generated from the IR for **{ir.name}** (spec_version {ir.spec_version}).
This report exists because the mapping is lossy in places, and a gap you cannot
see is a gap you cannot review (ADR-0012).

{tenant_section}

{sandbox_section}
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
"""
