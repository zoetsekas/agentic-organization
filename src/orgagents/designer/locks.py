"""Advisory locks over a design, and over parts of one (ADR-0033).

Locks are **advisory and time-boxed**: they tell a second editor that someone
else is working on this, and they expire so an abandoned browser tab does not
freeze a design until an administrator intervenes. They are not the only
protection — every write still carries a version and is checked optimistically
— which is why a stale or broken lock cannot corrupt anything.

Two scopes: the whole system, or one component. Component locks are what make
concurrent work pleasant: two people editing different agents in the same
design never collide.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import Lock, LockScope
from .rbac import Principal


class LockConflict(RuntimeError):
    """Raised when a lock is held by somebody else."""

    def __init__(self, lock: Lock) -> None:
        super().__init__(
            f"'{lock.target}' is locked by {lock.holder_name or lock.holder} "
            f"until {lock.expires_at}"
        )
        self.lock = lock


def _now() -> datetime:
    return datetime.now(timezone.utc)


def expired(lock: Lock, now: Optional[datetime] = None) -> bool:
    if not lock.expires_at:
        return False
    return datetime.fromisoformat(lock.expires_at) <= (now or _now())


class LockManager:
    """Acquire, refresh, release and break locks."""

    def __init__(self, repository, ttl_seconds: int = 900) -> None:
        self.repository = repository
        self.ttl_seconds = ttl_seconds

    # -- queries -----------------------------------------------------------

    def active(self, system_id: str, now: Optional[datetime] = None) -> list[Lock]:
        """Live locks, dropping any that have timed out."""
        now = now or _now()
        live = []
        for lock in self.repository.locks(system_id):
            if expired(lock, now):
                self.repository.drop_lock(lock.id)
            else:
                live.append(lock)
        return live

    def holder_of(self, system_id: str, target: str,
                  now: Optional[datetime] = None) -> Optional[Lock]:
        return next(
            (lock for lock in self.active(system_id, now) if lock.covers(target)), None
        )

    def blocks(self, system_id: str, target: str, principal: Principal,
               now: Optional[datetime] = None) -> Optional[Lock]:
        """The lock standing in this principal's way, if any."""
        lock = self.holder_of(system_id, target, now)
        return lock if lock and lock.holder != principal.user_id else None

    # -- mutations ---------------------------------------------------------

    def acquire(self, system_id: str, principal: Principal, *,
                scope: LockScope = LockScope.COMPONENT, target: str = "*",
                note: str = "", ttl_seconds: Optional[int] = None,
                now: Optional[datetime] = None) -> Lock:
        now = now or _now()
        blocking = self.blocks(system_id, target, principal, now)
        if blocking is not None:
            raise LockConflict(blocking)

        # Re-acquiring your own lock refreshes it rather than stacking a second.
        mine = self.holder_of(system_id, target, now)
        ttl = ttl_seconds or self.ttl_seconds
        if mine is not None and mine.holder == principal.user_id:
            mine.expires_at = (now + timedelta(seconds=ttl)).isoformat()
            return self.repository.put_lock(mine)

        lock = Lock(
            system_id=system_id, scope=scope, target=target,
            holder=principal.user_id, holder_name=principal.label,
            acquired_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=ttl)).isoformat(),
            note=note,
        )
        return self.repository.put_lock(lock)

    def heartbeat(self, system_id: str, principal: Principal, target: str = "*",
                  now: Optional[datetime] = None) -> Optional[Lock]:
        """Extend a lock the principal already holds; editors call this while typing."""
        now = now or _now()
        lock = self.holder_of(system_id, target, now)
        if lock is None or lock.holder != principal.user_id:
            return None
        lock.expires_at = (now + timedelta(seconds=self.ttl_seconds)).isoformat()
        return self.repository.put_lock(lock)

    def release(self, system_id: str, principal: Principal, target: str = "*",
                now: Optional[datetime] = None) -> bool:
        lock = self.holder_of(system_id, target, now)
        if lock is None or lock.holder != principal.user_id:
            return False
        return self.repository.drop_lock(lock.id)

    def break_lock(self, system_id: str, target: str = "*") -> bool:
        """Force-release someone else's lock. The caller checks permission first."""
        lock = self.holder_of(system_id, target)
        return self.repository.drop_lock(lock.id) if lock else False

    def release_all(self, system_id: str, principal: Principal) -> int:
        dropped = 0
        for lock in self.active(system_id):
            if lock.holder == principal.user_id:
                dropped += int(self.repository.drop_lock(lock.id))
        return dropped
