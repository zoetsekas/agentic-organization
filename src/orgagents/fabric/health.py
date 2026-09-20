"""Health and drift: belief versus observation (WS-030 M4).

The fabric holds a *belief* about each deployment — the state its lifecycle
says it is in, at the revision it was generated from. A target holds the truth.
Drift is the two disagreeing, and this module's whole job is to make the
disagreement, and the *age of the belief*, visible rather than implicit.

WS-030's sharpest warning is that a stale control plane is worse than none if
anybody trusts it. So staleness is in the model, not in a comment: an
observation carries when it was taken and how long it stays good for, a check
that has no fresh observation reports `Confidence.UNOBSERVED` or
`Confidence.STALE` and a health status of `unknown` — never `healthy` — and a
stale belief raises a `DriftSignal` in its own right.

No adapter here has ever spoken to a cloud account or a container daemon;
neither exists in this environment. `StubBackend` is a test double, and nothing
in this module should be read as a verified integration (WS-030 M5).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ..ids import now_iso
from ..store import Store
from .deployments import Deployment, DeploymentService, DeploymentState

HEALTH_CHECKS = "fabric_health_checks"

#: How long an observation is treated as describing the present. Short, because
#: the cost of a wrong "healthy" is higher than the cost of an extra poll.
DEFAULT_STALENESS_HORIZON = timedelta(minutes=5)


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    #: Nothing recent enough to say. Distinct from unhealthy on purpose.
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNOBSERVED = "unobserved"


class DriftKind(str, Enum):
    STATE_MISMATCH = "state_mismatch"
    REVISION_MISMATCH = "revision_mismatch"
    MISSING_ON_TARGET = "missing_on_target"
    UNKNOWN_TO_FABRIC = "unknown_to_fabric"
    STALE_BELIEF = "stale_belief"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class TargetObservation(BaseModel):
    """What a target reported about one deployment, and when."""

    deployment_id: str
    tenant_id: str = ""
    state: Optional[DeploymentState] = None
    revision: str = ""
    status: HealthStatus = HealthStatus.UNKNOWN
    observed_at: str = Field(default_factory=now_iso)
    detail: str = ""


class DriftSignal(BaseModel):
    """One disagreement between belief and observation."""

    deployment_id: str
    tenant_id: str = ""
    kind: DriftKind
    severity: Severity
    believed: str = ""
    observed: str = ""
    detail: str = ""
    raised_at: str = Field(default_factory=now_iso)


class HealthCheck(BaseModel):
    """The result of comparing one belief with one observation."""

    id: str
    deployment_id: str
    tenant_id: str = ""
    believed_state: DeploymentState
    believed_revision: str = ""
    observed_state: Optional[DeploymentState] = None
    observed_revision: str = ""
    status: HealthStatus = HealthStatus.UNKNOWN
    confidence: Confidence = Confidence.UNOBSERVED
    observed_at: Optional[str] = None
    #: Seconds between the observation and this check; None when unobserved.
    observation_age_seconds: Optional[float] = None
    staleness_horizon_seconds: float = DEFAULT_STALENESS_HORIZON.total_seconds()
    signals: list[DriftSignal] = Field(default_factory=list)
    checked_at: str = Field(default_factory=now_iso)

    @property
    def is_stale(self) -> bool:
        return self.confidence is not Confidence.FRESH

    @property
    def drifted(self) -> bool:
        return bool(self.signals)


@runtime_checkable
class HealthBackend(Protocol):
    """What a target adapter must provide. Injectable by construction."""

    def observe(self, deployment: Deployment) -> Optional[TargetObservation]: ...


class StubBackend:
    """An in-memory stand-in for a target. A double, not an integration.

    Tests drive it directly; `seen` lets a test model a target that has never
    heard of a deployment (`None`) versus one reporting nothing yet.
    """

    def __init__(self, observations: Optional[dict[str, TargetObservation]] = None) -> None:
        self.observations: dict[str, TargetObservation] = dict(observations or {})

    def report(self, observation: TargetObservation) -> TargetObservation:
        self.observations[observation.deployment_id] = observation
        return observation

    def forget(self, deployment_id: str) -> None:
        self.observations.pop(deployment_id, None)

    def observe(self, deployment: Deployment) -> Optional[TargetObservation]:
        return self.observations.get(deployment.id)


#: States where the fabric expects the target to know about the deployment at
#: all. Before `deployed` there is nothing out there to disagree with.
_EXPECTED_ON_TARGET = frozenset(
    {DeploymentState.DEPLOYED, DeploymentState.RUNNING, DeploymentState.STOPPED,
     DeploymentState.QUARANTINED}
)


def _parse(ts: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class HealthService:
    """Compares belief with observation and records the result."""

    def __init__(
        self,
        store: Store,
        deployments: DeploymentService,
        backend: HealthBackend,
        *,
        staleness_horizon: timedelta = DEFAULT_STALENESS_HORIZON,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store
        self.deployments = deployments
        self.backend = backend
        self.staleness_horizon = staleness_horizon
        self.clock = clock

    def check(self, deployment_id: str) -> HealthCheck:
        deployment = self.deployments.get(deployment_id)
        if deployment is None:
            raise KeyError(f"no such deployment '{deployment_id}'")
        now = self.clock()
        observation = self.backend.observe(deployment)
        check = HealthCheck(
            id=f"hc_{deployment.id}",
            deployment_id=deployment.id,
            tenant_id=deployment.tenant_id,
            believed_state=deployment.state,
            believed_revision=deployment.revision,
            staleness_horizon_seconds=self.staleness_horizon.total_seconds(),
            checked_at=now.isoformat(),
        )
        signals: list[DriftSignal] = []

        if observation is None:
            check.confidence = Confidence.UNOBSERVED
            check.status = HealthStatus.UNKNOWN
            if deployment.state in _EXPECTED_ON_TARGET:
                signals.append(
                    DriftSignal(
                        deployment_id=deployment.id,
                        tenant_id=deployment.tenant_id,
                        kind=DriftKind.MISSING_ON_TARGET,
                        severity=Severity.CRITICAL,
                        believed=deployment.state.value,
                        observed="absent",
                        detail=(
                            "the fabric believes this is on the target; the "
                            "target has never heard of it"
                        ),
                    )
                )
        else:
            observed_at = _parse(observation.observed_at)
            age = (now - observed_at).total_seconds() if observed_at else None
            check.observed_at = observation.observed_at
            check.observation_age_seconds = age
            check.observed_state = observation.state
            check.observed_revision = observation.revision
            fresh = age is not None and age <= self.staleness_horizon.total_seconds()
            check.confidence = Confidence.FRESH if fresh else Confidence.STALE
            # A stale observation may not colour the status: reporting the last
            # known good as current health is exactly the trust WS-030 warns of.
            check.status = observation.status if fresh else HealthStatus.UNKNOWN
            if not fresh:
                signals.append(
                    DriftSignal(
                        deployment_id=deployment.id,
                        tenant_id=deployment.tenant_id,
                        kind=DriftKind.STALE_BELIEF,
                        severity=Severity.WARNING,
                        believed=deployment.state.value,
                        observed=observation.status.value,
                        detail=(
                            f"last observed {age:.0f}s ago, past the "
                            f"{self.staleness_horizon.total_seconds():.0f}s "
                            "horizon; reported as unknown, not as health"
                            if age is not None
                            else "observation carries no usable timestamp"
                        ),
                    )
                )
            if observation.state is not None and observation.state != deployment.state:
                signals.append(
                    DriftSignal(
                        deployment_id=deployment.id,
                        tenant_id=deployment.tenant_id,
                        kind=DriftKind.STATE_MISMATCH,
                        severity=Severity.CRITICAL,
                        believed=deployment.state.value,
                        observed=observation.state.value,
                        detail="the target is not in the state the fabric records",
                    )
                )
            if (
                deployment.revision
                and observation.revision
                and observation.revision != deployment.revision
            ):
                signals.append(
                    DriftSignal(
                        deployment_id=deployment.id,
                        tenant_id=deployment.tenant_id,
                        kind=DriftKind.REVISION_MISMATCH,
                        severity=Severity.WARNING,
                        believed=deployment.revision,
                        observed=observation.revision,
                        detail="the target runs a different revision than was generated",
                    )
                )

        check.signals = signals
        self.store.put(HEALTH_CHECKS, check, parent=deployment.tenant_id)
        return check

    def check_tenant(self, tenant_id: str) -> list[HealthCheck]:
        return [self.check(d.id) for d in self.deployments.list(tenant_id)]

    def unknown_to_fabric(self, tenant_id: str, reported_ids: list[str]) -> list[DriftSignal]:
        """Things the target is running that the fabric has no record of.

        The other direction of drift, and the more alarming one: an unmanaged
        workload inside a tenant boundary the fabric thinks it drew.
        """
        known = {d.id for d in self.deployments.list(tenant_id)}
        return [
            DriftSignal(
                deployment_id=reported,
                tenant_id=tenant_id,
                kind=DriftKind.UNKNOWN_TO_FABRIC,
                severity=Severity.CRITICAL,
                believed="absent",
                observed=reported,
                detail="running on the target with no deployment record",
            )
            for reported in reported_ids
            if reported not in known
        ]

    def last_check(self, deployment_id: str) -> Optional[HealthCheck]:
        return self.store.get(HEALTH_CHECKS, f"hc_{deployment_id}", HealthCheck)
