"""Common services the fabric offers every tenant (WS-030 M1).

ADR-0049 makes sharing a *listed fabric decision*. The danger it guards against
is sharing by accident: two tenants that happen to read the same catalog row,
or write to the same telemetry index, are sharing a failure domain and a
channel whether or not anybody decided they should. So a service in this
registry that is marked shared must say, in the record itself, what crosses the
tenant boundary and why — and the registry refuses to hold one that does not.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ..ids import new_id, now_iso
from ..store import Store

SERVICES = "fabric_services"


@runtime_checkable
class TenantLike(Protocol):
    """The narrow view of a tenant that the operational modules need.

    The tenancy core (WS-028) owns the real model; depending on it through a
    protocol keeps this package compilable and testable on its own.
    """

    id: str
    name: str


class ServiceKind(str, Enum):
    CATALOG = "catalog"
    OBSERVABILITY = "observability"
    RECORD_LAYER = "record_layer"
    IDENTITY = "identity"


class Direction(str, Enum):
    """Which way something crosses the tenant boundary."""

    FABRIC_TO_TENANT = "fabric_to_tenant"
    TENANT_TO_FABRIC = "tenant_to_fabric"
    TENANT_TO_TENANT = "tenant_to_tenant"


class Exposure(BaseModel):
    """One named thing that crosses a tenant boundary, and why it may."""

    what: str
    direction: Direction
    why: str
    # Named so an auditor can ask the obvious question without reading code.
    visible_to_other_tenants: bool = False


class CommonService(BaseModel):
    """A fabric service, with its sharing stated rather than inferred."""

    id: str = Field(default_factory=lambda: new_id("svc"))
    name: str
    kind: ServiceKind
    summary: str
    shared: bool
    exposes: list[Exposure] = Field(default_factory=list)
    # What a tenant loses if this service is down; a shared service is a shared
    # failure domain and the record should say so out loud.
    failure_impact: str = ""
    registered_at: str = Field(default_factory=now_iso)

    def cross_tenant_exposures(self) -> list[Exposure]:
        return [e for e in self.exposes if e.visible_to_other_tenants]


class ServiceRegistryError(ValueError):
    """Raised when a service record does not state its sharing honestly."""


def _check(service: CommonService) -> None:
    if service.shared and not service.exposes:
        raise ServiceRegistryError(
            f"'{service.name}' is shared but lists nothing that crosses the "
            "tenant boundary; sharing is a listed decision (ADR-0049)"
        )
    if not service.shared and service.exposes:
        raise ServiceRegistryError(
            f"'{service.name}' is not shared but lists exposures; one of the "
            "two is wrong"
        )
    for exposure in service.exposes:
        if len(exposure.why.strip()) < 10:
            raise ServiceRegistryError(
                f"'{service.name}' exposes '{exposure.what}' without a reason"
            )
    if service.shared and not service.failure_impact.strip():
        raise ServiceRegistryError(
            f"'{service.name}' is shared but does not say what breaks with it"
        )


#: The services the fabric offers every tenant. Adding a row here is a design
#: decision; the checks above are what stop it from being a typo.
FABRIC_COMMON_SERVICES: tuple[CommonService, ...] = (
    CommonService(
        id="svc_catalog",
        name="Catalog of building blocks",
        kind=ServiceKind.CATALOG,
        summary=(
            "One curated catalog of skills, plugins, workflows and approved "
            "models, read by every tenant's design and compile path (WS-027)."
        ),
        shared=True,
        failure_impact=(
            "No tenant can resolve a building block; compiles fail closed "
            "rather than falling back to an unreviewed local copy."
        ),
        exposes=[
            Exposure(
                what="catalog entry metadata and version",
                direction=Direction.FABRIC_TO_TENANT,
                why=(
                    "a tenant must resolve the blocks its design names, and a "
                    "per-tenant copy would fork review and leave known-bad "
                    "versions in use"
                ),
                visible_to_other_tenants=True,
            ),
            Exposure(
                what="usage counts per catalog entry",
                direction=Direction.TENANT_TO_FABRIC,
                why=(
                    "the fabric deprecates an entry only when it knows who is "
                    "still on it; counts are aggregated and carry no payload"
                ),
                visible_to_other_tenants=False,
            ),
        ],
    ),
    CommonService(
        id="svc_observability",
        name="Observability sinks",
        kind=ServiceKind.OBSERVABILITY,
        summary=(
            "Traces, metrics and audit events from every tenant land in fabric "
            "sinks so an operator can answer 'is it healthy' in one place "
            "(WS-010)."
        ),
        shared=True,
        failure_impact=(
            "The fabric goes blind: health and drift fall back to belief "
            "alone, which health.py reports as unobserved rather than healthy."
        ),
        exposes=[
            Exposure(
                what="trace, metric and audit records tagged with tenant id",
                direction=Direction.TENANT_TO_FABRIC,
                why=(
                    "operations need cross-tenant aggregates; the tenant tag "
                    "is what keeps a query from reading across the boundary"
                ),
                visible_to_other_tenants=False,
            ),
        ],
    ),
    CommonService(
        id="svc_records",
        name="Record layer",
        kind=ServiceKind.RECORD_LAYER,
        summary=(
            "Decisions and workstreams (ADR-0001) describing the platform all "
            "tenants run on, plus the operational record each tenant feeds "
            "(WS-014)."
        ),
        shared=True,
        failure_impact=(
            "Operators lose the written reason for a platform behaviour; "
            "running tenants are unaffected."
        ),
        exposes=[
            Exposure(
                what="platform decision and workstream records",
                direction=Direction.FABRIC_TO_TENANT,
                why=(
                    "the rules a tenant is held to should be readable by the "
                    "people held to them"
                ),
                visible_to_other_tenants=True,
            ),
        ],
    ),
    CommonService(
        id="svc_identity",
        name="Identity",
        kind=ServiceKind.IDENTITY,
        summary=(
            "One issuer for operator and designer principals (ADR-0047). "
            "Workload identities inside a tenant are minted per tenant and are "
            "deliberately not shared (ADR-0050)."
        ),
        shared=True,
        failure_impact=(
            "Nobody can authenticate to the fabric; already-running tenants "
            "keep running on their own workload identities."
        ),
        exposes=[
            Exposure(
                what="human principal assertions (issuer, subject, roles)",
                direction=Direction.FABRIC_TO_TENANT,
                why=(
                    "an operator is one person across tenants and an audit "
                    "trail that cannot join them is not an audit trail"
                ),
                visible_to_other_tenants=False,
            ),
        ],
    ),
)


class ServiceRegistry:
    """The listed common services, persisted through the document store."""

    def __init__(self, store: Store, *, bootstrap: bool = True) -> None:
        self.store = store
        if bootstrap and store.count(SERVICES) == 0:
            for service in FABRIC_COMMON_SERVICES:
                self.register(service)

    def register(self, service: CommonService) -> CommonService:
        _check(service)
        self.store.put(SERVICES, service, name=service.name)
        return service

    def get(self, service_id: str) -> Optional[CommonService]:
        return self.store.get(SERVICES, service_id, CommonService)

    def list(self) -> list[CommonService]:
        return self.store.list(SERVICES, CommonService)

    def shared(self) -> list[CommonService]:
        return [s for s in self.list() if s.shared]

    def boundary_report(self) -> list[dict[str, str]]:
        """Every crossing, flattened — the answer to 'what is shared, and why'."""
        rows: list[dict[str, str]] = []
        for service in sorted(self.list(), key=lambda s: s.name):
            for exposure in service.exposes:
                rows.append(
                    {
                        "service": service.name,
                        "kind": service.kind.value,
                        "what": exposure.what,
                        "direction": exposure.direction.value,
                        "why": exposure.why,
                        "visible_to_other_tenants": str(
                            exposure.visible_to_other_tenants
                        ).lower(),
                    }
                )
        return rows
