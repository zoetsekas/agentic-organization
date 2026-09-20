"""Managing the catalog, and answering "may this design use this?" (ADR-0041).

Two jobs. The first is ordinary inventory: publish, search, review, entitle,
deprecate, retire. The second is the one that matters at compile time —
resolving an agent's **model policy** against the approved models and refusing
a binding that picks one the policy does not permit (ADR-0040).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..ids import now_iso
from ..store import Store
from .models import (
    ApprovalStatus,
    CatalogEntry,
    CatalogKind,
    Entitlement,
    ModelAttributes,
    typed_attributes,
)

CATALOG = "platform_catalog"


class CatalogError(RuntimeError):
    """Raised when an entry is missing, not selectable, or not entitled."""


@dataclass
class ModelDecision:
    """Whether a concrete model may serve an agent, and why."""

    allowed: bool
    reason: str
    entry: Optional[CatalogEntry] = None
    alternatives: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.allowed


class CatalogService:
    """The design system's inventory of building blocks."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- inventory ---------------------------------------------------------

    def publish(self, entry: CatalogEntry) -> CatalogEntry:
        entry.updated_at = now_iso()
        self.store.put(CATALOG, entry, parent=entry.kind.value, name=entry.name)
        return entry

    def get(self, entry_id: str) -> Optional[CatalogEntry]:
        return self.store.get(CATALOG, entry_id, CatalogEntry)

    def by_name(self, kind: CatalogKind, name: str) -> Optional[CatalogEntry]:
        return next(
            (e for e in self.list(kind) if e.name == name), None
        )

    def list(self, kind: Optional[CatalogKind] = None) -> list[CatalogEntry]:
        parent = kind.value if kind else None
        return self.store.list(CATALOG, CatalogEntry, parent=parent, limit=5000)

    def search(
        self,
        query: str = "",
        *,
        kind: Optional[CatalogKind] = None,
        status: Optional[ApprovalStatus] = None,
        tags: Optional[list[str]] = None,
        groups: Optional[list[str]] = None,
        workspace: str = "",
        environment: str = "development",
        selectable_only: bool = False,
    ) -> list[CatalogEntry]:
        lowered = query.lower()
        found = []
        for entry in self.list(kind):
            haystack = f"{entry.name} {entry.summary} {' '.join(entry.tags)}".lower()
            if lowered and lowered not in haystack:
                continue
            if status and entry.status is not status:
                continue
            if tags and not set(tags) <= set(entry.tags):
                continue
            if selectable_only and not entry.available_to(
                groups=groups, workspace=workspace, environment=environment
            ):
                continue
            found.append(entry)
        return sorted(found, key=lambda e: (e.kind.value, e.name))

    def review(self, entry_id: str, status: ApprovalStatus, *, reviewer: str,
               note: str = "") -> CatalogEntry:
        entry = self._require(entry_id)
        entry.status = status
        entry.reviewed_by = reviewer
        entry.reviewed_at = now_iso()
        entry.review_note = note
        return self.publish(entry)

    def entitle(self, entry_id: str, entitlement: Entitlement) -> CatalogEntry:
        entry = self._require(entry_id)
        entry.entitlement = entitlement
        return self.publish(entry)

    def retire(self, entry_id: str, *, reviewer: str,
               superseded_by: Optional[str] = None) -> CatalogEntry:
        entry = self.review(
            entry_id, ApprovalStatus.RETIRED, reviewer=reviewer,
            note=f"superseded by {superseded_by}" if superseded_by else "retired",
        )
        # Set the pointer after the review, which re-reads from the store.
        entry.superseded_by = superseded_by
        return self.publish(entry)

    def record_install(self, entry_id: str) -> CatalogEntry:
        entry = self._require(entry_id)
        entry.installs += 1
        return self.publish(entry)

    def _require(self, entry_id: str) -> CatalogEntry:
        entry = self.get(entry_id)
        if entry is None:
            raise CatalogError(f"no catalog entry '{entry_id}'")
        return entry

    # -- models (ADR-0040) -------------------------------------------------

    def models(self, **kwargs: Any) -> list[CatalogEntry]:
        return self.search(kind=CatalogKind.MODEL, **kwargs)

    def model_for(self, provider: str, model_id: str) -> Optional[CatalogEntry]:
        for entry in self.list(CatalogKind.MODEL):
            attributes = ModelAttributes.model_validate(entry.attributes)
            if attributes.model_id == model_id and (
                not provider or attributes.provider == provider
            ):
                return entry
        return None

    def permitted_models(
        self, policy, *, groups: Optional[list[str]] = None, workspace: str = "",
        environment: str = "development",
    ) -> list[CatalogEntry]:
        """Every catalogued model this policy permits, cheapest first."""
        allowed: list[tuple[float, CatalogEntry]] = []
        for entry in self.list(CatalogKind.MODEL):
            decision = self.check_model(entry, policy, groups=groups,
                                        workspace=workspace, environment=environment)
            if decision.allowed:
                attributes = ModelAttributes.model_validate(entry.attributes)
                allowed.append((attributes.cost_per_million or 0.0, entry))
        return [entry for _, entry in sorted(allowed, key=lambda pair: pair[0])]

    def check_model(
        self, entry: CatalogEntry, policy, *, groups: Optional[list[str]] = None,
        workspace: str = "", environment: str = "development",
    ) -> ModelDecision:
        """Whether one catalogued model satisfies a policy. Deny by default."""
        attributes = ModelAttributes.model_validate(entry.attributes)

        if entry.status is ApprovalStatus.RETIRED:
            return ModelDecision(False, f"'{entry.name}' is retired" + (
                f"; use {entry.superseded_by}" if entry.superseded_by else ""), entry)
        if not entry.selectable:
            return ModelDecision(
                False, f"'{entry.name}' is {entry.status.value}, not approved", entry)
        if not entry.available_to(groups=groups, workspace=workspace,
                                  environment=environment):
            return ModelDecision(
                False,
                f"'{entry.name}' is restricted and this agent is not entitled to it",
                entry,
            )
        if entry.id in policy.deny or entry.name in policy.deny:
            return ModelDecision(False, f"'{entry.name}' is denied by policy", entry)

        # An explicit allow list, when present, is the whole answer.
        if policy.allow:
            if entry.id in policy.allow or entry.name in policy.allow:
                return ModelDecision(True, f"'{entry.name}' is named in the policy",
                                     entry)
            return ModelDecision(
                False, f"'{entry.name}' is not in this agent's approved list", entry)

        wanted = {c.value if hasattr(c, "value") else c for c in policy.classes}
        if wanted and not wanted & set(attributes.classes):
            return ModelDecision(
                False,
                f"'{entry.name}' provides {attributes.classes or ['no declared class']}"
                f", the policy asks for {sorted(wanted)}",
                entry,
            )
        if policy.require_no_training_on_data and attributes.trains_on_data:
            return ModelDecision(
                False, f"'{entry.name}' trains on submitted data", entry)
        if policy.min_context_tokens and attributes.context_tokens < policy.min_context_tokens:
            return ModelDecision(
                False,
                f"'{entry.name}' has {attributes.context_tokens} context tokens, "
                f"below the required {policy.min_context_tokens}",
                entry,
            )
        cost = attributes.cost_per_million
        if policy.max_cost_per_million_tokens is not None and cost is not None:
            if cost > policy.max_cost_per_million_tokens:
                return ModelDecision(
                    False,
                    f"'{entry.name}' costs {cost} per million tokens, over the "
                    f"{policy.max_cost_per_million_tokens} ceiling",
                    entry,
                )
        if policy.require_regions:
            if not set(policy.require_regions) & set(attributes.regions):
                return ModelDecision(
                    False,
                    f"'{entry.name}' runs in {attributes.regions or ['unstated regions']}"
                    f", outside the required {policy.require_regions}",
                    entry,
                )
        return ModelDecision(True, f"'{entry.name}' satisfies the policy", entry)

    def resolve_model(
        self, policy, *, provider: str = "", model_id: str = "",
        groups: Optional[list[str]] = None, workspace: str = "",
        environment: str = "development",
    ) -> ModelDecision:
        """Check the bound model against the policy, and offer alternatives."""
        permitted = self.permitted_models(policy, groups=groups, workspace=workspace,
                                          environment=environment)
        names = [e.name for e in permitted]
        if not model_id:
            if not permitted:
                return ModelDecision(
                    False, "no catalogued model satisfies this policy", None, names)
            return ModelDecision(
                True, f"no model bound; '{permitted[0].name}' would be chosen",
                permitted[0], names)

        entry = self.model_for(provider, model_id)
        if entry is None:
            return ModelDecision(
                False,
                f"'{model_id}' is not in the catalog, so nobody has approved it",
                None, names,
            )
        decision = self.check_model(entry, policy, groups=groups, workspace=workspace,
                                    environment=environment)
        decision.alternatives = names
        return decision

    # -- reporting ---------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        entries = self.list()
        by_kind: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for entry in entries:
            by_kind[entry.kind.value] = by_kind.get(entry.kind.value, 0) + 1
            by_status[entry.status.value] = by_status.get(entry.status.value, 0) + 1
        return {
            "total": len(entries),
            "by_kind": by_kind,
            "by_status": by_status,
            "installs": sum(e.installs for e in entries),
            "unreviewed": [e.name for e in entries
                           if e.status in (ApprovalStatus.PROPOSED,
                                           ApprovalStatus.IN_REVIEW)],
            "deprecated_in_use": [e.name for e in entries
                                  if e.status is ApprovalStatus.DEPRECATED
                                  and e.installs],
        }

    def describe(self, entry_id: str) -> dict[str, Any]:
        entry = self._require(entry_id)
        return {
            "entry": entry.model_dump(mode="json"),
            "attributes": (
                typed_attributes(entry).model_dump(mode="json")
                if hasattr(typed_attributes(entry), "model_dump")
                else entry.attributes
            ),
            "selectable": entry.selectable,
        }
