"""A starting catalog, so a new installation is not an empty shelf (ADR-0041).

Model entries carry the figures a policy actually checks — class, context,
cost, region, whether the vendor trains on submitted data. Anthropic's models
are listed with their real identifiers; entries for other providers are marked
`proposed` with the fields an operator must fill in, because inventing another
vendor's pricing and context windows would put confident wrong numbers in front
of a cost policy.
"""
from __future__ import annotations

from .models import (
    ApprovalStatus,
    CatalogEntry,
    CatalogKind,
    Entitlement,
)


def _model(name: str, *, model_id: str, provider: str, classes: list[str],
           context: int, cost_in: float | None = None, cost_out: float | None = None,
           status: ApprovalStatus = ApprovalStatus.APPROVED,
           regions: list[str] | None = None, summary: str = "",
           vision: bool = False, hosting: str = "vendor_api",
           trains: bool = False) -> CatalogEntry:
    return CatalogEntry(
        id=f"cat_model_{name.replace('.', '_').replace('-', '_')}",
        kind=CatalogKind.MODEL, name=name, summary=summary,
        owner="Platform Architecture", status=status,
        tags=["model", provider, *classes],
        attributes={
            "provider": provider, "model_id": model_id, "classes": classes,
            "context_tokens": context, "cost_per_million_input": cost_in,
            "cost_per_million_output": cost_out,
            "regions": regions or ["us-east", "eu-west"], "hosting": hosting,
            "trains_on_data": trains, "supports_tools": True,
            "supports_vision": vision,
        },
    )


SEED_MODELS = [
    _model("claude-opus-5", model_id="claude-opus-5", provider="anthropic",
           classes=["frontier_reasoning", "code", "long_context", "vision"],
           context=200_000, cost_in=15.0, cost_out=75.0, vision=True,
           summary="Frontier reasoning; the default for leadership and review work."),
    _model("claude-sonnet-5", model_id="claude-sonnet-5", provider="anthropic",
           classes=["balanced", "code", "long_context", "vision"],
           context=200_000, cost_in=3.0, cost_out=15.0, vision=True,
           summary="Balanced capability and cost; the everyday choice."),
    _model("claude-haiku-4.5", model_id="claude-haiku-4-5-20251001",
           provider="anthropic", classes=["fast_cheap", "balanced"],
           context=200_000, cost_in=1.0, cost_out=5.0,
           summary="Fast and inexpensive; sub-agents and high-volume triage."),
    # Other providers are listed as proposals with the fields an operator must
    # complete. Publishing guessed pricing or context windows would be worse
    # than an empty row, because a cost policy would then check a fiction.
    CatalogEntry(
        id="cat_model_openai_placeholder", kind=CatalogKind.MODEL,
        name="openai-model (complete before approving)",
        summary="Add the model id, context window, pricing and regions, then review.",
        owner="Platform Architecture", status=ApprovalStatus.PROPOSED,
        tags=["model", "openai", "template"],
        attributes={"provider": "openai", "model_id": "", "classes": [],
                    "context_tokens": 0, "regions": []},
        review_note="Placeholder: figures must be supplied by the operator.",
    ),
    CatalogEntry(
        id="cat_model_self_hosted", kind=CatalogKind.MODEL,
        name="self-hosted (complete before approving)",
        summary="For an on-premises model: set the endpoint, class and region.",
        owner="Platform SRE", status=ApprovalStatus.PROPOSED,
        tags=["model", "on_premises", "template"],
        attributes={"provider": "self_hosted", "model_id": "",
                    "classes": ["on_premises"], "context_tokens": 0,
                    "hosting": "on_premises", "regions": []},
    ),
]

SEED_MCP_SERVERS = [
    CatalogEntry(
        id="cat_mcp_sql", kind=CatalogKind.MCP_SERVER, name="relational-sql",
        summary="Policy-enforcing SQL access: statement classes, row limits, masking.",
        owner="Data Platform", status=ApprovalStatus.APPROVED,
        tags=["mcp", "database"],
        attributes={"transport": "stdio", "command": "orgagents-mcp-sql",
                    "engine": "postgres", "read_only": True,
                    "tools": ["list_tables", "describe_table", "query"],
                    "secret_refs": ["DSN"]},
        requires=["a relational grant on the capability"],
    ),
    CatalogEntry(
        id="cat_mcp_knowledge", kind=CatalogKind.MCP_SERVER, name="knowledge-search",
        summary="Read and contribute to the shared knowledge base.",
        owner="Data Platform", status=ApprovalStatus.APPROVED,
        tags=["mcp", "knowledge"],
        attributes={"transport": "http", "url": "https://mcp.internal/knowledge",
                    "read_only": False, "tools": ["search", "fetch", "contribute"]},
    ),
    CatalogEntry(
        id="cat_mcp_repo", kind=CatalogKind.MCP_SERVER, name="source-repository",
        summary="Propose reviewed changes to source repositories.",
        owner="Platform Engineering", status=ApprovalStatus.RESTRICTED,
        tags=["mcp", "code"],
        entitlement=Entitlement(groups=["engineering"]),
        attributes={"transport": "stdio", "command": "github-mcp-server",
                    "read_only": False,
                    "tools": ["get_file_contents", "create_pull_request"],
                    "secret_refs": ["REPO_TOKEN"]},
        requires=["human approval on code_change"],
    ),
    CatalogEntry(
        id="cat_mcp_telemetry", kind=CatalogKind.MCP_SERVER, name="telemetry",
        summary="Read service health signals and incident history.",
        owner="Platform SRE", status=ApprovalStatus.APPROVED,
        tags=["mcp", "observability"],
        attributes={"transport": "http", "url": "https://mcp.internal/telemetry",
                    "read_only": True, "tools": ["query_metrics", "list_incidents"]},
    ),
]

SEED_ENVIRONMENTS = [
    CatalogEntry(
        id="cat_env_reasoning", kind=CatalogKind.ENVIRONMENT_TEMPLATE,
        name="reasoning", summary="No code execution; delegation and tool calls only.",
        owner="Security Engineering", status=ApprovalStatus.APPROVED,
        tags=["environment", "minimal"],
        attributes={"image": "python:3.11-slim", "tier": "minimal",
                    "network": "none", "toolchains": ["none"]},
    ),
    CatalogEntry(
        id="cat_env_analysis", kind=CatalogKind.ENVIRONMENT_TEMPLATE,
        name="analysis", summary="Dataframe and statistical work over extracts.",
        owner="Security Engineering", status=ApprovalStatus.APPROVED,
        tags=["environment", "data"],
        attributes={"image": "python:3.11", "tier": "medium", "network": "allowlist",
                    "toolchains": ["data_analysis"],
                    "packages": ["pandas", "numpy", "pyarrow", "duckdb"]},
    ),
    CatalogEntry(
        id="cat_env_build", kind=CatalogKind.ENVIRONMENT_TEMPLATE, name="build",
        summary="Developer environment for proposing code changes.",
        owner="Security Engineering", status=ApprovalStatus.RESTRICTED,
        tags=["environment", "engineering"],
        entitlement=Entitlement(groups=["engineering"]),
        attributes={"image": "ghcr.io/acme/agent-dev:3.11", "tier": "large",
                    "network": "allowlist", "toolchains": ["software_build"]},
    ),
    CatalogEntry(
        id="cat_env_cleanroom", kind=CatalogKind.ENVIRONMENT_TEMPLATE,
        name="isolated_review",
        summary="Air-gapped clean room for identifying data. Zero egress.",
        owner="Security Engineering", status=ApprovalStatus.APPROVED,
        tags=["environment", "regulated"],
        attributes={"image": "python:3.11-slim", "tier": "small", "network": "none",
                    "toolchains": ["scripting"]},
        requires=["a data class permitted in this environment"],
    ),
]

SEED_PERMISSION_SETS = [
    CatalogEntry(
        id="cat_perm_read_finance", kind=CatalogKind.PERMISSION_SET,
        name="finance-read", summary="Read finance data and query the warehouse.",
        owner="Security Engineering", status=ApprovalStatus.APPROVED,
        tags=["permissions", "finance"],
        attributes={"risk": "low", "requires_approval": False, "permissions": [
            {"action": "read", "resource_kind": "data_class",
             "resource": "finance_internal"},
            {"action": "query", "resource_kind": "capability",
             "resource": "warehouse_query"},
        ]},
    ),
    CatalogEntry(
        id="cat_perm_pii", kind=CatalogKind.PERMISSION_SET, name="pii-reconciliation",
        summary="Query identifying data inside the clean room, with approval.",
        owner="Security Engineering", status=ApprovalStatus.RESTRICTED,
        tags=["permissions", "regulated"],
        entitlement=Entitlement(groups=["finance", "compliance"]),
        attributes={"risk": "high", "requires_approval": True, "permissions": [
            {"action": "query", "resource_kind": "capability",
             "resource": "pii_reconciliation",
             "conditions": {"environments": ["isolated_review"],
                            "requires_approval": True}},
        ]},
    ),
    CatalogEntry(
        id="cat_perm_delegate", kind=CatalogKind.PERMISSION_SET, name="team-delegation",
        summary="Delegate to team members and approve their workflows.",
        owner="Platform Architecture", status=ApprovalStatus.APPROVED,
        tags=["permissions", "leadership"],
        attributes={"risk": "medium", "permissions": [
            {"action": "delegate", "resource_kind": "agent", "resource": "*"},
            {"action": "approve", "resource_kind": "workflow", "resource": "*"},
        ]},
    ),
]

SEED_GUARDRAILS = [
    CatalogEntry(
        id="cat_guard_secrets", kind=CatalogKind.GUARDRAIL, name="no-credentials-out",
        summary="Block credential-shaped content leaving an agent.",
        owner="Security Engineering", status=ApprovalStatus.APPROVED,
        tags=["guardrail", "baseline"],
        attributes={"checks": ["secrets"], "on_violation": "block",
                    "applies_to": ["output", "tool_output"]},
    ),
    CatalogEntry(
        id="cat_guard_pii", kind=CatalogKind.GUARDRAIL, name="mask-identifiers",
        summary="Redact identifying data on the way out.",
        owner="Security Engineering", status=ApprovalStatus.APPROVED,
        tags=["guardrail", "baseline"],
        attributes={"checks": ["pii"], "on_violation": "redact",
                    "applies_to": ["output"]},
    ),
]


def seed_entries() -> list[CatalogEntry]:
    return [*SEED_MODELS, *SEED_MCP_SERVERS, *SEED_ENVIRONMENTS,
            *SEED_PERMISSION_SETS, *SEED_GUARDRAILS]


def seed_catalog(service) -> int:
    """Publish the starting catalog, leaving anything already there alone."""
    published = 0
    for entry in seed_entries():
        if service.get(entry.id) is None:
            service.publish(entry)
            published += 1
    return published
