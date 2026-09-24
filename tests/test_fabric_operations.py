"""Fabric operations: common services, lifecycle, quotas, health and drift.

Every backend here is a stub. These tests prove the contracts hold, not that
the fabric can operate a real target — there is no cloud account and no
container daemon in this environment (WS-030 M5).
"""
from datetime import datetime, timedelta, timezone

import pytest

from orgagents.fabric.deployments import (
    DeploymentService,
    DeploymentState,
    IllegalTransition,
    OperatorRole,
    TransitionDenied,
    allowed_transitions,
)
from orgagents.fabric.health import (
    Confidence,
    DriftKind,
    HealthService,
    HealthStatus,
    StubBackend,
    TargetObservation,
)
from orgagents.fabric.quotas import (
    UNBOUNDED,
    Decision,
    Entitlements,
    NotEntitled,
    Quota,
    QuotaKind,
    QuotaService,
    evaluate,
)
from orgagents.fabric.services import (
    FABRIC_COMMON_SERVICES,
    CommonService,
    Direction,
    Exposure,
    ServiceKind,
    ServiceRegistry,
    ServiceRegistryError,
)
from orgagents.store import Store

TENANT = "tenant_finance"


@pytest.fixture()
def store(tmp_path) -> Store:
    return Store(tmp_path / "fabric.db")


@pytest.fixture()
def deployments(store) -> DeploymentService:
    return DeploymentService(store)


# -- M1 common services ----------------------------------------------------


def test_registry_bootstraps_the_listed_services(store):
    registry = ServiceRegistry(store)
    kinds = {s.kind for s in registry.list()}
    assert kinds == {
        ServiceKind.CATALOG,
        ServiceKind.OBSERVABILITY,
        ServiceKind.RECORD_LAYER,
        ServiceKind.IDENTITY,
    }
    assert len(registry.list()) == len(FABRIC_COMMON_SERVICES)


def test_every_shared_service_names_what_crosses_the_boundary(store):
    registry = ServiceRegistry(store)
    for service in registry.shared():
        assert service.exposes, f"{service.name} is shared but lists nothing"
        assert service.failure_impact.strip()
        for exposure in service.exposes:
            assert exposure.what and exposure.why
            assert exposure.direction in Direction


def test_boundary_report_flattens_every_crossing(store):
    rows = ServiceRegistry(store).boundary_report()
    assert rows
    assert {r["service"] for r in rows} >= {"Catalog of building blocks"}
    cross_tenant = [r for r in rows if r["visible_to_other_tenants"] == "true"]
    # The catalog is read by every tenant; that has to be visible as such.
    assert any(r["kind"] == "catalog" for r in cross_tenant)


def test_sharing_without_a_stated_exposure_is_refused(store):
    registry = ServiceRegistry(store)
    with pytest.raises(ServiceRegistryError):
        registry.register(
            CommonService(
                name="Secret sauce",
                kind=ServiceKind.CATALOG,
                summary="shared by accident",
                shared=True,
                failure_impact="everything",
            )
        )


def test_an_exposure_without_a_reason_is_refused(store):
    registry = ServiceRegistry(store)
    with pytest.raises(ServiceRegistryError):
        registry.register(
            CommonService(
                name="Chatty sink",
                kind=ServiceKind.OBSERVABILITY,
                summary="",
                shared=True,
                failure_impact="blind",
                exposes=[
                    Exposure(
                        what="everything",
                        direction=Direction.TENANT_TO_TENANT,
                        why="why not",
                    )
                ],
            )
        )


def test_unshared_service_may_not_list_exposures(store):
    with pytest.raises(ServiceRegistryError):
        ServiceRegistry(store).register(
            CommonService(
                name="Per-tenant secrets",
                kind=ServiceKind.IDENTITY,
                summary="",
                shared=False,
                exposes=[
                    Exposure(
                        what="workload identity",
                        direction=Direction.FABRIC_TO_TENANT,
                        why="a reason long enough to pass the check",
                    )
                ],
            )
        )


# -- M2 deployment lifecycle ----------------------------------------------


def test_the_happy_path_records_its_history(deployments):
    dep = deployments.request(TENANT, name="finance", revision="r1")
    assert dep.state is DeploymentState.REQUESTED
    deployments.transition(
        dep.id, DeploymentState.GENERATED, actor="ci", role=OperatorRole.AUTOMATION
    )
    deployments.transition(
        dep.id, DeploymentState.DEPLOYED, actor="ada", role=OperatorRole.OPERATOR,
        reason="change 41",
    )
    final = deployments.transition(
        dep.id, DeploymentState.RUNNING, actor="ci", role=OperatorRole.AUTOMATION
    )
    assert final.state is DeploymentState.RUNNING
    history = deployments.history(dep.id)
    assert [e.target for e in history] == [
        DeploymentState.GENERATED,
        DeploymentState.DEPLOYED,
        DeploymentState.RUNNING,
    ]
    assert history[1].actor == "ada" and history[1].reason == "change 41"
    assert all(e.at for e in history)


def test_an_illegal_transition_is_refused_not_coerced(deployments):
    dep = deployments.request(TENANT)
    with pytest.raises(IllegalTransition):
        deployments.transition(
            dep.id, DeploymentState.RUNNING, actor="ada", role=OperatorRole.ADMIN
        )
    # State and history are untouched by the refusal.
    after = deployments.get(dep.id)
    assert after.state is DeploymentState.REQUESTED
    assert after.history == []


def test_quarantine_never_returns_straight_to_running(deployments):
    dep = deployments.request(TENANT)
    deployments.transition(
        dep.id, DeploymentState.GENERATED, actor="ci", role=OperatorRole.AUTOMATION
    )
    deployments.transition(
        dep.id, DeploymentState.DEPLOYED, actor="ada", role=OperatorRole.OPERATOR
    )
    deployments.transition(
        dep.id, DeploymentState.RUNNING, actor="ci", role=OperatorRole.AUTOMATION
    )
    deployments.transition(
        dep.id, DeploymentState.QUARANTINED, actor="ci", role=OperatorRole.AUTOMATION,
        reason="guardrail breach",
    )
    with pytest.raises(IllegalTransition):
        deployments.transition(
            dep.id, DeploymentState.RUNNING, actor="root", role=OperatorRole.ADMIN
        )
    assert set(allowed_transitions(DeploymentState.QUARANTINED)) == {
        DeploymentState.STOPPED,
        DeploymentState.RETIRED,
    }


def test_a_legal_transition_by_the_wrong_role_is_denied(deployments):
    dep = deployments.request(TENANT)
    deployments.transition(
        dep.id, DeploymentState.GENERATED, actor="ci", role=OperatorRole.AUTOMATION
    )
    deployments.transition(
        dep.id, DeploymentState.DEPLOYED, actor="ada", role=OperatorRole.OPERATOR
    )
    deployments.transition(
        dep.id, DeploymentState.QUARANTINED, actor="ci", role=OperatorRole.AUTOMATION
    )
    with pytest.raises(TransitionDenied) as excinfo:
        deployments.transition(
            dep.id, DeploymentState.STOPPED, actor="ada", role=OperatorRole.OPERATOR
        )
    assert excinfo.value.role is OperatorRole.OPERATOR
    assert deployments.get(dep.id).state is DeploymentState.QUARANTINED


def test_retired_is_terminal(deployments):
    dep = deployments.request(TENANT)
    deployments.transition(
        dep.id, DeploymentState.RETIRED, actor="ada", role=OperatorRole.OPERATOR
    )
    assert deployments.get(dep.id).is_terminal
    assert allowed_transitions(DeploymentState.RETIRED) == {}
    for target in DeploymentState:
        if target is DeploymentState.RETIRED:
            continue
        with pytest.raises(IllegalTransition):
            deployments.transition(
                dep.id, target, actor="root", role=OperatorRole.ADMIN
            )


def test_deployments_persist_and_list_per_tenant(store, deployments):
    deployments.request(TENANT, name="a")
    deployments.request("tenant_other", name="b")
    assert {d.name for d in deployments.list(TENANT)} == {"a"}
    assert len(DeploymentService(store).list()) == 2


# -- M3 quotas and entitlements -------------------------------------------


@pytest.fixture()
def quotas(store) -> QuotaService:
    service = QuotaService(store)
    service.set_entitlements(
        Entitlements(
            tenant_id=TENANT,
            quotas={
                QuotaKind.DEPLOYMENTS: Quota(
                    kind=QuotaKind.DEPLOYMENTS, soft_limit=2, hard_ceiling=3
                ),
                QuotaKind.TOKENS_PER_DAY: Quota(
                    kind=QuotaKind.TOKENS_PER_DAY, soft_limit=100
                ),
            },
            catalog_entries=["skill.summarize", "workflow.reconcile"],
        )
    )
    return service


def test_a_tenant_may_only_use_entitled_catalog_entries(quotas):
    assert quotas.may_use(TENANT, "skill.summarize")
    assert not quotas.may_use(TENANT, "skill.exfiltrate")
    # Authorization refuses; it does not degrade.
    with pytest.raises(NotEntitled):
        quotas.require_entry(TENANT, "skill.exfiltrate")


def test_an_empty_allow_list_entitles_nothing(store):
    service = QuotaService(store)
    service.set_entitlements(Entitlements(tenant_id="tenant_new"))
    assert not service.may_use("tenant_new", "skill.summarize")


def test_the_soft_limit_is_inclusive_and_the_ceiling_refuses(quotas):
    first = quotas.consume(TENANT, QuotaKind.DEPLOYMENTS)
    assert first.decision is Decision.ALLOW
    at_limit = quotas.consume(TENANT, QuotaKind.DEPLOYMENTS)
    assert at_limit.decision is Decision.ALLOW and at_limit.usage_after == 2

    over_soft = quotas.consume(TENANT, QuotaKind.DEPLOYMENTS)
    assert over_soft.decision is Decision.ALLOW_DEGRADED
    assert over_soft.allowed and over_soft.breached
    assert over_soft.usage_after == 3  # the hard ceiling is reachable

    refused = quotas.consume(TENANT, QuotaKind.DEPLOYMENTS)
    assert refused.decision is Decision.REFUSE
    assert not refused.allowed
    # A refusal consumes nothing.
    assert refused.usage_after == 3

    kinds = [b["decision"] for b in quotas.breaches(TENANT)]
    assert kinds == ["allow_degraded", "refuse"]


def test_a_quota_without_a_ceiling_never_refuses(quotas):
    verdict = quotas.consume(TENANT, QuotaKind.TOKENS_PER_DAY, amount=10_000)
    assert verdict.decision is Decision.ALLOW_DEGRADED
    assert verdict.allowed
    assert quotas.entitlements(TENANT).quotas[QuotaKind.TOKENS_PER_DAY].refuses is False


def test_an_unset_quota_is_served_and_reported(quotas):
    verdict = quotas.check(TENANT, QuotaKind.AGENTS)
    assert verdict.decision is Decision.ALLOW_DEGRADED
    assert "no quota is set" in verdict.reason


def test_release_gives_capacity_back(quotas):
    quotas.consume(TENANT, QuotaKind.DEPLOYMENTS)
    quotas.consume(TENANT, QuotaKind.DEPLOYMENTS)
    assert quotas.release(TENANT, QuotaKind.DEPLOYMENTS) == 1
    assert quotas.consume(TENANT, QuotaKind.DEPLOYMENTS).decision is Decision.ALLOW


def test_evaluate_is_a_pure_readable_policy():
    quota = Quota(kind=QuotaKind.AGENTS, soft_limit=5, hard_ceiling=UNBOUNDED)
    assert evaluate(quota, 4).decision is Decision.ALLOW
    assert evaluate(quota, 5).decision is Decision.ALLOW_DEGRADED
    assert evaluate(quota, 500).decision is Decision.ALLOW_DEGRADED


def test_an_unknown_tenant_has_no_entitlements(quotas):
    with pytest.raises(KeyError):
        quotas.check("tenant_ghost", QuotaKind.AGENTS)


# -- M4 health and drift ---------------------------------------------------


def _running(deployments, revision="r1"):
    dep = deployments.request(TENANT, name="finance", revision=revision)
    deployments.transition(
        dep.id, DeploymentState.GENERATED, actor="ci", role=OperatorRole.AUTOMATION
    )
    deployments.transition(
        dep.id, DeploymentState.DEPLOYED, actor="ada", role=OperatorRole.OPERATOR
    )
    return deployments.transition(
        dep.id, DeploymentState.RUNNING, actor="ci", role=OperatorRole.AUTOMATION
    )


def test_agreement_is_healthy_and_fresh(store, deployments):
    dep = _running(deployments)
    backend = StubBackend()
    backend.report(
        TargetObservation(
            deployment_id=dep.id,
            state=DeploymentState.RUNNING,
            revision="r1",
            status=HealthStatus.HEALTHY,
        )
    )
    check = HealthService(store, deployments, backend).check(dep.id)
    assert check.status is HealthStatus.HEALTHY
    assert check.confidence is Confidence.FRESH
    assert not check.is_stale and not check.drifted


def test_state_and_revision_disagreement_raise_drift(store, deployments):
    dep = _running(deployments, revision="r1")
    backend = StubBackend()
    backend.report(
        TargetObservation(
            deployment_id=dep.id,
            state=DeploymentState.STOPPED,
            revision="r0",
            status=HealthStatus.UNHEALTHY,
        )
    )
    check = HealthService(store, deployments, backend).check(dep.id)
    assert check.drifted
    kinds = {s.kind for s in check.signals}
    assert kinds == {DriftKind.STATE_MISMATCH, DriftKind.REVISION_MISMATCH}
    mismatch = next(s for s in check.signals if s.kind is DriftKind.STATE_MISMATCH)
    assert mismatch.believed == "running" and mismatch.observed == "stopped"


def test_a_stale_observation_is_visible_and_never_reported_as_health(store, deployments):
    dep = _running(deployments)
    backend = StubBackend()
    stale_at = datetime.now(timezone.utc) - timedelta(hours=2)
    backend.report(
        TargetObservation(
            deployment_id=dep.id,
            state=DeploymentState.RUNNING,
            revision="r1",
            status=HealthStatus.HEALTHY,
            observed_at=stale_at.isoformat(),
        )
    )
    check = HealthService(store, deployments, backend).check(dep.id)
    assert check.confidence is Confidence.STALE
    assert check.is_stale
    assert check.status is HealthStatus.UNKNOWN  # not HEALTHY, though it said so
    assert check.observation_age_seconds > 3600
    assert check.staleness_horizon_seconds == 300
    assert [s.kind for s in check.signals] == [DriftKind.STALE_BELIEF]


def test_an_unobserved_deployment_is_unknown_and_missing_on_target(store, deployments):
    dep = _running(deployments)
    check = HealthService(store, deployments, StubBackend()).check(dep.id)
    assert check.confidence is Confidence.UNOBSERVED
    assert check.status is HealthStatus.UNKNOWN
    assert check.observed_at is None and check.observation_age_seconds is None
    assert [s.kind for s in check.signals] == [DriftKind.MISSING_ON_TARGET]


def test_nothing_is_expected_on_target_before_deployment(store, deployments):
    dep = deployments.request(TENANT)
    check = HealthService(store, deployments, StubBackend()).check(dep.id)
    assert check.confidence is Confidence.UNOBSERVED
    assert check.signals == []


def test_the_horizon_is_injectable_and_decides_freshness(store, deployments):
    dep = _running(deployments)
    backend = StubBackend()
    observed = datetime.now(timezone.utc) - timedelta(seconds=60)
    backend.report(
        TargetObservation(
            deployment_id=dep.id,
            state=DeploymentState.RUNNING,
            revision="r1",
            status=HealthStatus.DEGRADED,
            observed_at=observed.isoformat(),
        )
    )
    generous = HealthService(
        store, deployments, backend, staleness_horizon=timedelta(minutes=10)
    ).check(dep.id)
    assert generous.confidence is Confidence.FRESH
    assert generous.status is HealthStatus.DEGRADED

    strict = HealthService(
        store, deployments, backend, staleness_horizon=timedelta(seconds=5)
    ).check(dep.id)
    assert strict.confidence is Confidence.STALE
    assert strict.status is HealthStatus.UNKNOWN


def test_a_workload_the_fabric_does_not_know_is_drift_too(store, deployments):
    _running(deployments)
    signals = HealthService(store, deployments, StubBackend()).unknown_to_fabric(
        TENANT, ["dep_unmanaged"]
    )
    assert [s.kind for s in signals] == [DriftKind.UNKNOWN_TO_FABRIC]
    assert signals[0].severity.value == "critical"


def test_checks_are_persisted_for_the_command_centre(store, deployments):
    dep = _running(deployments)
    service = HealthService(store, deployments, StubBackend())
    service.check(dep.id)
    stored = service.last_check(dep.id)
    assert stored is not None
    assert stored.deployment_id == dep.id
    assert stored.tenant_id == TENANT
    assert service.check_tenant(TENANT)
