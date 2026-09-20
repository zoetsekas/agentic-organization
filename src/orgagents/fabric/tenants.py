"""Tenants and the isolation domains the fabric assigns them (ADR-0050).

A design never declares its own tenancy. The fabric mints a `Tenant`, gives it
a namespace prefix, and every generated artifact is qualified with that prefix
so two tenants compiling the same spec collide on nothing.

The prefix is the load-bearing part. It ends up inside Docker object names,
Compose project names, Terraform identifiers and DNS labels, and those four
have different rules; what is allowed here is their *intersection*, enforced
once at registration rather than discovered at apply time.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ..compiler.ir import TenantIR
from ..store import Store

TENANTS = "fabric_tenants"

# The intersection of four naming rules:
#   * DNS label        — lowercase alphanumerics and hyphens, alnum at both ends
#   * Compose project  — lowercase alphanumerics, hyphen and underscore, must
#                        start with a lowercase letter or digit
#   * Docker object    — [a-zA-Z0-9][a-zA-Z0-9_.-]*
#   * Terraform ident  — letters, digits, underscore and dash, never leading digit
# A leading letter satisfies all four; underscores and dots are dropped because
# DNS labels reject them; hyphens survive everywhere.
PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,18}[a-z0-9]$")
MIN_PREFIX = 3
MAX_PREFIX = 20

# Names that would make a generated artifact ambiguous or collide with a
# platform-owned object rather than a tenant-owned one.
RESERVED_PREFIXES = frozenset({
    "orgagents", "fabric", "designer", "platform", "system", "default",
    "control", "egress", "isolated", "admin", "root", "local", "terraform",
})


class TenantStatus(str, Enum):
    """Lifecycle of a tenant in the fabric."""

    PENDING = "pending"      # registered, nothing deployed yet
    ACTIVE = "active"        # may compile and deploy
    SUSPENDED = "suspended"  # kept, but refused a compile
    RETIRED = "retired"      # domain must never be reused


class PrefixError(ValueError):
    """A namespace prefix that is unsafe, reserved or already taken."""


class IsolationDomain(BaseModel):
    """Everything the fabric keeps apart for one tenant.

    Assigned, never requested: the names here are derived from the prefix, so a
    tenant cannot widen its own domain by asking for a different one.
    """

    id: str
    network: str
    identity_realm: str
    secret_scope: str
    data_scope: str
    # The project / account / subscription a cloud target deploys into. Empty
    # until the fabric has one; the Terraform targets take it as a variable.
    cloud_boundary: str = ""

    @classmethod
    def derive(cls, prefix: str) -> "IsolationDomain":
        return cls(
            id=f"{prefix}-domain",
            network=f"{prefix}-net",
            identity_realm=f"{prefix}-identities",
            secret_scope=f"{prefix}-secrets",
            data_scope=f"{prefix}-data",
        )


class Tenant(BaseModel):
    """One agentic organization running on the fabric."""

    id: str
    name: str
    namespace_prefix: str
    isolation_domain: IsolationDomain
    entitlements: list[str] = Field(default_factory=list)
    status: TenantStatus = TenantStatus.PENDING
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def qualify(self, name: str) -> str:
        """Prefix a generated name, idempotently."""
        return name if self.owns(name) else f"{self.namespace_prefix}-{name}"

    def owns(self, identifier: str) -> bool:
        return identifier.startswith(f"{self.namespace_prefix}-")

    def entitled_to(self, entitlement: str) -> bool:
        return entitlement in self.entitlements

    def may_deploy(self) -> bool:
        return self.status is TenantStatus.ACTIVE

    def to_ir(self) -> TenantIR:
        """The slice of a tenant that compilation needs."""
        return TenantIR(
            id=self.id,
            namespace_prefix=self.namespace_prefix,
            isolation_domain=self.isolation_domain.id,
            entitlements=list(self.entitlements),
            cloud_boundary=self.isolation_domain.cloud_boundary,
        )


def normalize_prefix(value: str) -> str:
    """Best-effort tidy-up of a human-typed prefix; still validated afterwards."""
    return re.sub(r"[^a-z0-9-]+", "-", value.strip().lower()).strip("-")


def validate_prefix(prefix: str, *, taken: Optional[set[str]] = None) -> str:
    """Raise unless `prefix` is safe in every generated identifier."""
    if not PREFIX_PATTERN.match(prefix):
        raise PrefixError(
            f"namespace prefix '{prefix}' is not safe in a generated identifier: "
            f"use {MIN_PREFIX}-{MAX_PREFIX} characters, lowercase letters, digits "
            f"and hyphens, starting with a letter and ending with a letter or digit"
        )
    if "--" in prefix:
        # Reserved by IDN ("xn--"), and a reader cannot tell one prefix from two.
        raise PrefixError(f"namespace prefix '{prefix}' must not contain '--'")
    if prefix in RESERVED_PREFIXES:
        raise PrefixError(f"namespace prefix '{prefix}' is reserved by the platform")
    for other in taken or set():
        # A prefix that is a prefix of another prefix makes "does this name
        # belong to me" ambiguous, which is the one question isolation asks.
        if prefix == other or prefix.startswith(f"{other}-") or other.startswith(
            f"{prefix}-"
        ):
            raise PrefixError(
                f"namespace prefix '{prefix}' collides with '{other}'"
            )
    return prefix


class TenantRegistry:
    """The fabric's record of who exists and what domain they were assigned."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- reads -------------------------------------------------------------

    def list(self) -> list[Tenant]:
        return sorted(self.store.list(TENANTS, Tenant, limit=10_000), key=lambda t: t.id)

    def get(self, tenant_id: str) -> Optional[Tenant]:
        return self.store.get(TENANTS, tenant_id, Tenant)

    def require(self, tenant_id: str) -> Tenant:
        tenant = self.get(tenant_id)
        if tenant is None:
            raise KeyError(f"no such tenant: {tenant_id}")
        return tenant

    def by_prefix(self, prefix: str) -> Optional[Tenant]:
        return next((t for t in self.list() if t.namespace_prefix == prefix), None)

    def prefixes(self, *, exclude: Optional[str] = None) -> set[str]:
        """Every prefix in the fabric, optionally minus one tenant's own.

        Compilation uses this to spot a spec reaching at somebody else's names.
        """
        return {t.namespace_prefix for t in self.list() if t.id != exclude}

    # -- writes ------------------------------------------------------------

    def register(
        self,
        *,
        id: str,
        name: str,
        namespace_prefix: Optional[str] = None,
        entitlements: Optional[list[str]] = None,
        cloud_boundary: str = "",
        status: TenantStatus = TenantStatus.ACTIVE,
    ) -> Tenant:
        """Assign a tenant its isolation domain. The tenant does not choose it."""
        if self.get(id) is not None:
            raise PrefixError(f"tenant '{id}' already exists")
        prefix = normalize_prefix(namespace_prefix or id)
        # A retired tenant keeps its prefix forever: reusing it would let a new
        # tenant inherit whatever the old one left behind.
        validate_prefix(prefix, taken=self.prefixes())
        domain = IsolationDomain.derive(prefix)
        domain.cloud_boundary = cloud_boundary
        tenant = Tenant(
            id=id,
            name=name,
            namespace_prefix=prefix,
            isolation_domain=domain,
            entitlements=sorted(entitlements or []),
            status=status,
        )
        self.store.put(TENANTS, tenant, name=tenant.name)
        return tenant

    def set_status(self, tenant_id: str, status: TenantStatus) -> Tenant:
        tenant = self.require(tenant_id)
        tenant.status = status
        self.store.put(TENANTS, tenant, name=tenant.name)
        return tenant

    def set_cloud_boundary(self, tenant_id: str, boundary: str) -> Tenant:
        tenant = self.require(tenant_id)
        tenant.isolation_domain.cloud_boundary = boundary
        self.store.put(TENANTS, tenant, name=tenant.name)
        return tenant

    def retire(self, tenant_id: str) -> Tenant:
        return self.set_status(tenant_id, TenantStatus.RETIRED)
