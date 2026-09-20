"""Quotas and per-tenant entitlements (WS-030 M3).

WS-030 asks what a hit quota should do, and notes that "refuse" is rarely what
an operator wants at 3am. The answer here splits the question in two, because
the two halves are not the same kind of limit:

* An **entitlement** is an authorization boundary — may this tenant use this
  catalog entry at all. Deny-by-default (ADR-0008, ADR-0021) applies and the
  answer is a refusal; letting an unentitled block through because it is late
  is exactly the failure the boundary exists to prevent.
* A **quota** is a capacity limit, and capacity limits are guesses. Refusing at
  a guessed number turns a number somebody typed last quarter into an outage.
  So a quota has a soft limit and a hard ceiling: below the soft limit a call
  is allowed silently; between soft and hard it is allowed, *recorded as a
  breach* and surfaced to the operator (`ALLOW_DEGRADED`); above the hard
  ceiling it is refused. The soft limit is the operator's number; the hard
  ceiling is the number the fabric cannot pay past.

Both limits are inclusive: usage may reach a limit, not exceed it. A quota with
no hard ceiling never refuses, and says so.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from ..ids import now_iso
from ..store import Store

ENTITLEMENTS = "fabric_entitlements"

#: Sentinel for "no hard ceiling": breaches are recorded, nothing is refused.
UNBOUNDED = -1


class QuotaKind(str, Enum):
    DEPLOYMENTS = "deployments"
    AGENTS = "agents"
    CONCURRENT_SESSIONS = "concurrent_sessions"
    MONTHLY_SPEND_USD = "monthly_spend_usd"
    TOKENS_PER_DAY = "tokens_per_day"


class Decision(str, Enum):
    ALLOW = "allow"
    #: Over the operator's number, under the ceiling: served and reported.
    ALLOW_DEGRADED = "allow_degraded"
    REFUSE = "refuse"


class Quota(BaseModel):
    kind: QuotaKind
    soft_limit: int
    hard_ceiling: int = UNBOUNDED
    unit: str = ""

    @property
    def refuses(self) -> bool:
        return self.hard_ceiling != UNBOUNDED


class QuotaVerdict(BaseModel):
    kind: QuotaKind
    decision: Decision
    usage_before: int
    usage_after: int
    soft_limit: int
    hard_ceiling: int
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision is not Decision.REFUSE

    @property
    def breached(self) -> bool:
        return self.decision is not Decision.ALLOW


class Entitlements(BaseModel):
    """What one tenant may use, and how much of it.

    `catalog_entries` is an allow-list: an entry not listed is not entitled.
    An empty list means the tenant may use nothing from the catalog, which is
    the deny-by-default reading and the one that fails safe on a bad write.
    """

    #: The tenant id doubles as the document id: one entitlement set per tenant.
    id: str = ""
    tenant_id: str
    quotas: dict[QuotaKind, Quota] = Field(default_factory=dict)
    catalog_entries: list[str] = Field(default_factory=list)
    usage: dict[QuotaKind, int] = Field(default_factory=dict)
    breaches: list[dict[str, str]] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now_iso)

    @model_validator(mode="after")
    def _id_follows_tenant(self) -> "Entitlements":
        if not self.id:
            self.id = self.tenant_id
        return self


class NotEntitled(PermissionError):
    """Raised when a tenant reaches for a catalog entry it may not use."""

    def __init__(self, tenant_id: str, entry_id: str) -> None:
        super().__init__(
            f"tenant '{tenant_id}' is not entitled to catalog entry "
            f"'{entry_id}'"
        )
        self.tenant_id = tenant_id
        self.entry_id = entry_id


def evaluate(quota: Quota, usage_before: int, amount: int = 1) -> QuotaVerdict:
    """Pure decision function, so the policy can be read without a store."""
    after = usage_before + amount
    if after <= quota.soft_limit:
        decision, reason = Decision.ALLOW, "within the quota"
    elif quota.refuses and after > quota.hard_ceiling:
        decision = Decision.REFUSE
        reason = (
            f"would reach {after}, past the hard ceiling of "
            f"{quota.hard_ceiling}"
        )
    else:
        decision = Decision.ALLOW_DEGRADED
        reason = (
            f"over the soft limit of {quota.soft_limit}; served and reported "
            "so an operator decides in daylight, not at 3am"
        )
    return QuotaVerdict(
        kind=quota.kind,
        decision=decision,
        usage_before=usage_before,
        usage_after=after if decision is not Decision.REFUSE else usage_before,
        soft_limit=quota.soft_limit,
        hard_ceiling=quota.hard_ceiling,
        reason=reason,
    )


class QuotaService:
    """Per-tenant entitlements and usage, persisted through the store."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def set_entitlements(self, entitlements: Entitlements) -> Entitlements:
        entitlements.updated_at = now_iso()
        self.store.put(
            ENTITLEMENTS, entitlements, parent=entitlements.tenant_id
        )
        return entitlements

    def entitlements(self, tenant_id: str) -> Optional[Entitlements]:
        return self.store.get(ENTITLEMENTS, tenant_id, Entitlements)

    def _require(self, tenant_id: str) -> Entitlements:
        found = self.entitlements(tenant_id)
        if found is None:
            raise KeyError(f"tenant '{tenant_id}' has no entitlements recorded")
        return found

    def may_use(self, tenant_id: str, entry_id: str) -> bool:
        return entry_id in self._require(tenant_id).catalog_entries

    def require_entry(self, tenant_id: str, entry_id: str) -> None:
        """Authorization, not capacity: this one refuses."""
        if not self.may_use(tenant_id, entry_id):
            raise NotEntitled(tenant_id, entry_id)

    def check(self, tenant_id: str, kind: QuotaKind, amount: int = 1) -> QuotaVerdict:
        """Decide without consuming."""
        entitlements = self._require(tenant_id)
        quota = entitlements.quotas.get(kind)
        if quota is None:
            # An unset quota is not an infinite one: it is an unanswered
            # question, so it is served and reported rather than assumed fine.
            return QuotaVerdict(
                kind=kind,
                decision=Decision.ALLOW_DEGRADED,
                usage_before=entitlements.usage.get(kind, 0),
                usage_after=entitlements.usage.get(kind, 0) + amount,
                soft_limit=0,
                hard_ceiling=UNBOUNDED,
                reason="no quota is set for this kind; served and reported",
            )
        return evaluate(quota, entitlements.usage.get(kind, 0), amount)

    def consume(self, tenant_id: str, kind: QuotaKind, amount: int = 1) -> QuotaVerdict:
        """Decide and, if allowed, record the usage and any breach."""
        entitlements = self._require(tenant_id)
        verdict = self.check(tenant_id, kind, amount)
        if verdict.allowed:
            entitlements.usage[kind] = verdict.usage_after
        if verdict.breached:
            entitlements.breaches.append(
                {
                    "at": now_iso(),
                    "kind": kind.value,
                    "decision": verdict.decision.value,
                    "reason": verdict.reason,
                }
            )
        self.set_entitlements(entitlements)
        return verdict

    def release(self, tenant_id: str, kind: QuotaKind, amount: int = 1) -> int:
        """Give back usage of a gauge-like quota (a stopped deployment)."""
        entitlements = self._require(tenant_id)
        remaining = max(0, entitlements.usage.get(kind, 0) - amount)
        entitlements.usage[kind] = remaining
        self.set_entitlements(entitlements)
        return remaining

    def breaches(self, tenant_id: str) -> list[dict[str, str]]:
        return list(self._require(tenant_id).breaches)
