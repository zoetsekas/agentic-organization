"""Private / protected / public data planes with per-agent access control.

Every read and write goes through `DataPlanes`, which resolves the calling
agent's `DataGrant`s against the record's visibility and groups. Denials raise
`AccessDenied` so they show up in the trace rather than silently returning
empty results.
"""
from __future__ import annotations

import fnmatch
from typing import Any, Optional

from ..ids import now_iso
from ..models import Agent, DataGrant, DataRecord, Visibility
from ..store import RECORDS, Store


class AccessDenied(PermissionError):
    """Raised when an agent's grants do not cover the requested operation."""


def _grant_matches(grant: DataGrant, record: DataRecord, agent: Agent) -> bool:
    if grant.visibility is not record.visibility:
        return False
    if not fnmatch.fnmatch(record.namespace, grant.namespace_glob):
        return False
    if record.visibility is Visibility.PRIVATE:
        return record.owner_agent_id == agent.id
    if record.visibility is Visibility.PROTECTED:
        allowed = set(grant.groups) & set(agent.groups)
        return bool(allowed & set(record.groups))
    return True  # PUBLIC


class DataPlanes:
    """Policy-enforcing façade over the record collection."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- policy ------------------------------------------------------------

    def can_read(self, agent: Agent, record: DataRecord) -> bool:
        return any(
            g.can_read and _grant_matches(g, record, agent)
            for g in agent.harness.data_grants
        )

    def can_write(self, agent: Agent, record: DataRecord) -> bool:
        # An agent can always write its own private records.
        if (
            record.visibility is Visibility.PRIVATE
            and record.owner_agent_id == agent.id
        ):
            return True
        return any(
            g.can_write and _grant_matches(g, record, agent)
            for g in agent.harness.data_grants
        )

    # -- operations --------------------------------------------------------

    def write(
        self,
        agent: Agent,
        namespace: str,
        key: str,
        value: Any,
        *,
        visibility: Visibility = Visibility.PRIVATE,
        groups: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> DataRecord:
        groups = groups or []
        if visibility is Visibility.PROTECTED and not groups:
            raise ValueError("protected records require at least one group")
        if visibility is Visibility.PROTECTED and not set(groups) <= set(agent.groups):
            raise AccessDenied(
                f"{agent.name} cannot publish to groups outside its membership"
            )
        existing = self.find(agent, namespace, key, visibility=visibility)
        record = existing or DataRecord(
            namespace=namespace,
            key=key,
            value=value,
            visibility=visibility,
            owner_agent_id=agent.id,
            groups=groups,
            tags=tags or [],
        )
        if existing:
            record.value = value
            record.updated_at = now_iso()
            if tags:
                record.tags = tags
        if not self.can_write(agent, record):
            raise AccessDenied(
                f"{agent.name} lacks write grant for {visibility.value}:{namespace}/{key}"
            )
        self.store.put(RECORDS, record, parent=record.namespace, name=record.key)
        return record

    def read(
        self,
        agent: Agent,
        namespace: str,
        key: str,
        *,
        visibility: Optional[Visibility] = None,
    ) -> Optional[DataRecord]:
        record = self.find(agent, namespace, key, visibility=visibility)
        if record is None:
            return None
        if not self.can_read(agent, record):
            raise AccessDenied(
                f"{agent.name} lacks read grant for {record.visibility.value}:{namespace}/{key}"
            )
        return record

    def find(
        self,
        agent: Agent,
        namespace: str,
        key: str,
        *,
        visibility: Optional[Visibility] = None,
    ) -> Optional[DataRecord]:
        """Locate a record without enforcing read policy (internal helper)."""
        for r in self.store.list(RECORDS, DataRecord, parent=namespace, limit=2000):
            if r.key != key:
                continue
            if visibility is not None and r.visibility is not visibility:
                continue
            if r.visibility is Visibility.PRIVATE and r.owner_agent_id != agent.id:
                continue
            return r
        return None

    def query(
        self,
        agent: Agent,
        *,
        namespace_glob: str = "*",
        tag: Optional[str] = None,
        limit: int = 100,
    ) -> list[DataRecord]:
        """Every record the agent is allowed to see, filtered."""
        out: list[DataRecord] = []
        for r in self.store.list(RECORDS, DataRecord, limit=5000):
            if not fnmatch.fnmatch(r.namespace, namespace_glob):
                continue
            if tag and tag not in r.tags:
                continue
            if self.can_read(agent, r):
                out.append(r)
            if len(out) >= limit:
                break
        return out

    def contribute_public(
        self, agent: Agent, namespace: str, key: str, value: Any, **kw: Any
    ) -> DataRecord:
        """Shorthand for the 'any agent may contribute' public knowledge base."""
        return self.write(
            agent, namespace, key, value, visibility=Visibility.PUBLIC, **kw
        )
