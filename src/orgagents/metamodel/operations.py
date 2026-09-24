"""Operations on the model, with UML's semantics (ADR-0102).

What a person does to an organisation — hire an agent into a team, share a
knowledge source, deploy an agent into a sandbox, delete a capability — is an
operation on instances of the metamodel. Each one here:

* works from the profile, not from where the YAML keeps things: `link` looks
  the relationship up and writes it by its shape, so a new relationship needs
  no new operation;
* follows UML's rules for what an operation does to the rest of the model —
  a composite part moves with, or is destroyed with, its whole; destroying an
  instance destroys its links; a link to an end whose multiplicity would drop
  below its lower bound is not destroyed silently but refused;
* is **transactional**: it is applied to a copy, the model's constraints are
  checked, and if it would introduce a violation the original is returned
  unchanged with the violations that refused it.

The designer, later, is a view that calls these; it adds no rules of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..spec import model as spec_model
from . import PROFILE, Relationship, RelKind, Shape, specialisations
from .constraints import Violation, check, integrity
from .instances import collect


class OperationError(ValueError):
    """The operation cannot even be attempted: no such instance, no such
    relationship between these kinds."""


@dataclass
class Result:
    spec: spec_model.SystemSpec
    accepted: bool
    #: What the operation did besides its own change, in words: the links it
    #: destroyed, the parts it moved or destroyed.
    effects: list[str] = field(default_factory=list)
    #: Why it was refused: the violations it would have introduced.
    violations: list[Violation] = field(default_factory=list)
    #: Accepted, but what the element it created still lacks before the
    #: model is complete: a tool that wraps nothing yet (ADR-0103).
    incomplete: list[Violation] = field(default_factory=list)


# -- lookups -----------------------------------------------------------------

def relationship(source_kind: str, target_kind: str,
                 name: str = "") -> Relationship:
    """The one relationship between two kinds, by stereotype or field when
    there are several (an agent `produces` and `relies on` a data class)."""
    found = [
        r for r in PROFILE.relationships
        if r.field and source_kind in specialisations(r.source)
        and target_kind in specialisations(r.target)
        and (not name or name in (r.stereotype, r.field, r.legacy))
    ]
    if len(found) != 1:
        options = ", ".join(sorted({r.stereotype for r in found})) or "none"
        raise OperationError(
            f"{len(found)} relationships from {source_kind} to {target_kind}"
            f"{f' named {name!r}' if name else ''} (options: {options})")
    return found[0]


def _find(spec: spec_model.SystemSpec, kind: str, id_: str) -> Any:
    inst = collect(spec).get(kind, id_)
    if inst is None:
        raise OperationError(f"no {kind} '{id_}'")
    return inst


def _collection_of(spec: spec_model.SystemSpec, kind: str) -> list:
    st = PROFILE.stereotype(kind)
    if st is None or not st.collection:
        raise OperationError(f"a {kind} is created inside its owner")
    obj: Any = spec.organization
    *path, last = st.collection.split(".")
    for step in path:
        obj = getattr(obj, step)
    return getattr(obj, last)


_MODEL_OF = {s.kind: s.model for s in PROFILE.stereotypes}


def _new(kind: str, id_: str, attrs: dict[str, Any]) -> Any:
    cls = getattr(spec_model, _MODEL_OF[kind])
    # An edge is identified by its ends, not by an id of its own.
    ident = {"id": id_} if "id" in cls.model_fields else {}
    return cls.model_validate({**ident, **attrs})


def _leaf(holder: Any, dotted: str) -> tuple[Any, str]:
    """The object that holds a relationship's last field, and that field's
    name: `interface.tools` is `tools` on the workflow's interface."""
    *path, last = dotted.split(".")
    for step in path:
        holder = getattr(holder, step)
    return holder, last


# -- the transaction ---------------------------------------------------------

def _transact(spec: spec_model.SystemSpec,
              change: Callable[[spec_model.SystemSpec, list[str]], None],
              created: frozenset[str] = frozenset()) -> Result:
    """Apply `change` to a copy and let the constraints decide.

    Refused if it breaks integrity anywhere, or leaves incomplete an element
    that was complete. The element it `created` may start incomplete — a
    tool dropped from the palette wraps nothing yet — and the answer says
    what it still lacks."""
    before = {str(v) for v in check(spec)}
    work = spec.model_copy(deep=True)
    effects: list[str] = []
    try:
        change(work, effects)
        work = spec_model.SystemSpec.model_validate(work.model_dump())
    except OperationError:
        raise
    except (ValueError, TypeError) as exc:
        return Result(spec, False, effects,
                      [Violation("well_formed", "-", str(exc).splitlines()[0])])
    new = [v for v in check(work) if str(v) not in before]
    broken = integrity(new)
    unfinished = [v for v in new if v not in broken]
    refused = broken + [v for v in unfinished if v.element not in created]
    if refused:
        return Result(spec, False, effects, refused)
    return Result(work, True, effects, incomplete=unfinished)


# -- operations ----------------------------------------------------------------

def create(spec: spec_model.SystemSpec, of: str, id_: str, *,
           owner: str = "", **attrs: Any) -> Result:
    """A new instance, created inside its owner — the whole of the
    composition that holds its kind, found in the profile: an agent in a
    team, a sub-agent in an agent, a step in a workflow, anything else in the
    organisation (the default owner). `of` is the kind: `kind` is left free
    for attributes of that name (a sub-agent's kind, a flow's kind)."""
    kind = of
    def change(s: spec_model.SystemSpec, effects: list[str]) -> None:
        obj = _new(kind, id_, attrs)
        model = collect(s)
        for rel in PROFILE.relationships:
            if rel.shape is not Shape.PART or \
                    kind not in specialisations(rel.target):
                continue
            default = (s.metadata.name if rel.source == "system"
                       else s.organization.id)
            whole = model.get(rel.source, owner or default)
            if whole is None or whole.kind not in specialisations(rel.source):
                continue
            target: Any = whole.obj
            *path, last = rel.field.split(".")
            for step in path:
                target = getattr(target, step)
            getattr(target, last).append(obj)
            # A graph dumps only what was written (ADR-0102), so the list a
            # step was appended to must count as written, or the first step of
            # an empty workflow would vanish on the next dump.
            fields_set = getattr(target, "model_fields_set", None)
            if isinstance(fields_set, set):
                fields_set.add(last)
            return
        raise OperationError(f"no {kind} can be created in '{owner}'")
    ref = (f"action:{owner}.{id_}" if kind == "action" else f"{kind}:{id_}")
    return _transact(spec, change, created=frozenset({ref}))


def update(spec: spec_model.SystemSpec, kind: str, id_: str,
           **attrs: Any) -> Result:
    """Change an instance's own attributes — what Properties edits.

    A set of references (an agent's knowledge, a policy's subjects) may be
    set whole here: it is the same as linking and unlinking each, and the
    constraints check every reference either way. What may not be set here is
    a composite part (a team's members: that is a move, `link`) or an
    association-class record (a flow: `link`), whose effects are the
    relationship's own."""
    structural = {r.field for r in PROFILE.relationships
                  if r.field and r.shape in (Shape.PART, Shape.RECORD)
                  and kind in specialisations(r.source)}
    if structural & set(attrs):
        raise OperationError(
            f"{sorted(structural & set(attrs))} of a {kind} are parts or "
            "links: link or unlink them")

    def change(s: spec_model.SystemSpec, effects: list[str]) -> None:
        obj = _find(s, kind, id_).obj
        validated = type(obj).model_validate(
            {**obj.model_dump(by_alias=True), **attrs})
        for name in attrs:
            setattr(obj, name, getattr(validated, name))
    return _transact(spec, change)


def link(spec: spec_model.SystemSpec, source: tuple[str, str],
         target: tuple[str, str], name: str = "", **attrs: Any) -> Result:
    """Create a link between two instances, written by the relationship's
    shape. Linking a composite part *moves* it: a part has one whole."""
    (sk, sid), (tk, tid) = source, target
    rel = relationship(sk, tk, name)

    def change(s: spec_model.SystemSpec, effects: list[str]) -> None:
        src = _find(s, sk, sid)
        dst = _find(s, tk, tid)
        if rel.shape is Shape.PART:
            _detach(s, dst, effects)
            getattr(src.obj, rel.field).append(dst.obj)
            return
        if rel.shape is Shape.RECORD:
            cls = getattr(spec_model, rel.association_class)
            getattr(s.organization, rel.field).append(
                cls.model_validate({"source": sid, "target": tid, **attrs}))
            return
        holder, name = _leaf(src.obj, rel.field)
        if rel.shape is Shape.REF:
            setattr(holder, name, tid)
        elif rel.shape is Shape.REFS:
            values = getattr(holder, name)
            if tid not in values:
                values.append(tid)
        elif rel.shape is Shape.REF_OBJECTS:
            cls = getattr(spec_model, rel.association_class)
            values = getattr(holder, name)
            if not any(getattr(v, rel.key) == tid for v in values):
                values.append(cls.model_validate({rel.key: tid, **attrs}))
    return _transact(spec, change)


def unlink(spec: spec_model.SystemSpec, source: tuple[str, str],
           target: tuple[str, str], name: str = "") -> Result:
    """Destroy one link. A composite part cannot be unlinked from its whole —
    it would have none — so that is `delete` or `link` to another whole."""
    (sk, sid), (tk, tid) = source, target
    rel = relationship(sk, tk, name)
    if rel.shape is Shape.PART:
        raise OperationError(
            f"a {tk} is a part of its {sk}: move it (link) or delete it")

    def change(s: spec_model.SystemSpec, effects: list[str]) -> None:
        holder = _find(s, sk, sid).obj
        _drop(s, rel, holder, tid, effects, source_id=sid)
    return _transact(spec, change)


def delete(spec: spec_model.SystemSpec, kind: str, id_: str) -> Result:
    """Destroy an instance, its composite parts, and every link to it.

    A link whose other end requires at least one (a trigger must fire an
    agent) is not destroyed: the multiplicity constraint then refuses the
    whole operation, naming the instance that still needs it."""
    def change(s: spec_model.SystemSpec, effects: list[str]) -> None:
        inst = _find(s, kind, id_)
        doomed = {(kind, id_)}
        if kind == "agent":
            doomed |= {("subagent", sub.id) for sub in inst.obj.subagents}
        _detach(s, inst, effects)
        model = collect(s)
        for dk, did in sorted(doomed):
            for rel in PROFILE.relationships:
                if not rel.field or rel.shape is Shape.PART:
                    continue
                if dk not in specialisations(rel.target) and not (
                        rel.shape is Shape.RECORD
                        and dk in specialisations(rel.source)):
                    continue
                if rel.shape is Shape.RECORD:
                    _drop(s, rel, None, did, effects)
                    continue
                for holder in model.of(rel.source):
                    if (holder.kind, holder.id) in doomed:
                        continue
                    _drop(s, rel, holder.obj, did, effects,
                          source_id=holder.id, keep_mandatory=True)
    return _transact(spec, change)


def set_leader(spec: spec_model.SystemSpec, team: str, agent: str) -> Result:
    """Leadership is set, not drawn: the leader {subsets members}."""
    def change(s: spec_model.SystemSpec, effects: list[str]) -> None:
        t = _find(s, "team", team).obj
        if t.leader and t.leader != agent:
            effects.append(f"'{t.leader}' no longer leads '{team}'")
        t.leader = agent
    return _transact(spec, change)


# -- helpers -----------------------------------------------------------------

def _detach(s: spec_model.SystemSpec, inst: Any, effects: list[str]) -> None:
    """Remove a part from its current whole (composition: one whole)."""
    owner = inst.owner
    if owner is None:
        return
    for rel in PROFILE.relationships:
        if rel.kind is not RelKind.COMPOSITION or rel.shape is not Shape.PART:
            continue
        if owner.kind not in specialisations(rel.source) or \
                inst.kind not in specialisations(rel.target):
            continue
        obj: Any = owner.obj
        for step in rel.field.split("."):
            obj = getattr(obj, step)
        if isinstance(obj, list) and any(x is inst.obj for x in obj):
            obj[:] = [x for x in obj if x is not inst.obj]
            if rel.field == "members" and getattr(owner.obj, "leader", "") \
                    == inst.id:
                owner.obj.leader = ""
                effects.append(f"'{inst.id}' no longer leads '{owner.id}'")
            return


def _drop(s: spec_model.SystemSpec, rel: Relationship, holder: Any,
          target_id: str, effects: list[str], *, source_id: str = "",
          keep_mandatory: bool = False) -> None:
    """Destroy the link(s) `rel` holds to `target_id`."""
    if rel.shape is Shape.RECORD:
        records = getattr(s.organization, rel.field)
        kept = [r for r in records
                if target_id not in (r.source, r.target)
                or (source_id and r.source != source_id)]
        for r in records:
            if r not in kept:
                effects.append(f"{rel.stereotype} {r.source}→{r.target} "
                               "destroyed")
        records[:] = kept
        return
    holder, name = _leaf(holder, rel.field)
    current = getattr(holder, name)
    if rel.shape is Shape.REF:
        if current == target_id:
            if keep_mandatory and not rel.target_mult.startswith("0"):
                return      # multiplicities_hold will name it and refuse
            setattr(holder, name, type(holder).model_fields[name].default)
            effects.append(f"{source_id} no longer {rel.stereotype} "
                           f"'{target_id}'")
    elif rel.shape is Shape.REFS:
        if target_id in current:
            current[:] = [v for v in current if v != target_id]
            effects.append(f"{source_id} no longer {rel.stereotype} "
                           f"'{target_id}' [{rel.field}]")
    elif rel.shape is Shape.REF_OBJECTS:
        kept = [v for v in current if getattr(v, rel.key) != target_id]
        if len(kept) != len(current):
            current[:] = kept
            effects.append(f"{source_id} no longer {rel.stereotype} "
                           f"'{target_id}' [{rel.field}]")


def violations_of(result: Result) -> set[str]:
    return {v.constraint for v in result.violations}


__all__ = ["Result", "OperationError", "create", "update", "link", "unlink",
           "delete", "set_leader", "relationship", "violations_of"]



# -- one request format for every caller -------------------------------------

def apply(spec: spec_model.SystemSpec, request: dict[str, Any]) -> Result:
    """Run one operation from its JSON form — what the designer sends.

    `{"op": "link", "source": {"kind", "id"}, "target": {"kind", "id"},
    "relationship": "...", "attrs": {...}}`; `unlink` likewise without
    attrs; `{"op": "create", "kind", "id", "owner", "attrs"}`;
    `{"op": "update", "kind", "id", "attrs"}`; `{"op": "delete", "kind",
    "id"}`; `{"op": "set_leader", "team", "agent"}`."""
    kind = request.get("op")
    attrs = dict(request.get("attrs") or {})

    def end(name: str) -> tuple[str, str]:
        e = request.get(name) or {}
        if not e.get("kind") or not e.get("id"):
            raise OperationError(f"'{name}' needs a kind and an id")
        return e["kind"], e["id"]

    if kind == "link":
        return link(spec, end("source"), end("target"),
                    request.get("relationship", ""), **attrs)
    if kind == "unlink":
        return unlink(spec, end("source"), end("target"),
                      request.get("relationship", ""))
    if kind == "create":
        return create(spec, request["kind"], request.get("id", ""),
                      owner=request.get("owner", ""), **attrs)
    if kind == "update":
        return update(spec, request["kind"], request["id"], **attrs)
    if kind == "delete":
        return delete(spec, request["kind"], request["id"])
    if kind == "set_leader":
        return set_leader(spec, request["team"], request["agent"])
    raise OperationError(f"no operation '{kind}'")
