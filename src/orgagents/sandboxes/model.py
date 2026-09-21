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


class Support(str, Enum):
    """A provider's answer to a capability question (ADR-0068).

    Three states and not a boolean, because "we have never confirmed this"
    is different from "this is not offered" — the same distinction the
    evaluation gate draws between not-evaluated and failed. Both are treated
    conservatively; only one of them is a reason to go and find out.
    """

    #: Documented and modelled here.
    YES = "yes"
    #: Documented as absent, or structurally impossible for this provider.
    NO = "no"
    #: Plausible from the vendor's documentation and never confirmed.
    UNKNOWN = "unknown"
    #: Someone else owns the answer — the generated cloud infrastructure.
    DELEGATED = "delegated"

    @property
    def is_affirmative(self) -> bool:
        """Only an outright yes lets a caller rely on the capability.

        `UNKNOWN` and `DELEGATED` both read as no here. A sandbox that assumes
        an unconfirmed partition is exactly the overstatement ADR-0054 §3 warns
        about; the difference from `NO` is what the report says, not what the
        platform does.
        """
        return self is Support.YES


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can do at the *environment* level (ADR-0068).

    Two questions, because rules 6 and 8 both turn on them and neither was
    previously answerable:

    * **Can it host the agent process?** If not, the agent runs beside its
      sandbox and its own boundary is whatever the target gives it — a Docker
      network, today — rather than the provider's policy engine (rule 8).
    * **Can it scope a filesystem per agent?** If not, co-residency degrades to
      one agent per sandbox, because two agents sharing an unscoped filesystem
      is a lateral path the org model does not govern (rule 6).

    Nothing here is measured. See the module docstring.
    """

    hosts_agent_process: Support
    scopes_filesystem_per_agent: Support
    #: Why, in a sentence a generated README can print. Required when either
    #: answer is not an outright yes.
    notes: tuple[str, ...] = ()
    verified: bool = False

    @property
    def max_agents_per_sandbox(self) -> Optional[int]:
        """`1` when co-residency cannot be scoped; `None` means no limit here.

        `None` is not permission to co-locate: rules 4 and 5 (one tenant, and
        the standing org chart connecting every pair) are decided above this
        seam and are not this object's business.
        """
        return None if self.scopes_filesystem_per_agent.is_affirmative else 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "hosts_agent_process": self.hosts_agent_process.value,
            "scopes_filesystem_per_agent": self.scopes_filesystem_per_agent.value,
            "max_agents_per_sandbox": self.max_agents_per_sandbox,
            "notes": list(self.notes),
            "verified": self.verified,
        }

    def as_text(self) -> str:
        lines = [
            f"  hosts the agent process: {self.hosts_agent_process.value}",
            f"  filesystem scoped per agent: {self.scopes_filesystem_per_agent.value}",
        ]
        if self.max_agents_per_sandbox == 1:
            lines.append("  agents per sandbox environment: 1 (co-residency unavailable)")
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


@dataclass(frozen=True)
class CoResidency:
    """Which agents actually share one sandbox environment, and why (ADR-0068).

    A design may ask for several. What it gets depends on whether the provider
    can scope their filesystems apart; where it cannot, this records the
    degradation rather than quietly putting them together anyway.
    """

    environment_id: str
    requested: tuple[str, ...]
    #: Groups of agents that end up sharing one sandbox environment.
    groups: tuple[tuple[str, ...], ...]
    degraded: bool = False
    reason: str = ""

    @property
    def shares_process_namespace(self) -> bool:
        """True when any group holds more than one agent.

        Nothing in this model partitions a process namespace, so a group of
        two or more is a boundary the statement must declare in words
        (ADR-0068 rule 7).
        """
        return any(len(g) > 1 for g in self.groups)

    def as_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment_id,
            "requested": list(self.requested),
            "groups": [list(g) for g in self.groups],
            "degraded": self.degraded,
            "reason": self.reason,
            "shares_process_namespace": self.shares_process_namespace,
        }


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
    capabilities: Optional[ProviderCapabilities] = None
    degradation: Optional[Degradation] = None

    @property
    def degraded(self) -> bool:
        return self.degradation is not None

    @property
    def hosts_agent_process(self) -> bool:
        """Whether the agent runs inside its sandbox or beside it (rule 8)."""
        return (
            self.capabilities is not None
            and self.capabilities.hosts_agent_process.is_affirmative
        )

    def report_lines(self) -> list[str]:
        lines = self.boundary.as_text().splitlines()
        if self.degradation is not None:
            lines.insert(1, f"  {self.degradation.as_text()}")
        if self.capabilities is not None:
            lines += self.capabilities.as_text().splitlines()
            if not self.hosts_agent_process:
                lines.append(
                    "  the agent process runs beside this sandbox, not inside it: "
                    "its own boundary is whatever the target provides"
                )
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
            "capabilities": (
                self.capabilities.as_dict() if self.capabilities else None
            ),
            "hosts_agent_process": self.hosts_agent_process,
        }
