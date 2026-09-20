"""Our model of NVIDIA OpenShell's four policy domains, and nothing more.

OpenShell (Apache 2.0, alpha/pre-1.0) runs a gateway control plane with a
policy engine over four domains: **filesystem** and **process**, locked at
sandbox creation, and **network** and **providers**, hot-reloadable. Credentials
are injected as environment variables at runtime and never written to the
sandbox filesystem, and egress is enforced by a proxy at HTTP method and path
level (L7) — a denial comes back as
``{"error":"policy_denied","detail":"POST /repos/... not permitted by policy"}``.

The published Python SDK's README documents no class names, no function
signatures and no policy YAML schema. So this module models the mapping in
*our* types and stops there. Serialization to OpenShell's wire format lives
behind `OpenShellAdapter`, which refuses to guess — an invented schema that
looks authoritative is worse than a stated gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import EnvironmentFacts

#: Hot-reloadable at runtime, per OpenShell's documentation.
HOT_RELOADABLE_DOMAINS = ("network", "providers")
#: Locked when the sandbox is created.
LOCKED_AT_CREATION_DOMAINS = ("filesystem", "process")


@dataclass(frozen=True)
class FilesystemPolicy:
    """Mount scopes, locked at sandbox creation."""

    readable: tuple[str, ...] = ()
    writable: tuple[str, ...] = ()
    persistent: bool = False


@dataclass(frozen=True)
class ProcessPolicy:
    """Process limits, locked at sandbox creation."""

    tier: str = "minimal"
    timeout_seconds: int = 300


@dataclass(frozen=True)
class NetworkPolicy:
    """Egress, hot-reloadable and enforced at L7 by OpenShell's proxy.

    Our environment class states hosts only, so `method_path_rules` is always
    empty: the field exists to name the expressiveness we are *not* using.
    """

    posture: str = "none"
    allowed_hosts: tuple[str, ...] = ()
    method_path_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderCredentialPolicy:
    """Credential references, injected as environment variables at runtime."""

    secret_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpenShellPolicy:
    """The four domains together, plus what our model could not say."""

    filesystem: FilesystemPolicy
    process: ProcessPolicy
    network: NetworkPolicy
    providers: ProviderCredentialPolicy
    tenant_id: str = ""
    gaps: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """Our own shape, for tests and mapping reports — NOT OpenShell's."""
        return {
            "filesystem": {
                "readable": list(self.filesystem.readable),
                "writable": list(self.filesystem.writable),
                "persistent": self.filesystem.persistent,
            },
            "process": {
                "tier": self.process.tier,
                "timeout_seconds": self.process.timeout_seconds,
            },
            "network": {
                "posture": self.network.posture,
                "allowed_hosts": list(self.network.allowed_hosts),
                "method_path_rules": list(self.network.method_path_rules),
            },
            "providers": {"secret_refs": list(self.providers.secret_refs)},
            "tenant": self.tenant_id,
            "gaps": list(self.gaps),
        }


#: Gaps between our environment class and OpenShell's policy engine. Stated on
#: every mapping so a report cannot quietly imply parity.
UNEXPRESSIBLE: tuple[str, ...] = (
    "OpenShell's network policy is hot-reloadable; our environment class is "
    "static and is resolved once at compile time, so a policy change after "
    "sandbox creation has no representation in the spec",
    "OpenShell enforces egress at HTTP method and path level (L7); our egress "
    "allowlist names hosts only, so the mapping is coarser than the engine",
    "OpenShell's filesystem and process domains lock at sandbox creation; our "
    "model has no notion of which of its fields are re-negotiable",
    "the policy document's field names and the SDK's call signatures are not "
    "documented, so nothing here is serialized to OpenShell's wire format",
)


def map_environment(facts: EnvironmentFacts) -> OpenShellPolicy:
    """Express one environment class in OpenShell's four domains."""
    gaps = UNEXPRESSIBLE
    if not facts.tenant_id:
        gaps = gaps + (
            "no tenant id was assigned, so this policy scopes nothing to a "
            "tenant (ADR-0050)",
        )
    persistent = facts.persistence != "ephemeral"
    return OpenShellPolicy(
        filesystem=FilesystemPolicy(
            readable=facts.mounts,
            writable=facts.mounts if persistent else (),
            persistent=persistent,
        ),
        process=ProcessPolicy(tier=facts.tier, timeout_seconds=facts.timeout_seconds),
        network=NetworkPolicy(
            posture=facts.network,
            allowed_hosts=() if facts.network == "none" else facts.egress_allowlist,
        ),
        providers=ProviderCredentialPolicy(secret_refs=facts.secret_refs),
        tenant_id=facts.tenant_id,
        gaps=gaps,
    )


class OpenShellAdapter:
    """The narrow boundary where our model would become OpenShell's wire format.

    TODO(openshell-schema): before implementing either method, confirm against a
    real OpenShell release — not this code, and not a model's recollection:
      1. the policy YAML's top-level key names and the schema of each of the
         four domains (filesystem, process, network, providers);
      2. how an egress rule expresses method+path, and whether a host-only rule
         is even valid;
      3. how a sandbox is bound to a tenant, if the gateway has such a concept
         at all;
      4. the Python SDK's entry point, client construction and the signatures
         behind `sandbox create` / `policy set` / `provider create`.
    Until then the documented CLI surface is the only thing we would drive:
      openshell sandbox create -- <agent>; openshell sandbox connect;
      openshell sandbox list; openshell policy set <name> --policy file.yaml;
      openshell policy get <name>; openshell provider create --type <type>
      --from-existing; openshell sandbox provider attach <sandbox> <provider>.
    """

    def to_policy_document(self, policy: OpenShellPolicy) -> str:
        raise NotImplementedError(
            "OpenShell's policy schema is not documented; see "
            "TODO(openshell-schema) in orgagents.sandboxes.openshell"
        )

    def apply(self, policy: OpenShellPolicy, sandbox: str) -> None:
        raise NotImplementedError(
            "driving `openshell policy set` needs the confirmed schema; see "
            "TODO(openshell-schema) in orgagents.sandboxes.openshell"
        )
