"""Bindings — where implementation choices are allowed to live.

The System Spec says *what* a system needs; a binding says *how* that is
realized for one target (ADR-0004). Vendor names, images, SDKs, providers and
resource types belong here and nowhere else, which is what keeps a spec
deployable to a laptop and to three clouds without being rewritten.

A binding may only produce something **at least as restrictive** as the spec it
binds: `orgagents.compiler` re-checks network posture, mounts and permissions
after applying one.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ModelBinding(BaseModel):
    """Which model serves an agent loop."""

    provider: str = "anthropic"
    model: str = "claude-opus-5"
    subagent_model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 8192


class RuntimeBinding(BaseModel):
    """Which agent framework executes the loop (ADR-0013)."""

    adapter: str = "echo"        # langchain_deepagents | openai_agents_sdk | ...
    options: dict[str, Any] = Field(default_factory=dict)


class EnvironmentBinding(BaseModel):
    """How an abstract environment class becomes a concrete workspace."""

    environment: str                      # EnvironmentClass id
    image: str = "python:3.11-slim"
    packages: list[str] = Field(default_factory=list)
    cpu: str = "1"
    memory: str = "2Gi"
    disk: str = "10Gi"
    accelerator: Optional[str] = None
    node_selector: dict[str, str] = Field(default_factory=dict)


class CapabilityBinding(BaseModel):
    """How an abstract capability becomes a concrete MCP server mount."""

    capability: str                       # Capability id
    server_name: str
    transport: str = "stdio"              # stdio | http | sse
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    engine: Optional[str] = None          # for relational capabilities
    dsn_secret_ref: Optional[str] = None  # a reference, never a value
    options: dict[str, Any] = Field(default_factory=dict)


class ChannelBinding(BaseModel):
    channel: str                          # ChannelSpec id or ChannelClass value
    provider: str = "internal"
    address: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class InfrastructureBinding(BaseModel):
    """Provider-side choices for the neutral resource set (ADR-0012)."""

    provider: str = "local"               # local | gcp | aws | azure
    region: str = ""
    project: str = ""
    state_backend: str = ""
    network: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class TargetBinding(BaseModel):
    """Everything one target needs beyond the spec."""

    target: str
    model: ModelBinding = Field(default_factory=ModelBinding)
    runtime: RuntimeBinding = Field(default_factory=RuntimeBinding)
    infrastructure: InfrastructureBinding = Field(default_factory=InfrastructureBinding)
    environments: list[EnvironmentBinding] = Field(default_factory=list)
    capabilities: list[CapabilityBinding] = Field(default_factory=list)
    channels: list[ChannelBinding] = Field(default_factory=list)
    secrets_backend: str = "environment"
    observability_sink: str = "otel_collector"
    # Per-agent overrides of the runtime/model binding.
    agent_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def environment_binding(self, env_id: str) -> Optional[EnvironmentBinding]:
        return next((e for e in self.environments if e.environment == env_id), None)

    def capability_binding(self, cap_id: str) -> Optional[CapabilityBinding]:
        return next((c for c in self.capabilities if c.capability == cap_id), None)


class Binding(BaseModel):
    """A binding document: one or more target bindings for a spec."""

    spec: str = ""                        # spec name this binding applies to
    version: str = "0.1.0"
    targets: list[TargetBinding] = Field(default_factory=list)

    def for_target(self, target: str) -> Optional[TargetBinding]:
        exact = next((t for t in self.targets if t.target == target), None)
        if exact:
            return exact
        # `terraform:gcp` falls back to a `terraform` binding.
        family = target.split(":")[0]
        return next((t for t in self.targets if t.target == family), None)


def default_binding(target: str) -> TargetBinding:
    """A usable binding for a spec that ships without one."""
    provider = target.split(":")[1] if ":" in target else "local"
    return TargetBinding(
        target=target,
        runtime=RuntimeBinding(adapter="echo"),
        infrastructure=InfrastructureBinding(provider=provider),
    )
