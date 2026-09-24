"""The command centre's namespace (ADR-0049, ADR-0051)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..designer import Principal
from ..designer.audit import AuditOutcome
from ..fabric import rbac as fabric_rbac
from ..fabric.audit import FabricAuditAction, scope_detail
from ..fabric.deployments import (
    TRANSITIONS,
    Deployment,
    DeploymentState,
    IllegalTransition,
    OperatorRole,
    TransitionDenied,
    allowed_transitions,
)
from ..fabric.quotas import Entitlements, Quota, QuotaKind
from ..fabric.rbac import FabricPermissionDenied
from ..fabric.tenants import (
    TENANT_TRANSITIONS,
    PrefixError,
    TenantIllegalTransition,
    TenantRetirementBlocked,
    TenantStatus,
    TenantTransitionDenied,
    allowed_tenant_transitions,
)
from .context import ApiContext
from .models import (
    OperatorActionRequest,
    OperatorGrantRequest,
    RequotaRequest,
    TenantRegistrationRequest,
)


@dataclass(frozen=True)
class OperatorContext:
    """The requester, with the operator roles the fabric granted them.

    The designer roles on the principal are not consulted and are not
    carried here: a designer grant must never become a fabric grant
    (ADR-0051).
    """

    principal: Principal
    roles: list[OperatorRole]


def _deployment_view(deployment: Deployment) -> dict:
    """A deployment with the moves its lifecycle allows from where it is.
    Shared with the designer's publish request, which returns one."""
    return {
        **deployment.model_dump(mode="json"),
        "allowed_transitions": {
            state.value: sorted(r.value for r in roles)
            for state, roles in allowed_transitions(deployment.state).items()
        },
    }


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router
    principal = ctx.principal
    fabric_operators = ctx.fabric_operators
    fabric_tenants = ctx.fabric_tenants
    fabric_deployments = ctx.fabric_deployments
    fabric_quotas = ctx.fabric_quotas
    fabric_services = ctx.fabric_services
    fabric_health = ctx.fabric_health
    fabric_audit = ctx.fabric_audit

    # -- fabric: the command centre's namespace (ADR-0049, ADR-0051) -------
    #
    # Reads here may cross tenants; nothing here may touch a spec. There is no
    # write route into the designer's models from this namespace by
    # construction, which is the only form of that guarantee worth having.

    def operator(user: Principal = Depends(principal)) -> OperatorContext:
        return OperatorContext(user, fabric_operators.roles_for(user.user_id))

    def _fabric_record(ctx: OperatorContext, action: FabricAuditAction, *,
                       route: str, outcome: AuditOutcome, permission: str = "",
                       tenant_id: str = "", deployment_id: str = "",
                       cross_tenant: bool = False, reason: str = "",
                       detail: Optional[dict[str, Any]] = None) -> None:
        fabric_audit.record(
            action, actor=ctx.principal.user_id,
            actor_name=getattr(ctx.principal, "label", ""),
            outcome=outcome, route=route, permission=permission,
            tenant_id=tenant_id, deployment_id=deployment_id,
            cross_tenant=cross_tenant, reason=reason,
            operator_roles=[r.value for r in ctx.roles], detail=detail,
        )

    def _fabric_require(ctx: OperatorContext, permission: str,
                        action: FabricAuditAction, route: str, *,
                        tenant_id: str = "", deployment_id: str = "") -> None:
        """Authorize, and record the refusal — a refused read is a fact too."""
        try:
            fabric_rbac.require(ctx.roles, permission)
        except FabricPermissionDenied as e:
            _fabric_record(ctx, action, route=route, outcome=AuditOutcome.DENIED,
                           permission=permission, tenant_id=tenant_id,
                           deployment_id=deployment_id, reason=str(e))
            raise HTTPException(403, str(e)) from e

    def _audited_read(ctx: OperatorContext, permission: str,
                      action: FabricAuditAction, route: str, payload_fn,
                      *, tenant_id: str = "") -> Any:
        """One audit row per request, carrying the scope it covered.

        Not one row per tenant returned: see `fabric/audit.py` on volume.
        """
        _fabric_require(ctx, permission, action, route, tenant_id=tenant_id)
        payload, tenant_ids = payload_fn()
        seen = sorted(set(tenant_ids))
        _fabric_record(
            ctx, action, route=route, outcome=AuditOutcome.SUCCESS,
            permission=permission, tenant_id=tenant_id,
            cross_tenant=len(seen) > 1, detail=scope_detail(seen),
        )
        return payload

    def _require_tenant(tenant_id: str):
        tenant = fabric_tenants.get(tenant_id)
        if tenant is None:
            raise HTTPException(404, f"no such tenant: {tenant_id}")
        return tenant

    def _require_deployment(deployment_id: str) -> Deployment:
        deployment = fabric_deployments.get(deployment_id)
        if deployment is None:
            raise HTTPException(404, f"no such deployment: {deployment_id}")
        return deployment

    def _tenant_view(tenant) -> dict:
        return {
            **tenant.model_dump(mode="json"),
            "allowed_transitions": {
                status.value: sorted(r.value for r in roles)
                for status, roles in allowed_tenant_transitions(tenant.status).items()
            },
        }

    def _quota_view(tenant_id: str) -> dict:
        entitlements = fabric_quotas.entitlements(tenant_id)
        tenant = fabric_tenants.get(tenant_id)
        if entitlements is None:
            return {
                "tenant_id": tenant_id,
                "quotas": {}, "usage": {}, "breaches": [],
                "catalog_entries": [],
                "tenant_entitlements": list(tenant.entitlements) if tenant else [],
                "recorded": False,
            }
        return {
            **entitlements.model_dump(mode="json"),
            "tenant_entitlements": list(tenant.entitlements) if tenant else [],
            "recorded": True,
        }

    @router.get("/api/fabric/whoami")
    def fabric_whoami(ctx: OperatorContext = Depends(operator)) -> dict:
        """Who the caller is *to the fabric*. Never reports designer roles."""
        return {
            "user_id": ctx.principal.user_id,
            "display_name": getattr(ctx.principal, "label", ""),
            "operator_roles": [r.value for r in ctx.roles],
            "permissions": sorted(fabric_rbac.permissions_for(ctx.roles)),
            "is_operator": bool(ctx.roles),
        }

    @router.get("/api/fabric/tenants")
    def fabric_list_tenants(ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            tenants = fabric_tenants.list()
            rows = []
            for tenant in tenants:
                deployments = fabric_deployments.list(tenant.id)
                rows.append({
                    **tenant.model_dump(mode="json"),
                    "deployment_count": len(deployments),
                    "states": sorted({d.state.value for d in deployments}),
                })
            return rows, [t.id for t in tenants]

        return _audited_read(ctx, fabric_rbac.TENANT_READ,
                             FabricAuditAction.TENANT_READ,
                             "GET /api/fabric/tenants", payload)

    @router.get("/api/fabric/tenants/{tenant_id}")
    def fabric_tenant_detail(tenant_id: str,
                             ctx: OperatorContext = Depends(operator)) -> dict:
        def payload():
            tenant = _require_tenant(tenant_id)
            deployments = fabric_deployments.list(tenant_id)
            checks = [fabric_health.check(d.id) for d in deployments]
            return (
                {
                    "tenant": tenant.model_dump(mode="json"),
                    "deployments": [_deployment_view(d) for d in deployments],
                    "health": [c.model_dump(mode="json") for c in checks],
                    "drift": [s.model_dump(mode="json") for c in checks
                              for s in c.signals],
                    "quotas": _quota_view(tenant_id),
                },
                [tenant_id],
            )

        return _audited_read(ctx, fabric_rbac.TENANT_READ,
                             FabricAuditAction.TENANT_READ,
                             "GET /api/fabric/tenants/{tenant_id}", payload,
                             tenant_id=tenant_id)

    @router.get("/api/fabric/deployments")
    def fabric_list_deployments(tenant_id: Optional[str] = None,
                                ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            if tenant_id is not None:
                _require_tenant(tenant_id)
                deployments = fabric_deployments.list(tenant_id)
            else:
                deployments = [d for t in fabric_tenants.list()
                               for d in fabric_deployments.list(t.id)]
            return ([_deployment_view(d) for d in deployments],
                    [d.tenant_id for d in deployments])

        return _audited_read(ctx, fabric_rbac.DEPLOYMENT_READ,
                             FabricAuditAction.DEPLOYMENT_READ,
                             "GET /api/fabric/deployments", payload,
                             tenant_id=tenant_id or "")

    @router.get("/api/fabric/deployments/{deployment_id}")
    def fabric_deployment_detail(deployment_id: str,
                                 ctx: OperatorContext = Depends(operator)) -> dict:
        def payload():
            deployment = _require_deployment(deployment_id)
            check = fabric_health.last_check(deployment_id)
            return (
                {
                    **_deployment_view(deployment),
                    "health": check.model_dump(mode="json") if check else None,
                },
                [deployment.tenant_id],
            )

        return _audited_read(ctx, fabric_rbac.DEPLOYMENT_READ,
                             FabricAuditAction.DEPLOYMENT_READ,
                             "GET /api/fabric/deployments/{deployment_id}",
                             payload)

    @router.get("/api/fabric/deployments/{deployment_id}/history")
    def fabric_deployment_history(deployment_id: str,
                                  ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            deployment = _require_deployment(deployment_id)
            return ([e.model_dump(mode="json") for e in deployment.history],
                    [deployment.tenant_id])

        return _audited_read(ctx, fabric_rbac.DEPLOYMENT_READ,
                             FabricAuditAction.DEPLOYMENT_READ,
                             "GET /api/fabric/deployments/{deployment_id}/history",
                             payload)

    @router.get("/api/fabric/health")
    def fabric_health_route(tenant_id: Optional[str] = None,
                            ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            tenants = ([_require_tenant(tenant_id)] if tenant_id is not None
                       else fabric_tenants.list())
            checks = [c for t in tenants for c in fabric_health.check_tenant(t.id)]
            return ([c.model_dump(mode="json") for c in checks],
                    [t.id for t in tenants])

        return _audited_read(ctx, fabric_rbac.HEALTH_READ,
                             FabricAuditAction.HEALTH_READ,
                             "GET /api/fabric/health", payload,
                             tenant_id=tenant_id or "")

    @router.get("/api/fabric/drift")
    def fabric_drift(tenant_id: Optional[str] = None,
                     ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            tenants = ([_require_tenant(tenant_id)] if tenant_id is not None
                       else fabric_tenants.list())
            signals = [s for t in tenants
                       for c in fabric_health.check_tenant(t.id) for s in c.signals]
            return ([s.model_dump(mode="json") for s in signals],
                    [t.id for t in tenants])

        return _audited_read(ctx, fabric_rbac.HEALTH_READ,
                             FabricAuditAction.HEALTH_READ,
                             "GET /api/fabric/drift", payload,
                             tenant_id=tenant_id or "")

    @router.get("/api/fabric/quotas")
    def fabric_quota_state(tenant_id: Optional[str] = None,
                           ctx: OperatorContext = Depends(operator)) -> list[dict]:
        def payload():
            tenants = ([_require_tenant(tenant_id)] if tenant_id is not None
                       else fabric_tenants.list())
            return ([_quota_view(t.id) for t in tenants], [t.id for t in tenants])

        return _audited_read(ctx, fabric_rbac.QUOTA_READ,
                             FabricAuditAction.QUOTA_READ,
                             "GET /api/fabric/quotas", payload,
                             tenant_id=tenant_id or "")

    @router.get("/api/fabric/services")
    def fabric_common_services(ctx: OperatorContext = Depends(operator)) -> list[dict]:
        # The common-service registry is fabric-wide, not per tenant, so there
        # is no tenant scope to record and nothing here crosses a boundary.
        return _audited_read(
            ctx, fabric_rbac.SERVICE_READ, FabricAuditAction.SERVICE_READ,
            "GET /api/fabric/services",
            lambda: ([s.model_dump(mode="json") for s in fabric_services.list()], []),
        )

    @router.get("/api/fabric/services/boundary")
    def fabric_boundary_report(ctx: OperatorContext = Depends(operator)) -> list[dict]:
        return _audited_read(
            ctx, fabric_rbac.SERVICE_READ, FabricAuditAction.SERVICE_READ,
            "GET /api/fabric/services/boundary",
            lambda: (fabric_services.boundary_report(), []),
        )

    @router.get("/api/fabric/audit")
    def fabric_audit_read(tenant_id: Optional[str] = None,
                          actor: Optional[str] = None,
                          cross_tenant_only: bool = False,
                          limit: int = 200,
                          ctx: OperatorContext = Depends(operator)) -> list[dict]:
        """The operator log. Administrative, like the designer's (ADR-0043)."""
        _fabric_require(ctx, fabric_rbac.AUDIT_READ, FabricAuditAction.AUDIT_READ,
                        "GET /api/fabric/audit", tenant_id=tenant_id or "")
        events = fabric_audit.query(tenant_id=tenant_id, actor=actor,
                                    cross_tenant_only=cross_tenant_only,
                                    limit=limit)
        # Recorded after the query so the reader's own read is in the log, but
        # not in the answer it just received.
        _fabric_record(ctx, FabricAuditAction.AUDIT_READ,
                       route="GET /api/fabric/audit", outcome=AuditOutcome.SUCCESS,
                       permission=fabric_rbac.AUDIT_READ, tenant_id=tenant_id or "",
                       detail={"returned": len(events)})
        return [e.model_dump(mode="json") for e in events]

    #: Operator action -> the lifecycle target it asks for. The transition
    #: table in `fabric/deployments.py` decides whether it is legal and who
    #: may make it; nothing here re-implements that judgement.
    OPERATOR_ACTIONS: dict[str, tuple[str, DeploymentState, FabricAuditAction]] = {
        # Compiling a requested deployment is the step before deploying it;
        # exposed so an operator can drive the whole path, audited as a deploy.
        "generate": (fabric_rbac.DEPLOY, DeploymentState.GENERATED,
                     FabricAuditAction.DEPLOY),
        "deploy": (fabric_rbac.DEPLOY, DeploymentState.DEPLOYED,
                   FabricAuditAction.DEPLOY),
        "stop": (fabric_rbac.STOP, DeploymentState.STOPPED,
                 FabricAuditAction.STOP),
        "quarantine": (fabric_rbac.QUARANTINE, DeploymentState.QUARANTINED,
                       FabricAuditAction.QUARANTINE),
        # Re-deploy is stopped -> running. The table refuses it out of
        # quarantine, which is the rule we want and must not restate.
        "redeploy": (fabric_rbac.REDEPLOY, DeploymentState.RUNNING,
                     FabricAuditAction.REDEPLOY),
    }

    def _acting_role(roles: list[OperatorRole], source: DeploymentState,
                     target: DeploymentState) -> OperatorRole:
        """Which granted role to act as for this move.

        A person may hold several. Prefer one the table accepts for *this*
        transition; otherwise act as the strongest held role, so the refusal
        that follows names a role the person actually has.
        """
        allowed = TRANSITIONS.get((source, target)) or frozenset()
        for role in reversed(fabric_rbac.ROLE_ORDER):
            if role in roles and role in allowed:
                return role
        return max(roles, key=fabric_rbac.ROLE_ORDER.index)

    @router.post("/api/fabric/deployments/{deployment_id}/actions/{action}")
    def fabric_operator_action(deployment_id: str, action: str,
                               req: OperatorActionRequest,
                               ctx: OperatorContext = Depends(operator)) -> dict:
        if action not in OPERATOR_ACTIONS:
            raise HTTPException(
                404, f"unknown operator action '{action}'; "
                     f"expected one of {sorted(OPERATOR_ACTIONS)}"
            )
        permission, target_state, audit_action = OPERATOR_ACTIONS[action]
        route = f"POST /api/fabric/deployments/{{deployment_id}}/actions/{action}"
        deployment = _require_deployment(deployment_id)
        _fabric_require(ctx, permission, audit_action, route,
                        tenant_id=deployment.tenant_id,
                        deployment_id=deployment_id)
        role = _acting_role(ctx.roles, deployment.state, target_state)
        try:
            moved = fabric_deployments.transition(
                deployment_id, target_state, actor=ctx.principal.user_id,
                role=role, reason=req.reason,
            )
        except IllegalTransition as e:
            _fabric_record(ctx, audit_action, route=route,
                           outcome=AuditOutcome.CONFLICT, permission=permission,
                           tenant_id=deployment.tenant_id,
                           deployment_id=deployment_id, reason=str(e))
            raise HTTPException(409, str(e)) from e
        except TransitionDenied as e:
            _fabric_record(ctx, audit_action, route=route,
                           outcome=AuditOutcome.DENIED, permission=permission,
                           tenant_id=deployment.tenant_id,
                           deployment_id=deployment_id, reason=str(e))
            raise HTTPException(403, str(e)) from e
        _fabric_record(ctx, audit_action, route=route,
                       outcome=AuditOutcome.SUCCESS, permission=permission,
                       tenant_id=moved.tenant_id, deployment_id=deployment_id,
                       reason=req.reason,
                       detail={"from": deployment.state.value,
                               "to": moved.state.value,
                               "acted_as": role.value})
        return _deployment_view(moved)

    @router.put("/api/fabric/tenants/{tenant_id}/quotas")
    def fabric_requota(tenant_id: str, req: RequotaRequest,
                       ctx: OperatorContext = Depends(operator)) -> dict:
        """Re-quota: capacity, not authorization, and never a spec change."""
        route = "PUT /api/fabric/tenants/{tenant_id}/quotas"
        _fabric_require(ctx, fabric_rbac.REQUOTA, FabricAuditAction.REQUOTA,
                        route, tenant_id=tenant_id)
        _require_tenant(tenant_id)
        current = fabric_quotas.entitlements(tenant_id) or Entitlements(
            tenant_id=tenant_id
        )
        before = {k.value: q.model_dump(mode="json")
                  for k, q in current.quotas.items()}
        try:
            for kind, quota in req.quotas.items():
                parsed = QuotaKind(kind)
                current.quotas[parsed] = Quota(kind=parsed, **quota)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, f"bad quota: {e}") from e
        if req.catalog_entries is not None:
            current.catalog_entries = list(req.catalog_entries)
        saved = fabric_quotas.set_entitlements(current)
        _fabric_record(ctx, FabricAuditAction.REQUOTA, route=route,
                       outcome=AuditOutcome.SUCCESS,
                       permission=fabric_rbac.REQUOTA, tenant_id=tenant_id,
                       reason=req.reason,
                       detail={"before": before,
                               "after": {k.value: q.model_dump(mode="json")
                                         for k, q in saved.quotas.items()}})
        return _quota_view(tenant_id)

    #: Tenant action -> the status it asks for. As with deployments, the
    #: transition table decides legality and who may make the move; this map
    #: only names the door.
    TENANT_ACTIONS: dict[str, TenantStatus] = {
        "activate": TenantStatus.ACTIVE,
        "suspend": TenantStatus.SUSPENDED,
        "resume": TenantStatus.ACTIVE,
        "retire": TenantStatus.RETIRED,
    }

    def _acting_tenant_role(roles: list[OperatorRole], source: TenantStatus,
                            target: TenantStatus) -> OperatorRole:
        """As `_acting_role`, over the tenant table."""
        allowed = TENANT_TRANSITIONS.get((source, target)) or frozenset()
        for role in reversed(fabric_rbac.ROLE_ORDER):
            if role in roles and role in allowed:
                return role
        return max(roles, key=fabric_rbac.ROLE_ORDER.index)

    @router.post("/api/fabric/tenants")
    def fabric_register_tenant(req: TenantRegistrationRequest,
                               ctx: OperatorContext = Depends(operator)) -> dict:
        """Mint a tenant and its isolation domain (ADR-0050).

        Every prefix rule lives in `fabric/tenants.py` and is called, not
        copied: collisions, reserved names and the shape of a safe identifier
        are decided in one place, including against retired tenants whose
        prefixes stay spent.
        """
        route = "POST /api/fabric/tenants"
        _fabric_require(ctx, fabric_rbac.TENANT_REGISTER,
                        FabricAuditAction.TENANT_REGISTER, route,
                        tenant_id=req.id)
        try:
            tenant = fabric_tenants.register(
                id=req.id, name=req.name or req.id,
                namespace_prefix=req.namespace_prefix,
                entitlements=req.entitlements,
                cloud_boundary=req.cloud_boundary,
            )
        except PrefixError as e:
            _fabric_record(ctx, FabricAuditAction.TENANT_REGISTER, route=route,
                           outcome=AuditOutcome.CONFLICT,
                           permission=fabric_rbac.TENANT_REGISTER,
                           tenant_id=req.id, reason=str(e))
            raise HTTPException(409, str(e)) from e
        _fabric_record(ctx, FabricAuditAction.TENANT_REGISTER, route=route,
                       outcome=AuditOutcome.SUCCESS,
                       permission=fabric_rbac.TENANT_REGISTER,
                       tenant_id=tenant.id, reason=req.reason,
                       detail={"namespace_prefix": tenant.namespace_prefix,
                               "isolation_domain": tenant.isolation_domain.id,
                               "status": tenant.status.value})
        return _tenant_view(tenant)

    @router.post("/api/fabric/tenants/{tenant_id}/actions/{action}")
    def fabric_tenant_action(tenant_id: str, action: str,
                             req: OperatorActionRequest,
                             ctx: OperatorContext = Depends(operator)) -> dict:
        if action not in TENANT_ACTIONS:
            raise HTTPException(
                404, f"unknown tenant action '{action}'; "
                     f"expected one of {sorted(TENANT_ACTIONS)}"
            )
        target = TENANT_ACTIONS[action]
        route = f"POST /api/fabric/tenants/{{tenant_id}}/actions/{action}"
        _fabric_require(ctx, fabric_rbac.TENANT_LIFECYCLE,
                        FabricAuditAction.TENANT_LIFECYCLE, route,
                        tenant_id=tenant_id)
        tenant = _require_tenant(tenant_id)
        role = _acting_tenant_role(ctx.roles, tenant.status, target)
        source = tenant.status
        try:
            moved = fabric_tenants.transition(
                tenant_id, target, actor=ctx.principal.user_id, role=role,
                reason=req.reason,
                # The registry cannot see deployments; the command centre can,
                # so it is the one that can honestly answer "is anything of
                # this tenant still up?".
                deployments=fabric_deployments.list(tenant_id),
            )
        except (TenantIllegalTransition, TenantRetirementBlocked) as e:
            _fabric_record(ctx, FabricAuditAction.TENANT_LIFECYCLE, route=route,
                           outcome=AuditOutcome.CONFLICT,
                           permission=fabric_rbac.TENANT_LIFECYCLE,
                           tenant_id=tenant_id, reason=str(e))
            raise HTTPException(409, str(e)) from e
        except TenantTransitionDenied as e:
            _fabric_record(ctx, FabricAuditAction.TENANT_LIFECYCLE, route=route,
                           outcome=AuditOutcome.DENIED,
                           permission=fabric_rbac.TENANT_LIFECYCLE,
                           tenant_id=tenant_id, reason=str(e))
            raise HTTPException(403, str(e)) from e
        _fabric_record(ctx, FabricAuditAction.TENANT_LIFECYCLE, route=route,
                       outcome=AuditOutcome.SUCCESS,
                       permission=fabric_rbac.TENANT_LIFECYCLE,
                       tenant_id=tenant_id, reason=req.reason,
                       detail={"from": source.value, "to": moved.status.value,
                               "acted_as": role.value})
        return _tenant_view(moved)

    @router.get("/api/fabric/operators")
    def fabric_list_operators(ctx: OperatorContext = Depends(operator)) -> list[dict]:
        _fabric_require(ctx, fabric_rbac.GRANT, FabricAuditAction.GRANT,
                        "GET /api/fabric/operators")
        return [g.model_dump(mode="json") for g in fabric_operators.list()]

    @router.post("/api/fabric/operators")
    def fabric_grant_operator(req: OperatorGrantRequest,
                              ctx: OperatorContext = Depends(operator)) -> dict:
        """Grant an operator role.

        Written here and nowhere else: a fabric grant is never derived from a
        designer role or a workspace membership (ADR-0051).
        """
        route = "POST /api/fabric/operators"
        _fabric_require(ctx, fabric_rbac.GRANT, FabricAuditAction.GRANT, route)
        try:
            roles = [OperatorRole(r) for r in req.roles]
        except ValueError as e:
            raise HTTPException(400, f"unknown operator role: {e}") from e
        grant = fabric_operators.grant(req.user_id, roles,
                                       granted_by=ctx.principal.user_id,
                                       reason=req.reason)
        _fabric_record(ctx, FabricAuditAction.GRANT, route=route,
                       outcome=AuditOutcome.SUCCESS, permission=fabric_rbac.GRANT,
                       reason=req.reason,
                       detail={"user_id": req.user_id,
                               "roles": [r.value for r in roles]})
        return grant.model_dump(mode="json")

    @router.delete("/api/fabric/operators/{user_id}")
    def fabric_revoke_operator(user_id: str,
                               ctx: OperatorContext = Depends(operator)) -> dict:
        route = "DELETE /api/fabric/operators/{user_id}"
        _fabric_require(ctx, fabric_rbac.GRANT, FabricAuditAction.REVOKE, route)
        revoked = fabric_operators.revoke(user_id)
        _fabric_record(ctx, FabricAuditAction.REVOKE, route=route,
                       outcome=AuditOutcome.SUCCESS, permission=fabric_rbac.GRANT,
                       detail={"user_id": user_id, "revoked": revoked})
        return {"revoked": revoked}


    return router
