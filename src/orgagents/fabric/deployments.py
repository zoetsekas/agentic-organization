"""The deployment lifecycle of a tenant's workload (WS-030 M2).

A design is not a deployment (ADR-0049): publishing from the designer creates a
`requested` deployment here, and everything after that is an operator act with
a name, an actor and a reason. The state machine is explicit for one reason —
an illegal transition is refused, never coerced into the nearest legal state,
because a control plane that quietly repairs its own inputs stops being a
record of what happened.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ..ids import new_id, now_iso
from ..store import Store

DEPLOYMENTS = "fabric_deployments"


class DeploymentState(str, Enum):
    REQUESTED = "requested"
    GENERATED = "generated"
    DEPLOYED = "deployed"
    RUNNING = "running"
    STOPPED = "stopped"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class OperatorRole(str, Enum):
    """Fabric roles. Deliberately disjoint from designer roles (ADR-0049)."""

    #: The fabric itself, acting on an observation rather than on an instruction.
    AUTOMATION = "fabric_automation"
    OPERATOR = "fabric_operator"
    ADMIN = "fabric_admin"


#: Legal transitions, and who may make each one. A role is listed only where it
#: is *sufficient*; ADMIN is not implicitly an OPERATOR, because "admin can do
#: anything" is how a quarantine gets lifted by the person who caused it.
TRANSITIONS: dict[tuple[DeploymentState, DeploymentState], frozenset[OperatorRole]] = {
    (DeploymentState.REQUESTED, DeploymentState.GENERATED): frozenset(
        {OperatorRole.AUTOMATION, OperatorRole.OPERATOR}
    ),
    (DeploymentState.REQUESTED, DeploymentState.RETIRED): frozenset(
        {OperatorRole.OPERATOR, OperatorRole.ADMIN}
    ),
    (DeploymentState.GENERATED, DeploymentState.DEPLOYED): frozenset(
        {OperatorRole.OPERATOR, OperatorRole.ADMIN}
    ),
    (DeploymentState.GENERATED, DeploymentState.RETIRED): frozenset(
        {OperatorRole.OPERATOR, OperatorRole.ADMIN}
    ),
    (DeploymentState.DEPLOYED, DeploymentState.RUNNING): frozenset(
        {OperatorRole.AUTOMATION, OperatorRole.OPERATOR}
    ),
    (DeploymentState.DEPLOYED, DeploymentState.STOPPED): frozenset(
        {OperatorRole.OPERATOR, OperatorRole.ADMIN}
    ),
    (DeploymentState.DEPLOYED, DeploymentState.QUARANTINED): frozenset(
        {OperatorRole.AUTOMATION, OperatorRole.OPERATOR, OperatorRole.ADMIN}
    ),
    (DeploymentState.RUNNING, DeploymentState.STOPPED): frozenset(
        {OperatorRole.OPERATOR, OperatorRole.ADMIN}
    ),
    (DeploymentState.RUNNING, DeploymentState.QUARANTINED): frozenset(
        {OperatorRole.AUTOMATION, OperatorRole.OPERATOR, OperatorRole.ADMIN}
    ),
    (DeploymentState.STOPPED, DeploymentState.RUNNING): frozenset(
        {OperatorRole.OPERATOR, OperatorRole.ADMIN}
    ),
    (DeploymentState.STOPPED, DeploymentState.QUARANTINED): frozenset(
        {OperatorRole.AUTOMATION, OperatorRole.OPERATOR, OperatorRole.ADMIN}
    ),
    (DeploymentState.STOPPED, DeploymentState.RETIRED): frozenset(
        {OperatorRole.OPERATOR, OperatorRole.ADMIN}
    ),
    # A quarantine is lifted only downwards, and only by an admin: the way out
    # of quarantine is stop-and-investigate, never straight back to running.
    (DeploymentState.QUARANTINED, DeploymentState.STOPPED): frozenset(
        {OperatorRole.ADMIN}
    ),
    (DeploymentState.QUARANTINED, DeploymentState.RETIRED): frozenset(
        {OperatorRole.ADMIN}
    ),
}

#: Retired is terminal. Re-running a retired design is a new request, so that
#: the history of the thing that ran stays the history of the thing that ran.
TERMINAL_STATES = frozenset({DeploymentState.RETIRED})


class IllegalTransition(ValueError):
    """Raised for a transition that is not in the machine."""

    def __init__(self, source: DeploymentState, target: DeploymentState) -> None:
        super().__init__(
            f"'{source.value}' -> '{target.value}' is not a legal deployment "
            "transition"
        )
        self.source = source
        self.target = target


class TransitionDenied(PermissionError):
    """Raised for a legal transition attempted by a role that may not make it."""

    def __init__(
        self,
        source: DeploymentState,
        target: DeploymentState,
        role: OperatorRole,
        allowed: frozenset[OperatorRole],
    ) -> None:
        names = ", ".join(sorted(r.value for r in allowed))
        super().__init__(
            f"role '{role.value}' may not move a deployment from "
            f"'{source.value}' to '{target.value}'; allowed: {names}"
        )
        self.source = source
        self.target = target
        self.role = role
        self.allowed = allowed


class TransitionEvent(BaseModel):
    """One recorded move. Append-only: the history is the audit trail."""

    at: str = Field(default_factory=now_iso)
    source: DeploymentState
    target: DeploymentState
    actor: str
    role: OperatorRole
    reason: str = ""


class Deployment(BaseModel):
    """What the fabric believes about one tenant's workload."""

    id: str = Field(default_factory=lambda: new_id("dep"))
    tenant_id: str
    name: str = ""
    system_id: str = ""
    #: The spec revision this deployment was generated from; drift compares it.
    revision: str = ""
    target: str = "local"
    state: DeploymentState = DeploymentState.REQUESTED
    history: list[TransitionEvent] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


def allowed_transitions(state: DeploymentState) -> dict[DeploymentState, frozenset[OperatorRole]]:
    return {t: roles for (s, t), roles in TRANSITIONS.items() if s is state}


class DeploymentService:
    """Creates deployments and moves them, refusing anything off the machine."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def request(
        self,
        tenant_id: str,
        *,
        name: str = "",
        system_id: str = "",
        revision: str = "",
        target: str = "local",
    ) -> Deployment:
        deployment = Deployment(
            tenant_id=tenant_id,
            name=name,
            system_id=system_id,
            revision=revision,
            target=target,
        )
        return self._save(deployment)

    def get(self, deployment_id: str) -> Optional[Deployment]:
        return self.store.get(DEPLOYMENTS, deployment_id, Deployment)

    def list(self, tenant_id: Optional[str] = None) -> list[Deployment]:
        return self.store.list(DEPLOYMENTS, Deployment, parent=tenant_id)

    def transition(
        self,
        deployment_id: str,
        target: DeploymentState,
        *,
        actor: str,
        role: OperatorRole,
        reason: str = "",
    ) -> Deployment:
        deployment = self.get(deployment_id)
        if deployment is None:
            raise KeyError(f"no such deployment '{deployment_id}'")
        source = deployment.state
        allowed = TRANSITIONS.get((source, target))
        if allowed is None:
            raise IllegalTransition(source, target)
        if role not in allowed:
            raise TransitionDenied(source, target, role, allowed)
        deployment.history.append(
            TransitionEvent(
                source=source, target=target, actor=actor, role=role, reason=reason
            )
        )
        deployment.state = target
        deployment.updated_at = now_iso()
        return self._save(deployment)

    def history(self, deployment_id: str) -> list[TransitionEvent]:
        deployment = self.get(deployment_id)
        return list(deployment.history) if deployment else []

    def _save(self, deployment: Deployment) -> Deployment:
        self.store.put(
            DEPLOYMENTS, deployment, parent=deployment.tenant_id, name=deployment.name
        )
        return deployment
