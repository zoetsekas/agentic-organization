"""Pluggable persistence for the designer (ADR-0031).

Three backends behind one protocol: an in-memory store for tests, a plain
**file system** layout that a team can keep in git, and a **relational** store
for a shared installation. The same test suite runs against all three, which is
the only honest way to claim the seam is real.

The protocol is deliberately small — list, get, save, delete, plus revisions,
workspaces, locks and settings — so a fourth backend is a day's work rather
than a redesign.
"""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from ..store import Store
from .audit import AuditEvent
from .models import (
    DesignerSettings,
    Lock,
    Revision,
    SystemRecord,
    Workspace,
)

SYSTEMS = "designer_systems"
REVISIONS = "designer_revisions"
WORKSPACES = "designer_workspaces"
LOCKS = "designer_locks"
SETTINGS = "designer_settings"
AUDIT = "designer_audit"


class VersionConflict(RuntimeError):
    """Raised when a write is based on a version that is no longer current."""

    def __init__(self, expected: int, actual: int, current: SystemRecord) -> None:
        super().__init__(
            f"write is based on version {expected}, but the stored version is "
            f"{actual}"
        )
        self.expected = expected
        self.actual = actual
        self.current = current


@runtime_checkable
class Repository(Protocol):
    """What every persistence backend must provide."""

    def list_systems(self, workspace_id: Optional[str] = None) -> list[SystemRecord]: ...
    def get_system(self, system_id: str) -> Optional[SystemRecord]: ...
    def save_system(self, record: SystemRecord, *, expected_version: Optional[int],
                    author: str, message: str = "") -> SystemRecord: ...
    def delete_system(self, system_id: str) -> bool: ...
    def revisions(self, system_id: str, limit: int = 50) -> list[Revision]: ...
    def revision(self, system_id: str, version: int) -> Optional[Revision]: ...
    def list_workspaces(self) -> list[Workspace]: ...
    def get_workspace(self, workspace_id: str) -> Optional[Workspace]: ...
    def save_workspace(self, workspace: Workspace) -> Workspace: ...
    def delete_workspace(self, workspace_id: str) -> bool: ...
    def locks(self, system_id: str) -> list[Lock]: ...
    def put_lock(self, lock: Lock) -> Lock: ...
    def drop_lock(self, lock_id: str) -> bool: ...
    def settings(self) -> DesignerSettings: ...
    def save_settings(self, settings: DesignerSettings) -> DesignerSettings: ...
    # Audit is append-only on purpose: no update, no delete (ADR-0043).
    def append_audit(self, event: AuditEvent) -> AuditEvent: ...
    def audit_events(self, system_id: Optional[str] = None,
                     limit: int = 1000) -> list[AuditEvent]: ...


class _Base:
    """Shared write logic: version checks, revision capture, pruning."""

    max_revisions = 100

    def _prepare(
        self, record: SystemRecord, current: Optional[SystemRecord],
        expected_version: Optional[int], author: str,
    ) -> SystemRecord:
        if current is not None:
            if expected_version is None:
                raise VersionConflict(0, current.version, current)
            if expected_version != current.version:
                raise VersionConflict(expected_version, current.version, current)
            record.created_at = current.created_at
            record.created_by = current.created_by
            record.version = current.version + 1
        else:
            record.version = 1
            record.created_by = record.created_by or author
        record.updated_by = author
        record.updated_at = datetime.now(timezone.utc).isoformat()
        return record

    def _revision(self, record: SystemRecord, author: str, message: str) -> Revision:
        return Revision(
            system_id=record.id, version=record.version, spec=record.spec,
            binding=record.binding, layout=record.layout, author=author,
            message=message,
        )


class MemoryRepository(_Base):
    """Everything in process. Used by tests and by ephemeral previews."""

    def __init__(self) -> None:
        self._systems: dict[str, SystemRecord] = {}
        self._revisions: dict[str, list[Revision]] = {}
        self._workspaces: dict[str, Workspace] = {}
        self._locks: dict[str, Lock] = {}
        self._settings = DesignerSettings(persistence="memory")
        self._audit: list[AuditEvent] = []
        self._lock = threading.RLock()

    def list_systems(self, workspace_id: Optional[str] = None) -> list[SystemRecord]:
        found = [
            r for r in self._systems.values()
            if workspace_id is None or r.workspace_id == workspace_id
        ]
        return sorted(found, key=lambda r: r.updated_at, reverse=True)

    def get_system(self, system_id: str) -> Optional[SystemRecord]:
        record = self._systems.get(system_id)
        return record.model_copy(deep=True) if record else None

    def save_system(self, record, *, expected_version, author, message="") -> SystemRecord:
        with self._lock:
            current = self._systems.get(record.id)
            record = self._prepare(record, current, expected_version, author)
            self._systems[record.id] = record.model_copy(deep=True)
            history = self._revisions.setdefault(record.id, [])
            history.append(self._revision(record, author, message))
            del history[: max(0, len(history) - self.max_revisions)]
            return record

    def delete_system(self, system_id: str) -> bool:
        with self._lock:
            self._revisions.pop(system_id, None)
            return self._systems.pop(system_id, None) is not None

    def revisions(self, system_id: str, limit: int = 50) -> list[Revision]:
        return list(reversed(self._revisions.get(system_id, [])))[:limit]

    def revision(self, system_id: str, version: int) -> Optional[Revision]:
        return next(
            (r for r in self._revisions.get(system_id, []) if r.version == version), None
        )

    def list_workspaces(self) -> list[Workspace]:
        return list(self._workspaces.values())

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        return self._workspaces.get(workspace_id)

    def save_workspace(self, workspace: Workspace) -> Workspace:
        self._workspaces[workspace.id] = workspace
        return workspace

    def delete_workspace(self, workspace_id: str) -> bool:
        return self._workspaces.pop(workspace_id, None) is not None

    def locks(self, system_id: str) -> list[Lock]:
        return [lock for lock in self._locks.values() if lock.system_id == system_id]

    def put_lock(self, lock: Lock) -> Lock:
        self._locks[lock.id] = lock
        return lock

    def drop_lock(self, lock_id: str) -> bool:
        return self._locks.pop(lock_id, None) is not None

    def settings(self) -> DesignerSettings:
        return self._settings

    def save_settings(self, settings: DesignerSettings) -> DesignerSettings:
        self._settings = settings
        return settings

    def append_audit(self, event: AuditEvent) -> AuditEvent:
        with self._lock:
            self._audit.append(event.model_copy(deep=True))
        return event

    def audit_events(self, system_id: Optional[str] = None,
                     limit: int = 1000) -> list[AuditEvent]:
        found = [
            e.model_copy(deep=True) for e in self._audit
            if system_id is None or e.system_id == system_id
        ]
        return found[-limit:]


class FileSystemRepository(_Base):
    """JSON on disk, laid out so a team can keep the whole thing in git.

        <root>/settings.json
        <root>/workspaces/<workspace>.json
        <root>/systems/<system>.json
        <root>/systems/<system>.revisions/<version>.json
        <root>/locks/<lock>.json
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        for folder in ("workspaces", "systems", "locks", "audit"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- helpers -----------------------------------------------------------

    def _system_path(self, system_id: str) -> Path:
        return self.root / "systems" / f"{system_id}.json"

    def _revision_dir(self, system_id: str) -> Path:
        return self.root / "systems" / f"{system_id}.revisions"

    @staticmethod
    def _read(path: Path) -> Optional[dict[str, Any]]:
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _write(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a crash mid-write never leaves a half file.
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)

    # -- systems -----------------------------------------------------------

    def list_systems(self, workspace_id: Optional[str] = None) -> list[SystemRecord]:
        found = []
        for path in (self.root / "systems").glob("*.json"):
            data = self._read(path)
            if data is None:
                continue
            record = SystemRecord.model_validate(data)
            if workspace_id is None or record.workspace_id == workspace_id:
                found.append(record)
        return sorted(found, key=lambda r: r.updated_at, reverse=True)

    def get_system(self, system_id: str) -> Optional[SystemRecord]:
        data = self._read(self._system_path(system_id))
        return SystemRecord.model_validate(data) if data else None

    def save_system(self, record, *, expected_version, author, message="") -> SystemRecord:
        with self._lock:
            current = self.get_system(record.id)
            record = self._prepare(record, current, expected_version, author)
            self._write(self._system_path(record.id), record.model_dump(mode="json"))
            revision = self._revision(record, author, message)
            self._write(
                self._revision_dir(record.id) / f"{record.version:06d}.json",
                revision.model_dump(mode="json"),
            )
            self._prune_revisions(record.id)
            return record

    def _prune_revisions(self, system_id: str) -> None:
        files = sorted(self._revision_dir(system_id).glob("*.json"))
        for stale in files[: max(0, len(files) - self.max_revisions)]:
            stale.unlink(missing_ok=True)

    def delete_system(self, system_id: str) -> bool:
        with self._lock:
            path = self._system_path(system_id)
            existed = path.is_file()
            path.unlink(missing_ok=True)
            shutil.rmtree(self._revision_dir(system_id), ignore_errors=True)
            return existed

    def revisions(self, system_id: str, limit: int = 50) -> list[Revision]:
        files = sorted(self._revision_dir(system_id).glob("*.json"), reverse=True)
        out = []
        for path in files[:limit]:
            data = self._read(path)
            if data:
                out.append(Revision.model_validate(data))
        return out

    def revision(self, system_id: str, version: int) -> Optional[Revision]:
        data = self._read(self._revision_dir(system_id) / f"{version:06d}.json")
        return Revision.model_validate(data) if data else None

    # -- workspaces, locks, settings --------------------------------------

    def list_workspaces(self) -> list[Workspace]:
        out = []
        for path in (self.root / "workspaces").glob("*.json"):
            data = self._read(path)
            if data:
                out.append(Workspace.model_validate(data))
        return out

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        data = self._read(self.root / "workspaces" / f"{workspace_id}.json")
        return Workspace.model_validate(data) if data else None

    def save_workspace(self, workspace: Workspace) -> Workspace:
        self._write(self.root / "workspaces" / f"{workspace.id}.json",
                    workspace.model_dump(mode="json"))
        return workspace

    def delete_workspace(self, workspace_id: str) -> bool:
        path = self.root / "workspaces" / f"{workspace_id}.json"
        existed = path.is_file()
        path.unlink(missing_ok=True)
        return existed

    def locks(self, system_id: str) -> list[Lock]:
        out = []
        for path in (self.root / "locks").glob("*.json"):
            data = self._read(path)
            if data and data.get("system_id") == system_id:
                out.append(Lock.model_validate(data))
        return out

    def put_lock(self, lock: Lock) -> Lock:
        self._write(self.root / "locks" / f"{lock.id}.json", lock.model_dump(mode="json"))
        return lock

    def drop_lock(self, lock_id: str) -> bool:
        path = self.root / "locks" / f"{lock_id}.json"
        existed = path.is_file()
        path.unlink(missing_ok=True)
        return existed

    def settings(self) -> DesignerSettings:
        data = self._read(self.root / "settings.json")
        return (
            DesignerSettings.model_validate(data) if data
            else DesignerSettings(persistence="filesystem", storage_path=str(self.root))
        )

    def save_settings(self, settings: DesignerSettings) -> DesignerSettings:
        self._write(self.root / "settings.json", settings.model_dump(mode="json"))
        return settings

    def append_audit(self, event: AuditEvent) -> AuditEvent:
        # One file per event, never reopened: concurrent writers cannot lose
        # each other's history, and nothing here rewrites an existing file.
        self._write(self.root / "audit" / f"{event.id}.json",
                    event.model_dump(mode="json"))
        return event

    def audit_events(self, system_id: Optional[str] = None,
                     limit: int = 1000) -> list[AuditEvent]:
        found = []
        for path in (self.root / "audit").glob("*.json"):
            data = self._read(path)
            if data is None:
                continue
            event = AuditEvent.model_validate(data)
            if system_id is None or event.system_id == system_id:
                found.append(event)
        found.sort(key=lambda e: e.order_key)
        return found[-limit:]


class SqlRepository(_Base):
    """The shared installation's backend, over the platform's document store."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = threading.RLock()

    def list_systems(self, workspace_id: Optional[str] = None) -> list[SystemRecord]:
        records = self.store.list(SYSTEMS, SystemRecord, parent=workspace_id, limit=2000)
        return sorted(records, key=lambda r: r.updated_at, reverse=True)

    def get_system(self, system_id: str) -> Optional[SystemRecord]:
        return self.store.get(SYSTEMS, system_id, SystemRecord)

    def save_system(self, record, *, expected_version, author, message="") -> SystemRecord:
        with self._lock:
            current = self.get_system(record.id)
            record = self._prepare(record, current, expected_version, author)
            self.store.put(SYSTEMS, record, parent=record.workspace_id, name=record.name)
            revision = self._revision(record, author, message)
            self.store.put(REVISIONS, revision, parent=record.id,
                           name=str(record.version))
            self._prune_revisions(record.id)
            return record

    def _prune_revisions(self, system_id: str) -> None:
        history = sorted(
            self.store.list(REVISIONS, Revision, parent=system_id, limit=5000),
            key=lambda r: r.version,
        )
        for stale in history[: max(0, len(history) - self.max_revisions)]:
            self.store.delete(REVISIONS, stale.id)

    def delete_system(self, system_id: str) -> bool:
        for revision in self.store.list(REVISIONS, Revision, parent=system_id,
                                        limit=5000):
            self.store.delete(REVISIONS, revision.id)
        return self.store.delete(SYSTEMS, system_id)

    def revisions(self, system_id: str, limit: int = 50) -> list[Revision]:
        history = self.store.list(REVISIONS, Revision, parent=system_id, limit=5000)
        return sorted(history, key=lambda r: r.version, reverse=True)[:limit]

    def revision(self, system_id: str, version: int) -> Optional[Revision]:
        return next(
            (r for r in self.revisions(system_id, limit=5000) if r.version == version),
            None,
        )

    def list_workspaces(self) -> list[Workspace]:
        return self.store.list(WORKSPACES, Workspace, limit=500)

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        return self.store.get(WORKSPACES, workspace_id, Workspace)

    def save_workspace(self, workspace: Workspace) -> Workspace:
        self.store.put(WORKSPACES, workspace, name=workspace.name)
        return workspace

    def delete_workspace(self, workspace_id: str) -> bool:
        return self.store.delete(WORKSPACES, workspace_id)

    def locks(self, system_id: str) -> list[Lock]:
        return self.store.list(LOCKS, Lock, parent=system_id, limit=500)

    def put_lock(self, lock: Lock) -> Lock:
        self.store.put(LOCKS, lock, parent=lock.system_id, name=lock.target)
        return lock

    def drop_lock(self, lock_id: str) -> bool:
        return self.store.delete(LOCKS, lock_id)

    def settings(self) -> DesignerSettings:
        stored = self.store.list(SETTINGS, DesignerSettings, limit=1)
        return stored[0] if stored else DesignerSettings(persistence="relational")

    def save_settings(self, settings: DesignerSettings) -> DesignerSettings:
        # Settings are a singleton; the id is fixed so a save replaces it.
        class _Wrapper(DesignerSettings):
            id: str = "designer_settings"

        wrapped = _Wrapper(**settings.model_dump())
        self.store.put(SETTINGS, wrapped, name="settings")
        return settings

    def append_audit(self, event: AuditEvent) -> AuditEvent:
        # Parent is the system so "the log for this design" is one indexed read;
        # events with no system (settings, membership) hang off the workspace.
        self.store.put(AUDIT, event, parent=event.system_id or event.workspace_id,
                       name=event.action.value)
        return event

    def audit_events(self, system_id: Optional[str] = None,
                     limit: int = 1000) -> list[AuditEvent]:
        found = self.store.list(AUDIT, AuditEvent, parent=system_id, limit=5000)
        found.sort(key=lambda e: e.order_key)
        return found[-limit:]


class PostgresRepository(SqlRepository):
    """The relational backend (ADR-0113): the model in tables generated from
    the UML profiles, in PostgreSQL.

    A save is a revision: a `core.revision` row and a snapshot of the spec's
    and the binding's rows under it (copy-on-save), written in one
    transaction with the `designer.system` row that carries the version
    optimistic concurrency checks (ADR-0033). A draft the model refuses is
    stored whole on its revision. Workspaces, locks, settings and the audit
    log keep their document shape in `designer.documents` (ADR-0031,
    ADR-0043), in the same database.
    """

    def __init__(self, eng: Any, *, migrate: bool = True) -> None:
        from ..persistence.store import DocumentStore
        from ..persistence.store import migrate as apply_migrations
        if migrate:
            apply_migrations(eng)
        self.engine = eng
        super().__init__(DocumentStore(eng))  # type: ignore[arg-type]

    @classmethod
    def from_url(cls, url: str = "", **kwargs: Any) -> "PostgresRepository":
        from ..persistence.store import engine
        return cls(engine(url), **kwargs)

    # -- reading -----------------------------------------------------------

    _SYSTEM = ("SELECT s.model_id, s.workspace_id, s.name, s.description, "
               "s.status, s.tags, s.version, s.created_by, s.created_at, "
               "s.updated_by, s.updated_at, m.head_revision "
               "FROM designer.system s JOIN core.model m USING (model_id)")

    def _records(self, conn: Any, heads: list[Any]) -> list[SystemRecord]:
        from ..persistence.store import read_revisions
        revisions = read_revisions(conn, [h["head_revision"] for h in heads
                                          if h["head_revision"]])
        layouts = self._layouts(conn, list(revisions))
        out = []
        for h in heads:
            rev = revisions.get(h["head_revision"])
            out.append(SystemRecord(
                id=h["model_id"], workspace_id=h["workspace_id"],
                name=h["name"], description=h["description"],
                status=h["status"], tags=list(h["tags"] or []),
                spec=(rev.spec if rev and rev.spec is not None else {}),
                binding=rev.binding if rev else None,
                layout=layouts.get(h["head_revision"], {}).get("layout")
                or {}, version=h["version"], created_by=h["created_by"],
                created_at=h["created_at"], updated_by=h["updated_by"],
                updated_at=h["updated_at"]))
        return out

    @staticmethod
    def _layouts(conn: Any, revision_ids: list[int]) -> dict[int, Any]:
        from sqlalchemy import text
        if not revision_ids:
            return {}
        rows = conn.execute(text(
            "SELECT revision_id, revision_key, layout, created_at "
            "FROM designer.revision WHERE revision_id = ANY(:ids)"),
            {"ids": revision_ids}).mappings()
        return {r["revision_id"]: dict(r) for r in rows}

    def list_systems(self, workspace_id: Optional[str] = None) -> list[SystemRecord]:
        from sqlalchemy import text
        sql = self._SYSTEM + (" WHERE s.workspace_id = :w" if workspace_id
                              is not None else "")
        with self.engine.connect() as conn:
            heads = conn.execute(text(sql), {"w": workspace_id}).mappings() \
                .all()
            records = self._records(conn, list(heads))
        return sorted(records, key=lambda r: r.updated_at, reverse=True)

    def get_system(self, system_id: str) -> Optional[SystemRecord]:
        with self.engine.connect() as conn:
            return self._get(conn, system_id)

    def _get(self, conn: Any, system_id: str) -> Optional[SystemRecord]:
        from sqlalchemy import text
        heads = conn.execute(text(self._SYSTEM + " WHERE s.model_id = :i"),
                             {"i": system_id}).mappings().all()
        return self._records(conn, list(heads))[0] if heads else None

    # -- writing -----------------------------------------------------------

    def save_system(self, record, *, expected_version, author, message="") -> SystemRecord:
        from sqlalchemy import text

        from ..persistence.store import write_revision
        with self.engine.begin() as conn:
            # One writer per design at a time; the version check below is
            # then exact (ADR-0033), and a racing second writer sees the
            # first one's version and is told it is stale.
            conn.execute(text("SELECT pg_advisory_xact_lock("
                              "hashtextextended(:i, 113))"), {"i": record.id})
            current = self._get(conn, record.id)
            record = self._prepare(record, current, expected_version, author)
            written = write_revision(
                conn, model_id=record.id, version=record.version,
                spec=record.spec, binding=record.binding, author=author,
                message=message)
            revision = self._revision(record, author, message)
            conn.execute(text(
                "INSERT INTO designer.revision (revision_id, revision_key, "
                "layout, created_at) VALUES (:r, :k, CAST(:l AS json), :c)"),
                {"r": written["revision_id"], "k": revision.id,
                 "l": record.layout.model_dump_json(),
                 "c": revision.created_at})
            conn.execute(text(
                "INSERT INTO designer.system (model_id, workspace_id, name, "
                "description, status, tags, version, created_by, created_at, "
                "updated_by, updated_at) VALUES (:i, :w, :n, :d, :s, :t, :v, "
                ":cb, :ca, :ub, :ua) ON CONFLICT (model_id) DO UPDATE SET "
                "workspace_id = EXCLUDED.workspace_id, name = EXCLUDED.name, "
                "description = EXCLUDED.description, status = EXCLUDED.status,"
                " tags = EXCLUDED.tags, version = EXCLUDED.version, "
                "updated_by = EXCLUDED.updated_by, "
                "updated_at = EXCLUDED.updated_at"),
                {"i": record.id, "w": record.workspace_id, "n": record.name,
                 "d": record.description, "s": record.status.value,
                 "t": list(record.tags), "v": record.version,
                 "cb": record.created_by, "ca": record.created_at,
                 "ub": record.updated_by, "ua": record.updated_at})
            conn.execute(text(
                "DELETE FROM core.revision WHERE model_id = :i "
                "AND version <= :v"),
                {"i": record.id, "v": record.version - self.max_revisions})
        return record

    def delete_system(self, system_id: str) -> bool:
        from sqlalchemy import text
        with self.engine.begin() as conn:
            return conn.execute(text(
                "DELETE FROM core.model WHERE model_id = :i"),
                {"i": system_id}).rowcount > 0

    # -- revisions ---------------------------------------------------------

    def _revisions(self, system_id: str, limit: int,
                   version: Optional[int] = None) -> list[Revision]:
        from sqlalchemy import text

        from ..persistence.store import read_revisions
        with self.engine.connect() as conn:
            ids = conn.execute(text(
                "SELECT revision_id FROM core.revision WHERE model_id = :i"
                + (" AND version = :v" if version is not None else "")
                + " ORDER BY version DESC LIMIT :l"),
                {"i": system_id, "v": version, "l": limit}).scalars().all()
            stored = read_revisions(conn, ids)
            layouts = self._layouts(conn, list(ids))
        out = []
        for rid in ids:
            s, extra = stored[rid], layouts.get(rid, {})
            out.append(Revision(
                id=extra.get("revision_key") or f"rev_{rid}",
                system_id=system_id, version=s.version,
                spec=s.spec if s.spec is not None else {},
                binding=s.binding, layout=extra.get("layout") or {},
                author=s.author, message=s.message,
                created_at=extra.get("created_at") or str(s.created_at)))
        return out

    def revisions(self, system_id: str, limit: int = 50) -> list[Revision]:
        return self._revisions(system_id, limit)

    def revision(self, system_id: str, version: int) -> Optional[Revision]:
        found = self._revisions(system_id, 1, version)
        return found[0] if found else None

    def settings(self) -> DesignerSettings:
        stored = self.store.list(SETTINGS, DesignerSettings, limit=1)
        return stored[0] if stored else DesignerSettings(
            persistence="relational")


def build_repository(settings: DesignerSettings,
                     store: Optional[Store] = None,
                     database_url: Optional[str] = None) -> Repository:
    """Instantiate the backend the settings ask for.

    `relational` is PostgreSQL when `ORGAGENTS_DATABASE_URL` (or
    `database_url`) is set (ADR-0113); without one it stays the SQLite
    document store it was, for tests and single-user use."""
    import os
    if settings.persistence == "filesystem":
        return FileSystemRepository(settings.storage_path)
    if settings.persistence == "relational":
        url = database_url if database_url is not None else \
            os.environ.get("ORGAGENTS_DATABASE_URL", "")
        if url:
            return PostgresRepository.from_url(url)
        if store is None:
            raise ValueError("the relational backend needs a Store")
        return SqlRepository(store)
    return MemoryRepository()
