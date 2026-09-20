"""Two-tier agent memory: session and long term (ADR-0028).

**Session memory** is short term and scoped to one session. It holds what the
agent is working with right now — intermediate findings, sub-agent results, the
shape of the task. It is cheap to write, never shared, and expires with the
session.

**Long-term memory** survives sessions and is recalled *into* them. It is the
same governed material as any other data: every entry carries a data class and
lives in a namespace with a sharing scope, so who can recall what is decided by
the same rules that decide who can read anything else (ADR-0017).

The bridge between them is **promotion**, and it is deliberately narrow: a
session memory becomes long term only when the policy allows it, only if its
data class is permitted in the target namespace, and — where the policy says so
— only after a human agrees. Everything an agent happens to see does not become
something it remembers forever.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from .data.planes import AccessDenied
from .ids import new_id, now_iso
from .models import Visibility
from .spec.model import MemoryNamespace, MemoryPolicy, MemoryTier, RecallMode, SharingScope
from .store import Store

MEMORIES = "memories"


class MemoryError(RuntimeError):
    """Raised when a memory operation violates the memory contract."""


class MemoryEntry(BaseModel):
    """One remembered thing."""

    id: str = Field(default_factory=lambda: new_id("mem"))
    agent_id: str
    tier: MemoryTier = MemoryTier.SESSION
    session_id: Optional[str] = None
    namespace: str = "default"
    key: str = ""
    content: Any = None
    data_class: str = ""
    scope: SharingScope = SharingScope.PRIVATE
    groups: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    # Where it came from: a session, a promotion, a human.
    source: str = ""
    created_at: str = Field(default_factory=now_iso)
    expires_at: Optional[str] = None
    recall_count: int = 0
    last_recalled_at: Optional[str] = None

    def expired(self, now: Optional[datetime] = None) -> bool:
        if not self.expires_at:
            return False
        moment = now or datetime.now(timezone.utc)
        return datetime.fromisoformat(self.expires_at) <= moment


@dataclass
class ResolvedMemory:
    """An agent's effective memory contract, resolved by the compiler."""

    agent_id: str
    session: MemoryPolicy
    long_term: MemoryPolicy
    namespaces: list[MemoryNamespace] = field(default_factory=list)
    groups: tuple[str, ...] = ()
    readable_data_classes: tuple[str, ...] = ()

    def namespace(self, namespace_id: str) -> Optional[MemoryNamespace]:
        return next((n for n in self.namespaces if n.id == namespace_id), None)

    def policy(self, tier: MemoryTier) -> MemoryPolicy:
        return self.session if tier is MemoryTier.SESSION else self.long_term


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"\W+", text.lower()) if len(t) > 2}


class MemoryManager:
    """Reads and writes memory under an agent's resolved contract."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- writing -----------------------------------------------------------

    def remember(
        self,
        contract: ResolvedMemory,
        content: Any,
        *,
        session_id: Optional[str] = None,
        key: str = "",
        tier: MemoryTier = MemoryTier.SESSION,
        namespace: str = "default",
        data_class: str = "",
        tags: Optional[list[str]] = None,
        now: Optional[datetime] = None,
        source: str = "",
    ) -> MemoryEntry:
        now = now or datetime.now(timezone.utc)
        policy = contract.policy(tier)
        if not policy.enabled:
            raise MemoryError(f"{tier.value} memory is disabled for {contract.agent_id}")
        if tier is MemoryTier.SESSION and not session_id:
            raise MemoryError("session memory needs a session id")

        self._check_data_class(contract, policy, data_class, tier)
        scope, groups = SharingScope.PRIVATE, []
        if tier is MemoryTier.LONG_TERM:
            ns = contract.namespace(namespace)
            if ns is None:
                raise MemoryError(
                    f"namespace '{namespace}' is not available to {contract.agent_id}"
                )
            if ns.data_classes and data_class and data_class not in ns.data_classes:
                raise MemoryError(
                    f"namespace '{namespace}' does not hold data class '{data_class}'"
                )
            scope, groups = ns.scope, list(ns.groups)
            if scope is SharingScope.PROTECTED and not set(groups) & set(contract.groups):
                raise AccessDenied(
                    f"{contract.agent_id} is not in a group that owns namespace "
                    f"'{namespace}'"
                )

        expires = self._expiry(policy, tier, now)
        entry = MemoryEntry(
            agent_id=contract.agent_id,
            tier=tier,
            session_id=session_id if tier is MemoryTier.SESSION else None,
            namespace=namespace,
            key=key,
            content=self._redact(content, policy, data_class),
            data_class=data_class,
            scope=scope,
            groups=groups,
            tags=tags or [],
            source=source or (session_id or "direct"),
            expires_at=expires.isoformat() if expires else None,
        )
        self._enforce_cap(contract, policy, tier, session_id, namespace, now)
        parent = session_id if tier is MemoryTier.SESSION else f"lt:{namespace}"
        self.store.put(MEMORIES, entry, parent=parent, name=key or entry.id)
        return entry

    def _expiry(
        self, policy: MemoryPolicy, tier: MemoryTier, now: datetime
    ) -> Optional[datetime]:
        if policy.retention_days:
            return now + timedelta(days=policy.retention_days)
        if tier is MemoryTier.SESSION:
            # A session memory with no retention still expires; it is short term
            # by definition, and an unbounded "session" store is a long-term one
            # nobody declared.
            return now + timedelta(hours=24)
        return None

    def _check_data_class(
        self, contract: ResolvedMemory, policy: MemoryPolicy, data_class: str,
        tier: MemoryTier,
    ) -> None:
        if data_class and contract.readable_data_classes and (
            data_class not in contract.readable_data_classes
        ):
            raise AccessDenied(
                f"{contract.agent_id} may not read data class '{data_class}', so it "
                "may not remember it"
            )
        if policy.data_classes and data_class and data_class not in policy.data_classes:
            raise MemoryError(
                f"{tier.value} memory does not accept data class '{data_class}'"
            )

    def _redact(self, content: Any, policy: MemoryPolicy, data_class: str) -> Any:
        if data_class and data_class in policy.redact_data_classes:
            return "[redacted: " + data_class + "]"
        return content

    def _enforce_cap(
        self, contract: ResolvedMemory, policy: MemoryPolicy, tier: MemoryTier,
        session_id: Optional[str], namespace: str, now: datetime,
    ) -> None:
        """Keep a tier within its item cap by dropping the oldest entries."""
        parent = session_id if tier is MemoryTier.SESSION else f"lt:{namespace}"
        existing = [
            e for e in self.store.list(MEMORIES, MemoryEntry, parent=parent, limit=5000)
            if e.agent_id == contract.agent_id
        ]
        live = [e for e in existing if not e.expired(now)]
        for stale in (e for e in existing if e.expired(now)):
            self.store.delete(MEMORIES, stale.id)
        overflow = len(live) - policy.max_items + 1
        if overflow > 0:
            for entry in sorted(live, key=lambda e: e.created_at)[:overflow]:
                self.store.delete(MEMORIES, entry.id)

    # -- reading -----------------------------------------------------------

    def session_memories(
        self, contract: ResolvedMemory, session_id: str,
        now: Optional[datetime] = None,
    ) -> list[MemoryEntry]:
        now = now or datetime.now(timezone.utc)
        return [
            e for e in self.store.list(MEMORIES, MemoryEntry, parent=session_id,
                                       limit=2000)
            if e.agent_id == contract.agent_id and not e.expired(now)
        ]

    def recall(
        self,
        contract: ResolvedMemory,
        query: str = "",
        *,
        session_id: Optional[str] = None,
        namespace_glob: str = "*",
        tier: Optional[MemoryTier] = None,
        limit: int = 10,
        now: Optional[datetime] = None,
    ) -> list[MemoryEntry]:
        """Find memories this agent may see, most relevant first.

        Relevance is token overlap: deliberately simple and explainable. A
        target may bind a vector index instead; the contract is what matters.
        """
        now = now or datetime.now(timezone.utc)
        if contract.long_term.recall is RecallMode.NONE and tier is not MemoryTier.SESSION:
            return []
        wanted = _tokens(query)
        candidates: list[MemoryEntry] = []
        for entry in self.store.list(MEMORIES, MemoryEntry, limit=5000):
            if entry.expired(now):
                continue
            if tier is not None and entry.tier is not tier:
                continue
            if entry.tier is MemoryTier.SESSION:
                if session_id is None or entry.session_id != session_id:
                    continue
                if entry.agent_id != contract.agent_id:
                    continue
            else:
                if not fnmatch.fnmatch(entry.namespace, namespace_glob):
                    continue
                if not self.may_recall(contract, entry):
                    continue
            candidates.append(entry)

        def score(entry: MemoryEntry) -> tuple[float, str]:
            if not wanted:
                return (0.0, entry.created_at)
            text = f"{entry.key} {entry.content} {' '.join(entry.tags)}"
            overlap = len(wanted & _tokens(str(text)))
            return (overlap, entry.created_at)

        ranked = sorted(candidates, key=score, reverse=True)
        if wanted:
            ranked = [e for e in ranked if score(e)[0] > 0] or []
        found = ranked[:limit]
        for entry in found:
            entry.recall_count += 1
            entry.last_recalled_at = now.isoformat()
            parent = entry.session_id if entry.tier is MemoryTier.SESSION else (
                f"lt:{entry.namespace}"
            )
            self.store.put(MEMORIES, entry, parent=parent, name=entry.key or entry.id)
        return found

    def may_recall(self, contract: ResolvedMemory, entry: MemoryEntry) -> bool:
        """Long-term recall obeys the same sharing rules as any other data."""
        if entry.scope is SharingScope.PRIVATE:
            return entry.agent_id == contract.agent_id
        if entry.scope is SharingScope.PROTECTED:
            return bool(set(entry.groups) & set(contract.groups))
        return True

    # -- promotion ---------------------------------------------------------

    def promote(
        self,
        contract: ResolvedMemory,
        entry_id: str,
        *,
        namespace: str,
        approved: bool = False,
        now: Optional[datetime] = None,
    ) -> MemoryEntry:
        """Move a session memory into long-term storage, if policy allows."""
        now = now or datetime.now(timezone.utc)
        source = self.store.get(MEMORIES, entry_id, MemoryEntry)
        if source is None:
            raise MemoryError(f"no memory '{entry_id}'")
        if source.tier is not MemoryTier.SESSION:
            raise MemoryError("only session memories are promoted")
        if source.agent_id != contract.agent_id:
            raise AccessDenied("an agent may only promote its own session memories")

        policy = contract.long_term
        if not policy.promotion_allowed:
            raise MemoryError("promotion to long-term memory is not permitted")
        if policy.promotion_requires_approval and not approved:
            raise MemoryError(
                "promotion needs human approval; ask an approver before remembering "
                "this beyond the session"
            )

        promoted = self.remember(
            contract,
            source.content,
            key=source.key,
            tier=MemoryTier.LONG_TERM,
            namespace=namespace,
            data_class=source.data_class,
            tags=source.tags,
            now=now,
            source=f"promoted:{source.id}",
        )
        return promoted

    def forget(self, contract: ResolvedMemory, entry_id: str) -> bool:
        entry = self.store.get(MEMORIES, entry_id, MemoryEntry)
        if entry is None:
            return False
        if entry.agent_id != contract.agent_id:
            raise AccessDenied("an agent may only forget its own memories")
        return self.store.delete(MEMORIES, entry_id)

    def expire(self, now: Optional[datetime] = None) -> int:
        """Drop everything past its retention; safe to run on a schedule."""
        now = now or datetime.now(timezone.utc)
        dropped = 0
        for entry in self.store.list(MEMORIES, MemoryEntry, limit=10000):
            if entry.expired(now):
                self.store.delete(MEMORIES, entry.id)
                dropped += 1
        return dropped

    def stats(self, agent_id: Optional[str] = None) -> dict[str, Any]:
        entries = self.store.list(MEMORIES, MemoryEntry, limit=10000)
        if agent_id:
            entries = [e for e in entries if e.agent_id == agent_id]
        by_tier: dict[str, int] = {}
        for entry in entries:
            by_tier[entry.tier.value] = by_tier.get(entry.tier.value, 0) + 1
        return {
            "total": len(entries),
            "by_tier": by_tier,
            "namespaces": sorted({e.namespace for e in entries
                                  if e.tier is MemoryTier.LONG_TERM}),
            "recalls": sum(e.recall_count for e in entries),
        }


def visibility_of(scope: SharingScope) -> Visibility:
    return {
        SharingScope.PRIVATE: Visibility.PRIVATE,
        SharingScope.PROTECTED: Visibility.PROTECTED,
        SharingScope.PUBLIC: Visibility.PUBLIC,
    }[scope]
