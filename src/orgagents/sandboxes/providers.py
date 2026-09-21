"""The `SandboxProvider` seam, its four providers and the registry (ADR-0054).

No provider name belongs in `orgagents.spec`: the spec says *what* the boundary
must be, this layer says *how*. Nothing here has been run against a real
provider — there is no Docker daemon, no `sbx` binary and no `openshell` binary
in this environment.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Callable, Optional, Protocol, runtime_checkable

from . import openshell as openshell_mod
from .model import (
    CoResidency,
    ProviderCapabilities,
    Support,
    CONTAINER,
    DEFAULT_PROVIDER,
    MICROVM_SBX,
    OPENSHELL,
    TARGET_NATIVE,
    Availability,
    BoundaryStatement,
    Degradation,
    DetectionContext,
    EnvironmentFacts,
    ProviderMapping,
    SandboxResolution,
    TenantScoping,
)


@runtime_checkable
class SandboxProvider(Protocol):
    """What a sandbox provider must be able to say about itself."""

    name: str

    def detect(self, ctx: DetectionContext) -> Availability: ...

    def boundary_statement(self) -> BoundaryStatement: ...

    def map_environment(self, facts: EnvironmentFacts) -> ProviderMapping: ...

    def capabilities(self) -> ProviderCapabilities: ...


class ContainerProvider:
    """Today's behaviour: ordinary containers on a shared host kernel."""

    name = CONTAINER

    def detect(self, ctx: DetectionContext) -> Availability:
        # The portable floor is always "available": it is what everything else
        # degrades *to*, so declaring it unavailable would leave nowhere to go.
        return Availability(True, "portable floor", {"docker_socket": ctx.docker_socket})

    def boundary_statement(self) -> BoundaryStatement:
        return BoundaryStatement(
            provider=CONTAINER,
            summary=(
                "Ordinary containers on a shared host kernel. A Docker-object "
                "boundary, not a kernel or account one."
            ),
            kernel_boundary=False,
            enforces=(
                "separate filesystems, networks and named volumes per sandbox",
                "declared mounts and resource limits, as container configuration",
            ),
            does_not_enforce=(
                "a kernel boundary — a kernel escape reaches the host and every "
                "other tenant on it",
                "protection from anyone who can reach the Docker socket, which "
                "reaches every tenant on this host",
                "egress at HTTP method or path level; host-level rules only",
            ),
            tenant_scoping=TenantScoping.NAME_SCOPED_ONLY,
            tenant_note=(
                "tenants are separated by generated resource names and networks "
                "only; a host-level actor sees all of them"
            ),
        )

    def map_environment(self, facts: EnvironmentFacts) -> ProviderMapping:
        gaps = [
            "egress is applied per host, not per HTTP method and path",
            "isolation is a Docker-object boundary, so the mapping cannot claim "
            "a kernel boundary however strict the class is",
        ]
        if not facts.tenant_id:
            gaps.append(
                "no tenant id was assigned, so generated names carry no tenant "
                "prefix (ADR-0050)"
            )
        return ProviderMapping(
            provider=CONTAINER,
            environment_id=facts.environment_id,
            tenant_id=facts.tenant_id,
            settings={
                "tier": facts.tier,
                "network": facts.network,
                "egress_allowlist": list(facts.egress_allowlist),
                "mounts": list(facts.mounts),
                "persistence": facts.persistence,
                "timeout_seconds": facts.timeout_seconds,
                "secret_refs": list(facts.secret_refs),
            },
            unexpressible=tuple(gaps),
        )


    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            # A container can of course run a process. What it cannot do is put
            # the agent under a *policy engine*, because there isn't one: the
            # agent's network posture is enforced by Compose networks outside
            # the sandbox, which is why ADR-0068 rule 8 names the container
            # floor as the case where the agent runs beside its sandbox.
            hosts_agent_process=Support.NO,
            scopes_filesystem_per_agent=Support.NO,
            notes=(
                "the agent's boundary here is a Docker network, not this "
                "sandbox: there is no policy engine to place it under",
                "one filesystem per container and no per-agent partition "
                "within it, so co-resident agents would share everything",
            ),
        )

class MicroVMSbxProvider:
    """Docker Sandboxes (`sbx`): a dedicated microVM per agent, driven by a CLI.

    Docker documents no programmatic API, so driving this means invoking the
    binary — `sbx run`, `sbx exec`, with sandbox kits as OCI references. This
    module only builds the argv; nothing here has ever been executed.
    """

    name = MICROVM_SBX
    #: Docker states macOS, Windows and Ubuntu.
    SUPPORTED_OS = ("darwin", "windows", "ubuntu")

    def detect(self, ctx: DetectionContext) -> Availability:
        if "sbx" not in ctx.binaries:
            return Availability(False, "the `sbx` binary is not on PATH")
        if ctx.os_name.lower() not in self.SUPPORTED_OS:
            return Availability(
                False,
                f"Docker Sandboxes documents macOS, Windows and Ubuntu; this host "
                f"reports '{ctx.os_name}'",
            )
        return Availability(True, "`sbx` found on PATH")

    def boundary_statement(self) -> BoundaryStatement:
        return BoundaryStatement(
            provider=MICROVM_SBX,
            summary=(
                "Docker Sandboxes: a dedicated microVM per agent, which Docker "
                "describes as a \"hard security boundary from the host\". That "
                "claim is Docker's, quoted, not measured here."
            ),
            kernel_boundary=True,
            enforces=(
                "a per-agent microVM with its own kernel",
                "only the project workspace mounted into the sandbox",
                "configurable network controls at the sandbox edge",
            ),
            does_not_enforce=(
                "organization-wide network or filesystem policy — that is Docker "
                "AI Governance, a separate product",
                "egress at HTTP method or path level",
                "anything on a host outside macOS, Windows or Ubuntu",
            ),
            tenant_scoping=TenantScoping.NAME_SCOPED_ONLY,
            tenant_note=(
                "a microVM per agent separates workloads, but `sbx` has no "
                "documented tenant concept; tenancy remains ours to name"
            ),
        )

    def map_environment(self, facts: EnvironmentFacts) -> ProviderMapping:
        gaps = [
            "`sbx` mounts the project workspace; per-data-class mount scopes "
            "have no documented equivalent",
            "egress is applied per host, not per HTTP method and path",
            "driving `sbx` means invoking a CLI and parsing its output; no "
            "programmatic API is documented",
        ]
        if not facts.tenant_id:
            gaps.append("no tenant id was assigned (ADR-0050)")
        return ProviderMapping(
            provider=MICROVM_SBX,
            environment_id=facts.environment_id,
            tenant_id=facts.tenant_id,
            settings={
                "kit": self.kit_reference(facts),
                "tier": facts.tier,
                "network": facts.network,
                "egress_allowlist": list(facts.egress_allowlist),
                "workspace_mounts": list(facts.mounts),
                "persistence": facts.persistence,
                "timeout_seconds": facts.timeout_seconds,
                "run_argv": self.run_argv(facts),
            },
            unexpressible=tuple(gaps),
        )

    def kit_reference(self, facts: EnvironmentFacts) -> str:
        """The sandbox kit is an OCI reference; ADR-0053 owns which image."""
        toolchain = facts.toolchains[0] if facts.toolchains else "none"
        return f"sandbox-kit:{toolchain}"

    def run_argv(self, facts: EnvironmentFacts) -> list[str]:
        return ["sbx", "run", "--kit", self.kit_reference(facts)]

    def exec_argv(self, sandbox: str, command: list[str]) -> list[str]:
        return ["sbx", "exec", sandbox, *command]


    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            # A developer-machine CLI with no documented server-side API is not
            # something to hand a long-running agent process to.
            hosts_agent_process=Support.NO,
            scopes_filesystem_per_agent=Support.NO,
            notes=(
                "driven by a CLI with no documented programmatic lifecycle, so "
                "there is nothing to place a long-running agent process under",
                "a microVM per agent is the shape on offer; co-residency is not "
                "something this provider expresses",
            ),
        )

class OpenShellProvider:
    """NVIDIA OpenShell: a gateway control plane and a policy engine.

    Apache 2.0 and **alpha / pre-1.0**. ADR-0054 names it a candidate, not a
    dependency. The policy mapping lives in `orgagents.sandboxes.openshell`;
    the wire format deliberately does not exist yet.
    """

    name = OPENSHELL

    def detect(self, ctx: DetectionContext) -> Availability:
        if "openshell" not in ctx.binaries:
            return Availability(False, "the `openshell` binary is not on PATH")
        if not ctx.openshell_gateway_url:
            return Availability(False, "no OpenShell gateway address is configured")
        return Availability(True, "`openshell` found and a gateway is configured")

    def boundary_statement(self) -> BoundaryStatement:
        return BoundaryStatement(
            provider=OPENSHELL,
            summary=(
                "NVIDIA OpenShell: a gateway control plane over sandbox lifecycle "
                "with a policy engine over filesystem, process, network and "
                "provider credentials. Alpha software, pre-1.0."
            ),
            kernel_boundary=False,  # microVM or container; the ADR does not let us claim either
            enforces=(
                "egress through a policy proxy at HTTP method and path level, "
                "denied requests returning policy_denied",
                "filesystem and process policy locked at sandbox creation",
                "network and provider policy reloadable without recreating the sandbox",
                "credentials injected as environment variables at runtime, never "
                "written to the sandbox filesystem",
            ),
            does_not_enforce=(
                "a guaranteed kernel boundary — OpenShell runs microVM or "
                "container sandboxes, and which one is in force is a deployment "
                "choice this seam does not read",
                "any stability commitment: it is alpha, pre-1.0, and its API "
                "will move",
            ),
            tenant_scoping=TenantScoping.NOT_EXPRESSIBLE,
            tenant_note=(
                "nothing documented binds an OpenShell sandbox to a tenant, so "
                "the tenant boundary is not enforced by this provider and must "
                "not be assumed (ADR-0050)"
            ),
            maturity="alpha (pre-1.0)",
        )

    def map_environment(self, facts: EnvironmentFacts) -> ProviderMapping:
        policy = openshell_mod.map_environment(facts)
        return ProviderMapping(
            provider=OPENSHELL,
            environment_id=facts.environment_id,
            tenant_id=facts.tenant_id,
            settings={"policy_domains": policy.as_dict()},
            unexpressible=policy.gaps,
        )

    def policy_for(self, facts: EnvironmentFacts) -> "openshell_mod.OpenShellPolicy":
        return openshell_mod.map_environment(facts)


    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            # A gateway control plane over sandbox lifecycle is exactly a thing
            # that runs workloads inside sandboxes it governs.
            hosts_agent_process=Support.YES,
            # Filesystem policy is documented as locked at sandbox creation.
            # Whether it can be partitioned *per agent within* one sandbox is
            # not something the published SDK says, and inventing an answer
            # here would be the invented schema `openshell.py` already refuses
            # to write.
            scopes_filesystem_per_agent=Support.UNKNOWN,
            notes=(
                "the policy engine covers the agent's own egress, filesystem "
                "and injected credentials, not only the code it executes",
                "per-agent filesystem partitioning inside one sandbox is "
                "plausible from the policy model and is not documented; until "
                "it is confirmed, co-residency degrades to one agent",
            ),
        )

class TargetNativeProvider:
    """The cloud target's own isolation. Mostly a declaration, not code."""

    name = TARGET_NATIVE

    def detect(self, ctx: DetectionContext) -> Availability:
        if ctx.target.startswith("terraform:") or ctx.target == "kubernetes":
            return Availability(True, f"target '{ctx.target}' owns its own isolation")
        return Availability(
            False,
            f"target '{ctx.target}' has no native sandbox isolation to delegate to",
        )

    def boundary_statement(self) -> BoundaryStatement:
        return BoundaryStatement(
            provider=TARGET_NATIVE,
            summary=(
                "Whatever the generated cloud infrastructure already isolates "
                "with. This seam neither configures nor inspects it."
            ),
            kernel_boundary=False,  # unknown to us, so not claimed
            enforces=(
                "nothing added by this seam; the generated infrastructure keeps "
                "owning the boundary",
            ),
            does_not_enforce=(
                "any boundary this platform can state on the target's behalf — "
                "read the target's own mapping report",
            ),
            tenant_scoping=TenantScoping.DELEGATED_TO_TARGET,
            tenant_note=(
                "tenant isolation is the generated infrastructure's to enforce "
                "and to report (ADR-0050)"
            ),
        )

    def map_environment(self, facts: EnvironmentFacts) -> ProviderMapping:
        gaps = [
            "the environment class is handed to the target unchanged; this seam "
            "cannot state what the target enforces",
        ]
        if not facts.tenant_id:
            gaps.append("no tenant id was assigned (ADR-0050)")
        return ProviderMapping(
            provider=TARGET_NATIVE,
            environment_id=facts.environment_id,
            tenant_id=facts.tenant_id,
            settings={
                "delegated": True,
                "tier": facts.tier,
                "network": facts.network,
                "egress_allowlist": list(facts.egress_allowlist),
                "mounts": list(facts.mounts),
            },
            unexpressible=tuple(gaps),
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            # The generated infrastructure already runs the agent; that is the
            # whole point of this provider.
            hosts_agent_process=Support.YES,
            scopes_filesystem_per_agent=Support.DELEGATED,
            notes=(
                "the generated target owns both answers; this seam makes no "
                "claim on its behalf, and a delegated answer is not a yes",
            ),
        )


_REGISTRY: dict[str, SandboxProvider] = {}

def register_provider(provider: SandboxProvider) -> None:
    _REGISTRY[provider.name] = provider


def get_provider(name: str) -> SandboxProvider:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown sandbox provider '{name}'; known: {', '.join(provider_names())}"
        ) from None


def provider_names() -> list[str]:
    return sorted(_REGISTRY)


def _install_defaults() -> None:
    for provider in (
        ContainerProvider(),
        MicroVMSbxProvider(),
        OpenShellProvider(),
        TargetNativeProvider(),
    ):
        register_provider(provider)


_install_defaults()


_DEGRADATIONS: list[Degradation] = []


def record_degradation(degradation: Degradation) -> None:
    """Keep degradations retrievable: a fallback nobody can read is a silent one."""
    _DEGRADATIONS.append(degradation)


def degradations() -> list[Degradation]:
    return list(_DEGRADATIONS)


def clear_degradations() -> None:
    _DEGRADATIONS.clear()


def detect_context(
    *,
    target: str = "local",
    which: Optional[Callable[[str], Optional[str]]] = None,
    os_name: Optional[str] = None,
    openshell_gateway_url: str = "",
) -> DetectionContext:
    """Probe this machine. Injectable so tests need not own the real PATH."""
    import os
    import platform as platform_mod
    import shutil

    which = which or shutil.which
    binaries = tuple(b for b in ("sbx", "openshell") if which(b))
    return DetectionContext(
        target=target,
        os_name=os_name or platform_mod.system().lower(),
        binaries=binaries,
        docker_socket=os.path.exists("/var/run/docker.sock"),
        openshell_gateway_url=openshell_gateway_url
        or os.environ.get("OPENSHELL_GATEWAY_URL", ""),
    )


def resolve_provider(
    requested: Optional[str],
    facts: EnvironmentFacts,
    ctx: Optional[DetectionContext] = None,
) -> SandboxResolution:
    """Resolve the provider in force, degrading loudly to `container`.

    ADR-0054 §3: an environment that believes it has a kernel boundary and does
    not is worse than one that never claimed it.
    """
    requested = requested or DEFAULT_PROVIDER
    ctx = ctx or detect_context()
    try:
        provider = get_provider(requested)
        availability = provider.detect(ctx)
        reason = availability.reason
        ok = availability.available
    except KeyError as exc:
        provider, ok, reason = get_provider(DEFAULT_PROVIDER), False, str(exc)

    degradation = None
    if not ok:
        degradation = Degradation(
            requested=requested,
            used=DEFAULT_PROVIDER,
            reason=reason or "provider unavailable",
            environment_id=facts.environment_id,
            tenant_id=facts.tenant_id,
        )
        record_degradation(degradation)
        provider = get_provider(DEFAULT_PROVIDER)

    return SandboxResolution(
        requested=requested,
        provider_name=provider.name,
        boundary=provider.boundary_statement(),
        mapping=provider.map_environment(facts),
        capabilities=provider.capabilities(),
        degradation=degradation,
    )


def resolve_co_residency(
    resolution: SandboxResolution,
    agents: Sequence[str],
) -> CoResidency:
    """Decide how many sandbox environments a set of agents actually gets.

    This answers ADR-0068 **rule 6** only. Rules 4 and 5 — one tenant, and the
    standing org chart connecting every pair — are decided above this seam, at
    the phase gate, because they are properties of the organization and not of
    the provider. A caller that has not checked them must not read a permissive
    answer here as approval.

    Where the provider cannot scope a filesystem per agent, the request
    degrades to one agent per sandbox and the degradation is recorded, because
    two agents sharing an unscoped filesystem is a lateral path the org model
    does not govern.
    """
    requested = tuple(agents)
    caps = resolution.capabilities
    limit = caps.max_agents_per_sandbox if caps else 1

    if limit is None or len(requested) <= 1:
        return CoResidency(
            environment_id=resolution.mapping.environment_id,
            requested=requested,
            groups=(requested,) if requested else (),
        )

    answer = caps.scopes_filesystem_per_agent if caps else Support.NO
    reason = (
        f"provider '{resolution.provider_name}' cannot scope a filesystem per "
        f"agent ({answer.value}), so {len(requested)} co-resident agents would "
        "share one"
    )
    degradation = Degradation(
        requested=f"{len(requested)} agents per sandbox",
        used="1 agent per sandbox",
        reason=reason,
        environment_id=resolution.mapping.environment_id,
        tenant_id=resolution.mapping.tenant_id,
    )
    record_degradation(degradation)
    return CoResidency(
        environment_id=resolution.mapping.environment_id,
        requested=requested,
        groups=tuple((a,) for a in requested),
        degraded=True,
        reason=reason,
    )


def capabilities(name: str) -> ProviderCapabilities:
    """What this provider can do at the environment level (ADR-0068)."""
    return get_provider(name).capabilities()


def boundary_statement(name: str) -> BoundaryStatement:
    """What this provider actually enforces, for a README or mapping report."""
    return get_provider(name).boundary_statement()
