"""The platform catalog: the building blocks a designer may choose from.

A spec says an agent needs `frontier_reasoning` and a capability bound to a
relational MCP server. The **catalog** is what turns those into choices a
person can actually make: which models are approved here, which MCP servers are
available, which sandbox templates security has published, which plugins and
tools exist, which permission sets may be granted (ADR-0041).

Every entry carries the same governance spine — an owner, a status, a version,
entitlements — so "what may this team use" is answerable, and so a model that
is retired stops being selectable rather than being quietly reused.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ..ids import new_id, now_iso


class CatalogKind(str, Enum):
    """What kind of building block an entry describes."""

    MODEL = "model"                  # an LLM
    MCP_SERVER = "mcp_server"
    PLUGIN = "plugin"
    TOOL = "tool"
    ENVIRONMENT_TEMPLATE = "environment_template"
    SKILL = "skill"
    GUARDRAIL = "guardrail"
    PERMISSION_SET = "permission_set"
    KNOWLEDGE_SOURCE = "knowledge_source"
    WORKFLOW = "workflow"
    AGENT_TEMPLATE = "agent_template"
    ENDPOINT = "endpoint"


class ApprovalStatus(str, Enum):
    """Where an entry sits in its review life.

    Only `approved` entries may be selected in a design; the others exist so
    that "we looked at this" survives the person who looked.
    """

    PROPOSED = "proposed"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    RESTRICTED = "restricted"    # approved, but only for entitled groups
    DEPRECATED = "deprecated"    # usable, not for new designs
    RETIRED = "retired"          # not selectable at all
    REJECTED = "rejected"


class Entitlement(BaseModel):
    """Who may select an entry, when not everyone may."""

    groups: list[str] = Field(default_factory=list)
    workspaces: list[str] = Field(default_factory=list)
    environments: list[str] = Field(
        default_factory=lambda: ["development", "staging", "production"]
    )

    def permits(self, *, groups: list[str], workspace: str = "",
                environment: str = "development") -> bool:
        if self.groups and not set(self.groups) & set(groups):
            return False
        if self.workspaces and workspace and workspace not in self.workspaces:
            return False
        if self.environments and environment not in self.environments:
            return False
        return True


class CatalogEntry(BaseModel):
    """One building block, with the governance every block needs."""

    id: str = Field(default_factory=lambda: new_id("cat"))
    kind: CatalogKind
    name: str
    version: str = "1.0.0"
    summary: str = ""
    description: str = ""
    owner: str = ""                       # the team accountable for it
    status: ApprovalStatus = ApprovalStatus.PROPOSED
    entitlement: Entitlement = Field(default_factory=Entitlement)
    tags: list[str] = Field(default_factory=list)
    # Kind-specific detail, typed by the models below.
    attributes: dict[str, Any] = Field(default_factory=dict)
    # What using this entry requires elsewhere — a secret, a capability, a
    # network posture — so a choice does not quietly imply a dependency.
    requires: list[str] = Field(default_factory=list)
    documentation_url: str = ""
    superseded_by: Optional[str] = None
    installs: int = 0
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    reviewed_by: str = ""
    reviewed_at: str = ""
    review_note: str = ""

    @property
    def selectable(self) -> bool:
        """Whether a design may choose this entry at all."""
        return self.status in (
            ApprovalStatus.APPROVED, ApprovalStatus.RESTRICTED,
            ApprovalStatus.DEPRECATED,
        )

    def available_to(self, *, groups: Optional[list[str]] = None,
                     workspace: str = "",
                     environment: str = "development") -> bool:
        if not self.selectable:
            return False
        if self.status is ApprovalStatus.RESTRICTED or self.entitlement.groups:
            return self.entitlement.permits(groups=groups or [],
                                            workspace=workspace,
                                            environment=environment)
        return self.entitlement.permits(groups=groups or [], workspace=workspace,
                                        environment=environment)


# --------------------------------------------------------------------------
# Typed attribute shapes, so the catalog is more than a bag of dictionaries
# --------------------------------------------------------------------------


class ModelAttributes(BaseModel):
    """What the platform needs to know about an LLM to govern its use."""

    provider: str = ""
    model_id: str = ""
    classes: list[str] = Field(default_factory=list)     # ModelClass values
    context_tokens: int = 0
    max_output_tokens: int = 0
    cost_per_million_input: Optional[float] = None
    cost_per_million_output: Optional[float] = None
    # Where inference happens, for residency checks.
    regions: list[str] = Field(default_factory=list)
    hosting: Literal["vendor_api", "cloud_region", "on_premises"] = "vendor_api"
    trains_on_data: bool = False
    supports_tools: bool = True
    supports_vision: bool = False
    supports_streaming: bool = True
    knowledge_cutoff: str = ""

    @property
    def cost_per_million(self) -> Optional[float]:
        """A single blended figure for policy comparison (3:1 in:out)."""
        if self.cost_per_million_input is None:
            return None
        output = self.cost_per_million_output or self.cost_per_million_input
        return round((3 * self.cost_per_million_input + output) / 4, 3)


class MCPServerAttributes(BaseModel):
    transport: Literal["stdio", "http", "sse"] = "stdio"
    command: str = ""
    url: str = ""
    engine: str = ""
    tools: list[str] = Field(default_factory=list)
    read_only: bool = True
    secret_refs: list[str] = Field(default_factory=list)


class EnvironmentTemplateAttributes(BaseModel):
    image: str = ""
    toolchains: list[str] = Field(default_factory=list)
    tier: str = "small"
    network: str = "none"
    packages: list[str] = Field(default_factory=list)


class PermissionSetAttributes(BaseModel):
    """A reusable bundle of permissions, so grants are chosen not written."""

    permissions: list[dict[str, Any]] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "low"
    requires_approval: bool = False


ATTRIBUTE_MODELS: dict[CatalogKind, type[BaseModel]] = {
    CatalogKind.MODEL: ModelAttributes,
    CatalogKind.MCP_SERVER: MCPServerAttributes,
    CatalogKind.ENVIRONMENT_TEMPLATE: EnvironmentTemplateAttributes,
    CatalogKind.PERMISSION_SET: PermissionSetAttributes,
}


def typed_attributes(entry: CatalogEntry) -> BaseModel | dict[str, Any]:
    """Parse an entry's attributes into its typed shape, where one exists."""
    model = ATTRIBUTE_MODELS.get(entry.kind)
    return model.model_validate(entry.attributes) if model else entry.attributes
