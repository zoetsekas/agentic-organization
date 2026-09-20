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
    EDITORIAL_FIELDS,
    SUBSTANTIVE_FIELDS,
    ApprovalStatus,
    CatalogEntry,
    CatalogEvent,
    CatalogKind,
    Entitlement,
    FigureMethod,
    FigureProvenance,
    ModelAttributes,
    attribute_schema,
    typed_attributes,
)
from .sources import FigureSource
from .usage import CatalogUsage, UsageIndex, references_in_ir

# The phrase every stale-figure verdict carries, so callers downstream — the
# IR, the registry — can spot one without re-deriving the arithmetic.
STALE_MARKER = "stale figure"


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
    # True when the figures this verdict turned on are past their horizon.
    stale_figures: bool = False

    def __bool__(self) -> bool:
        return self.allowed


@dataclass
class RefreshReport:
    """What one refresh did, including what it deliberately did not touch."""

    source: str
    updated: dict[str, list[str]] = field(default_factory=dict)
    reconfirmed: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)

    @property
    def changed_any(self) -> bool:
        return bool(self.updated)

    def summary(self) -> str:
        return (f"{self.source}: {len(self.updated)} updated, "
                f"{len(self.reconfirmed)} reconfirmed, "
                f"{len(self.uncovered)} left alone (no figure in source)")


class CatalogService:
    """The design system's inventory of building blocks."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.usage = UsageIndex(store)

    # -- inventory ---------------------------------------------------------

    def publish(self, entry: CatalogEntry, *, actor: str = "") -> CatalogEntry:
        # A first publish is itself a mutation worth attributing; later
        # publishes are how every other verb persists, and those record their
        # own event before calling in here.
        if actor and not entry.history:
            entry.history.append(CatalogEvent(
                action="created", actor=actor,
                note=f"{entry.kind.value} '{entry.name}' v{entry.version}"))
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
        entry.history.append(CatalogEvent(
            action="reviewed", actor=reviewer, changes=["status"],
            note=f"status -> {status.value}" + (f": {note}" if note else "")))
        return self.publish(entry)

    def entitle(self, entry_id: str, entitlement: Entitlement, *,
                actor: str = "") -> CatalogEntry:
        entry = self._require(entry_id)
        entry.entitlement = entitlement
        entry.history.append(CatalogEvent(
            action="entitled", actor=actor, changes=["entitlement"],
            note=", ".join(entitlement.groups) or "everyone"))
        return self.publish(entry)

    def retire(self, entry_id: str, *, reviewer: str,
               superseded_by: Optional[str] = None,
               force: bool = False) -> CatalogEntry:
        """Retire an entry, refusing while designs still reference it.

        Refusing is the point of the usage index: retiring an entry breaks
        every design bound to it at the next compile, and `force` exists so
        that is a decision somebody makes rather than one they discover.
        """
        users = self.usage.systems_using(entry_id)
        if users and not force:
            raise CatalogError(
                f"'{entry_id}' is used by {', '.join(users)}; "
                "migrate them or retire with force=True")
        entry = self.review(
            entry_id, ApprovalStatus.RETIRED, reviewer=reviewer,
            note=f"superseded by {superseded_by}" if superseded_by else "retired",
        )
        # Set the pointer after the review, which re-reads from the store.
        entry.superseded_by = superseded_by
        return self.publish(entry)

    # -- editing (ADR-0062) ------------------------------------------------

    def attribute_schema(self, kind: CatalogKind) -> list[dict[str, Any]]:
        """The declared attributes of a kind, so one form serves all twelve."""
        return attribute_schema(kind)

    def update(self, entry_id: str, changes: dict[str, Any], *,
               actor: str = "", note: str = "") -> CatalogEntry:
        """Edit the editorial fields, at any status (ADR-0062 rule 1).

        Substantive keys are refused here rather than ignored: silently
        dropping an attribute change would let an operator believe the catalog
        holds something it does not.
        """
        entry = self._require(entry_id)
        offered = {k: v for k, v in changes.items() if v is not None}
        substantive = sorted(set(offered) & set(SUBSTANTIVE_FIELDS))
        if substantive:
            raise CatalogError(
                f"{', '.join(substantive)} {'are' if len(substantive) > 1 else 'is'} "
                "substantive; use amend")
        unknown = sorted(set(offered) - set(EDITORIAL_FIELDS))
        if unknown:
            raise CatalogError(
                f"not an editable field: {', '.join(unknown)}; "
                f"editorial fields are {', '.join(EDITORIAL_FIELDS)}")
        changed = [k for k, v in offered.items() if getattr(entry, k) != v]
        for key, value in offered.items():
            setattr(entry, key, value)
        entry.history.append(CatalogEvent(
            action="updated", actor=actor, changes=sorted(changed), note=note))
        return self.publish(entry)

    def amend(self, entry_id: str, changes: dict[str, Any], *,
              actor: str = "", note: str = "") -> CatalogEntry:
        """Edit the substantive fields, refused on an approved entry.

        The refusal names both ways forward, because an operator stopped here
        needs to know which one they want, not merely that they were stopped
        (ADR-0062 rule 2).
        """
        entry = self._require(entry_id)
        offered = {k: v for k, v in changes.items() if v is not None}
        unknown = sorted(set(offered) - set(SUBSTANTIVE_FIELDS))
        if unknown:
            raise CatalogError(
                f"not a substantive field: {', '.join(unknown)}; use update")
        if entry.substantively_locked:
            raise CatalogError(self.amend_refusal(entry))
        changed = []
        for key, value in offered.items():
            if key == "kind":
                value = CatalogKind(value)
            if getattr(entry, key) != value:
                changed.append(key)
            setattr(entry, key, value)
        entry.history.append(CatalogEvent(
            action="amended", actor=actor, changes=sorted(changed), note=note))
        return self.publish(entry)

    def amend_refusal(self, entry: CatalogEntry) -> str:
        """Why a substantive edit is unavailable, and the two ways forward.

        Exposed so the UI can show the reason *before* a form is filled in
        rather than after it is submitted.
        """
        return (
            f"'{entry.name}' is {entry.status.value}; kind, version and "
            "attributes decide what a design bound to it resolves to and "
            "cannot be edited after review. Either publish the next version "
            "as a new entry and supersede this one, or send this one back for "
            "review.")

    def send_back(self, entry_id: str, *, actor: str = "",
                  note: str = "") -> CatalogEntry:
        """Move an approved entry back to `proposed` (ADR-0062 rule 3).

        Deliberate, and consequential: the entry stops being selectable, so
        the systems already using it are named in the event.
        """
        entry = self._require(entry_id)
        users = self.usage.systems_using(entry_id)
        reason = note or "sent back for review"
        if users:
            reason += f"; unselectable for {', '.join(users)}"
        return self.review(entry_id, ApprovalStatus.PROPOSED, reviewer=actor,
                           note=reason)

    def delete(self, entry_id: str, *, actor: str = "") -> str:
        """Erase a draft nobody approved and nothing uses (ADR-0062 rule 6).

        Anything else retires: the review history is the only record of why
        something was allowed, and deleting it removes the answer.
        """
        entry = self._require(entry_id)
        if entry.status is not ApprovalStatus.PROPOSED or entry.reviewed_at:
            raise CatalogError(
                f"'{entry.name}' is {entry.status.value} and has been reviewed; "
                "retire it instead, so the decision stays readable")
        users = self.usage.systems_using(entry_id)
        if users:
            raise CatalogError(
                f"'{entry.name}' is used by {', '.join(users)}; retire it instead")
        self.store.delete(CATALOG, entry_id)
        return entry_id

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
        allowed: list[tuple[bool, float, CatalogEntry]] = []
        for entry in self.list(CatalogKind.MODEL):
            decision = self.check_model(entry, policy, groups=groups,
                                        workspace=workspace, environment=environment)
            if decision.allowed:
                attributes = ModelAttributes.model_validate(entry.attributes)
                # Sorted by (stale, cost): fallback picks the cheapest model
                # whose price we still believe, and only reaches a stale row
                # when no fresh one qualifies.
                allowed.append((entry.figures_stale(),
                                attributes.cost_per_million or 0.0, entry))
        return [entry for _, _, entry in sorted(allowed, key=lambda t: (t[0], t[1]))]

    def check_model(
        self, entry: CatalogEntry, policy, *, groups: Optional[list[str]] = None,
        workspace: str = "", environment: str = "development",
    ) -> ModelDecision:
        """Whether one catalogued model satisfies a policy. Deny by default."""
        attributes = ModelAttributes.model_validate(entry.attributes)
        stale = entry.figures_stale()
        staleness = f" [{STALE_MARKER}: {entry.provenance.describe()}]" if stale else ""

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
                return ModelDecision(
                    True, f"'{entry.name}' is named in the policy" + staleness,
                    entry, stale_figures=stale)
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
                f"below the required {policy.min_context_tokens}" + staleness,
                entry, stale_figures=stale,
            )
        cost = attributes.cost_per_million
        if policy.max_cost_per_million_tokens is not None and cost is not None:
            if cost > policy.max_cost_per_million_tokens:
                # A refusal on a stale figure still refuses: the safe side of a
                # cost ceiling is "too expensive", and the reason says which
                # number it doubted so an operator can refresh and retry.
                return ModelDecision(
                    False,
                    f"'{entry.name}' costs {cost} per million tokens, over the "
                    f"{policy.max_cost_per_million_tokens} ceiling" + staleness,
                    entry, stale_figures=stale,
                )
        if policy.require_regions:
            if not set(policy.require_regions) & set(attributes.regions):
                return ModelDecision(
                    False,
                    f"'{entry.name}' runs in {attributes.regions or ['unstated regions']}"
                    f", outside the required {policy.require_regions}",
                    entry,
                )
        # Permitting on a stale figure is allowed but never silent: the verdict
        # carries the marker all the way into the IR and the registry.
        return ModelDecision(True, f"'{entry.name}' satisfies the policy" + staleness,
                             entry, stale_figures=stale)

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

    # -- usage (WS-027 M5) -------------------------------------------------

    def record_usage(self, usage: CatalogUsage) -> CatalogUsage:
        return self.usage.record(usage)

    def record_ir_usage(self, ir: Any) -> list[CatalogUsage]:
        """Record every catalog reference a compiled design makes."""
        self.usage.forget_system(getattr(ir, "name", ""))
        return [self.usage.record(u) for u in references_in_ir(ir)]

    def usage_for(self, entry_id: str) -> list[CatalogUsage]:
        return self.usage.for_entry(entry_id)

    def usage_report(self) -> dict[str, Any]:
        """Who uses what, and which entries nobody uses."""
        rows = self.usage.all()
        by_entry: dict[str, list[str]] = {}
        for row in rows:
            by_entry.setdefault(row.entry_id, [])
            if row.system and row.system not in by_entry[row.entry_id]:
                by_entry[row.entry_id].append(row.system)
        names = {e.id: e.name for e in self.list()}
        return {
            "references": len(rows),
            "by_entry": {names.get(k, k): sorted(v) for k, v in by_entry.items()},
            "unused": sorted(n for i, n in names.items() if i not in by_entry),
        }

    # -- refreshing figures (WS-026 M6) ------------------------------------

    def refresh_figures(
        self, source: FigureSource, *, now: Optional[str] = None,
        horizon_days: Optional[int] = None,
        kind: CatalogKind = CatalogKind.MODEL,
    ) -> "RefreshReport":
        """Update entries from a source and record where the numbers came from.

        An entry the source has no figure for is left exactly as it was and
        reported as uncovered, rather than being blanked or marked confirmed —
        a source's silence says nothing about the figure already held.
        """
        report = RefreshReport(source=getattr(source, "name", "unnamed source"))
        for entry in self.list(kind):
            quote = source.quote(entry)
            figures = quote.clean() if quote else {}
            if not figures:
                report.uncovered.append(entry.name)
                continue
            changed = sorted(k for k, v in figures.items()
                             if entry.attributes.get(k) != v)
            entry.attributes = {**entry.attributes, **figures}
            entry.provenance = FigureProvenance(
                method=FigureMethod.IMPORTED,
                source=report.source,
                source_url=quote.source_url,
                confirmed_at=quote.observed_at or (now or now_iso()),
                staleness_horizon_days=(
                    horizon_days if horizon_days is not None
                    else entry.provenance.staleness_horizon_days),
                fields=sorted(figures),
                note=quote.note,
            )
            self.publish(entry)
            if changed:
                report.updated[entry.name] = changed
            else:
                report.reconfirmed.append(entry.name)
        return report

    def stale_entries(self, *, now: Optional[str] = None) -> list[CatalogEntry]:
        return [e for e in self.list() if e.figures_stale(now=now)]

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
            # Figures a policy may already have decided on, past their horizon.
            "stale_figures": [e.name for e in entries if e.figures_stale()],
            "placeholder_figures": [
                e.name for e in entries
                if e.provenance.method is FigureMethod.PLACEHOLDER],
            "figure_sources": sorted({e.provenance.source for e in entries
                                      if e.provenance.source}),
            "referenced_entries": len(self.usage_report()["by_entry"]),
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
