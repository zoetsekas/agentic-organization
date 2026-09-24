"""Instances of the profile's stereotypes in one spec (ADR-0102).

The metamodel says what kinds exist and how they relate; this module finds the
objects of each kind in a `SystemSpec` and the object that owns each one, so
constraints and operations can be written against the model — "every Agent",
"this SubAgent's owner" — instead of against where the YAML happens to keep it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from ..spec import model as spec_model
from . import PROFILE, SYSTEM_OWNED, Profile, specialisations


@dataclass
class Instance:
    kind: str
    id: str
    obj: Any
    #: The instance that owns this one by composition, if any.
    owner: Optional["Instance"] = None

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.id}"


@dataclass
class Instances:
    """Every instance in one spec, by kind and id."""

    spec: spec_model.SystemSpec
    by_kind: dict[str, list[Instance]] = field(default_factory=dict)

    def add(self, inst: Instance) -> Instance:
        self.by_kind.setdefault(inst.kind, []).append(inst)
        return inst

    def of(self, kind: str, profile: Profile = PROFILE) -> list[Instance]:
        """Instances of a kind, including those of its specialisations and
        realisers when it is abstract."""
        out: list[Instance] = []
        for concrete in specialisations(kind, profile):
            out.extend(self.by_kind.get(concrete, []))
        return out

    def ids(self, kind: str) -> set[str]:
        return {i.id for i in self.of(kind)}

    def get(self, kind: str, id_: str) -> Optional[Instance]:
        return next((i for i in self.of(kind) if i.id == id_), None)

    def __iter__(self) -> Iterator[Instance]:
        for items in self.by_kind.values():
            yield from items


def _collection(spec: spec_model.SystemSpec, path: str) -> list[Any]:
    """A collection by its dotted path from the System or the Organization."""
    for root in (spec.organization, spec):
        obj: Any = root
        ok = True
        for step in path.split("."):
            if not hasattr(type(obj), "model_fields") or \
                    step not in type(obj).model_fields:
                ok = False
                break
            obj = getattr(obj, step)
        if ok:
            return list(obj) if isinstance(obj, list) else [obj]
    return []


def collect(spec: spec_model.SystemSpec,
            profile: Profile = PROFILE) -> Instances:
    """Every instance of every stereotype in the spec, with its owner."""
    out = Instances(spec)
    system = out.add(Instance("system", spec.metadata.name, spec))
    org = out.add(Instance("organization", spec.organization.id,
                           spec.organization, owner=system))

    def walk(team: Any, inst: Instance) -> None:
        for agent in team.members:
            a = out.add(Instance("agent", agent.id, agent, owner=inst))
            for sub in agent.subagents:
                out.add(Instance("subagent", sub.id, sub, owner=a))
        for child in team.teams:
            walk(child, out.add(Instance("team", child.id, child, owner=inst)))

    walk(spec.organization, org)

    for st in profile.stereotypes:
        if not st.collection or st.kind in ("organization", "team"):
            continue
        owner = system if st.collection in SYSTEM_OWNED else org
        for obj in _collection(spec, st.collection):
            out.add(Instance(st.kind, getattr(obj, "id", ""), obj, owner=owner))

    for wf in out.by_kind.get("workflow", []):
        for node in wf.obj.graph.nodes:
            out.add(Instance("action", node.id, node, owner=wf))
        for n, edge in enumerate(wf.obj.graph.edges):
            out.add(Instance("control_flow", f"{wf.id}#{n}", edge, owner=wf))
    return out
