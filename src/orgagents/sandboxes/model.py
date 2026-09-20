"""Shared types for the sandbox provider seam (ADR-0054).

Nothing in this package is verified against a running provider: this
environment has no Docker daemon, no `sbx` binary and no `openshell` binary, so
every capability claim here is a restatement of a vendor's documentation, not a
measurement. That is why `BoundaryStatement` carries `verified=False`
everywhere — a reader of a generated README should know the difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

CONTAINER = "container"
MICROVM_SBX = "microvm_sbx"
OPENSHELL = "openshell"
TARGET_NATIVE = "target_native"

#: The portable floor. ADR-0054 §3: anything else degrades to this.
DEFAULT_PROVIDER = CONTAINER


class TenantScoping(str, Enum):
    """How faithfully a provider can express "this sandbox is one tenant's".

    ADR-0050 puts the tenant boundary above data classification and calls it
    absolute, so a provider that cannot express it must say so rather than be
    assumed safe.
    """

    #: The provider itself keeps one tenant's sandbox away from another's.
    ENFORCED = "enforced"
    #: Only resource naming separates tenants; a host-level actor sees all.
    NAME_SCOPED_ONLY = "name_scoped_only"
    #: The provider has no notion of tenancy at all.
    NOT_EXPRESSIBLE = "not_expressible"
    #: Someone else owns it — the generated cloud infrastructure.
    DELEGATED_TO_TARGET = "delegated_to_target"


@dataclass(frozen=True)
class EnvironmentFacts:
    """The environment class (ADR-0009) as the seam needs to see it.

    Deliberately a plain structure rather than `spec.model.EnvironmentClass`:
    no provider name may enter the spec layer, and the seam must not drag the
    spec's types into the binding layer in the other direction either.
    """

    environment_id: str
    tier: str = "minimal"
    network: str = "none"
    egress_allowlist: tuple[str, ...] = ()
    mounts: tuple[str, ...] = ()
    persistence: str = "ephemeral"
    timeout_seconds: int = 300
    secret_refs: tuple[str, ...] = ()
    toolchains: tuple[str, ...] = ()
    #: Assigned by the fabric (ADR-0050), never declared by a spec.
    tenant_id: str = ""

    @classmethod
    def from_environment_class(
        cls, env: Any, *, tenant_id: str = ""
    ) -> "EnvironmentFacts":
        """Adapt a `spec.model.EnvironmentClass` without importing it."""

        def val(x: Any) -> Any:
            return getattr(x, "value", x)

        return cls(
            environment_id=getattr(env, "id", ""),
            tier=str(val(getattr(env, "tier", "minimal"))),
            network=str(val(getattr(env, "network", "none"))),
            egress_allowlist=tuple(getattr(env, "egress_allowlist", ()) or ()),
            mounts=tuple(getattr(env, "mounts", ()) or ()),
            persistence=str(val(getattr(env, "persistence", "ephemeral"))),
            timeout_seconds=int(getattr(env, "timeout_seconds", 300) or 300),
            secret_refs=tuple(getattr(env, "secret_refs", ()) or ()),
            toolchains=tuple(
                str(val(t)) for t in (getattr(env, "toolchains", ()) or ())
            ),
            tenant_id=tenant_id,
        )


@dataclass(frozen=True)
class BoundaryStatement:
    """What a provider actually enforces, in words a README can print.

    `enforces` and `does_not_enforce` are both required on purpose: a statement
    that only lists strengths is the overstatement ADR-0054 §3 warns about.
    """

    provider: str
    summary: str
    kernel_boundary: bool
    enforces: tuple[str, ...]
    does_not_enforce: tuple[str, ...]
    tenant_scoping: TenantScoping
    tenant_note: str
    #: Always False here; see the module docstring.
    verified: bool = False
    maturity: str = "stable"

    def as_text(self) -> str:
        lines = [f"sandbox provider: {self.provider} ({self.maturity})", self.summary]
        lines += [f"  enforces: {e}" for e in self.enforces]
        lines += [f"  does not enforce: {d}" for d in self.does_not_enforce]
        lines.append(f"  tenant isolation: {self.tenant_scoping.value} — {self.tenant_note}")
        lines.append(
            "  not verified here: no provider was run in this environment"
            if not self.verified
            else "  verified against a running provider"
        )
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "summary": self.summary,
            "kernel_boundary": self.kernel_boundary,
            "enforces": list(self.enforces),
            "does_not_enforce": list(self.does_not_enforce),
            "tenant_scoping": self.tenant_scoping.value,
            "tenant_note": self.tenant_note,
            "verified": self.verified,
            "maturity": self.maturity,
        }


@dataclass(frozen=True)
class Availability:
    """Result of probing for a provider on this machine."""

    available: bool
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderMapping:
    """An environment class expressed in one provider's terms.

    `unexpressible` is the honest half: what our model asked for that this
    provider cannot carry, or what the provider offers that our model cannot
    say. Both directions are gaps a mapping report must print.
    """

    provider: str
    environment_id: str
    tenant_id: str
    settings: dict[str, Any] = field(default_factory=dict)
    unexpressible: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "environment": self.environment_id,
            "tenant": self.tenant_id,
            "settings": dict(self.settings),
            "unexpressible": list(self.unexpressible),
        }


@dataclass(frozen=True)
class DetectionContext:
    """What availability detection is allowed to look at.

    Injected rather than read from the process so tests can describe a machine
    that does not exist here — which is every machine with `sbx` on it.
    """

    target: str = "local"
    os_name: str = "linux"
    binaries: tuple[str, ...] = ()
    docker_socket: bool = False
    openshell_gateway_url: str = ""


@dataclass(frozen=True)
class Degradation:
    """A record that a requested provider was not the one used."""

    requested: str
    used: str
    reason: str
    environment_id: str = ""
    tenant_id: str = ""

    def as_text(self) -> str:
        return (
            f"sandbox provider '{self.requested}' degraded to '{self.used}': {self.reason}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "used": self.used,
            "reason": self.reason,
            "environment": self.environment_id,
            "tenant": self.tenant_id,
        }


@dataclass(frozen=True)
class SandboxResolution:
    """The provider in force for one environment class, and why.

    This is the single object the local target and the runtime need: the name
    to print, the boundary to state, the mapping to emit and the degradation to
    confess.
    """

    requested: str
    provider_name: str
    boundary: BoundaryStatement
    mapping: ProviderMapping
    degradation: Optional[Degradation] = None

    @property
    def degraded(self) -> bool:
        return self.degradation is not None

    def report_lines(self) -> list[str]:
        lines = self.boundary.as_text().splitlines()
        if self.degradation is not None:
            lines.insert(1, f"  {self.degradation.as_text()}")
        lines += [f"  cannot express: {g}" for g in self.mapping.unexpressible]
        return lines

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "provider": self.provider_name,
            "degraded": self.degraded,
            "degradation": self.degradation.as_dict() if self.degradation else None,
            "boundary": self.boundary.as_dict(),
            "mapping": self.mapping.as_dict(),
        }
