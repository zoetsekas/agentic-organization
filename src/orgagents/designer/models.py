"""Designer domain: workspaces, systems, layout, locks and revisions.

The designer is a multi-user editor over many agentic systems (ADR-0031). Its
own objects are deliberately separate from the System Spec: a spec describes an
organization of agents, while these describe *who is editing what, where it is
stored, and who holds the pen right now*.

One rule shapes the model: **canvas layout never enters the spec** (ADR-0034).
Node positions are how a person reads a design; they are not part of what gets
compiled, and mixing them would make two specs that deploy identically differ
byte-for-byte because someone moved a box.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from ..ids import new_id, now_iso


class UserRole(str, Enum):
    """A person's role *in the designer*, not in the agent organization.

    Distinct from `HumanRole` (ADR-0026), which is how a person relates to an
    agent. This is platform access: who may read, edit, review or administer a
    design (ADR-0032).
    """

    OWNER = "owner"        # everything, including deleting the workspace
    ADMIN = "admin"        # manage members and settings
    EDITOR = "editor"      # create and change systems
    REVIEWER = "reviewer"  # comment and approve; no edits
    VIEWER = "viewer"      # read only


class SystemStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class NodeKind(str, Enum):
    """What a box on the canvas represents."""

    TEAM = "team"
    AGENT = "agent"
    SUBAGENT = "subagent"
    ROLE = "role"
    CAPABILITY = "capability"
    ENVIRONMENT = "environment"
    CHANNEL = "channel"
    TRIGGER = "trigger"
    KNOWLEDGE = "knowledge"
    ENDPOINT = "endpoint"
    MEMORY = "memory_namespace"
    WORKFLOW = "workflow"
    DATA_CLASS = "data_class"
    POLICY = "policy"
    NOTE = "note"


class CanvasNode(BaseModel):
    """Where a component sits on the canvas. Pure presentation."""

    id: str                      # matches the spec object's id
    kind: NodeKind
    x: float = 0
    y: float = 0
    width: float = 220
    height: float = 90
    collapsed: bool = False
    color: Optional[str] = None
    note: str = ""


class CanvasEdge(BaseModel):
    """A drawn relationship. Derived from the spec, positioned by the canvas."""

    id: str = Field(default_factory=lambda: new_id("edge"))
    source: str
    target: str
    kind: Literal[
        "reports_to", "member_of", "delegate", "consult", "notify", "escalate",
        "uses", "triggers", "reads"
    ] = "reports_to"
    label: str = ""


class Layout(BaseModel):
    """The canvas: node positions, drawn edges and the viewport."""

    nodes: dict[str, CanvasNode] = Field(default_factory=dict)
    edges: list[CanvasEdge] = Field(default_factory=list)
    viewport: dict[str, float] = Field(
        default_factory=lambda: {"x": 0.0, "y": 0.0, "zoom": 1.0}
    )
    updated_at: str = Field(default_factory=now_iso)


class SystemRecord(BaseModel):
    """One agentic system under design, with its spec, binding and layout."""

    id: str = Field(default_factory=lambda: new_id("sys"))
    workspace_id: str = "default"
    name: str
    description: str = ""
    status: SystemStatus = SystemStatus.DRAFT
    spec: dict[str, Any] = Field(default_factory=dict)
    binding: Optional[dict[str, Any]] = None
    layout: Layout = Field(default_factory=Layout)
    tags: list[str] = Field(default_factory=list)
    # Optimistic concurrency: every accepted write increments this (ADR-0033).
    version: int = 1
    created_by: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_by: str = ""
    updated_at: str = Field(default_factory=now_iso)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "version": self.version,
            "tags": self.tags,
            "agents": _count_agents(self.spec),
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


def _count_agents(spec: dict[str, Any]) -> int:
    def walk(team: dict[str, Any]) -> int:
        return len(team.get("members", [])) + sum(
            walk(child) for child in team.get("teams", [])
        )

    org = spec.get("organization")
    return walk(org) if isinstance(org, dict) else 0


class Revision(BaseModel):
    """An immutable snapshot taken on every accepted write."""

    id: str = Field(default_factory=lambda: new_id("rev"))
    system_id: str
    version: int
    spec: dict[str, Any] = Field(default_factory=dict)
    binding: Optional[dict[str, Any]] = None
    layout: Layout = Field(default_factory=Layout)
    author: str = ""
    message: str = ""
    created_at: str = Field(default_factory=now_iso)


class Member(BaseModel):
    user_id: str
    display_name: str = ""
    email: str = ""
    role: UserRole = UserRole.VIEWER
    added_at: str = Field(default_factory=now_iso)


class Workspace(BaseModel):
    """A container for systems and the people who may work on them."""

    id: str = Field(default_factory=lambda: new_id("ws"))
    name: str
    description: str = ""
    members: list[Member] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)

    def member(self, user_id: str) -> Optional[Member]:
        return next((m for m in self.members if m.user_id == user_id), None)


class LockScope(str, Enum):
    """How much of a system a lock covers (ADR-0033)."""

    SYSTEM = "system"        # the whole design
    COMPONENT = "component"  # one node, e.g. an agent or a team


class Lock(BaseModel):
    id: str = Field(default_factory=lambda: new_id("lck"))
    system_id: str
    scope: LockScope = LockScope.COMPONENT
    target: str = "*"              # component id, or "*" for the whole system
    holder: str = ""
    holder_name: str = ""
    acquired_at: str = Field(default_factory=now_iso)
    expires_at: str = ""
    note: str = ""

    def covers(self, target: str) -> bool:
        return self.scope is LockScope.SYSTEM or self.target == target


class DesignerSettings(BaseModel):
    """How this designer installation behaves (ADR-0031)."""

    persistence: Literal["filesystem", "relational", "memory"] = "relational"
    storage_path: str = "./designer-data"
    # Concurrency (ADR-0033).
    concurrency: Literal["optimistic", "locking", "hybrid"] = "hybrid"
    lock_ttl_seconds: int = 900
    lock_break_requires: UserRole = UserRole.ADMIN
    default_merge_strategy: Literal["reject", "merge"] = "merge"
    max_revisions: int = 100
    # Access (ADR-0032, ADR-0044). The mode is explicit and visible here:
    # `trusted_proxy` means an authenticating proxy owns identity and we read
    # its headers; `oidc` means we verify ID tokens ourselves and a bare header
    # authenticates nothing; `none` is single-user local use.
    auth_mode: Literal["trusted_proxy", "oidc", "none"] = "trusted_proxy"
    oidc_issuer: str = ""
    oidc_audiences: list[str] = Field(default_factory=list)
    oidc_jwks_uri: str = ""
    oidc_jwks_ttl_seconds: int = 300
    oidc_groups_claim: str = "groups"
    oidc_clock_skew_seconds: int = 60
    oidc_max_age_seconds: Optional[int] = None
    oidc_require_nonce: bool = False
    # Group -> role, or workspace id -> {group -> role}. Anything not named
    # here grants nothing.
    oidc_group_roles: dict[str, Any] = Field(default_factory=dict)
    default_role: UserRole = UserRole.VIEWER

    @field_validator("auth_mode", mode="before")
    @classmethod
    def _legacy_header_mode(cls, value: Any) -> Any:
        """`header` was the old name for what is now trusted-proxy mode."""
        return "trusted_proxy" if value == "header" else value
    require_review_before_publish: bool = True
    # Presentation defaults for new canvases.
    snap_to_grid: int = 10
    autosave_seconds: int = 0      # 0 disables autosave
    updated_at: str = Field(default_factory=now_iso)
    updated_by: str = ""


class Conflict(BaseModel):
    """One place where two edits disagree (ADR-0033)."""

    path: str
    base: Any = None
    ours: Any = None
    theirs: Any = None
    kind: Literal["value", "add_add", "edit_delete", "delete_edit"] = "value"

    def describe(self) -> str:
        return f"{self.path}: yours={self.ours!r} theirs={self.theirs!r}"
