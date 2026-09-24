"""Typed exchange: YAML and JSON in which every element names its UML type
(ADR-0113 §4–6).

`dump(spec_or_binding, format="yaml"|"json", typed=True)` writes the
canonical document — the same one `dump_spec` writes — with a `type:
<Profile>::<Name>` on every object whose class a profile declares (a
stereotype, a DataType or an association class), and at the root `type:
Core::Model` (or `Deployment::Binding`) with the versions of the profiles it
was written against.

`strip_types(data, root)` is the loader's pass: it checks every `type` that is
present against the position it sits in, and removes them, so the Pydantic
model never sees one. A file with no types — everything written before
ADR-0113 — passes through unchanged; the types are inferred from position.

Which class sits at which position is read from the model's annotations; what
that class *is* in UML is read from the profiles (`metamodel.completeness.
declarations`), so a class added to a profile is typed here with no code.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator, Optional

import yaml
from pydantic import BaseModel

from .issue_codes import catalog, display_number

__all__ = [
    "TypeIssue", "TypedDocumentError", "uml_type_of", "root_type",
    "profile_versions", "add_types", "strip_types", "canonical", "dump",
    "load", "typed_json_schema", "FORMATS",
]

FORMATS = ("yaml", "json")

#: The root of a spec is the UML Model (ADR-0113 §4); `Core::System` names
#: the same thing by its stereotype and is accepted on input.
SPEC_ROOT = "Core::Model"
SPEC_ROOT_ALIASES = {"Core::Model", "Core::System"}
BINDING_ROOT = "Deployment::Binding"


# --------------------------------------------------------------------------
# What each class is in UML
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _declared() -> dict[str, str]:
    """Python class name → `Profile::Name`, read from the profiles."""
    from ..metamodel import PROFILES
    from ..metamodel.completeness import declarations

    names: dict[tuple[str, str], str] = {}
    for p in PROFILES:
        for s in p.stereotypes:
            names[(p.name, s.kind)] = s.name
    out: dict[str, str] = {}
    for cls, where in declarations().items():
        d = where[0]
        name = names.get((d.profile, d.element), d.element) \
            if d.what == "stereotype" else d.element
        out[cls] = f"{d.profile}::{name}"
    return out


@lru_cache(maxsize=1)
def known_types() -> frozenset[str]:
    return frozenset(_declared().values()) | SPEC_ROOT_ALIASES | {BINDING_ROOT}


def uml_type_of(cls: type) -> Optional[str]:
    """The UML type a model class is declared as, or None."""
    return _declared().get(cls.__name__)


def root_type(cls: type) -> str:
    from .binding import Binding
    return BINDING_ROOT if issubclass(cls, Binding) else SPEC_ROOT


def profile_versions(cls: type) -> dict[str, str]:
    """The profiles a document of this root is written against, with their
    versions: the spec profiles for a spec, all of them for a binding."""
    from ..metamodel import PROFILES, SPEC_PROFILES
    from .binding import Binding
    profiles = PROFILES if issubclass(cls, Binding) else SPEC_PROFILES
    return {p.name: p.version for p in profiles}


# --------------------------------------------------------------------------
# Walking a document by its model
# --------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _fields(cls: type) -> tuple[tuple[tuple[str, ...], Optional[type], bool], ...]:
    """(keys the field may be written under, model class of its values or
    None, many) for each field of a model class."""
    from ..metamodel.completeness import _is_model, annotation, shape_of
    out = []
    for name, field in cls.model_fields.items():
        sh = shape_of(annotation(cls, name))
        keys = (name,) if not field.alias or field.alias == name \
            else (name, field.alias)
        out.append((keys, sh.base if _is_model(sh.base) else None, sh.many))
    return tuple(out)


@lru_cache(maxsize=None)
def _subs(cls: type) -> dict[str, type]:
    """Key → model class of the values under it, for every field of `cls`
    holding model objects (under its name and its alias). At the spec's
    root, the pre-ADR-0101 layout's loose collections are the
    Organization's."""
    from .model import ORGANIZATION_COLLECTIONS, Organization, SystemSpec
    out = {k: sub for keys, sub, _many in _fields(cls) if sub is not None
           for k in keys}
    if cls is SystemSpec:
        org = _subs(Organization)
        for loose, field in ORGANIZATION_COLLECTIONS.items():
            if field in org:
                out.setdefault(loose, org[field])
    return out


def _children(data: dict[str, Any], cls: type
              ) -> Iterator[tuple[str, Any, type]]:
    """(where, value, class) for every nested model value of a node."""
    for key, sub in _subs(cls).items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, list):
            for i, item in enumerate(value):
                yield f"{key}[{i}]", item, sub
        else:
            yield key, value, sub


def unknown_keys(data: Any, cls: type, where: str = "") -> list[str]:
    """Paths of keys the model would drop: not a field (by name or alias)
    of the class at that position, in a class that keeps no extras. A
    document with none loses nothing by being validated and dumped again."""
    if not isinstance(data, dict):
        return []
    from .model import ORGANIZATION_COLLECTIONS, SystemSpec
    known = {k for keys, _s, _m in _fields(cls) for k in keys}
    if cls is SystemSpec:
        known |= set(ORGANIZATION_COLLECTIONS)
    keeps_extra = cls.model_config.get("extra") == "allow"
    out = []
    if not keeps_extra:
        out += [f"{where}.{k}" if where else str(k)
                for k in data if k not in known]
    for key, value, sub in _children(data, cls):
        out += unknown_keys(value, sub, f"{where}.{key}" if where else key)
    return out


# --------------------------------------------------------------------------
# Adding types
# --------------------------------------------------------------------------

def add_types(data: dict[str, Any], cls: type) -> dict[str, Any]:
    """A copy of `data` with `type` first in every declared object and the
    root's `type` and `profiles`."""
    body = _typed(data, cls)
    body.pop("type", None)
    return {"type": root_type(cls), "profiles": profile_versions(cls), **body}


def _typed(node: Any, cls: type) -> Any:
    if not isinstance(node, dict):
        return node
    t = uml_type_of(cls)
    subs = _subs(cls)
    out: dict[str, Any] = {"type": t} if t else {}
    for key, value in node.items():
        if key == "type" and t:
            continue
        sub = subs.get(key)
        if sub is None:
            out[key] = value
        elif isinstance(value, list):
            out[key] = [_typed(v, sub) for v in value]
        else:
            out[key] = _typed(value, sub)
    return out


# --------------------------------------------------------------------------
# Checking and stripping types
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TypeIssue:
    """One `type` that is wrong, with its catalogued code (ADR-0113 §5)."""

    code: str
    where: str
    message: str

    @property
    def number(self) -> str:
        entry = catalog().get(self.code)
        return display_number(int(entry["number"])) if entry else ""

    def __str__(self) -> str:
        return f"{self.number} {self.code} at {self.where or '(root)'}: " \
               f"{self.message}"


class TypedDocumentError(ValueError):
    """A typed document whose types contradict their positions."""

    def __init__(self, issues: list[TypeIssue]) -> None:
        self.issues = issues
        head = str(issues[0])
        more = f" (and {len(issues) - 1} more)" if len(issues) > 1 else ""
        super().__init__(head + more)


def strip_types(data: Any, cls: type, *, check: bool = True) -> Any:
    """`data` with every `type` removed (and the root's `profiles`), after
    checking each one against its position. Raises `TypedDocumentError`
    naming every wrong one. The input is not modified."""
    if not isinstance(data, dict):
        return data
    data = copy.deepcopy(data)
    issues: list[TypeIssue] = []
    expected_root = root_type(cls)
    t = data.pop("type", None)
    if t is not None and check:
        ok = t in (SPEC_ROOT_ALIASES if expected_root == SPEC_ROOT
                   else {expected_root})
        if not ok:
            issues.append(_mismatch("", t, expected_root))
    profiles = data.pop("profiles", None)
    if profiles is not None and check:
        issues += _check_profiles(profiles, cls)
    for key, value, sub in _children(data, cls):
        _strip(value, sub, key, issues, check)
    if issues:
        raise TypedDocumentError(issues)
    return data


def _strip(node: Any, cls: type, where: str, issues: list[TypeIssue],
           check: bool) -> None:
    if not isinstance(node, dict):
        return
    expected = uml_type_of(cls)
    if "type" in node and expected is not None:
        t = node.pop("type")
        if check and t != expected:
            issues.append(_mismatch(where, t, expected))
    for key, value, sub in _children(node, cls):
        _strip(value, sub, f"{where}.{key}", issues, check)


def _mismatch(where: str, t: Any, expected: str) -> TypeIssue:
    if not isinstance(t, str) or t not in known_types():
        return TypeIssue(
            "unknown_uml_type", where,
            f"`{t}` is not a type any profile declares; {expected} belongs "
            f"here")
    return TypeIssue(
        "uml_type_mismatch", where,
        f"`{t}` sits where {'an' if expected.split('::')[1][:1] in 'AEIOU' else 'a'} "
        f"{expected} belongs")


def _check_profiles(profiles: Any, cls: type) -> list[TypeIssue]:
    known = profile_versions(cls)
    if not isinstance(profiles, dict):
        return [TypeIssue("unsupported_profile_version", "profiles",
                          "`profiles` must map each profile to its version")]
    out = []
    for name, version in profiles.items():
        if name not in known:
            out.append(TypeIssue(
                "unsupported_profile_version", f"profiles.{name}",
                f"no profile `{name}`; this platform has "
                f"{', '.join(sorted(known))}"))
            continue
        mine = str(known[name]).split(".")[0]
        theirs = str(version).split(".")[0]
        if theirs != mine:
            out.append(TypeIssue(
                "unsupported_profile_version", f"profiles.{name}",
                f"written against {name} {version}; this platform has "
                f"{known[name]}, and a different major version may mean "
                f"something else"))
    return out


# --------------------------------------------------------------------------
# Dumping and loading
# --------------------------------------------------------------------------

def canonical(obj: BaseModel) -> dict[str, Any]:
    """The canonical document of a spec or a binding: what `dump_spec`
    writes, as data — defaults left out, `spec_version` always kept."""
    from .model import SystemSpec
    data = obj.model_dump(mode="json", exclude_defaults=True)
    if isinstance(obj, SystemSpec):
        data.setdefault("metadata", {})["spec_version"] = \
            obj.metadata.spec_version
    return data


def serialise(data: dict[str, Any], format: str = "yaml") -> str:
    if format == "json":
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if format == "yaml":
        return yaml.safe_dump(data, sort_keys=False, width=100,
                              allow_unicode=True)
    raise ValueError(f"unknown format `{format}`; use yaml or json")


def dump(obj: BaseModel, format: str = "yaml", typed: bool = True) -> str:
    """A spec or a binding as YAML or JSON, typed unless asked not to be."""
    data = canonical(obj)
    if typed:
        data = add_types(data, type(obj))
    return serialise(data, format)


def load(text: str, cls: Optional[type] = None) -> BaseModel:
    """A typed or untyped spec or binding from YAML or JSON text. The root's
    `type` decides which, when `cls` is not given; without either, a
    document with `targets` and no `metadata` is a binding."""
    from .binding import Binding
    from .loader import load_spec_text
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("a spec or binding must be a mapping")
    if cls is None:
        cls = binding_or_spec(data)
    if issubclass(cls, Binding):
        return Binding.model_validate(strip_types(data, Binding))
    return load_spec_text(text)


def binding_or_spec(data: dict[str, Any]) -> type:
    from .binding import Binding
    from .model import SystemSpec
    t = data.get("type")
    if t == BINDING_ROOT:
        return Binding
    if t in SPEC_ROOT_ALIASES:
        return SystemSpec
    return Binding if ("targets" in data and "metadata" not in data) \
        else SystemSpec


# --------------------------------------------------------------------------
# The JSON Schema of the typed format
# --------------------------------------------------------------------------

def typed_json_schema(cls: Optional[type] = None) -> dict[str, Any]:
    """The JSON Schema of the typed format (ADR-0113 §6): the model's own
    schema with a `type` constant on every declared class and the root's
    `type` and `profiles`. `type` is optional, as it is on input."""
    from .binding import Binding
    from .model import SystemSpec
    if cls is None:
        spec, binding = typed_json_schema(SystemSpec), \
            typed_json_schema(Binding)
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://orgagents.dev/schema/typed-exchange.json",
            "title": "OrgAgents typed exchange (ADR-0113)",
            "description": "A System Spec or a Binding in which every element "
                           "names its UML type as <Profile>::<Name>.",
            "oneOf": [{"$ref": "#/$defs/SystemSpecDocument"},
                      {"$ref": "#/$defs/BindingDocument"}],
            "$defs": {**spec["$defs"], **binding["$defs"],
                      "SystemSpecDocument": spec["root"],
                      "BindingDocument": binding["root"]},
        }
    schema = cls.model_json_schema(mode="validation")
    defs = schema.pop("$defs", {})
    for name, d in defs.items():
        t = _declared().get(name)
        if t and isinstance(d.get("properties"), dict):
            d["properties"] = {"type": {"const": t}, **d["properties"]}
    root = dict(schema)
    root["properties"] = {
        "type": {"const": root_type(cls)} if cls is not SystemSpec
        else {"enum": sorted(SPEC_ROOT_ALIASES)},
        "profiles": {
            "type": "object",
            "properties": {k: {"type": "string"}
                           for k in profile_versions(cls)},
            "additionalProperties": False,
        },
        **root.get("properties", {}),
    }
    return {"root": root, "$defs": defs}


def schema_text() -> str:
    return json.dumps(typed_json_schema(), indent=2, sort_keys=True) + "\n"

