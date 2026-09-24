"""A validated spec or binding to rows, and rows back (ADR-0113).

Driven entirely by `relational.plan()`: `to_rows` walks a model object along
the plan and emits one dict per row, keyed by table; `from_rows` walks the
same plan over rows and rebuilds the document the model validates. There is
no code per stereotype, so a stereotype added to a profile is stored and
read with no change here.

Row ids are handed in (`ids`), so the caller can take them from the
database's sequence in one round trip; the tests hand in a counter.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Optional

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from .relational import (
    ELEMENT,
    ClassTable,
    Flat,
    Links,
    Parts,
    Plan,
    Ref,
    Scalar,
    concrete_kinds,
    plan,
)

__all__ = ["Rows", "to_rows", "from_rows", "resolve"]


@dataclass
class Rows:
    """Rows to write, by qualified table name, and the element index."""

    tables: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(list))
    #: (stereotype kind, id) → row ids, for resolving references.
    index: dict[tuple[str, str], list[int]] = field(
        default_factory=lambda: defaultdict(list))
    #: Deferred reference resolutions: (row, column, kinds, id, host row).
    pending: list[tuple[dict[str, Any], str, frozenset[str], str, int]] = \
        field(default_factory=list)
    #: Row → the row that owns it, for scoping references.
    parent: dict[int, int] = field(default_factory=dict)

    def count(self) -> int:
        return sum(len(v) for v in self.tables.values())


def _json(value: Any) -> Any:
    return to_jsonable_python(value)


def _scalar(value: Any, f: Scalar) -> Any:
    if value is None:
        return None
    if f.sql == "json":
        return _json(value)
    if f.many:
        return [getattr(v, "value", v) for v in value]
    return getattr(value, "value", value)


def to_rows(obj: BaseModel, *, revision_id: int, model_id: str,
            model_version: int, ids: Callable[[], int],
            rows: Optional[Rows] = None, p: Optional[Plan] = None) -> Rows:
    """Every row of one spec or binding. Call once for the spec and once for
    the binding with the same `rows`, then `resolve(rows)`, so a binding's
    references resolve against the spec's elements."""
    p = p or plan()
    rows = rows if rows is not None else Rows()
    root = p.classes[type(obj)]
    _emit(obj, root, rows, p, ids, revision_id, model_id, model_version,
          owner=None)
    return rows


def _emit(obj: BaseModel, t: ClassTable, rows: Rows, p: Plan,
          ids: Callable[[], int], rev: int, model_id: str, version: int,
          owner: Optional[tuple[int, str, int]]) -> int:
    row_id = ids()
    if owner is not None:
        rows.parent[row_id] = owner[0]
    for level in t.chain():
        row: dict[str, Any] = {"row_id": row_id, "revision_id": rev}
        if level.element:
            row["model_id"] = model_id
            row["model_version"] = version
        if level.owned:
            if level is t and owner is not None:
                row["owner_row"], row["owner_field"], row["ordinal"] = owner
            else:
                row["owner_row"] = row["owner_field"] = row["ordinal"] = None
        if level.as_written:
            row["fields_set"] = sorted(obj.model_fields_set)
            row["extra"] = _json(obj.__pydantic_extra__) \
                if obj.__pydantic_extra__ else None
        _fill(obj, level.fields, row, row_id, rows, p, ids, rev, model_id,
              version, prefix_path="")
        rows.tables[level.q].append(row)
    if t.element:
        ident_ = _element_id(obj, t)
        rows.tables[ELEMENT].append({
            "row_id": row_id, "revision_id": rev, "model_id": model_id,
            "model_version": version, "id": ident_,
            "uml_type": t.uml_type, "profile": t.uml_type.split("::")[0],
            "stereotype": t.kind, "metaclass": t.metaclass,
            "name": _element_name(obj)})
        if ident_:
            rows.index[(t.kind, ident_)].append(row_id)
    return row_id


def _element_id(obj: BaseModel, t: ClassTable) -> Optional[str]:
    value = getattr(obj, "id", None)
    if isinstance(value, str):
        return value
    # The Model is found by its name (a binding's `spec` names it).
    metadata = getattr(obj, "metadata", None)
    name = getattr(metadata, "name", None)
    return name if isinstance(name, str) else None


def _element_name(obj: BaseModel) -> Optional[str]:
    for attr in ("name", "title"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def _fill(obj: BaseModel, fields: list[Any], row: dict[str, Any],
          host: int, rows: Rows, p: Plan, ids: Callable[[], int], rev: int,
          model_id: str, version: int, prefix_path: str) -> None:
    for f in fields:
        value = getattr(obj, f.attr) if obj is not None else None
        if isinstance(f, Scalar):
            row[f.col] = _scalar(value, f)
        elif isinstance(f, Ref):
            row[f.col] = value
            row[f.row_col] = None
            if value:
                rows.pending.append((row, f.row_col, f.targets, value, host))
        elif isinstance(f, Links):
            for i, target in enumerate(value or []):
                link = {"revision_id": rev, "owner_row": host,
                        "owner_field": f.path, "ordinal": i,
                        "target_id": target, "target_row": None}
                rows.tables[f.link.q].append(link)
                rows.pending.append((link, "target_row", f.link.targets, target,
                                     host))
        elif isinstance(f, Flat):
            if f.optional:
                row[f.set_col] = value is not None
            if f.as_written:
                row[f.fields_col] = sorted(value.model_fields_set) \
                    if value is not None else None
                extra = getattr(value, "__pydantic_extra__", None)
                row[f.extra_col] = _json(extra) if extra else None
            _fill(value, f.children, row, host, rows, p, ids, rev, model_id,
                  version, f.path)
        elif isinstance(f, Parts):
            items = value if f.many else ([] if value is None else [value])
            for i, item in enumerate(items or []):
                _emit(item, f.table, rows, p, ids, rev, model_id, version,
                      owner=(host, f.path, i))


def _chain(rows: Rows, row_id: int) -> list[int]:
    out = [row_id]
    while out[-1] in rows.parent:
        out.append(rows.parent[out[-1]])
    return out


def resolve(rows: Rows) -> int:
    """Fill every reference's row with the element its id names, among the
    kinds it may refer to. An id can be scoped — a server's id is unique in
    its target, a step's in its workflow — so when several elements share
    it, the one nearest the reference in the ownership tree wins (a
    capability binding's server is the one in the same target); a tie, or
    no element at all, leaves the row empty. Returns how many stayed
    unresolved."""
    unresolved = 0
    for row, col, kinds, target, host in rows.pending:
        found: set[int] = set()
        for k in kinds:
            for concrete in concrete_kinds(k):
                found.update(rows.index.get((concrete, target), []))
        if len(found) > 1:
            here = _chain(rows, host)
            depth = {c: next((i for i, a in enumerate(here)
                              if a in set(_chain(rows, c))), len(here))
                     for c in found}
            best = min(depth.values())
            found = {c for c, d in depth.items() if d == best}
        if len(found) == 1:
            row[col] = found.pop()
        else:
            unresolved += 1
    rows.pending.clear()
    return unresolved


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

class _Index:
    """Rows of one revision, indexed for the walk back."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self.by_id: dict[str, dict[int, dict[str, Any]]] = {}
        self.parts: dict[tuple[str, int, str], list[dict[str, Any]]] = \
            defaultdict(list)
        for q, rs in tables.items():
            by_id = self.by_id.setdefault(q, {})
            for r in rs:
                if "row_id" in r:
                    by_id[r["row_id"]] = r
                if r.get("owner_row") is not None:
                    self.parts[(q, r["owner_row"], r["owner_field"])] \
                        .append(r)
        for v in self.parts.values():
            v.sort(key=lambda r: r["ordinal"])


def from_rows(tables: dict[str, list[dict[str, Any]]], cls: type,
              p: Optional[Plan] = None) -> dict[str, Any]:
    """The document the rows of one revision hold, for the root `cls`
    (SystemSpec or Binding), ready for `cls.model_validate`."""
    p = p or plan()
    t = p.classes[cls]
    roots = tables.get(t.q) or []
    if len(roots) != 1:
        raise LookupError(f"{len(roots)} {t.uml_type} rows in this revision")
    return _build(t, roots[0]["row_id"], _Index(tables))


def _build(t: ClassTable, row_id: int, ix: _Index) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for level in t.chain():
        row = ix.by_id[level.q][row_id]
        _read(level.fields, row, row_id, ix, out)
        if level.as_written:
            out = _written(level.cls, out, row["fields_set"], row["extra"])
    return out


def _written(cls: type, values: dict[str, Any], fields_set: Iterable[str],
             extra: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Only the fields the author wrote, and their extra keys (`_AsWritten`,
    ADR-0110)."""
    keep = set(fields_set or [])
    out = {}
    for name, info in cls.model_fields.items():
        key = info.alias or name
        if name in keep and key in values:
            out[key] = values[key]
    out.update(extra or {})
    return out


def _read(fields: list[Any], row: dict[str, Any], host: int, ix: _Index,
          out: dict[str, Any]) -> None:
    for f in fields:
        if isinstance(f, Scalar):
            value = row.get(f.col)
            out[f.key] = list(value) if f.many and value is not None \
                else value
        elif isinstance(f, Ref):
            out[f.key] = row.get(f.col)
        elif isinstance(f, Links):
            out[f.key] = [r["target_id"] for r in
                          ix.parts.get((f.link.q, host, f.path), [])]
        elif isinstance(f, Flat):
            if f.optional and not row.get(f.set_col):
                out[f.key] = None
                continue
            sub: dict[str, Any] = {}
            _read(f.children, row, host, ix, sub)
            if f.as_written:
                sub = _written(f.cls, sub, row.get(f.fields_col) or [],
                               row.get(f.extra_col))
            out[f.key] = sub
        elif isinstance(f, Parts):
            kids = [_build(f.table, r["row_id"], ix) for r in
                    ix.parts.get((f.table.q, host, f.path), [])]
            if f.many:
                out[f.key] = kids
            else:
                out[f.key] = kids[0] if kids else None
            if not f.many and not kids and not f.optional:
                out.pop(f.key)


def iter_tables(p: Optional[Plan] = None) -> Iterator[str]:
    """Every table a revision's rows may be in, the element index first."""
    p = p or plan()
    yield ELEMENT
    for t in p.tables():
        yield t.q
