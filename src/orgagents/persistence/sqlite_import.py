"""Move a designer's SQLite document store into PostgreSQL, once (ADR-0113).

`orgagents db migrate-from-sqlite <designer.db>` reads every workspace,
design, revision, lock, setting and audit event the SQLite store holds and
writes them to PostgreSQL: each revision becomes a `core.revision` with its
rows (or its draft, kept whole), at the version it had, by the author and at
the time it had; the other records keep their documents.

The SQLite file is never written. It is copied — with its `-wal` and `-shm`
companions, so changes not yet checkpointed are read too — to a temporary
directory, and the copy is read. A design already in PostgreSQL is skipped,
so running it twice adds nothing; `--once` skips the whole file if it was
imported before (recorded in `designer.imports`), which is what the
container's first start uses.

Every revision is checked after it is written: read back from PostgreSQL,
it must be the same model as the document it came from.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SYSTEMS = "designer_systems"
REVISIONS = "designer_revisions"
#: The designer's other records: copied as documents, unchanged.
DOCUMENTS = ("designer_workspaces", "designer_locks", "designer_settings",
             "designer_audit")


@dataclass
class SystemReport:
    id: str
    name: str
    versions: list[int] = field(default_factory=list)
    modelled: int = 0
    drafts: int = 0
    relaid: int = 0
    mismatches: list[str] = field(default_factory=list)
    skipped: str = ""


@dataclass
class Report:
    source: str
    systems: list[SystemReport] = field(default_factory=list)
    documents: dict[str, int] = field(default_factory=dict)
    skipped: str = ""

    @property
    def ok(self) -> bool:
        return not any(s.mismatches for s in self.systems)

    def summary(self) -> str:
        if self.skipped:
            return f"{self.source}: {self.skipped}"
        lines = [f"imported from {self.source}:"]
        for s in self.systems:
            if s.skipped:
                lines.append(f"  {s.name} ({s.id}): skipped, {s.skipped}")
                continue
            lines.append(
                f"  {s.name} ({s.id}): {len(s.versions)} revision(s) "
                f"v{min(s.versions)}..v{max(s.versions)}, {s.modelled} as "
                f"model rows, {s.drafts} kept whole as drafts"
                + (f", {s.relaid} moved to the current layout (ADR-0101)"
                   if s.relaid else "")
                + ("" if not s.mismatches else
                   f"; MISMATCH in {', '.join(s.mismatches)}"))
        for coll, n in sorted(self.documents.items()):
            lines.append(f"  {coll}: {n} document(s)")
        lines.append("every revision read back equal to its source" if self.ok
                     else "SOME REVISIONS DID NOT READ BACK EQUAL")
        return "\n".join(lines)


def _copy(source: Path) -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="orgagents-sqlite-"))
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(source) + suffix)
        if p.is_file():
            shutil.copy2(p, tmp / f"designer.db{suffix}")
    return tmp, tmp / "designer.db"


def _documents(conn: sqlite3.Connection, collection: str
               ) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            "SELECT id, parent, name, body FROM documents WHERE "
            "collection = ?", (collection,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"id": r[0], "parent": r[1], "name": r[2],
             "body": json.loads(r[3])} for r in rows]


def _same_model(stored: Any, original: Any, cls: type) -> bool:
    """Is what PostgreSQL gives back the same model as the source?"""
    from ..spec.exchange import strip_types
    if stored == original:
        return True
    try:
        return cls.model_validate(strip_types(stored, cls)) == \
            cls.model_validate(strip_types(original, cls))
    except Exception:
        return False


def migrate_from_sqlite(path: str | Path, eng: Any, *,
                        once: bool = False) -> Report:
    from sqlalchemy import text

    from ..designer.models import Layout, SystemRecord
    from ..spec.model import ORGANIZATION_COLLECTIONS, SystemSpec
    from .store import DocumentStore, read_revisions, write_revision

    source = Path(path).resolve()
    report = Report(str(source))
    key = f"sqlite:{source}"
    if not source.is_file():
        report.skipped = "no such file"
        return report
    with eng.connect() as conn:
        if once and conn.execute(text(
                "SELECT 1 FROM designer.imports WHERE source = :s"),
                {"s": key}).first():
            report.skipped = "imported before; nothing to do"
            return report
    tmp, copy = _copy(source)
    try:
        lite = sqlite3.connect(copy)
        systems = _documents(lite, SYSTEMS)
        revisions = _documents(lite, REVISIONS)
        others = {c: _documents(lite, c) for c in DOCUMENTS}
        lite.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    docs = DocumentStore(eng)
    with eng.begin() as conn:
        for coll, items in others.items():
            for d in items:
                docs.put_raw(conn, coll, d["id"], d["parent"], d["name"],
                             d["body"])
            report.documents[coll] = len(items)

    by_system: dict[str, list[dict[str, Any]]] = {}
    for r in revisions:
        by_system.setdefault(r["body"].get("system_id") or r["parent"],
                             []).append(r["body"])
    for doc in sorted(systems, key=lambda d: d["id"]):
        record = SystemRecord.model_validate(doc["body"])
        rep = SystemReport(record.id, record.name)
        report.systems.append(rep)
        with eng.connect() as conn:
            if conn.execute(text(
                    "SELECT 1 FROM designer.system WHERE model_id = :i"),
                    {"i": record.id}).first():
                rep.skipped = "already in PostgreSQL"
                continue
        history = {int(r["version"]): r for r in
                   by_system.get(record.id, [])}
        # The head is the record itself, whatever the revision log kept.
        history[record.version] = {
            "id": None, "version": record.version, "spec": record.spec,
            "binding": record.binding,
            "layout": record.layout.model_dump(mode="json"),
            "author": record.updated_by, "message": "",
            "created_at": record.updated_at,
            **({k: v for k, v in history.get(record.version, {}).items()
                if k in ("id", "author", "message", "created_at")})}
        written: dict[int, tuple[int, dict[str, Any]]] = {}
        with eng.begin() as conn:
            for version in sorted(history):
                rev = history[version]
                out = write_revision(
                    conn, model_id=record.id, version=version,
                    spec=rev.get("spec") or {}, binding=rev.get("binding"),
                    author=rev.get("author") or "",
                    message=rev.get("message") or "",
                    created_at=rev.get("created_at") or None)
                layout = Layout.model_validate(rev.get("layout") or {})
                conn.execute(text(
                    "INSERT INTO designer.revision (revision_id, "
                    "revision_key, layout, created_at) VALUES "
                    "(:r, :k, CAST(:l AS json), :c)"),
                    {"r": out["revision_id"],
                     "k": rev.get("id") or f"rev_imported_{version}",
                     "l": layout.model_dump_json(),
                     "c": rev.get("created_at") or record.updated_at})
                written[version] = (out["revision_id"], rev)
                rep.versions.append(version)
                if out["spec_state"] == "model":
                    rep.modelled += 1
                    spec = rev.get("spec") or {}
                    if any(k in spec for k in ORGANIZATION_COLLECTIONS):
                        rep.relaid += 1
                else:
                    rep.drafts += 1
            conn.execute(text(
                "INSERT INTO designer.system (model_id, workspace_id, name, "
                "description, status, tags, version, created_by, "
                "created_at, updated_by, updated_at) VALUES (:i, :w, :n, "
                ":d, :s, :t, :v, :cb, :ca, :ub, :ua)"),
                {"i": record.id, "w": record.workspace_id, "n": record.name,
                 "d": record.description, "s": record.status.value,
                 "t": list(record.tags), "v": record.version,
                 "cb": record.created_by, "ca": record.created_at,
                 "ub": record.updated_by, "ua": record.updated_at})
            conn.execute(text(
                "UPDATE core.model SET head_revision = :r WHERE model_id = :i"),
                {"r": written[record.version][0], "i": record.id})
        from ..spec.binding import Binding
        with eng.connect() as conn:
            back = read_revisions(conn, [w[0] for w in written.values()])
        for version, (rid, rev) in written.items():
            got = back[rid]
            if not _same_model(got.spec, rev.get("spec") or {}, SystemSpec):
                rep.mismatches.append(f"v{version} spec")
            original_binding = rev.get("binding")
            if original_binding and got.binding != original_binding and \
                    not _binding_equal(got.binding, original_binding,
                                       Binding):
                rep.mismatches.append(f"v{version} binding")

    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO designer.imports (source, detail) VALUES "
            "(:s, CAST(:d AS json)) ON CONFLICT (source) DO NOTHING"),
            {"s": key, "d": json.dumps({
                "systems": [s.id for s in report.systems if not s.skipped],
                "ok": report.ok})})
    return report


def _binding_equal(a: Any, b: Any, binding_cls: type) -> bool:
    from ..spec.binding import TargetBinding
    try:
        cls = binding_cls if "targets" in b else TargetBinding
        return cls.model_validate(a) == cls.model_validate(b)
    except Exception:
        return False

