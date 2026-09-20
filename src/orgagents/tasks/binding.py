"""Binding a task backend, and refusing one that cannot be an agent.

ADR-0057 rule 2: an agent acts as itself or not at all. A backend that cannot
give a non-human principal an identity is refused **at bind time**, with the
reason. It is never worked around by borrowing a person's credentials — an
agent doing work under somebody's account destroys the accountability the
pairing model exists to create, and makes the audit trail a lie.

Bind time, not first call: by the time the first call is made the deployment
is already live and the refusal has become an incident.
"""
from __future__ import annotations

from dataclasses import dataclass

from .port import BackendCapabilities, PrincipalKind, TaskPort


class BackendRefused(RuntimeError):
    """Raised at bind time for a backend that cannot satisfy rule 2 or rule 6."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"task backend '{name or '<unnamed>'}' refused: {reason}")
        self.name = name
        self.reason = reason


@dataclass(frozen=True)
class BoundTaskBackend:
    """A backend that passed the bind-time checks, and the evidence it did.

    Everything downstream takes one of these rather than a bare `TaskPort`, so
    an unchecked backend cannot reach the runtime by being passed a layer
    lower down.
    """

    port: TaskPort
    capabilities: BackendCapabilities
    tenant_id: str


def bind(port: TaskPort, *, tenant_id: str) -> BoundTaskBackend:
    """Check a backend's declared identity and tenancy, or refuse it."""
    capabilities = port.capabilities()
    name = capabilities.name

    if capabilities.principal_kind is PrincipalKind.HUMAN:
        raise BackendRefused(
            name,
            "it can only act as a human principal, and an agent never borrows "
            "a person's credentials (ADR-0057 rule 2)",
        )
    if capabilities.principal_kind is not PrincipalKind.SERVICE:
        raise BackendRefused(
            name,
            "it declares no non-human principal, so an agent could not act as "
            "itself on it (ADR-0057 rule 2)",
        )
    if not capabilities.agent_principal_id:
        raise BackendRefused(
            name,
            "it declares a service principal kind but names no principal, so "
            "there is nothing for the agent to be (ADR-0057 rule 2)",
        )
    if not capabilities.supports_non_human_assignee:
        raise BackendRefused(
            name,
            "it cannot make a non-human principal the assignee of a task, so "
            "work could only be assigned to a person (ADR-0057 rule 2)",
        )
    if not tenant_id:
        raise BackendRefused(name, "no tenant was named for this binding")
    if capabilities.tenant_id and capabilities.tenant_id != tenant_id:
        raise BackendRefused(
            name,
            f"it serves tenant '{capabilities.tenant_id}', not '{tenant_id}'; "
            "a shared instance across tenants is a cross-tenant channel "
            "(ADR-0050, rule 6)",
        )
    return BoundTaskBackend(port=port, capabilities=capabilities, tenant_id=tenant_id)
