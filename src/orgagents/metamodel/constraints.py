"""The model's constraints, stated once and checked on any spec (ADR-0102).

Each constraint has a context (the stereotype it is about), its rule in OCL —
the notation UML uses for exactly this — and an executable check. Two kinds:

* **Generic** ones are derived from the profile, so no relationship can be
  added without them applying to it: every reference resolves to an instance
  of the right kind, every multiplicity holds, and ids are unique per kind.
* **Domain** ones are the rules the platform's ADRs state in prose: a leader is
  a member, a sub-agent never exceeds its parent, an overseer does not sit
  inside what it oversees.

A constraint reports the instance that breaks it and why. `check(spec)`
returns every violation; an empty list is a valid model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..spec import model as spec_model
from . import PROFILE, Relationship, Shape
from .instances import Instance, Instances, collect


@dataclass(frozen=True)
class Violation:
    constraint: str
    element: str        # kind:id
    message: str

    def __str__(self) -> str:
        return f"[{self.constraint}] {self.element}: {self.message}"


INTEGRITY = "integrity"
COMPLETENESS = "completeness"


@dataclass(frozen=True)
class Constraint:
    name: str
    context: str        # stereotype kind
    ocl: str
    doc: str
    check: Callable[[Instances], Iterable[Violation]]
    #: `integrity`: never broken by any edit — a dangling reference, a
    #: duplicate, a sub-agent wider than its parent. `completeness`: what a
    #: finished model has and a draft may still lack — a required end not
    #: yet filled in. Drafts may be incomplete; publishing may not (ADR-0103).
    severity: str = INTEGRITY


def _lower(mult: str) -> int:
    head = mult.split("..")[0]
    return 0 if head == "*" else int(head)


def _upper(mult: str) -> float:
    tail = mult.split("..")[-1]
    return float("inf") if tail == "*" else int(tail)


def _values(rel: Relationship, obj: Any) -> list[str]:
    """The ids a relationship's field holds on one owning object."""
    value: Any = obj
    for step in rel.field.split("."):
        value = getattr(value, step, None)
    if value is None or value == "":
        return []
    if rel.shape is Shape.REF:
        return [value]
    if rel.shape is Shape.REF_OBJECTS:
        # An object whose key is empty is not a link: a human counterpart
        # written inline rather than as a reference to a Person.
        return [k for k in (getattr(v, rel.key, "") for v in value) if k]
    return list(value)


# -- generic, from the profile ---------------------------------------------

def _references_resolve(model: Instances) -> Iterable[Violation]:
    """Every reference names an instance of the kind the relationship says.

    An abstract end (Principal, Resource, Callable) accepts an instance of
    any kind that realises it, so it is resolved against their union."""
    for rel in PROFILE.relationships:
        if rel.shape is Shape.PART or not rel.field:
            continue
        if rel.source == "control_flow":
            continue        # scoped to its workflow; see _control_flow_ends
        targets = model.ids(rel.target)
        if rel.shape is Shape.RECORD:
            for rec in getattr(model.spec.organization, rel.field):
                for end, kind in (("source", rel.source),
                                  ("target", rel.target)):
                    ref = getattr(rec, end)
                    if ref not in model.ids(kind):
                        yield Violation(
                            "references_resolve", f"{rel.field}",
                            f"{rel.stereotype} {end} '{ref}' is not a "
                            f"{kind}")
            continue
        for inst in model.of(rel.source):
            for ref in _values(rel, inst.obj):
                if ref == "*" and "'*'" in rel.constraint:
                    continue        # a wildcard the relationship allows
                if "__" in ref and "'c__op'" in rel.constraint and \
                        ref.split("__", 1)[0] in targets:
                    continue        # an operation of a capability
                if ref not in targets:
                    yield Violation(
                        "references_resolve", inst.ref,
                        f"{rel.stereotype} [{rel.field}] names '{ref}', "
                        f"which is not a {rel.target}")


def _multiplicities_hold(model: Instances) -> Iterable[Violation]:
    """Each end holds as many as its multiplicity allows."""
    for rel in PROFILE.relationships:
        if rel.shape in (Shape.PART, Shape.RECORD) or not rel.field:
            continue
        lo, hi = _lower(rel.target_mult), _upper(rel.target_mult)
        if lo == 0 and hi == float("inf"):
            continue
        for inst in model.of(rel.source):
            n = len(_values(rel, inst.obj))
            if n < lo or n > hi:
                yield Violation(
                    "multiplicities_hold", inst.ref,
                    f"{rel.stereotype} [{rel.field}] holds {n}; "
                    f"{rel.target_mult} required")


def _ids_are_unique(model: Instances) -> Iterable[Violation]:
    """No two instances of one kind share an id — and an agent is therefore in
    exactly one team."""
    for kind, items in model.by_kind.items():
        if kind in ("control_flow",):
            continue
        seen: dict[str, Instance] = {}
        for inst in items:
            scope = inst.owner.ref if kind == "action" and inst.owner else ""
            key = f"{scope}/{inst.id}"
            if key in seen:
                yield Violation("ids_are_unique", inst.ref,
                                f"a second {kind} with id '{inst.id}'")
            seen[key] = inst


# -- domain, from the ADRs -------------------------------------------------

def _leader_is_member(model: Instances) -> Iterable[Violation]:
    for inst in model.of("team"):
        team = inst.obj
        if team.leader and team.leader not in {m.id for m in team.members}:
            yield Violation("leader_is_member", inst.ref,
                            f"leader '{team.leader}' is not a member")


def _effective_capabilities(model: Instances, agent: Any) -> set[str]:
    held = set(agent.capabilities)
    roles = {r.id: r for r in model.spec.roles}
    for assignment in agent.roles:
        role = roles.get(assignment.role)
        if role:
            held |= set(role.capabilities) - set(assignment.withhold)
    return held


def _subagent_within_parent(model: Instances) -> Iterable[Violation]:
    for inst in model.of("subagent"):
        parent = inst.owner.obj
        extra = set(inst.obj.capabilities) - _effective_capabilities(model,
                                                                      parent)
        if extra:
            yield Violation(
                "subagent_within_parent", inst.ref,
                f"holds {sorted(extra)}, which its parent '{parent.id}' does "
                "not")
        envs = set(inst.obj.environments) - {e.environment
                                              for e in parent.environments}
        if envs:
            yield Violation(
                "subagent_within_parent", inst.ref,
                f"runs in {sorted(envs)}, which its parent '{parent.id}' "
                "does not")


def _subtree(team: Any) -> set[str]:
    return {t.id for t in team.walk()}


def _unit_links_respect_containment(model: Instances) -> Iterable[Violation]:
    teams = {i.id: i.obj for i in model.of("team")}
    for link in model.spec.unit_links:
        kind = spec_model.UnitLinkKind(link.kind).value
        ref = f"unit_link:{link.source}->{link.target}"
        if link.source == link.target:
            yield Violation("unit_links_respect_containment", ref,
                            "a unit linked to itself")
            continue
        src, tgt = teams.get(link.source), teams.get(link.target)
        if not (src and tgt):
            continue
        if kind == "oversees" and link.source in _subtree(tgt):
            yield Violation("unit_links_respect_containment", ref,
                            "an overseer inside what it oversees")
        if kind == "escalates_to" and link.target in _subtree(src):
            yield Violation("unit_links_respect_containment", ref,
                            "an escalation that lands in the escalating "
                            "unit's own subtree")


def _flows_join_two_agents(model: Instances) -> Iterable[Violation]:
    for flow in model.spec.interaction_flows:
        if flow.source == flow.target:
            yield Violation("flows_join_two_agents",
                            f"flow:{flow.source}->{flow.target}",
                            "an agent in a flow with itself")


def _no_self_successor(model: Instances) -> Iterable[Violation]:
    for inst in model.of("agent"):
        if inst.obj.successor and inst.obj.successor == inst.id:
            yield Violation("no_self_successor", inst.ref,
                            "its own successor")


def _root_places(model: Instances) -> Iterable[Violation]:
    if not model.spec.organization.placement:
        yield Violation("root_places", f"organization:{model.spec.organization.id}",
                        "the organisation must declare placement (ADR-0069)")


def _separations_hold(model: Instances) -> Iterable[Violation]:
    for rule in model.spec.separations:
        apart = set(rule.decisions)
        for inst in model.of("agent"):
            mandate = inst.obj.mandate
            held = set(mandate.decisions) if mandate else set()
            if len(held & apart) > 1:
                yield Violation(
                    "separations_hold", inst.ref,
                    f"holds {sorted(held & apart)}, which '{rule.id}' keeps "
                    "apart")


_ACTION_TARGET = {"tool": "tool", "agent": "agent", "workflow": "workflow"}


def _actions_name_what_they_call(model: Instances) -> Iterable[Violation]:
    for inst in model.of("action"):
        kind = spec_model.ActivityNodeKind(inst.obj.kind).value
        need = _ACTION_TARGET.get(kind)
        if need and not getattr(inst.obj, need):
            yield Violation(
                "actions_name_what_they_call",
                f"action:{inst.owner.id}.{inst.id}",
                f"a {kind} step that names no {need}")


def _control_flow_ends(model: Instances) -> Iterable[Violation]:
    for wf in model.of("workflow"):
        steps = {n.id for n in wf.obj.graph.nodes}
        graph = wf.obj.graph
        if steps and graph.entry and graph.entry not in steps:
            yield Violation("control_flow_ends", wf.ref,
                            f"entry '{graph.entry}' is not a step")
        for edge in graph.edges:
            for end in (edge.source, edge.target):
                if end != "END" and end not in steps:
                    yield Violation(
                        "control_flow_ends", wf.ref,
                        f"an edge {edge.source}→{edge.target} names "
                        f"'{end}', which is not a step")


CONSTRAINTS: list[Constraint] = [
    Constraint("references_resolve", "*",
               "self.<end>->forAll(r | <target kind>.allInstances()"
               "->exists(t | t.id = r))",
               "Every reference names an existing instance of the kind its "
               "relationship declares. Derived from the profile.",
               _references_resolve),
    Constraint("multiplicities_hold", "*",
               "self.<end>->size() >= lower and <= upper",
               "Every end holds as many as its multiplicity allows. Derived "
               "from the profile. A lower bound not yet met is incompleteness,"
               " which a draft may have.", _multiplicities_hold,
               COMPLETENESS),
    Constraint("ids_are_unique", "*",
               "<kind>.allInstances()->isUnique(id)",
               "No two instances of a kind share an id; an agent is therefore "
               "in exactly one team.", _ids_are_unique),
    Constraint("leader_is_member", "team",
               "self.leader <> '' implies self.members->exists(m | "
               "m.id = self.leader)",
               "A team's leader {subsets members} (ADR-0006).",
               _leader_is_member),
    Constraint("subagent_within_parent", "subagent",
               "self.capabilities->forAll(c | owner.effectiveCapabilities"
               "->includes(c)) and self.environments->forAll(e | "
               "owner.environments.environment->includes(e))",
               "A sub-agent is narrower than the agent that owns it "
               "(ADR-0027, ADR-0082).", _subagent_within_parent),
    Constraint("unit_links_respect_containment", "unit_link",
               "self.source <> self.target and (kind = oversees implies "
               "not target.subtree->includes(source)) and (kind = "
               "escalates_to implies not source.subtree->includes(target))",
               "An association between units never contradicts containment "
               "(ADR-0081).", _unit_links_respect_containment),
    Constraint("flows_join_two_agents", "interaction_flow",
               "self.source <> self.target",
               "A flow is between two agents (ADR-0024).",
               _flows_join_two_agents),
    Constraint("no_self_successor", "agent",
               "self.successor <> self.id",
               "An agent cannot stand in for itself (ADR-0094).",
               _no_self_successor),
    Constraint("root_places", "organization", "self.placement = true",
               "The organisation is always a placement boundary (ADR-0069).",
               _root_places),
    Constraint("separations_hold", "separation",
               "Agent.allInstances()->forAll(a | (a.mandate.decisions->"
               "intersection(self.decisions))->size() <= 1)",
               "No agent holds two decisions a separation keeps apart "
               "(ADR-0070).", _separations_hold),
    Constraint("actions_name_what_they_call", "action",
               "(kind = tool implies tool <> '') and (kind = agent implies "
               "agent <> '') and (kind = workflow implies workflow <> '')",
               "A step that calls something says what (ADR-0102).",
               _actions_name_what_they_call, COMPLETENESS),
    Constraint("control_flow_ends", "workflow",
               "self.graph.edges->forAll(e | steps->includes(e.source) and "
               "(e.target = 'END' or steps->includes(e.target)))",
               "Edges join steps of their own workflow (ADR-0096).",
               _control_flow_ends),
]


SEVERITY = {c.name: c.severity for c in CONSTRAINTS}


def check(spec: spec_model.SystemSpec) -> list[Violation]:
    """Every violation of every constraint; empty means a valid, complete
    model."""
    model = collect(spec)
    return [v for c in CONSTRAINTS for v in c.check(model)]


def integrity(violations: Iterable[Violation]) -> list[Violation]:
    return [v for v in violations if SEVERITY.get(v.constraint,
                                                  INTEGRITY) == INTEGRITY]
