"""Catalog and marketplace.

One index over everything installable or discoverable — agents, skills,
plugins, workflows, sandbox templates and shareable sessions — with
marketplace affordances: publish, search, filter, install, rate, and
visibility-aware listing so a PROTECTED listing only surfaces to its groups.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from .ids import now_iso
from .models import (
    Agent,
    AgentSession,
    CatalogEntry,
    Plugin,
    SandboxTemplate,
    Skill,
    Visibility,
    WorkflowRef,
)
from .store import (
    AGENTS,
    CATALOG,
    PLUGINS,
    SESSIONS,
    SKILLS,
    Store,
    WORKFLOWS,
)
from .store import SANDBOX_TEMPLATES as TEMPLATE_COLLECTION

Kind = Literal["agent", "skill", "plugin", "workflow", "sandbox_template", "session"]

_COLLECTION: dict[str, tuple[str, type]] = {
    "agent": (AGENTS, Agent),
    "skill": (SKILLS, Skill),
    "plugin": (PLUGINS, Plugin),
    "workflow": (WORKFLOWS, WorkflowRef),
    "sandbox_template": (TEMPLATE_COLLECTION, SandboxTemplate),
    "session": (SESSIONS, AgentSession),
}


class Catalog:
    def __init__(self, store: Store, base_url: str = "http://localhost:8000") -> None:
        self.store = store
        self.base_url = base_url.rstrip("/")

    # -- publishing --------------------------------------------------------

    def publish(
        self,
        kind: Kind,
        ref_id: str,
        *,
        name: str = "",
        summary: str = "",
        owner: str = "",
        tags: Optional[list[str]] = None,
        visibility: Visibility = Visibility.PUBLIC,
        groups: Optional[list[str]] = None,
        version: str = "0.1.0",
        org_unit_id: Optional[str] = None,
    ) -> CatalogEntry:
        if visibility is Visibility.PROTECTED and not groups:
            raise ValueError("protected listings require groups")
        collection, model = _COLLECTION[kind]
        obj = self.store.get(collection, ref_id, model)
        if obj is None:
            raise KeyError(f"no {kind} with id {ref_id}")
        existing = self.entry_for(kind, ref_id)
        entry = existing or CatalogEntry(kind=kind, ref_id=ref_id, name=name or ref_id)
        entry.name = name or getattr(obj, "name", "") or entry.name
        entry.summary = summary or getattr(obj, "description", "") or entry.summary
        entry.owner = owner or entry.owner
        entry.tags = tags if tags is not None else entry.tags
        entry.visibility = visibility
        entry.groups = groups or []
        entry.version = version
        entry.org_unit_id = org_unit_id or entry.org_unit_id
        entry.published_at = now_iso()
        self.store.put(CATALOG, entry, parent=kind, name=entry.name)
        if kind == "agent" and isinstance(obj, Agent):
            obj.published = True
            self.store.put(AGENTS, obj, parent=obj.org_unit_id)
        return entry

    def unpublish(self, entry_id: str) -> bool:
        return self.store.delete(CATALOG, entry_id)

    def entry_for(self, kind: Kind, ref_id: str) -> Optional[CatalogEntry]:
        return next(
            (
                e
                for e in self.store.list(CATALOG, CatalogEntry, parent=kind, limit=2000)
                if e.ref_id == ref_id
            ),
            None,
        )

    # -- discovery ---------------------------------------------------------

    def search(
        self,
        q: str = "",
        *,
        kind: Optional[Kind] = None,
        tags: Optional[list[str]] = None,
        viewer_groups: Optional[list[str]] = None,
        sort: Literal["recent", "installs", "rating", "name"] = "recent",
        limit: int = 50,
    ) -> list[CatalogEntry]:
        entries = self.store.list(CATALOG, CatalogEntry, parent=kind or None, limit=5000)
        viewer_groups = viewer_groups or []
        ql = q.lower()

        def visible(e: CatalogEntry) -> bool:
            if e.visibility is Visibility.PUBLIC:
                return True
            if e.visibility is Visibility.PROTECTED:
                return bool(set(e.groups) & set(viewer_groups))
            return False  # private listings never surface in the marketplace

        def matches(e: CatalogEntry) -> bool:
            if ql and ql not in f"{e.name} {e.summary} {' '.join(e.tags)}".lower():
                return False
            if tags and not set(tags) <= set(e.tags):
                return False
            return visible(e)

        found = [e for e in entries if matches(e)]
        keys = {
            "recent": lambda e: e.published_at,
            "installs": lambda e: e.installs,
            "rating": lambda e: e.rating,
            "name": lambda e: e.name.lower(),
        }
        found.sort(key=keys[sort], reverse=sort != "name")
        return found[:limit]

    def get(self, entry_id: str) -> Optional[CatalogEntry]:
        return self.store.get(CATALOG, entry_id, CatalogEntry)

    def detail(self, entry_id: str) -> Optional[dict[str, Any]]:
        entry = self.get(entry_id)
        if entry is None:
            return None
        collection, model = _COLLECTION[entry.kind]
        obj = self.store.get(collection, entry.ref_id, model)
        return {
            "entry": entry.model_dump() | {"rating": entry.rating},
            "object": obj.model_dump() if obj else None,
            "url": self.url(entry),
        }

    def url(self, entry: CatalogEntry) -> str:
        if entry.kind == "session":
            return f"{self.base_url}/sessions/{entry.ref_id}"
        return f"{self.base_url}/catalog/{entry.id}"

    # -- marketplace actions ----------------------------------------------

    def install(self, entry_id: str, target_agent_id: str) -> dict[str, Any]:
        """Attach a catalog item to an agent, recording the install."""
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(f"no catalog entry {entry_id}")
        agent = self.store.get(AGENTS, target_agent_id, Agent)
        if agent is None:
            raise KeyError(f"no agent {target_agent_id}")
        if entry.visibility is Visibility.PROTECTED and not (
            set(entry.groups) & set(agent.groups)
        ):
            raise PermissionError(
                f"{agent.name} is not in a group entitled to '{entry.name}'"
            )

        if entry.kind == "skill":
            _append_unique(agent.skill_ids, entry.ref_id)
        elif entry.kind == "plugin":
            _append_unique(agent.plugin_ids, entry.ref_id)
            plugin = self.store.get(PLUGINS, entry.ref_id, Plugin)
            if plugin:
                for skill_id in plugin.provides_skills:
                    _append_unique(agent.skill_ids, skill_id)
                for tool in plugin.provides_tools:
                    if not any(t.name == tool.name for t in agent.harness.tools):
                        agent.harness.tools.append(tool)
        elif entry.kind == "workflow":
            _append_unique(agent.workflow_ids, entry.ref_id)
        elif entry.kind == "sandbox_template":
            from .models import SandboxSpec

            agent.sandbox = SandboxSpec(template_id=entry.ref_id)
        elif entry.kind == "agent":
            # Installing an agent means hiring a copy into your own org unit.
            source = self.store.get(AGENTS, entry.ref_id, Agent)
            if source is None:
                raise KeyError("source agent missing")
            clone = source.model_copy(deep=True)
            clone.id = f"agt_{entry.ref_id[-8:]}_{target_agent_id[-6:]}"
            clone.manager_agent_id = target_agent_id
            clone.org_unit_id = agent.org_unit_id
            clone.published = False
            self.store.put(AGENTS, clone, parent=clone.org_unit_id)
            _append_unique(agent.report_agent_ids, clone.id)
        else:
            raise ValueError(f"'{entry.kind}' entries are not installable")

        agent.updated_at = now_iso()
        self.store.put(AGENTS, agent, parent=agent.org_unit_id)
        entry.installs += 1
        self.store.put(CATALOG, entry, parent=entry.kind)
        return {"installed": entry.kind, "ref_id": entry.ref_id, "agent_id": agent.id}

    def rate(self, entry_id: str, stars: float) -> CatalogEntry:
        if not 1 <= stars <= 5:
            raise ValueError("rating must be between 1 and 5")
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(f"no catalog entry {entry_id}")
        entry.rating_sum += stars
        entry.rating_count += 1
        self.store.put(CATALOG, entry, parent=entry.kind)
        return entry

    def stats(self) -> dict[str, Any]:
        entries = self.store.list(CATALOG, CatalogEntry, limit=5000)
        by_kind: dict[str, int] = {}
        for e in entries:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        top = sorted(entries, key=lambda e: e.installs, reverse=True)[:5]
        return {
            "total": len(entries),
            "by_kind": by_kind,
            "installs": sum(e.installs for e in entries),
            "top": [{"name": e.name, "kind": e.kind, "installs": e.installs} for e in top],
        }


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
