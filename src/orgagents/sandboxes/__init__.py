"""Pluggable sandbox execution providers (ADR-0054).

The spec keeps describing *what* the boundary must be (ADR-0009); a provider
decides *how*. `container` is the default and the portable floor, and anything
unavailable degrades to it loudly.

Nothing in this package has been executed against a real provider: this
environment has no Docker daemon, no `sbx` binary and no `openshell` binary.
"""

from .model import (
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
from .openshell import OpenShellAdapter, OpenShellPolicy
from .model import (
    CoResidency,
    ProviderCapabilities,
    Support,
)
from .providers import (
    capabilities,
    resolve_co_residency,
    ContainerProvider,
    MicroVMSbxProvider,
    OpenShellProvider,
    SandboxProvider,
    TargetNativeProvider,
    boundary_statement,
    clear_degradations,
    degradations,
    detect_context,
    get_provider,
    provider_names,
    record_degradation,
    register_provider,
    resolve_provider,
)

__all__ = [
    "CONTAINER",
    "MICROVM_SBX",
    "OPENSHELL",
    "TARGET_NATIVE",
    "DEFAULT_PROVIDER",
    "Availability",
    "BoundaryStatement",
    "Degradation",
    "DetectionContext",
    "EnvironmentFacts",
    "ProviderMapping",
    "SandboxResolution",
    "ProviderCapabilities",
    "CoResidency",
    "Support",
    "capabilities",
    "resolve_co_residency",
    "TenantScoping",
    "SandboxProvider",
    "ContainerProvider",
    "MicroVMSbxProvider",
    "OpenShellProvider",
    "TargetNativeProvider",
    "OpenShellAdapter",
    "OpenShellPolicy",
    "register_provider",
    "get_provider",
    "provider_names",
    "resolve_provider",
    "boundary_statement",
    "detect_context",
    "degradations",
    "record_degradation",
    "clear_degradations",
]
