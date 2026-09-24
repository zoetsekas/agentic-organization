"""Which designs use which catalog entry (WS-027 M5).

Retiring an entry is a decision about other people's systems. Until something
records the reference, "retire the old model" is taken blind: the catalog knows
what it holds and nothing about who depends on it.

Usage is recorded from the resolved IR rather than from the spec, because the
IR is where a reference is concrete — after fallback, after entitlement — so
the record says what a design would actually run on.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..ids import new_id, now_iso


class CatalogUsage(BaseModel):
    """One design's reference to one catalog entry."""

    id: str = Field(default_factory=lambda: new_id("use"))
    entry_id: str
    entry_name: str = ""
    system: str = ""            # the design, by spec name
    subject: str = ""           # the agent (or other object) referring to it
    role: str = "model"         # what it is used as
    note: str = ""
    recorded_at: str = Field(default_factory=now_iso)


def references_in_ir(ir: Any) -> list[CatalogUsage]:
    """Every catalog reference a resolved IR makes.

    Duck-typed on purpose: the catalog does not import the compiler, and any
    object shaped like a `SystemIR` can be scanned.
    """
    found: list[CatalogUsage] = []
    for agent in getattr(ir, "agents", []):
        approval = getattr(agent, "model_approval", None)
        if approval is None:
            continue
        for entry_id, role, model in (
            (approval.catalog_entry, "model", approval.model),
            (approval.subagent_catalog_entry, "subagent_model",
             approval.subagent_model or ""),
        ):
            if not entry_id:
                continue
            found.append(CatalogUsage(
                entry_id=entry_id, system=getattr(ir, "name", ""),
                subject=agent.id, role=role,
                note=f"bound to '{model}'" if model else "",
            ))
    return found


class UsageIndex:
    """Stored references, one row per design/subject/entry."""

    COLLECTION = "catalog_usage"

    def __init__(self, store) -> None:
        self.store = store

    def record(self, usage: CatalogUsage) -> CatalogUsage:
        # Re-compiling the same design must not pile up duplicate rows, so a
        # reference is keyed by (system, subject, role) and replaced.
        existing = next(
            (u for u in self.for_system(usage.system)
             if u.subject == usage.subject and u.role == usage.role), None)
        if existing is not None:
            usage.id = existing.id
        usage.recorded_at = now_iso()
        self.store.put(self.COLLECTION, usage, parent=usage.entry_id,
                       name=f"{usage.system}:{usage.subject}:{usage.role}")
        return usage

    def all(self) -> list[CatalogUsage]:
        return self.store.list(self.COLLECTION, CatalogUsage, limit=5000)

    def for_entry(self, entry_id: str) -> list[CatalogUsage]:
        return self.store.list(self.COLLECTION, CatalogUsage, parent=entry_id,
                               limit=5000)

    def for_system(self, system: str) -> list[CatalogUsage]:
        return [u for u in self.all() if u.system == system]

    def forget_system(self, system: str) -> int:
        """Drop a design's references — it was deleted, or no longer uses them."""
        rows = self.for_system(system)
        for row in rows:
            self.store.delete(self.COLLECTION, row.id)
        return len(rows)

    def systems_using(self, entry_id: str) -> list[str]:
        return sorted({u.system for u in self.for_entry(entry_id) if u.system})
