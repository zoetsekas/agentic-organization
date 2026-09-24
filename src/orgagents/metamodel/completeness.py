"""Is everything the platform models declared in a profile? (ADR-0112)

The profiles are authoritative for meaning; `spec.model` and `spec.binding`
are the Python realisation. This module compares the two, both ways:

* **model → profile** (`gap()`): every class and enumeration reachable from
  `SystemSpec` or `Binding` is declared in exactly one profile, and every
  field of every class is a declared Property or a relationship end;
* **profile → model** (`mismatches()`): every declaration names a class,
  field or literal the models have, and a Property's type and multiplicity
  are the field's.

A gap is the migration checklist; a mismatch is always an error.
"""
from __future__ import annotations

import enum
import typing
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from pydantic import BaseModel

from ..spec import binding as _binding
from ..spec import model as _model
from . import PROFILES, Profile, Relationship, Shape

#: UML primitive types, and what realises each in Python. `Map` is a
#: free-form key/value map (labels, options, conditions) the model does not
#: look inside; `Any` is a value the model does not type at all.
PRIMITIVES: dict[str, tuple[type, ...]] = {
    "String": (str,),
    "Integer": (int,),
    "Real": (float, int),
    "Boolean": (bool,),
    "Map": (dict,),
    "Any": (object,),
}

_MODULES = (_model, _binding)


def py_class(name: str) -> Optional[type]:
    """A spec or binding class (or enum) by name."""
    for m in _MODULES:
        c = getattr(m, name, None)
        if isinstance(c, type):
            return c
    return None


def _module_of(cls: type):
    return _binding if cls.__module__.endswith("binding") else _model


def annotation(cls: type, name: str) -> Any:
    """A field's annotation, forward references resolved."""
    ann = cls.model_fields[name].annotation
    if isinstance(ann, typing.ForwardRef):
        ann = ann.__forward_arg__
    if isinstance(ann, str):
        ns = {**vars(typing), **vars(_module_of(cls))}
        ann = eval(ann.strip("'\""), ns)  # noqa: S307 - our own source
        if isinstance(ann, str):
            ann = eval(ann, ns)  # noqa: S307
    return ann


@dataclass(frozen=True)
class Shape_:
    """What a field's annotation says about its values."""

    base: Any              # the element type: a class, an enum, a Literal
    many: bool             # a list
    optional: bool         # may be None


def shape_of(ann: Any) -> Shape_:
    optional = many = False
    while True:
        origin = typing.get_origin(ann)
        args = typing.get_args(ann)
        if origin is typing.Union:
            rest = [a for a in args if a is not type(None)]
            optional = optional or len(rest) < len(args)
            ann = rest[0] if len(rest) == 1 else typing.Any
            continue
        if origin in (list, tuple, set):
            many = True
            ann = args[0] if args else typing.Any
            continue
        break
    return Shape_(ann, many, optional)


def _is_model(t: Any) -> bool:
    return isinstance(t, type) and issubclass(t, BaseModel)


def _is_enum(t: Any) -> bool:
    return isinstance(t, type) and issubclass(t, enum.Enum)


def reachable(root: type) -> list[type]:
    """Every model class and enum reachable from `root` through fields and
    bases, in walk order. Private helper bases (`_AsWritten`) are skipped:
    they add behaviour, not meaning."""
    seen: list[type] = []
    todo = [root]
    while todo:
        c = todo.pop(0)
        if c in seen:
            continue
        seen.append(c)
        if not _is_model(c):
            continue
        for base in c.__mro__[1:]:
            if _is_model(base) and base is not BaseModel \
                    and not base.__name__.startswith("_"):
                todo.append(base)
        for name in c.model_fields:
            for leaf in _leaves(annotation(c, name)):
                if _is_model(leaf) or _is_enum(leaf):
                    todo.append(leaf)
    return seen


def _leaves(ann: Any) -> list[Any]:
    args = typing.get_args(ann)
    if not args or typing.get_origin(ann) is typing.Literal:
        return [ann]
    out = []
    for a in args:
        out += _leaves(a)
    return out


def own_fields(cls: type) -> list[str]:
    """The fields a class declares itself, not those it inherits from a
    modelled base (an Agent's `knowledge` is a Worker's)."""
    inherited: set[str] = set()
    for base in cls.__mro__[1:]:
        if _is_model(base) and base is not BaseModel \
                and not base.__name__.startswith("_"):
            inherited |= set(base.model_fields)
    return [n for n in cls.model_fields if n not in inherited]


# -- what the profiles declare ------------------------------------------------

@dataclass(frozen=True)
class Declared:
    profile: str
    element: str           # stereotype kind, DataType / Enumeration name
    what: str              # stereotype | datatype | enumeration | association class


def declarations(profiles: Iterable[Profile] = ()) -> dict[str, list[Declared]]:
    """Python class name → where the profiles declare it."""
    out: dict[str, list[Declared]] = {}
    for p in profiles or PROFILES:
        for s in p.stereotypes:
            if s.model:
                out.setdefault(s.model, []).append(
                    Declared(p.name, s.kind, "stereotype"))
        for d in p.datatypes:
            out.setdefault(d.model, []).append(
                Declared(p.name, d.name, "datatype"))
        for e in p.enumerations:
            if e.model:
                out.setdefault(e.model, []).append(
                    Declared(p.name, e.name, "enumeration"))
        for ac in {r.association_class for r in p.relationships
                   if r.association_class}:
            out.setdefault(ac, []).append(
                Declared(p.name, ac, "association class"))
    return out


def _owner_class(owner: str) -> Optional[type]:
    """The Python class an owner name stands for: a stereotype's model, a
    DataType's, or an association class."""
    for p in PROFILES:
        st = p.stereotype(owner)
        if st is not None:
            return py_class(st.model) if st.model else None
        dt = p.datatype(owner)
        if dt is not None:
            return py_class(dt.model)
    return py_class(owner)


def _walk_field(cls: type, dotted: str) -> list[tuple[type, str]]:
    """Each (class, field) a dotted field passes through."""
    out = []
    for step in dotted.split("."):
        if cls is None or step not in cls.model_fields:
            out.append((cls, step))
            return out
        out.append((cls, step))
        base = shape_of(annotation(cls, step)).base
        cls = base if _is_model(base) else None
    return out


def declared_fields(profiles: Iterable[Profile] = ()) -> dict[type, set[str]]:
    """Python class → the fields a Property or a relationship end declares."""
    out: dict[type, set[str]] = {}

    def add(cls: Optional[type], name: str) -> None:
        if cls is not None:
            out.setdefault(cls, set()).add(name)

    for p in profiles or PROFILES:
        for prop in p.properties:
            add(_owner_class(prop.owner), prop.name)
        for r in p.relationships:
            _relationship_fields(r, add)
    return out


def _relationship_fields(r: Relationship, add) -> None:
    if not r.field:
        return
    ac = py_class(r.association_class) if r.association_class else None
    if r.shape is Shape.RECORD:
        add(_model.Organization, r.field)
        add(ac, "source")
        add(ac, "target")
        return
    for cls, step in _walk_field(_owner_class(r.owner_kind()), r.field):
        add(cls, step)
    if r.shape is Shape.REF_OBJECTS and r.key:
        add(ac, r.key)


# -- the two directions ----------------------------------------------------------

ROOTS = (_model.SystemSpec, _binding.Binding)


def classes() -> list[type]:
    out: list[type] = []
    for root in ROOTS:
        out += [c for c in reachable(root) if c not in out]
    return out


def gap() -> list[str]:
    """What the models have and the profiles do not declare: `Class` for a
    class or enumeration, `Class.field` for a field of a declared class."""
    decl = declarations()
    fields = declared_fields()
    out = []
    for c in classes():
        if c.__name__ not in decl:
            out.append(c.__name__)
            continue
        if _is_model(c):
            for name in own_fields(c):
                if name not in fields.get(c, set()):
                    out.append(f"{c.__name__}.{name}")
    return sorted(out)


def duplicates() -> list[str]:
    """Classes declared in more than one profile, or twice in one."""
    out = []
    for name, where in declarations().items():
        profiles = {d.profile for d in where}
        kinds = {(d.what, d.element) for d in where}
        if len(profiles) > 1 or len({k for k in kinds
                                     if k[0] != "association class"}) > 1:
            out.append(f"{name}: " + ", ".join(
                f"{d.profile}::{d.element} ({d.what})" for d in where))
    return out


def _uml_type_of(base: Any) -> set[str]:
    """The UML type names a Python element type may be declared as."""
    if typing.get_origin(base) is typing.Literal:
        lits = tuple(typing.get_args(base))
        return {e.name for p in PROFILES for e in p.enumerations
                if not e.model and e.literals == lits}
    if _is_enum(base):
        return {e.name for p in PROFILES for e in p.enumerations
                if e.model == base.__name__}
    if _is_model(base):
        # A DataType, or an AssociationClass used as a value (a mission's
        # sponsor is a HumanCounterpart).
        return {d.name for p in PROFILES for d in p.datatypes
                if d.model == base.__name__} | {
            r.association_class for p in PROFILES for r in p.relationships
            if r.association_class == base.__name__}
    if base is typing.Any:
        return {"Any"}
    if isinstance(base, type):
        if typing.get_origin(base) is dict or base is dict:
            return {"Map"}
        return {n for n, py in PRIMITIVES.items()
                if n not in ("Any", "Map") and base in py[:1]}
    if typing.get_origin(base) is dict:
        return {"Map"}
    return set()


def _mult(m: str) -> tuple[str, str]:
    lo, _, hi = m.partition("..")
    return (lo, hi or lo)


def mismatches() -> list[str]:
    """Declarations the models contradict."""
    out: list[str] = []
    for p in PROFILES:
        for e in p.enumerations:
            if not e.model:
                continue
            py = py_class(e.model)
            if not _is_enum(py):
                out.append(f"{p.name}::{e.name}: no enum {e.model}")
            elif tuple(m.value for m in py) != e.literals:
                out.append(f"{p.name}::{e.name}: literals "
                           f"{list(e.literals)} ≠ {[m.value for m in py]}")
        for d in p.datatypes:
            if not _is_model(py_class(d.model)):
                out.append(f"{p.name}::{d.name}: no class {d.model}")
        for prop in p.properties:
            cls = _owner_class(prop.owner)
            where = f"{p.name}::{prop.owner}.{prop.name}"
            if cls is None or prop.name not in cls.model_fields:
                out.append(f"{where}: no such field")
                continue
            sh = shape_of(annotation(cls, prop.name))
            allowed = _uml_type_of(sh.base)
            if prop.type not in allowed:
                out.append(f"{where}: typed {prop.type}, the field is "
                           f"{sh.base!r} ({sorted(allowed) or 'untyped'})")
            lo, hi = _mult(prop.multiplicity)
            # `0..0` on a list: a field that exists only to be refused.
            if (hi == "*") != sh.many and not (sh.many and hi == "0"):
                out.append(f"{where}: multiplicity {prop.multiplicity} but "
                           f"the field is {'a list' if sh.many else 'single'}")
            if sh.optional and lo != "0":
                out.append(f"{where}: multiplicity {prop.multiplicity} but "
                           f"the field may be None")
        for r in p.relationships:
            if not r.field or r.shape in (Shape.RECORD, Shape.PART):
                continue
            path = _walk_field(_owner_class(r.owner_kind()), r.field)
            cls, step = path[-1]
            if cls is None or step not in cls.model_fields:
                out.append(f"{p.name}: {r.owner_kind()}.{r.field} is not a "
                           f"field")
                continue
            sh = shape_of(annotation(cls, step))
            hi = _mult(r.target_mult)[1]
            if r.shape is Shape.REF and (sh.many or hi not in ("1",)):
                out.append(f"{p.name}: {r.owner_kind()}.{r.field} is a single "
                           f"reference; target multiplicity {r.target_mult}")
            if r.shape in (Shape.REFS, Shape.REF_OBJECTS) and not sh.many:
                out.append(f"{p.name}: {r.owner_kind()}.{r.field} is declared "
                           f"many but the field is single")
    return out


def summary() -> dict[str, Any]:
    """Counts for the gap, for the reference and the API."""
    g = gap()
    return {"gap": len(g),
            "classes": len([x for x in g if "." not in x]),
            "fields": len([x for x in g if "." in x])}
