"""The model in PostgreSQL: connecting, migrating, and writing and reading
revisions (ADR-0113). SQLAlchemy Core over psycopg 3.

A revision is a snapshot: `write_revision` inserts a `core.revision` row and
every row of the spec and the binding under its id, in one transaction;
`read_revisions` reads any number of revisions with one query per table.
What the model refuses — a draft mid-edit — is kept whole in the revision's
`draft_spec` / `draft_binding` instead of being normalised, so nothing an
author saved is lost (ADR-0113 1.1.0).
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Optional, Type, TypeVar

from pydantic import BaseModel

from . import mapper
from .relational import ELEMENT, plan, structure

T = TypeVar("T", bound=BaseModel)

ENV_URL = "ORGAGENTS_DATABASE_URL"


def database_url(url: str = "") -> str:
    """The SQLAlchemy URL for a PostgreSQL URL, with the psycopg 3 driver."""
    url = url or os.environ.get(ENV_URL, "")
    if not url:
        raise ValueError(f"no database: set {ENV_URL}")
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def engine(url: str = "", **kwargs: Any):
    from sqlalchemy import create_engine
    return create_engine(database_url(url), pool_pre_ping=True,
                         future=True, **kwargs)


def migrate(eng: Any) -> list[str]:
    """Apply the committed migrations the database lacks."""
    from .migrations import apply
    with eng.begin() as conn:
        return apply(conn)


# --------------------------------------------------------------------------
# Tables as SQLAlchemy Core constructs, from the structure
# --------------------------------------------------------------------------

_TABLES: dict[str, Any] = {}


@lru_cache(maxsize=1)
def _structure() -> dict[str, Any]:
    return structure()


def table(q: str):
    """A lightweight Core table for a qualified name, its json columns
    typed so dicts and lists are written as JSON."""
    if q in _TABLES:
        return _TABLES[q]
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import JSON
    s = _structure()["tables"][q]
    cols = [sa.column(name, JSON(none_as_null=True))
            if c["type"] == "json" else sa.column(name)
            for name, c in s["columns"].items()]
    schema, name = q.split(".")
    _TABLES[q] = sa.table(name, *cols, schema=schema)
    return _TABLES[q]


class _Ids:
    """Row ids from `core.row_seq`, fetched in blocks."""

    def __init__(self, conn: Any, block: int = 512) -> None:
        self.conn, self.block, self.free = conn, block, []

    def __call__(self) -> int:
        if not self.free:
            from sqlalchemy import text
            self.free = [r[0] for r in self.conn.execute(text(
                "SELECT nextval('core.row_seq') FROM generate_series(1, :n)"),
                {"n": self.block})]
            self.free.reverse()
        return self.free.pop()


# --------------------------------------------------------------------------
# Classifying what is saved: a model, or a draft kept whole
# --------------------------------------------------------------------------

@dataclass
class Classified:
    state: str                      # model | draft | none (| binding | target)
    obj: Optional[BaseModel] = None
    draft: Any = None
    reason: str = ""


def classify_spec(spec: Any) -> Classified:
    from ..spec.exchange import strip_types, unknown_keys
    from ..spec.model import SystemSpec
    if spec is None or spec == {}:
        return Classified("none") if spec is None else \
            Classified("draft", draft=spec, reason="empty")
    try:
        data = strip_types(spec, SystemSpec)
        obj = SystemSpec.model_validate(data)
    except Exception as e:  # the model refuses it: a draft
        return Classified("draft", draft=spec,
                          reason=f"{type(e).__name__}: {str(e)[:200]}")
    lost = unknown_keys(data, SystemSpec)
    if lost:
        return Classified("draft", draft=spec,
                          reason="keys the model would drop: "
                                 + ", ".join(lost[:5]))
    return Classified("model", obj=obj)


def classify_binding(binding: Any) -> Classified:
    """A binding document, one target's binding (what the designer stores
    per revision), or a draft."""
    from ..spec.binding import Binding, TargetBinding
    from ..spec.exchange import strip_types, unknown_keys
    if binding is None:
        return Classified("none")
    if isinstance(binding, dict) and isinstance(binding.get("targets"), list):
        cls, state = Binding, "binding"
    elif isinstance(binding, dict) and "target" in binding:
        cls, state = TargetBinding, "target"
    else:
        return Classified("draft", draft=binding, reason="not a binding")
    try:
        data = strip_types(binding, Binding) if cls is Binding else binding
        obj = cls.model_validate(data)
    except Exception as e:
        return Classified("draft", draft=binding,
                          reason=f"{type(e).__name__}: {str(e)[:200]}")
    lost = unknown_keys(data, cls)
    if lost:
        return Classified("draft", draft=binding,
                          reason="keys the model would drop: "
                                 + ", ".join(lost[:5]))
    if cls is TargetBinding:
        obj = Binding(spec="", targets=[obj])
    return Classified(state, obj=obj)


# --------------------------------------------------------------------------
# Writing and reading revisions
# --------------------------------------------------------------------------

def write_revision(conn: Any, *, model_id: str, version: int, spec: Any,
                   binding: Any = None, author: str = "", message: str = "",
                   created_at: Optional[str] = None) -> dict[str, Any]:
    """Insert one revision of a model — `core.revision` and every row —
    and make it the head. Returns the revision id and what was stored."""
    from sqlalchemy import insert, text
    s, b = classify_spec(spec), classify_binding(binding)
    conn.execute(text(
        "INSERT INTO core.model (model_id) VALUES (:m) "
        "ON CONFLICT (model_id) DO NOTHING"), {"m": model_id})
    rev = conn.execute(text(
        "INSERT INTO core.revision (model_id, version, author, message, "
        "spec_state, draft_spec, binding_state, draft_binding"
        + (", created_at" if created_at else "") + ") VALUES "
        "(:m, :v, :a, :msg, :ss, CAST(:ds AS json), :bs, CAST(:db AS json)"
        + (", CAST(:c AS timestamptz)" if created_at else "") +
        ") RETURNING revision_id"),
        {"m": model_id, "v": version, "a": author, "msg": message,
         "ss": s.state, "ds": _dumps(s.draft), "bs": b.state,
         "db": _dumps(b.draft), "c": created_at}).scalar_one()
    rows = mapper.Rows()
    ids = _Ids(conn)
    if s.obj is not None:
        mapper.to_rows(s.obj, revision_id=rev, model_id=model_id,
                       model_version=version, ids=ids, rows=rows)
    if b.obj is not None:
        mapper.to_rows(b.obj, revision_id=rev, model_id=model_id,
                       model_version=version, ids=ids, rows=rows)
    unresolved = mapper.resolve(rows)
    # The element index first: every element row's key references it.
    order = [ELEMENT] + sorted(q for q in rows.tables if q != ELEMENT)
    for q in order:
        data = rows.tables.get(q)
        if data:
            conn.execute(insert(table(q)), data)
    conn.execute(text(
        "UPDATE core.model SET head_revision = :r, updated_at = now() "
        "WHERE model_id = :m"), {"r": rev, "m": model_id})
    return {"revision_id": rev, "spec_state": s.state,
            "binding_state": b.state, "rows": rows.count(),
            "unresolved": unresolved, "spec_reason": s.reason,
            "binding_reason": b.reason}


def _dumps(value: Any) -> Optional[str]:
    import json
    return None if value is None else json.dumps(value)


@dataclass
class StoredRevision:
    revision_id: int
    model_id: str
    version: int
    author: str
    message: str
    created_at: Any
    spec: Optional[dict[str, Any]]
    binding: Optional[dict[str, Any]]
    spec_state: str
    binding_state: str


def read_revisions(conn: Any, revision_ids: Iterable[int]
                   ) -> dict[int, StoredRevision]:
    """Revisions by id, each rebuilt from its rows (or its draft), with one
    query per table however many are asked for."""
    from sqlalchemy import text
    from ..spec.binding import Binding
    from ..spec.exchange import canonical
    from ..spec.model import SystemSpec
    ids = sorted(set(revision_ids))
    if not ids:
        return {}
    heads = conn.execute(text(
        "SELECT revision_id, model_id, version, author, message, created_at,"
        " spec_state, draft_spec, binding_state, draft_binding "
        "FROM core.revision WHERE revision_id = ANY(:ids)"),
        {"ids": ids}).mappings().all()
    modelled = [h["revision_id"] for h in heads
                if h["spec_state"] == "model"
                or h["binding_state"] in ("binding", "target")]
    by_rev: dict[int, dict[str, list[dict[str, Any]]]] = \
        defaultdict(lambda: defaultdict(list))
    if modelled:
        for q in [t.q for t in plan().tables()]:
            schema, name = q.split(".")
            for r in conn.execute(text(
                    f'SELECT * FROM "{schema}"."{name}" '
                    f"WHERE revision_id = ANY(:ids)"),
                    {"ids": modelled}).mappings():
                by_rev[r["revision_id"]][q].append(dict(r))
    out = {}
    for h in heads:
        rid = h["revision_id"]
        tables = by_rev.get(rid, {})
        spec = h["draft_spec"]
        if h["spec_state"] == "model":
            spec = canonical(SystemSpec.model_validate(
                mapper.from_rows(tables, SystemSpec)))
        elif h["spec_state"] == "none":
            spec = None
        binding = h["draft_binding"]
        if h["binding_state"] in ("binding", "target"):
            obj = Binding.model_validate(mapper.from_rows(tables, Binding))
            binding = canonical(obj)
            if h["binding_state"] == "target":
                binding = canonical(obj.targets[0])
        elif h["binding_state"] == "none":
            binding = None
        out[rid] = StoredRevision(
            rid, h["model_id"], h["version"], h["author"], h["message"],
            h["created_at"], spec, binding, h["spec_state"],
            h["binding_state"])
    return out


# --------------------------------------------------------------------------
# The designer's other records: the document table, in PostgreSQL
# --------------------------------------------------------------------------

class DocumentStore:
    """`orgagents.store.Store`'s document methods over `designer.documents`,
    so workspaces, locks, settings and audit keep their shape (ADR-0031,
    ADR-0043) and live in the same database as the model."""

    def __init__(self, eng: Any) -> None:
        self.engine = eng

    def put(self, collection: str, obj: BaseModel, *,
            parent: Optional[str] = None,
            name: Optional[str] = None) -> BaseModel:
        from sqlalchemy import text
        with self.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO designer.documents (collection, id, parent, "
                "name, body) VALUES (:c, :i, :p, :n, CAST(:b AS json)) "
                "ON CONFLICT (collection, id) DO UPDATE SET "
                "body = EXCLUDED.body, parent = EXCLUDED.parent, "
                "name = EXCLUDED.name, updated_at = clock_timestamp()"),
                {"c": collection, "i": getattr(obj, "id"), "p": parent,
                 "n": name or getattr(obj, "name", None),
                 "b": obj.model_dump_json()})
        return obj

    def put_raw(self, conn: Any, collection: str, oid: str,
                parent: Optional[str], name: Optional[str],
                body: dict[str, Any]) -> None:
        from sqlalchemy import text
        conn.execute(text(
            "INSERT INTO designer.documents (collection, id, parent, name, "
            "body) VALUES (:c, :i, :p, :n, CAST(:b AS json)) "
            "ON CONFLICT (collection, id) DO NOTHING"),
            {"c": collection, "i": oid, "p": parent, "n": name,
             "b": _dumps(body)})

    def get(self, collection: str, oid: str, model: Type[T]) -> Optional[T]:
        from sqlalchemy import text
        with self.engine.connect() as conn:
            body = conn.execute(text(
                "SELECT body FROM designer.documents WHERE collection = :c "
                "AND id = :i"), {"c": collection, "i": oid}).scalar()
        return model.model_validate(body) if body is not None else None

    def list(self, collection: str, model: Type[T], *,
             parent: Optional[str] = None, limit: int = 500,
             offset: int = 0) -> list[T]:
        from sqlalchemy import text
        sql = "SELECT body FROM designer.documents WHERE collection = :c"
        args: dict[str, Any] = {"c": collection, "l": limit, "o": offset}
        if parent is not None:
            sql += " AND parent = :p"
            args["p"] = parent
        sql += " ORDER BY updated_at DESC LIMIT :l OFFSET :o"
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), args).scalars().all()
        return [model.model_validate(b) for b in rows]

    def delete(self, collection: str, oid: str) -> bool:
        from sqlalchemy import text
        with self.engine.begin() as conn:
            n = conn.execute(text(
                "DELETE FROM designer.documents WHERE collection = :c AND "
                "id = :i"), {"c": collection, "i": oid}).rowcount
        return n > 0
