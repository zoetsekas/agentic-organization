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

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional, Union, get_args, get_origin

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


# --------------------------------------------------------------------------
# Where a figure came from, and when it was last true (WS-026 M6)
# --------------------------------------------------------------------------


class FigureMethod(str, Enum):
    """How a figure got into the catalog."""

    OPERATOR = "operator_entered"   # a person typed it, citing whatever they read
    IMPORTED = "imported"           # a named source supplied it
    PLACEHOLDER = "placeholder"     # no figure at all; the row is a to-do


# A price or context window that has not been confirmed for half a year is not
# a fact, it is a memory. Six months is long enough that ordinary catalogues do
# not nag, short enough that a provider's repricing surfaces before a quarter's
# spend is committed against it.
DEFAULT_STALENESS_HORIZON_DAYS = 180


class FigureProvenance(BaseModel):
    """Where an entry's numbers came from, and when they were last confirmed.

    Attached to the entry rather than to each number: a source publishes a
    model's whole row at once, and per-field provenance would imply an
    independence the sources do not have.
    """

    method: FigureMethod = FigureMethod.OPERATOR
    source: str = ""                 # the named source, or who entered it
    source_url: str = ""
    confirmed_at: str = ""           # ISO; empty means never confirmed
    staleness_horizon_days: int = DEFAULT_STALENESS_HORIZON_DAYS
    fields: list[str] = Field(default_factory=list)   # what the source covered
    note: str = ""

    def age_days(self, *, now: Optional[str] = None) -> Optional[float]:
        """Days since the figures were last confirmed, or None if never."""
        if not self.confirmed_at:
            return None
        try:
            confirmed = datetime.fromisoformat(self.confirmed_at)
        except ValueError:
            return None
        moment = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
        if confirmed.tzinfo is None:
            confirmed = confirmed.replace(tzinfo=timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return (moment - confirmed).total_seconds() / 86400.0

    def is_stale(self, *, now: Optional[str] = None) -> bool:
        age = self.age_days(now=now)
        if age is None:
            # Never confirmed is the worst case, not an exemption: a
            # placeholder row must never read as fresh.
            return True
        return age > self.staleness_horizon_days

    def describe(self, *, now: Optional[str] = None) -> str:
        age = self.age_days(now=now)
        if age is None:
            return f"figures never confirmed ({self.method.value})"
        return (f"figures from {self.source or 'an unnamed source'} "
                f"({self.method.value}), last confirmed {int(age)} days ago, "
                f"horizon {self.staleness_horizon_days} days")


# --------------------------------------------------------------------------
# What may be edited, and what must be versioned (ADR-0062)
# --------------------------------------------------------------------------


# Editorial fields describe the entry to people. They decide nothing, so they
# may be corrected in place at any status.
EDITORIAL_FIELDS = (
    "name", "summary", "description", "owner", "tags", "documentation_url",
)

# Substantive fields decide what a design bound to this entry resolves to.
# Changing one after review changes what was reviewed, so on an approved or
# restricted entry they make a new version instead.
SUBSTANTIVE_FIELDS = ("kind", "version", "attributes")


class CatalogEvent(BaseModel):
    """One recorded mutation of an entry: who, when, and what changed."""

    action: str                      # created | updated | amended | reviewed | ...
    actor: str = ""
    at: str = Field(default_factory=now_iso)
    changes: list[str] = Field(default_factory=list)
    note: str = ""


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
    # Where the entry's figures came from and when they were last confirmed;
    # set on import, and defaulted to "an operator typed this" otherwise.
    provenance: FigureProvenance = Field(default_factory=lambda: FigureProvenance())
    # Every mutation, in order, so a correction is as traceable as a review.
    history: list[CatalogEvent] = Field(default_factory=list)

    @property
    def figure_state(self) -> str:
        """One word for how much this entry's numbers can be trusted."""
        if self.kind is not CatalogKind.MODEL:
            return "n/a"
        if self.provenance.method is FigureMethod.PLACEHOLDER:
            return "placeholder"
        return "stale" if self.provenance.is_stale() else "fresh"

    def figures_stale(self, *, now: Optional[str] = None) -> bool:
        """Whether this entry's governed figures are past their horizon.

        Only models carry figures a policy decides on; a placeholder is
        excluded because it holds no figure to trust and is already visible as
        a proposal an operator must complete.
        """
        if self.kind is not CatalogKind.MODEL:
            return False
        if self.provenance.method is FigureMethod.PLACEHOLDER:
            return False
        return self.provenance.is_stale(now=now)

    @property
    def substantively_locked(self) -> bool:
        """Whether substantive edits are refused on this entry (ADR-0062 r2)."""
        return self.status in (ApprovalStatus.APPROVED, ApprovalStatus.RESTRICTED)

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


class PluginAttributes(BaseModel):
    package: str = ""
    entrypoint: str = ""
    capabilities: list[str] = Field(default_factory=list)
    sandboxed: bool = True


class ToolAttributes(BaseModel):
    interface: Literal["function", "http", "cli"] = "function"
    endpoint: str = ""
    parameters: list[str] = Field(default_factory=list)
    side_effects: Literal["none", "read", "write"] = "read"


class SkillAttributes(BaseModel):
    instructions_url: str = ""
    applies_to: list[str] = Field(default_factory=list)
    level: Literal["basic", "advanced"] = "basic"


class GuardrailAttributes(BaseModel):
    checks: list[str] = Field(default_factory=list)
    on_violation: Literal["block", "redact", "warn", "escalate"] = "block"
    applies_to: list[str] = Field(default_factory=list)


class KnowledgeSourceAttributes(BaseModel):
    uri: str = ""
    format: str = ""
    classification: Literal["public", "internal", "confidential", "restricted"] = (
        "internal")
    refresh_interval_hours: int = 24


class WorkflowAttributes(BaseModel):
    engine: str = ""
    definition_url: str = ""
    steps: list[str] = Field(default_factory=list)
    idempotent: bool = True


class AgentTemplateAttributes(BaseModel):
    role: str = ""
    model_class: str = ""
    capabilities: list[str] = Field(default_factory=list)
    autonomy: Literal["suggest", "act_with_approval", "act"] = "act_with_approval"


class EndpointAttributes(BaseModel):
    url: str = ""
    protocol: Literal["http", "grpc", "a2a", "websocket"] = "http"
    auth: str = ""
    scopes: list[str] = Field(default_factory=list)


# Every kind has a declared attribute shape, so a form can be generated for all
# twelve rather than hand-written twelve times (ADR-0062).
ATTRIBUTE_MODELS: dict[CatalogKind, type[BaseModel]] = {
    CatalogKind.MODEL: ModelAttributes,
    CatalogKind.MCP_SERVER: MCPServerAttributes,
    CatalogKind.ENVIRONMENT_TEMPLATE: EnvironmentTemplateAttributes,
    CatalogKind.PERMISSION_SET: PermissionSetAttributes,
    CatalogKind.PLUGIN: PluginAttributes,
    CatalogKind.TOOL: ToolAttributes,
    CatalogKind.SKILL: SkillAttributes,
    CatalogKind.GUARDRAIL: GuardrailAttributes,
    CatalogKind.KNOWLEDGE_SOURCE: KnowledgeSourceAttributes,
    CatalogKind.WORKFLOW: WorkflowAttributes,
    CatalogKind.AGENT_TEMPLATE: AgentTemplateAttributes,
    CatalogKind.ENDPOINT: EndpointAttributes,
}


def _field_descriptor(name: str, field: Any) -> dict[str, Any]:
    """One attribute described well enough for a UI to render an input for it."""
    annotation = field.annotation
    origin = get_origin(annotation)
    descriptor: dict[str, Any] = {
        "name": name,
        "label": name.replace("_", " ").capitalize(),
        "type": "string",
        "choices": [],
    }
    if origin is Union:      # Optional[X]
        args = [a for a in get_args(annotation) if a is not type(None)]
        annotation = args[0] if args else str
        origin = get_origin(annotation)
        descriptor["optional"] = True
    if origin is Literal:
        descriptor["type"] = "choice"
        descriptor["choices"] = [str(a) for a in get_args(annotation)]
    elif origin in (list, set, tuple):
        item = (get_args(annotation) or (str,))[0]
        descriptor["type"] = "objects" if item is not str else "list"
    elif annotation is bool:
        descriptor["type"] = "boolean"
    elif annotation is int:
        descriptor["type"] = "integer"
    elif annotation is float:
        descriptor["type"] = "number"
    elif annotation is dict or origin is dict:
        descriptor["type"] = "objects"
    return descriptor


def attribute_schema(kind: CatalogKind) -> list[dict[str, Any]]:
    """The declared attributes of a kind, as form field descriptors.

    Derived from the pydantic shape rather than restated, so a field added to
    an attribute model appears in the UI without anyone editing the UI.
    """
    model = ATTRIBUTE_MODELS.get(kind)
    if model is None:
        return []
    return [_field_descriptor(name, field)
            for name, field in model.model_fields.items()]


def typed_attributes(entry: CatalogEntry) -> BaseModel | dict[str, Any]:
    """Parse an entry's attributes into its typed shape, where one exists."""
    model = ATTRIBUTE_MODELS.get(entry.kind)
    return model.model_validate(entry.attributes) if model else entry.attributes
