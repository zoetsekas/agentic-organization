"""SQLite-backed document store.

Deliberately boring: every domain object is persisted as a JSON document in a
typed collection, with a few indexed columns for the queries the API needs.
Swap this class for Postgres by reimplementing the same six methods.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    collection TEXT NOT NULL,
    id         TEXT NOT NULL,
    parent     TEXT,
    name       TEXT,
    body       TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (collection, id)
);
CREATE INDEX IF NOT EXISTS idx_documents_parent ON documents(collection, parent);
CREATE INDEX IF NOT EXISTS idx_documents_name   ON documents(collection, name);
"""


class Store:
    """Thread-safe JSON document store over SQLite."""

    def __init__(self, path: str | Path = "orgagents.db") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- core operations ---------------------------------------------------

    def put(
        self,
        collection: str,
        obj: BaseModel,
        *,
        parent: Optional[str] = None,
        name: Optional[str] = None,
    ) -> BaseModel:
        body = obj.model_dump_json()
        oid = getattr(obj, "id")
        with self._lock:
            self._conn.execute(
                "INSERT INTO documents(collection,id,parent,name,body) VALUES(?,?,?,?,?) "
                "ON CONFLICT(collection,id) DO UPDATE SET "
                "body=excluded.body, parent=excluded.parent, name=excluded.name, "
                "updated_at=datetime('now')",
                (collection, oid, parent, name or getattr(obj, "name", None), body),
            )
            self._conn.commit()
        return obj

    def get(self, collection: str, oid: str, model: Type[T]) -> Optional[T]:
        cur = self._conn.execute(
            "SELECT body FROM documents WHERE collection=? AND id=?", (collection, oid)
        )
        row = cur.fetchone()
        return model.model_validate_json(row["body"]) if row else None

    def list(
        self,
        collection: str,
        model: Type[T],
        *,
        parent: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[T]:
        sql = "SELECT body FROM documents WHERE collection=?"
        args: list[Any] = [collection]
        if parent is not None:
            sql += " AND parent=?"
            args.append(parent)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [
            model.model_validate_json(r["body"]) for r in self._conn.execute(sql, args)
        ]

    def delete(self, collection: str, oid: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM documents WHERE collection=? AND id=?", (collection, oid)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def search(self, collection: str, model: Type[T], q: str, limit: int = 100) -> list[T]:
        """Substring search across the serialized document."""
        rows = self._conn.execute(
            "SELECT body FROM documents WHERE collection=? AND body LIKE ? LIMIT ?",
            (collection, f"%{q}%", limit),
        )
        return [model.model_validate_json(r["body"]) for r in rows]

    def count(self, collection: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE collection=?", (collection,)
        )
        return int(cur.fetchone()["n"])

    # -- convenience -------------------------------------------------------

    def put_many(self, collection: str, objs: Iterable[BaseModel]) -> None:
        for o in objs:
            self.put(collection, o)

    def raw(self, collection: str, oid: str) -> Optional[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT body FROM documents WHERE collection=? AND id=?", (collection, oid)
        )
        row = cur.fetchone()
        return json.loads(row["body"]) if row else None

    def close(self) -> None:
        self._conn.close()


# Collection names used across the codebase.
AGENTS = "agents"
ORG_UNITS = "org_units"
SESSIONS = "sessions"
EVENTS = "events"
MESSAGES = "messages"
RECORDS = "records"
SKILLS = "skills"
PLUGINS = "plugins"
WORKFLOWS = "workflows"
SANDBOX_TEMPLATES = "sandbox_templates"
CATALOG = "catalog"
ALERTS = "alerts"
