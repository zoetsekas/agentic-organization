"""Request bodies the routes accept."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from ..models import Visibility


class CatalogEditRequest(BaseModel):
    """An editorial edit (ADR-0062 rule 1). Omitted fields are left alone."""

    name: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    tags: Optional[list[str]] = None
    documentation_url: Optional[str] = None
    note: str = ""


class CatalogAmendRequest(BaseModel):
    """A substantive edit (ADR-0062 rule 2), refused on an approved entry."""

    kind: Optional[str] = None
    version: Optional[str] = None
    attributes: Optional[dict[str, Any]] = None
    note: str = ""


class CatalogSendBackRequest(BaseModel):
    note: str = ""


class RunRequest(BaseModel):
    prompt: str
    created_by: str = "ui"
    session_id: Optional[str] = None


class ResumeRequest(BaseModel):
    response: str
    actor: str = "human"


class InstallRequest(BaseModel):
    agent_id: str


class CreateSystemRequest(BaseModel):
    workspace_id: str
    name: str
    description: str = ""
    spec: Optional[dict[str, Any]] = None


class SaveSystemRequest(BaseModel):
    spec: Optional[dict[str, Any]] = None
    layout: Optional[dict[str, Any]] = None
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    base_version: Optional[int] = None
    strategy: Optional[str] = None
    resolutions: dict[str, Any] = {}
    message: str = ""


class LockRequest(BaseModel):
    target: str = "*"
    scope: str = "component"
    note: str = ""


class WorkspaceRequest(BaseModel):
    name: str
    description: str = ""


class MemberRequest(BaseModel):
    user_id: str
    display_name: str = ""
    email: str = ""
    role: str = "viewer"


class PublishRequest(BaseModel):
    kind: str
    ref_id: str
    owner: str = ""
    tags: list[str] = []
    visibility: Visibility = Visibility.PUBLIC
    groups: list[str] = []


class OperatorActionRequest(BaseModel):
    """Why an operator acted. Recorded; not validated beyond being present."""

    reason: str = ""


class RequotaRequest(BaseModel):
    #: QuotaKind value -> {soft_limit, hard_ceiling?, unit?}
    quotas: dict[str, dict[str, Any]] = {}
    #: Omitted leaves the entitlement allow-list alone; [] empties it.
    catalog_entries: Optional[list[str]] = None
    reason: str = ""


class TenantRegistrationRequest(BaseModel):
    """What an operator supplies to mint a tenant.

    The isolation domain is absent on purpose: it is derived from the prefix
    by the fabric (ADR-0050), never asked for.
    """

    id: str
    name: str = ""
    namespace_prefix: Optional[str] = None
    entitlements: list[str] = []
    cloud_boundary: str = ""
    reason: str = ""


class OperatorGrantRequest(BaseModel):
    user_id: str
    roles: list[str] = []
    reason: str = ""
