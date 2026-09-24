"""The relational schema, generated from the UML profiles (ADR-0113).

Nothing here names a stereotype. The plan is derived from two things only:

* the **profiles** (`metamodel.PROFILES`): which Python class each
  stereotype, DataType and association class is, which profile — and so which
  PostgreSQL schema — declares it, which fields are references and to what,
  and each enumeration's literals;
* the **model's annotations** (`spec.model`, `spec.binding`, which the
  completeness test holds equal to the profiles): what type and multiplicity
  each field has.

From them, `plan()` builds the mapping ADR-0113 §1 describes (and its 1.1.0
section makes concrete): a table per stereotype, association class and
multi-valued DataType; flattened columns for a single DataType; arrays for
multi-valued primitives; a two-column reference (the id as written and the
row it resolves to) for a reference to one; a link table for a reference to
many; `owner_row`/`owner_field`/`ordinal` and a cascading key for a part; the
general row shared by `row_id` for a generalisation; and the `core.element`
index. `structure()` renders the plan as data, `ddl()` as SQL, and `diff()`
compares two structures for a forward-only migration (ADR-0042).

The mapper (`orgagents.persistence.mapper`) walks the same plan to write and
read rows, so the schema and the mapper cannot disagree.
"""
from __future__ import annotations

import enum
import hashlib
import json
import re
import typing
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional, Union

from pydantic import BaseModel

__all__ = [
    "ClassTable", "LinkTable", "Scalar", "Ref", "Links", "Flat", "Parts",
    "Plan", "plan", "structure", "ddl", "diff", "ident", "qualified",
    "SYSTEM_COLUMNS", "ELEMENT",
]

#: PostgreSQL truncates identifiers at 63 bytes; longer ones get a stable
#: hash suffix instead, so two long names never collide.
MAX_IDENT = 63

#: Columns every generated table may carry; no model field may use them.
SYSTEM_COLUMNS = ("row_id", "revision_id", "model_id", "model_version",
                  "owner_row", "owner_field", "ordinal", "fields_set",
                  "extra", "target_id", "target_row")

#: The element index: any element by id, without knowing its table.
ELEMENT = "core.element"

_PRIMITIVE_SQL = {str: "text", int: "bigint", float: "double precision",
                  bool: "boolean"}


def ident(name: str) -> str:
    if len(name.encode("utf-8")) <= MAX_IDENT:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[:MAX_IDENT - 9]}_{digest}"


def qualified(schema: str, name: str) -> str:
    return f"{schema}.{name}"


def quote(q: str) -> str:
    """`schema.table` or a column, quoted for PostgreSQL."""
    return ".".join(f'"{part}"' for part in q.split("."))


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------

@dataclass(eq=False)
class Scalar:
    """A value column: a primitive, an enumeration, a Map/Any (jsonb), or an
    array of primitives or enumeration literals."""

    attr: str
    key: str
    col: str
    sql: str
    many: bool
    nullable: bool
    literals: tuple[str, ...] = ()
    enumeration: str = ""      # the UML Enumeration typing it, if any


@dataclass(eq=False)
class Ref:
    """A reference to one element: the id as written, and the row it
    resolves to."""

    attr: str
    key: str
    col: str
    nullable: bool
    targets: frozenset[str]    # stereotype kinds, as the profile names them

    @property
    def row_col(self) -> str:
        return ident(f"{self.col}__row")


@dataclass(eq=False)
class Links:
    """A reference to many: rows of a link table."""

    attr: str
    key: str
    path: str                  # dotted path inside the host row
    link: "LinkTable"


@dataclass(eq=False)
class Flat:
    """A single-valued DataType (or association class used as a value),
    flattened into its host's columns."""

    attr: str
    key: str
    cls: type
    prefix: str                # column prefix, `mandate__enforcement`
    path: str                  # dotted path, `mandate.enforcement`
    optional: bool
    children: list[Any] = field(default_factory=list)
    as_written: bool = False

    @property
    def set_col(self) -> str:
        return ident(f"{self.prefix}__set")

    @property
    def fields_col(self) -> str:
        return ident(f"{self.prefix}__fields_set")

    @property
    def extra_col(self) -> str:
        return ident(f"{self.prefix}__extra")


@dataclass(eq=False)
class Parts:
    """Owned objects with a table of their own: a composition's parts, a
    multi-valued DataType's values, an association class's instances."""

    attr: str
    key: str
    path: str
    table: "ClassTable"
    many: bool
    optional: bool


FieldMap = Union[Scalar, Ref, Links, Flat, Parts]


@dataclass(eq=False)
class ClassTable:
    """The table of one model class."""

    cls: type
    schema: str
    name: str
    uml_type: str              # `Organisation::Agent`
    what: str                  # stereotype | datatype | association class
    kind: str = ""             # the stereotype kind, for a stereotype
    metaclass: str = ""
    base: Optional["ClassTable"] = None
    fields: list[FieldMap] = field(default_factory=list)
    as_written: bool = False
    #: Tables whose rows own rows of this one (empty for a root).
    owners: list["ClassTable"] = field(default_factory=list)
    #: Tables specialising this one (their rows share `row_id` with it).
    specials: list["ClassTable"] = field(default_factory=list)
    root: bool = False

    @property
    def q(self) -> str:
        return qualified(self.schema, self.name)

    @property
    def element(self) -> bool:
        return self.what == "stereotype"

    @property
    def owned(self) -> bool:
        return not self.root and bool(self.owners)

    def chain(self) -> list["ClassTable"]:
        """This table and its general ones, most general first."""
        out, t = [], self
        while t is not None:
            out.insert(0, t)
            t = t.base
        return out


@dataclass(eq=False)
class LinkTable:
    """Rows of a to-many reference, named after the class that declares the
    field and the field: `authority.mandate__decisions`."""

    schema: str
    name: str
    targets: frozenset[str]
    owners: list[ClassTable] = field(default_factory=list)

    @property
    def q(self) -> str:
        return qualified(self.schema, self.name)


@dataclass
class Plan:
    classes: dict[type, ClassTable]
    links: dict[tuple[type, str], LinkTable]
    roots: dict[type, ClassTable]
    #: Stereotype kind → its ClassTable, for resolving references.
    by_kind: dict[str, ClassTable]
    #: Enumerations: qualified reader table → literals.
    enumerations: dict[str, tuple[str, ...]]

    def target_table(self, kinds: frozenset[str]) -> str:
        """The table a reference's row key points at: the target's own
        table when every target kind is one table (a generalisation's
        general table included), else the element index."""
        tables = {self.by_kind[k].q if k in self.by_kind else None
                  for k in kinds}
        if len(tables) == 1 and None not in tables:
            return tables.pop()
        return ELEMENT

    def tables(self) -> list[Any]:
        return sorted(self.classes.values(), key=lambda t: t.q) + \
            sorted(self.links.values(), key=lambda t: t.q)


# --------------------------------------------------------------------------
# Building the plan from the profiles
# --------------------------------------------------------------------------

def _all_profile():
    from ..metamodel import PROFILES, Profile
    return Profile(
        name="all",
        stereotypes=[s for p in PROFILES for s in p.stereotypes],
        relationships=[r for p in PROFILES for r in p.relationships],
        enumerations=[e for p in PROFILES for e in p.enumerations],
        datatypes=[d for p in PROFILES for d in p.datatypes],
        properties=[x for p in PROFILES for x in p.properties])


@lru_cache(maxsize=1)
def all_profile():
    return _all_profile()


@lru_cache(maxsize=None)
def concrete_kinds(kind: str) -> frozenset[str]:
    from ..metamodel import specialisations
    return frozenset(specialisations(kind, all_profile()))


def _reference_fields() -> dict[tuple[type, str], set[str]]:
    """(class, field) → the stereotype kinds it refers to, from every
    relationship the profiles declare that is stored as an id."""
    from ..metamodel import PROFILES, RelKind, Shape
    from ..metamodel.completeness import _owner_class, _walk_field, py_class

    out: dict[tuple[type, str], set[str]] = {}
    for p in PROFILES:
        for r in p.relationships:
            if not r.field or r.kind is RelKind.GENERALIZATION \
                    or r.shape is Shape.PART:
                continue
            referenced = r.target if r.owner == "source" else r.source
            if r.shape is Shape.RECORD:
                ac = py_class(r.association_class)
                out.setdefault((ac, "source"), set()).add(r.source)
                out.setdefault((ac, "target"), set()).add(r.target)
            elif r.shape is Shape.REF_OBJECTS:
                ac = py_class(r.association_class)
                if ac is not None and r.key:
                    out.setdefault((ac, r.key), set()).add(referenced)
            else:
                path = _walk_field(_owner_class(r.owner_kind()), r.field)
                cls, step = path[-1]
                if cls is not None:
                    out.setdefault((cls, step), set()).add(referenced)
    return out


def _declarations() -> dict[str, tuple[str, str, str, str]]:
    """Class name → (profile, element, what, metaclass)."""
    from ..metamodel import PROFILES
    from ..metamodel.completeness import declarations
    meta = {(p.name, s.kind): (s.name, s.extends.value)
            for p in PROFILES for s in p.stereotypes}
    out = {}
    for name, where in declarations().items():
        d = where[0]
        if d.what == "stereotype":
            uml, mc = meta[(d.profile, d.element)]
            out[name] = (d.profile, d.element, d.what, mc, uml)
        else:
            mc = "AssociationClass" if d.what == "association class" \
                else "DataType"
            out[name] = (d.profile, d.element, d.what, mc, d.element)
    return out


def _enumeration_of(base: Any) -> tuple[tuple[str, ...], str]:
    """(literals, UML Enumeration name) for an Enum or Literal type."""
    from ..metamodel.completeness import _uml_type_of
    if typing.get_origin(base) is typing.Literal:
        lits = tuple(str(a) for a in typing.get_args(base))
    else:
        lits = tuple(str(m.value) for m in base)
    names = sorted(_uml_type_of(base))
    return lits, (names[0] if names else "")


def _is_enum(t: Any) -> bool:
    return (isinstance(t, type) and issubclass(t, enum.Enum)) or \
        typing.get_origin(t) is typing.Literal


class _Builder:
    def __init__(self) -> None:
        from ..metamodel.completeness import own_fields
        self.own_fields = own_fields
        self.decl = _declarations()
        self.refs = _reference_fields()
        self.classes: dict[type, ClassTable] = {}
        self.links: dict[tuple[type, str], LinkTable] = {}

    # -- tables ----------------------------------------------------------

    def table(self, cls: type) -> ClassTable:
        if cls in self.classes:
            return self.classes[cls]
        profile, element, what, mc, uml = self.decl[cls.__name__]
        name = element if what == "stereotype" else snake(element)
        t = ClassTable(cls=cls, schema=profile.lower(), name=ident(name),
                       uml_type=f"{profile}::{uml}", what=what,
                       kind=element if what == "stereotype" else "",
                       metaclass=mc,
                       as_written=_as_written(cls))
        self.classes[cls] = t
        base = _modelled_base(cls)
        if base is not None:
            t.base = self.table(base)
            t.base.specials.append(t)
        names = self.own_fields(cls) if base is not None \
            else list(cls.model_fields)
        for name in names:
            self._field(t, t, cls, name, prefix="", path="",
                        required=not t.as_written)
        return t

    def _field(self, host: ClassTable, table: ClassTable, cls: type,
               name: str, *, prefix: str, path: str, required: bool) -> None:
        """Map one field of `cls` (a table's class, or a flattened DataType
        inside it) into `table`'s columns or its parts and links."""
        out = self._map(host, cls, name, prefix=prefix, path=path,
                        required=required)
        if out is not None:
            table.fields.append(out)

    def _map(self, host: ClassTable, cls: type, name: str, *, prefix: str,
             path: str, required: bool) -> Optional[FieldMap]:
        from ..metamodel.completeness import _is_model, annotation, shape_of
        info = cls.model_fields[name]
        key = info.alias or name
        col = ident(f"{prefix}__{name}" if prefix else name)
        dotted = f"{path}.{name}" if path else name
        sh = shape_of(annotation(cls, name))
        base = sh.base
        nullable = sh.optional or not required
        if col in SYSTEM_COLUMNS:
            raise ValueError(f"{cls.__name__}.{name} collides with a "
                             f"generated column")
        targets = self.refs.get((cls, name))
        if targets:
            if sh.many:
                if sh.optional:
                    raise NotImplementedError(
                        f"{cls.__name__}.{name}: an optional list of ids")
                link = self._link(cls, name, frozenset(targets))
                link.owners.append(host)
                return Links(name, key, dotted, link)
            return Ref(name, key, col, nullable, frozenset(targets))
        if _is_model(base):
            declared = self.decl.get(base.__name__)
            if declared is None:
                raise ValueError(f"{base.__name__} is in no profile")
            if sh.many or declared[2] == "stereotype":
                if sh.many and sh.optional:
                    raise NotImplementedError(
                        f"{cls.__name__}.{name}: an optional list of parts")
                part = self.table(base)
                if host not in part.owners:
                    part.owners.append(host)
                return Parts(name, key, dotted, part, sh.many, sh.optional)
            flat = Flat(name, key, base, prefix=col, path=dotted,
                        optional=sh.optional, as_written=_as_written(base))
            inner_required = required and not sh.optional \
                and not flat.as_written
            for sub in base.model_fields:
                m = self._map(host, base, sub, prefix=col, path=dotted,
                              required=inner_required)
                if m is not None:
                    flat.children.append(m)
            return flat
        if _is_enum(base):
            lits, uml = _enumeration_of(base)
            return Scalar(name, key, col, "text", sh.many, nullable,
                          literals=lits, enumeration=uml)
        if base in _PRIMITIVE_SQL:
            return Scalar(name, key, col, _PRIMITIVE_SQL[base], sh.many,
                          nullable)
        # Map and Any: free-form, stored as the JSON it is.
        return Scalar(name, key, col, "json", False,
                      nullable or base is typing.Any)

    def _link(self, cls: type, name: str, targets: frozenset[str]
              ) -> LinkTable:
        if (cls, name) in self.links:
            return self.links[(cls, name)]
        profile, element, what, _mc, _uml = self.decl[cls.__name__]
        owner = element if what == "stereotype" else snake(element)
        link = LinkTable(profile.lower(), ident(f"{owner}__{name}"), targets)
        self.links[(cls, name)] = link
        return link


def _as_written(cls: type) -> bool:
    return any(b.__name__ == "_AsWritten" for b in cls.__mro__[1:])


def _modelled_base(cls: type) -> Optional[type]:
    for b in cls.__mro__[1:]:
        if isinstance(b, type) and issubclass(b, BaseModel) \
                and b is not BaseModel and not b.__name__.startswith("_"):
            return b
    return None


@lru_cache(maxsize=1)
def plan() -> Plan:
    """The mapping, built once from the profiles and the model."""
    from ..metamodel import PROFILES
    from ..spec.binding import Binding
    from ..spec.model import SystemSpec

    b = _Builder()
    roots = {}
    for root in (SystemSpec, Binding):
        t = b.table(root)
        t.root = True
        roots[root] = t
    by_kind: dict[str, ClassTable] = {}
    for t in b.classes.values():
        if t.kind:
            by_kind[t.kind] = t
    enumerations: dict[str, tuple[str, ...]] = {}
    for p in PROFILES:
        for e in p.enumerations:
            enumerations[qualified(p.name.lower(),
                                   ident(f"enum_{snake(e.name)}"))] = e.literals
    names = [t.q for t in b.classes.values()] + \
        [t.q for t in b.links.values()] + list(enumerations)
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"two tables would share a name: {sorted(dupes)}")
    return Plan(b.classes, b.links, roots, by_kind, enumerations)


# --------------------------------------------------------------------------
# The plan as a structure: what the database holds
# --------------------------------------------------------------------------

def _columns(fields: list[FieldMap], out: dict[str, dict[str, Any]],
             checks: dict[str, str], fks: dict[str, dict[str, Any]],
             table_q: str, p: Plan) -> None:
    for f in fields:
        if isinstance(f, Scalar):
            sql = f.sql + ("[]" if f.many else "")
            out[f.col] = {"type": sql, "nullable": f.nullable}
            if f.literals:
                lits = ", ".join("'" + x.replace("'", "''") + "'"
                                 for x in f.literals)
                expr = (f'"{f.col}" <@ ARRAY[{lits}]::text[]' if f.many
                        else f'"{f.col}" IN ({lits})')
                checks[ident(f"ck_{table_q.split('.')[1]}__{f.col}")] = expr
        elif isinstance(f, Ref):
            out[f.col] = {"type": "text", "nullable": f.nullable}
            out[f.row_col] = {"type": "bigint", "nullable": True}
            fks[ident(f"fk_{table_q.split('.')[1]}__{f.row_col}")] = {
                "columns": [f.row_col],
                "references": p.target_table(f.targets),
                "ref_columns": ["row_id"], "on_delete": "SET NULL"}
        elif isinstance(f, Flat):
            if f.optional:
                out[f.set_col] = {"type": "boolean", "nullable": False}
            if f.as_written:
                out[f.fields_col] = {"type": "text[]", "nullable": True}
                out[f.extra_col] = {"type": "json", "nullable": True}
            _columns(f.children, out, checks, fks, table_q, p)


def _owner_target(owners: list[ClassTable]) -> Optional[str]:
    """The table an owner key references: the one owner table, or the
    element index when several element tables may own the row."""
    tables = {o.q for o in owners}
    if len(tables) == 1:
        return tables.pop()
    if all(o.element for o in owners):
        return ELEMENT
    return None


def structure(p: Optional[Plan] = None) -> dict[str, Any]:
    """The whole schema as data: schemas, tables with their columns, keys,
    checks and indexes, and the enumeration reader tables' rows. What a
    migration is diffed from, and what `ddl()` renders."""
    p = p or plan()
    tables: dict[str, dict[str, Any]] = {}
    tables.update(_infrastructure())
    for t in p.classes.values():
        cols: dict[str, dict[str, Any]] = {
            "row_id": {"type": "bigint", "nullable": False},
            "revision_id": {"type": "bigint", "nullable": False},
        }
        checks: dict[str, str] = {}
        fks: dict[str, dict[str, Any]] = {
            ident(f"fk_{t.name}__revision"): {
                "columns": ["revision_id"], "references": "core.revision",
                "ref_columns": ["revision_id"], "on_delete": "CASCADE"}}
        indexes: dict[str, list[str]] = {
            ident(f"ix_{t.name}__revision"): ["revision_id"]}
        if t.element:
            cols["model_id"] = {"type": "text", "nullable": False}
            cols["model_version"] = {"type": "integer", "nullable": False}
            fks[ident(f"fk_{t.name}__element")] = {
                "columns": ["row_id"], "references": ELEMENT,
                "ref_columns": ["row_id"], "on_delete": "CASCADE"}
        if t.base is not None:
            fks[ident(f"fk_{t.name}__general")] = {
                "columns": ["row_id"], "references": t.base.q,
                "ref_columns": ["row_id"], "on_delete": "CASCADE"}
        if t.owned:
            # A general table whose specialisations are owned elsewhere
            # (a Team's row of the Organization) leaves them empty there.
            general = bool(t.specials)
            cols["owner_row"] = {"type": "bigint", "nullable": general}
            cols["owner_field"] = {"type": "text", "nullable": general}
            cols["ordinal"] = {"type": "integer", "nullable": general}
            target = _owner_target(t.owners)
            if target:
                fks[ident(f"fk_{t.name}__owner")] = {
                    "columns": ["owner_row"], "references": target,
                    "ref_columns": ["row_id"], "on_delete": "CASCADE"}
            indexes[ident(f"ix_{t.name}__owner")] = ["owner_row",
                                                      "owner_field"]
        if t.as_written:
            cols["fields_set"] = {"type": "text[]", "nullable": False}
            cols["extra"] = {"type": "json", "nullable": True}
        _columns(t.fields, cols, checks, fks, t.q, p)
        for f in _all_fields(t.fields):
            if isinstance(f, Ref):
                indexes[ident(f"ix_{t.name}__{f.row_col}")] = [f.row_col]
        if t.element and "id" in cols:
            indexes[ident(f"ix_{t.name}__id")] = ["revision_id", "id"]
        tables[t.q] = {"columns": cols, "primary_key": ["row_id"],
                       "checks": checks, "foreign_keys": fks,
                       "indexes": indexes,
                       "comment": f"{t.uml_type} ({t.what})"}
    for t in p.links.values():
        fks = {
            ident(f"fk_{t.name}__revision"): {
                "columns": ["revision_id"], "references": "core.revision",
                "ref_columns": ["revision_id"], "on_delete": "CASCADE"},
            ident(f"fk_{t.name}__target"): {
                "columns": ["target_row"],
                "references": p.target_table(t.targets),
                "ref_columns": ["row_id"], "on_delete": "SET NULL"},
        }
        target = _owner_target(t.owners)
        if target:
            fks[ident(f"fk_{t.name}__owner")] = {
                "columns": ["owner_row"], "references": target,
                "ref_columns": ["row_id"], "on_delete": "CASCADE"}
        tables[t.q] = {
            "columns": {
                "revision_id": {"type": "bigint", "nullable": False},
                "owner_row": {"type": "bigint", "nullable": False},
                "owner_field": {"type": "text", "nullable": False},
                "ordinal": {"type": "integer", "nullable": False},
                "target_id": {"type": "text", "nullable": False},
                "target_row": {"type": "bigint", "nullable": True},
            },
            "primary_key": ["owner_row", "owner_field", "ordinal"],
            "checks": {}, "foreign_keys": fks,
            "indexes": {ident(f"ix_{t.name}__revision"): ["revision_id"],
                        ident(f"ix_{t.name}__target"): ["target_row"]},
            "comment": "link: " + ", ".join(sorted(t.targets)),
        }
    for q, lits in p.enumerations.items():
        tables[q] = {
            "columns": {"literal": {"type": "text", "nullable": False},
                        "ordinal": {"type": "integer", "nullable": False}},
            "primary_key": ["literal"], "checks": {}, "foreign_keys": {},
            "indexes": {}, "comment": "enumeration",
            "rows": [[lit, i] for i, lit in enumerate(lits)],
        }
    schemas = sorted({q.split(".")[0] for q in tables})
    return {"format": 1, "schemas": schemas,
            "tables": {k: tables[k] for k in sorted(tables)}}


def _all_fields(fields: list[FieldMap]):
    for f in fields:
        yield f
        if isinstance(f, Flat):
            yield from _all_fields(f.children)


def _infrastructure() -> dict[str, dict[str, Any]]:
    """The tables that are not the model: models and their revisions, the
    element index, and the designer's own records (ADR-0113 1.1.0)."""
    def col(t: str, nullable: bool = False, default: str = "") -> dict:
        c: dict[str, Any] = {"type": t, "nullable": nullable}
        if default:
            c["default"] = default
        return c

    return {
        "core.model": {
            "columns": {"model_id": col("text"),
                        "head_revision": col("bigint", True),
                        "created_at": col("timestamptz", False, "now()"),
                        "updated_at": col("timestamptz", False, "now()")},
            "primary_key": ["model_id"], "checks": {},
            "foreign_keys": {"fk_model__head": {
                "columns": ["head_revision"], "references": "core.revision",
                "ref_columns": ["revision_id"], "on_delete": "SET NULL"}},
            "indexes": {}, "comment": "a design: one UML Model",
        },
        "core.revision": {
            "columns": {
                "revision_id": col("bigint", False,
                                   "nextval('core.row_seq')"),
                "model_id": col("text"), "version": col("integer"),
                "created_at": col("timestamptz", False, "now()"),
                "author": col("text", False, "''"),
                "message": col("text", False, "''"),
                "spec_state": col("text"), "draft_spec": col("json", True),
                "binding_state": col("text"),
                "draft_binding": col("json", True)},
            "primary_key": ["revision_id"],
            "checks": {
                "ck_revision__spec_state":
                    "\"spec_state\" IN ('model', 'draft', 'none')",
                "ck_revision__binding_state":
                    "\"binding_state\" IN ('binding', 'target', 'draft', "
                    "'none')"},
            "foreign_keys": {"fk_revision__model": {
                "columns": ["model_id"], "references": "core.model",
                "ref_columns": ["model_id"], "on_delete": "CASCADE"}},
            "indexes": {"ux_revision__model_version": ["model_id", "version"]},
            "unique": {"ux_revision__model_version": ["model_id", "version"]},
            "comment": "a snapshot of a design's rows",
        },
        "core.element": {
            "columns": {"row_id": col("bigint"),
                        "revision_id": col("bigint"),
                        "model_id": col("text"),
                        "model_version": col("integer"),
                        "id": col("text", True),
                        "uml_type": col("text"), "profile": col("text"),
                        "stereotype": col("text"),
                        "metaclass": col("text"),
                        "name": col("text", True)},
            "primary_key": ["row_id"], "checks": {},
            "foreign_keys": {"fk_element__revision": {
                "columns": ["revision_id"], "references": "core.revision",
                "ref_columns": ["revision_id"], "on_delete": "CASCADE"}},
            "indexes": {"ix_element__revision": ["revision_id", "id"],
                        "ix_element__type": ["uml_type"]},
            "comment": "every element, by id, whatever its table",
        },
        "designer.system": {
            "columns": {"model_id": col("text"),
                        "workspace_id": col("text"), "name": col("text"),
                        "description": col("text", False, "''"),
                        "status": col("text"),
                        "tags": col("text[]", False, "'{}'"),
                        "version": col("integer"),
                        "created_by": col("text", False, "''"),
                        "created_at": col("text"),
                        "updated_by": col("text", False, "''"),
                        "updated_at": col("text")},
            "primary_key": ["model_id"], "checks": {},
            "foreign_keys": {"fk_system__model": {
                "columns": ["model_id"], "references": "core.model",
                "ref_columns": ["model_id"], "on_delete": "CASCADE"}},
            "indexes": {"ix_system__workspace": ["workspace_id"]},
            "comment": "a design as the designer lists it (ADR-0031)",
        },
        "designer.revision": {
            "columns": {"revision_id": col("bigint"),
                        "revision_key": col("text"),
                        "layout": col("json"),
                        "created_at": col("text")},
            "primary_key": ["revision_id"], "checks": {},
            "foreign_keys": {"fk_drevision__revision": {
                "columns": ["revision_id"], "references": "core.revision",
                "ref_columns": ["revision_id"], "on_delete": "CASCADE"}},
            "indexes": {}, "comment": "a revision's canvas layout (ADR-0034)",
        },
        "designer.documents": {
            "columns": {"collection": col("text"), "id": col("text"),
                        "parent": col("text", True),
                        "name": col("text", True), "body": col("json"),
                        "updated_at": col("timestamptz", False,
                                          "clock_timestamp()")},
            "primary_key": ["collection", "id"], "checks": {},
            "foreign_keys": {},
            "indexes": {"ix_documents__parent": ["collection", "parent"],
                        "ix_documents__name": ["collection", "name"]},
            "comment": "workspaces, locks, settings and audit (ADR-0031, "
                       "ADR-0043), as in the SQLite store",
        },
        "designer.imports": {
            "columns": {"source": col("text"),
                        "imported_at": col("timestamptz", False, "now()"),
                        "detail": col("json", False, "'{}'")},
            "primary_key": ["source"], "checks": {}, "foreign_keys": {},
            "indexes": {},
            "comment": "one-time imports from a SQLite designer store",
        },
    }


# --------------------------------------------------------------------------
# SQL
# --------------------------------------------------------------------------

SEQUENCE = "core.row_seq"


def _column_sql(name: str, c: dict[str, Any]) -> str:
    out = f'"{name}" {c["type"]}'
    if not c["nullable"]:
        out += " NOT NULL"
    if c.get("default"):
        out += f" DEFAULT {c['default']}"
    return out


def _create_table(q: str, t: dict[str, Any]) -> list[str]:
    lines = [_column_sql(n, c) for n, c in t["columns"].items()]
    lines.append("PRIMARY KEY (" + ", ".join(f'"{c}"' for c in
                                            t["primary_key"]) + ")")
    for name, expr in sorted(t.get("checks", {}).items()):
        lines.append(f'CONSTRAINT "{name}" CHECK ({expr})')
    for name, cols in sorted(t.get("unique", {}).items()):
        lines.append(f'CONSTRAINT "{name}" UNIQUE (' +
                     ", ".join(f'"{c}"' for c in cols) + ")")
    out = [f"CREATE TABLE {quote(q)} (\n    " + ",\n    ".join(lines)
           + "\n);"]
    if t.get("comment"):
        out.append(f"COMMENT ON TABLE {quote(q)} IS "
                   f"'{t['comment'].replace(chr(39), chr(39) * 2)}';")
    return out


def _fk_sql(q: str, name: str, fk: dict[str, Any]) -> str:
    cols = ", ".join(f'"{c}"' for c in fk["columns"])
    refs = ", ".join(f'"{c}"' for c in fk["ref_columns"])
    return (f'ALTER TABLE {quote(q)} ADD CONSTRAINT "{name}" FOREIGN KEY '
            f"({cols}) REFERENCES {quote(fk['references'])} ({refs}) "
            f"ON DELETE {fk['on_delete']} DEFERRABLE INITIALLY DEFERRED;")


def _index_sql(q: str, name: str, cols: list[str], t: dict) -> str:
    if name in t.get("unique", {}):
        return ""
    return (f'CREATE INDEX "{name}" ON {quote(q)} (' +
            ", ".join(f'"{c}"' for c in cols) + ");")


def _rows_sql(q: str, rows: list[list[Any]]) -> str:
    vals = ", ".join("(" + ", ".join(
        "'" + str(v).replace("'", "''") + "'" if isinstance(v, str)
        else str(v) for v in r) + ")" for r in rows)
    return f"INSERT INTO {quote(q)} VALUES {vals};" if rows else ""


def ddl(s: Optional[dict[str, Any]] = None) -> str:
    """The whole schema as PostgreSQL DDL, deterministic: schemas, the row
    sequence, tables with their checks, then every foreign key (deferrable,
    so rows can be written in any order within a transaction), indexes and
    the enumeration rows."""
    s = s or structure()
    out = ["-- Generated from the UML profiles by orgagents.persistence."
           "relational (ADR-0113). Do not edit."]
    for schema in s["schemas"]:
        out.append(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')
    out.append(f"CREATE SEQUENCE {quote(SEQUENCE)};")
    for q, t in s["tables"].items():
        out += _create_table(q, t)
    for q, t in s["tables"].items():
        for name, fk in sorted(t["foreign_keys"].items()):
            out.append(_fk_sql(q, name, fk))
    for q, t in s["tables"].items():
        for name, cols in sorted(t["indexes"].items()):
            sql = _index_sql(q, name, cols, t)
            if sql:
                out.append(sql)
    for q, t in s["tables"].items():
        if t.get("rows"):
            out.append(_rows_sql(q, t["rows"]))
    return "\n".join(out) + "\n"


def diff(old: Optional[dict[str, Any]], new: dict[str, Any]) -> list[str]:
    """Forward-only statements taking a database from `old` to `new`
    (ADR-0042): schemas and tables created or dropped, columns added,
    dropped or retyped, checks, keys and indexes replaced, enumeration rows
    rewritten. With no `old`, the whole DDL."""
    if not old:
        return [ddl(new)]
    out: list[str] = []
    for schema in new["schemas"]:
        if schema not in old["schemas"]:
            out.append(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')
    ot, nt = old["tables"], new["tables"]
    fk_add: list[str] = []
    for q, t in nt.items():
        if q not in ot:
            out += _create_table(q, t)
            for name, fk in sorted(t["foreign_keys"].items()):
                fk_add.append(_fk_sql(q, name, fk))
            for name, cols in sorted(t["indexes"].items()):
                sql = _index_sql(q, name, cols, t)
                if sql:
                    fk_add.append(sql)
            if t.get("rows"):
                fk_add.append(_rows_sql(q, t["rows"]))
            continue
        o = ot[q]
        for name in sorted(set(o["foreign_keys"]) - set(t["foreign_keys"])):
            out.append(f'ALTER TABLE {quote(q)} DROP CONSTRAINT "{name}";')
        for name, fk in sorted(t["foreign_keys"].items()):
            if name in o["foreign_keys"] and o["foreign_keys"][name] != fk:
                out.append(f'ALTER TABLE {quote(q)} DROP CONSTRAINT '
                           f'"{name}";')
                fk_add.append(_fk_sql(q, name, fk))
            elif name not in o["foreign_keys"]:
                fk_add.append(_fk_sql(q, name, fk))
        for name, expr in sorted(o["checks"].items()):
            if t["checks"].get(name) != expr:
                out.append(f'ALTER TABLE {quote(q)} DROP CONSTRAINT '
                           f'"{name}";')
        for name in sorted(set(o["indexes"]) - set(t["indexes"])):
            if name not in o.get("unique", {}):
                out.append(f'DROP INDEX "{q.split(".")[0]}"."{name}";')
        for col in o["columns"]:
            if col not in t["columns"]:
                out.append(f'ALTER TABLE {quote(q)} DROP COLUMN "{col}";')
        for col, c in t["columns"].items():
            oc = o["columns"].get(col)
            if oc is None:
                add = dict(c)
                if not c["nullable"] and not c.get("default"):
                    # Existing revisions have no value: the column starts
                    # nullable, filled by the next save of each design.
                    add["nullable"] = True
                out.append(f"ALTER TABLE {quote(q)} ADD COLUMN "
                           f"{_column_sql(col, add)};")
                continue
            if oc["type"] != c["type"]:
                out.append(f'ALTER TABLE {quote(q)} ALTER COLUMN "{col}" '
                           f'TYPE {c["type"]} USING "{col}"::{c["type"]};')
            if oc["nullable"] != c["nullable"]:
                out.append(f'ALTER TABLE {quote(q)} ALTER COLUMN "{col}" '
                           + ("DROP NOT NULL;" if c["nullable"]
                              else "SET NOT NULL;"))
            if oc.get("default") != c.get("default"):
                out.append(f'ALTER TABLE {quote(q)} ALTER COLUMN "{col}" '
                           + (f"SET DEFAULT {c['default']};"
                              if c.get("default") else "DROP DEFAULT;"))
        for name, expr in sorted(t["checks"].items()):
            if o["checks"].get(name) != expr:
                out.append(f'ALTER TABLE {quote(q)} ADD CONSTRAINT "{name}" '
                           f"CHECK ({expr});")
        for name, cols in sorted(t["indexes"].items()):
            if o["indexes"].get(name) != cols:
                if name in o["indexes"] and name not in o.get("unique", {}):
                    out.append(f'DROP INDEX "{q.split(".")[0]}"."{name}";')
                sql = _index_sql(q, name, cols, t)
                if sql:
                    fk_add.append(sql)
        if t.get("rows") != o.get("rows"):
            out.append(f"DELETE FROM {quote(q)};")
            if t.get("rows"):
                out.append(_rows_sql(q, t["rows"]))
        if t.get("comment") != o.get("comment") and t.get("comment"):
            out.append(f"COMMENT ON TABLE {quote(q)} IS "
                       f"'{t['comment'].replace(chr(39), chr(39) * 2)}';")
    for q in sorted(set(ot) - set(nt), reverse=True):
        out.append(f"DROP TABLE {quote(q)} CASCADE;")
    for schema in old["schemas"]:
        if schema not in new["schemas"]:
            out.append(f'DROP SCHEMA "{schema}" CASCADE;')
    return out + fk_add


def structure_json(s: Optional[dict[str, Any]] = None) -> str:
    return json.dumps(s or structure(), indent=1, sort_keys=True) + "\n"
