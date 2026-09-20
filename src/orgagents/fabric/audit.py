"""The operator audit trail (ADR-0043, ADR-0051).

ADR-0051 asks for every command centre action *and every cross-tenant read* to
be recorded, because reading across tenants is precisely what an operator is
trusted with and precisely what must be reviewable afterwards.

This extends the designer's append-only log rather than inventing a second
discipline: `AuditEvent` and `AuditOutcome` are imported from
`designer/audit.py`, and the same two properties hold — no update, no delete on
this API, so the only way to rewrite history is to reach past the product into
the storage backend.

Why the rows live in their own collection: `designer.AuditAction` is a closed
enum in a module this work may not rewrite, and its readers validate every row
back into `AuditEvent`. Writing `fabric.*` actions into the same collection
would make the designer's own log unreadable. So operator events are a
`FabricAuditEvent` in `fabric_audit`, one backend and one store — the shared
backend of ADR-0051 — with a distinct action vocabulary.

**Volume.** One event per *request*, never one per record returned. A list
read over forty tenants is one row naming the scope and the count, not forty;
otherwise an operator refreshing a dashboard buries the one row somebody needs
to find. The cost of that choice is that the log says "this operator listed
every tenant" rather than "this operator saw tenant X", which is the right
granularity for review: the question is who looked across the boundary, not
which pixel they read.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

from pydantic import Field

from ..ids import new_id, now_iso
from ..store import Store
from ..designer.audit import AuditEvent, AuditOutcome

logger = logging.getLogger(__name__)

FABRIC_AUDIT = "fabric_audit"

#: How many tenant ids a scope summary carries before it is truncated. A log
#: row is for review, not for reconstruction; the count is always exact.
MAX_SCOPE_IDS = 50


class FabricAuditAction(str, Enum):
    """What an operator did. One value per thing a reviewer would ask about."""

    TENANT_READ = "fabric.tenant.read"
    DEPLOYMENT_READ = "fabric.deployment.read"
    HEALTH_READ = "fabric.health.read"
    QUOTA_READ = "fabric.quota.read"
    SERVICE_READ = "fabric.service.read"
    AUDIT_READ = "fabric.audit.read"

    DEPLOY = "fabric.deployment.deploy"
    STOP = "fabric.deployment.stop"
    QUARANTINE = "fabric.deployment.quarantine"
    REDEPLOY = "fabric.deployment.redeploy"
    REQUOTA = "fabric.quota.write"
    GRANT = "fabric.operator.grant"
    REVOKE = "fabric.operator.revoke"


class FabricAuditEvent(AuditEvent):
    """A designer audit event, re-scoped to the fabric.

    `action` is overridden to the fabric vocabulary; everything else — actor,
    outcome, reason, detail, the total order on (timestamp, id) — is inherited
    so one reviewer reads both logs the same way.
    """

    action: FabricAuditAction  # type: ignore[assignment]
    tenant_id: str = ""
    deployment_id: str = ""
    #: True when this request saw more than one tenant's operational state.
    cross_tenant: bool = False
    route: str = ""
    operator_roles: list[str] = Field(default_factory=list)


class FabricAuditLog:
    """Append and query. Deliberately no update and no delete (ADR-0043)."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def record(
        self,
        action: FabricAuditAction,
        *,
        actor: str = "",
        actor_name: str = "",
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        tenant_id: str = "",
        deployment_id: str = "",
        cross_tenant: bool = False,
        route: str = "",
        permission: str = "",
        reason: str = "",
        operator_roles: Optional[list[str]] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> Optional[FabricAuditEvent]:
        """Write one event.

        As in the designer, auditing must not take down the thing it observes,
        so a storage failure is logged at ERROR with the lost event rather than
        raised at the caller.
        """
        event = FabricAuditEvent(
            id=new_id("fab"),
            timestamp=now_iso(),
            action=action,
            actor=actor,
            actor_name=actor_name,
            outcome=outcome,
            tenant_id=tenant_id,
            deployment_id=deployment_id,
            cross_tenant=cross_tenant,
            route=route,
            permission=permission,
            reason=reason,
            operator_roles=list(operator_roles or []),
            detail=dict(detail or {}),
        )
        try:
            self.store.put(FABRIC_AUDIT, event, parent=tenant_id or None,
                           name=action.value)
            return event
        except Exception:
            logger.error("fabric audit event was not persisted: %s",
                         event.model_dump(mode="json"), exc_info=True)
            return None

    def query(
        self,
        *,
        tenant_id: Optional[str] = None,
        actor: Optional[str] = None,
        action: Optional[FabricAuditAction] = None,
        outcome: Optional[AuditOutcome] = None,
        cross_tenant_only: bool = False,
        limit: int = 200,
    ) -> list[FabricAuditEvent]:
        """Newest first."""
        events = self.store.list(FABRIC_AUDIT, FabricAuditEvent, limit=10_000)
        found = [
            e for e in events
            if (tenant_id is None or e.tenant_id == tenant_id)
            and (actor is None or e.actor == actor)
            and (action is None or e.action is action)
            and (outcome is None or e.outcome is outcome)
            and (not cross_tenant_only or e.cross_tenant)
        ]
        found.sort(key=lambda e: e.order_key, reverse=True)
        return found[:limit]


def scope_detail(tenant_ids: list[str]) -> dict[str, Any]:
    """The summary a read event carries instead of one row per record."""
    return {
        "tenant_count": len(tenant_ids),
        "tenant_ids": sorted(tenant_ids)[:MAX_SCOPE_IDS],
        "truncated": len(tenant_ids) > MAX_SCOPE_IDS,
    }
