"""The transformation from the model to its physical representation (ADR-0104).

Model-driven: the model is transformed, not re-interpreted. This module states
the transformation once — for every stereotype, what it becomes in the IR, and
for every relationship, where the IR carries the link — and `trace()` checks a
compiled IR against it, both ways:

* **forward** — every model element and every link arrives in the IR where the
  mapping says it does, or is named as not built and why;
* **backward** — nothing in the IR's element collections comes from nowhere:
  each traces to a model element, or to a derivation the mapping names.

The IR is the platform-neutral physical model. Each target's generated
artifacts are a second, model-to-text step (`trace_targets`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Optional

from ..spec import model as spec_model
from . import PROFILE, RelKind, Shape, specialisations
from .instances import Instance, Instances, collect


class Mode(str, Enum):
    #: One IR element per model element, in an IR collection, by id.
    COLLECTION = "collection"
    #: A part, built inside its owner's image (a sub-agent in its agent).
    OWNED = "owned"
    #: Copied into the image of each agent that holds it; one held by nobody
    #: is not built, which the trace says rather than hides.
    PER_HOLDER = "per_holder"
    #: Not an IR element: resolved into computed fields (a role into the
    #: permissions and capabilities of each agent that plays it).
    RESOLVED = "resolved"
    #: The design's own: nothing is built from it (a note on the canvas).
    DESIGN_ONLY = "design_only"


@dataclass(frozen=True)
class ElementRule:
    kind: str
    mode: Mode
    #: For COLLECTION: the dotted path of the IR collection. For OWNED and
    #: PER_HOLDER: the field of the owner's/holder's image. For RESOLVED:
    #: what it is resolved into, in words.
    into: str
    doc: str = ""


E, M = ElementRule, Mode

ELEMENTS: list[ElementRule] = [
    E("system", M.COLLECTION, "", "the SystemIR itself"),
    E("organization", M.COLLECTION, "teams", "the root TeamIR"),
    E("team", M.COLLECTION, "teams"),
    E("agent", M.COLLECTION, "agents"),
    E("subagent", M.OWNED, "subagents", "a SubAgentIR in its agent's image"),
    E("person", M.COLLECTION, "people"),
    E("role", M.RESOLVED, "each player's role_ids, capabilities and "
      "permissions", "a role grants; the IR holds what was granted"),
    E("decision", M.RESOLVED, "the mandate of each unit and agent that holds "
      "it (MandateIR)"),
    E("separation", M.COLLECTION, "separations"),
    E("policy", M.COLLECTION, "policies"),
    E("capability", M.COLLECTION, "capabilities"),
    E("data_class", M.COLLECTION, "data_classes"),
    E("environment", M.COLLECTION, "environments"),
    E("endpoint", M.PER_HOLDER, "endpoints"),
    E("mission", M.COLLECTION, "missions"),
    E("workflow", M.COLLECTION, "workflows"),
    E("trigger", M.COLLECTION, "triggers"),
    E("channel", M.COLLECTION, "channels"),
    E("knowledge", M.COLLECTION, "knowledge"),
    E("memory_namespace", M.COLLECTION, "memory.namespaces"),
    E("guardrail", M.COLLECTION, "guardrails"),
    E("output_contract", M.PER_HOLDER, "output_contract"),
    E("evaluation", M.COLLECTION, "lifecycle.evaluations"),
    E("skill", M.PER_HOLDER, "skills"),
    E("plugin", M.RESOLVED, "each holder's skills and tools (what it "
      "provides) and its id in AgentIR.plugins", "a package, unpacked"),
    E("tool", M.PER_HOLDER, "tools"),
    E("action", M.OWNED, "graph.nodes", "a step of its workflow"),
    E("control_flow", M.OWNED, "graph.edges", "an edge of its workflow"),
    E("artifact_store", M.COLLECTION, "artifact_stores",
      "a workspace, carried whole (ADR-0036)"),
    E("note", M.DESIGN_ONLY, "", "a comment on the canvas"),
]

#: IR collections that hold elements derived from the model rather than
#: copied from it, and the derivation. Backward trace accepts them.
DERIVED = {
    "placements": "one per (placement unit, environment) with agents "
                  "deployed there (ADR-0069, ADR-0082)",
    "identities": "one per agent: the principal it runs as",
    "resources": "the platform resources the design needs",
    "placement_rules": "the placement boundary each unit falls in",
}


@dataclass(frozen=True)
class LinkRule:
    """Where the IR carries one relationship, when not under its own name on
    the owner's image."""

    source: str
    field: str
    #: The field of the owner's image that carries it.
    ir_field: str
    #: The ids in that field are a superset (roles add capabilities, the
    #: reporting line adds delegates) rather than equal.
    superset: bool = True
    doc: str = ""


L = LinkRule

LINKS: list[LinkRule] = [
    L("team", "teams", "child_team_ids"),
    L("team", "members", "member_ids"),
    L("team", "leader", "leader_agent_id"),
    L("agent", "successor", "successor_agent_id"),
    L("agent", "roles", "role_ids"),
    L("agent", "humans", "humans"),
    L("trigger", "agent", "agent_id"),
    L("agent", "data_dependencies", "data_dependencies"),
    L("agent", "peers", "delegates_to",
      doc="a lateral link is someone the agent may delegate to"),
]

#: Relationships the IR resolves rather than carries, and how the trace
#: checks the resolution instead.
RESOLVED_LINKS: dict[tuple[str, str], str] = {
    ("role", "capabilities"): "every capability a role grants is among each "
                              "player's capabilities, less what it withholds",
    ("separation", "decisions"): "carried on the separation; enforced over "
                                 "each agent's resolved mandate",
    ("team", "roles"): "a unit's roles become its permissions, inherited "
                       "by its sub-teams, and their capabilities reach every "
                       "member (checked with each agent's effective set)",
    ("plugin", "provides_skills"): "every skill a plugin provides is among "
                                   "each holder's skills",
    ("plugin", "provides_tools"): "every tool a plugin provides is among each "
                                  "holder's tools",
    ("person", "channel"): "not carried: PersonIR holds authority only "
                           "(ADR-0079), so where a person is reached stays "
                           "in the design",
    ("mission", "roles"): "a mission's roles become the permissions it "
                          "grants each member, intersected with what they "
                          "already hold (MissionIR.granted_permissions)",
    ("plugin", "requires_capabilities"): "every capability a plugin requires "
                                         "is held by each holder",
}


def _rule(kind: str) -> Optional[ElementRule]:
    return next((r for r in ELEMENTS if r.kind == kind), None)


def _link_rule(kind: str, field_: str) -> Optional[LinkRule]:
    for r in LINKS:
        if field_ == r.field and kind in specialisations(r.source):
            return r
    return None


def _ids(value: Any) -> set[str]:
    """The ids an IR value names, whatever form the IR keeps it in."""
    if value is None or value == "":
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, Enum):
        return {value.value}
    if isinstance(value, (list, tuple, set)):
        out: set[str] = set()
        for v in value:
            out |= _ids(v)
        return out
    for key in ("id", "environment", "person", "role", "data_class",
                "target"):
        v = getattr(value, key, None)
        if isinstance(v, str) and v:
            return {v}
    return set()


def _walk(obj: Any, path: str) -> Any:
    for step in path.split("."):
        obj = getattr(obj, step, None)
        if obj is None:
            return None
    return obj


def image_class(kind: str) -> Optional[type]:
    """The IR class a model element of this kind becomes, from the mapping
    and the IR's own annotations — so the check below is against the real
    IR, not a list kept beside it."""
    import typing

    from ..compiler import ir as ir_model

    rule = _rule(kind)
    if rule is None or rule.mode in (Mode.RESOLVED, Mode.DESIGN_ONLY):
        return None
    if rule.kind == "system":
        return ir_model.SystemIR
    if rule.mode is Mode.COLLECTION:
        owner: Any = ir_model.SystemIR
        path = rule.into
    elif rule.mode is Mode.PER_HOLDER:
        owner, path = ir_model.AgentIR, rule.into
    else:   # OWNED
        parent = {"subagent": "agent", "action": "workflow",
                  "control_flow": "workflow"}[kind]
        owner, path = image_class(parent), rule.into
    for step in path.split("."):
        ann = owner.model_fields[step].annotation
        args = typing.get_args(ann)
        owner = next((a for a in args if isinstance(a, type)
                      and hasattr(a, "model_fields")), ann) if args else ann
    return owner if hasattr(owner, "model_fields") else None


def carried_by(kind: str, field_: str) -> Optional[str]:
    """The IR field that carries a relationship of this kind, or None when
    it is resolved rather than carried."""
    if any(field_ == f and kind in specialisations(src)
           for src, f in RESOLVED_LINKS):
        return None
    link = _link_rule(kind, field_)
    return link.ir_field if link else field_


# -- the trace ---------------------------------------------------------------

@dataclass
class Trace:
    #: model element → where it is in the IR.
    elements: dict[str, str] = field(default_factory=dict)
    #: (model element, relationship, target) → the IR field that carries it.
    links: list[tuple[str, str, str, str]] = field(default_factory=list)
    #: Model elements deliberately not built, and why.
    not_built: dict[str, str] = field(default_factory=dict)
    #: What the mapping says should be there and is not, both directions.
    gaps: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.gaps


def _images(model: Instances, ir: Any, trace: Trace) -> dict[str, Any]:
    """The IR image of every model instance the mapping builds."""
    images: dict[str, Any] = {}
    agents_ir = {a.id: a for a in ir.agents}
    for inst in model:
        rule = _rule(inst.kind)
        if rule is None:
            trace.gaps.append(f"{inst.ref}: no transformation rule for "
                              f"{inst.kind}")
            continue
        if rule.mode is Mode.DESIGN_ONLY:
            trace.not_built[inst.ref] = rule.doc
            continue
        if rule.mode is Mode.RESOLVED:
            trace.not_built[inst.ref] = f"resolved into {rule.into}"
            continue
        if rule.kind == "system":
            images[inst.ref] = ir
            trace.elements[inst.ref] = "SystemIR"
            continue
        if rule.mode is Mode.COLLECTION:
            found = next((x for x in (_walk(ir, rule.into) or [])
                          if getattr(x, "id", None) == inst.id), None)
            if found is None:
                trace.gaps.append(f"{inst.ref}: not built — expected in "
                                  f"{rule.into}")
            else:
                images[inst.ref] = found
                trace.elements[inst.ref] = f"{rule.into}[{inst.id}]"
            continue
        if rule.mode is Mode.OWNED:
            owner = images.get(inst.owner.ref) if inst.owner else None
            items = _walk(owner, rule.into) if owner is not None else None
            if inst.kind == "control_flow":
                index = int(inst.id.rsplit("#", 1)[1])
                found = items[index] if items and index < len(items) else None
            else:
                found = next((x for x in (items or [])
                              if getattr(x, "id", None) == inst.id), None)
            if found is None:
                trace.gaps.append(f"{inst.ref}: not built — expected in "
                                  f"{inst.owner.ref if inst.owner else '?'}"
                                  f".{rule.into}")
            else:
                images[inst.ref] = found
                trace.elements[inst.ref] = (f"{trace.elements.get(inst.owner.ref, '?')}"
                                            f".{rule.into}[{inst.id}]")
            continue
        # PER_HOLDER: the first holder's copy is its image.
        holders = [a for a in ir.agents
                   if inst.id in _ids(getattr(a, rule.into, None))]
        if not holders:
            trace.not_built[inst.ref] = "held by no agent, so nothing runs it"
            continue
        value = getattr(holders[0], rule.into)
        items = value if isinstance(value, list) else [value]
        images[inst.ref] = next((x for x in items
                                 if inst.id in _ids(x)), holders[0])
        trace.elements[inst.ref] = (f"agents[{', '.join(a.id for a in holders)}]"
                                    f".{rule.into}[{inst.id}]")
    return images


def _forward_links(model: Instances, ir: Any, images: dict[str, Any],
                   trace: Trace) -> None:
    from .constraints import _values

    for rel in PROFILE.relationships:
        if not rel.field or rel.kind is RelKind.GENERALIZATION:
            continue
        if rel.shape is Shape.RECORD:
            carried = {(r.source, r.target) for r in getattr(ir, "flows" if
                       rel.field == "interaction_flows" else rel.field)}
            for rec in getattr(model.spec.organization, rel.field):
                if (rec.source, rec.target) in carried:
                    trace.links.append((f"{rel.source}:{rec.source}",
                                        rel.stereotype,
                                        f"{rel.target}:{rec.target}",
                                        "flows" if rel.field ==
                                        "interaction_flows" else rel.field))
                else:
                    trace.gaps.append(f"{rel.stereotype} {rec.source}→"
                                      f"{rec.target}: not carried into the IR")
            continue
        if (rel.source, rel.field) in RESOLVED_LINKS:
            continue
        if rel.stereotype == "owns":
            # The IR flattens the Model's and the Organization's ownership
            # onto SystemIR's collections; where each owned element lands is
            # the element trace's to check, not a link on the owner's image.
            continue
        for inst in model.of(rel.source):
            image = images.get(inst.ref)
            if image is None:
                continue
            if rel.shape is Shape.PART:
                obj: Any = inst.obj
                for step in rel.field.split("."):
                    obj = getattr(obj, step)
                wanted = {getattr(x, "id", "") for x in obj} - {""}
            else:
                wanted = set(_values(rel, inst.obj)) - {"*"}
            if not wanted:
                continue
            link = _link_rule(inst.kind, rel.field)
            ir_field = link.ir_field if link else rel.field
            if ir_field in ("graph.nodes", "graph.edges") or \
                    rel.target == "control_flow":
                continue           # owned parts, traced as elements
            have = _ids(_walk(image, ir_field))
            missing = wanted - have
            if missing:
                trace.gaps.append(
                    f"{inst.ref}: {rel.stereotype} [{rel.field}] → "
                    f"{sorted(missing)} not in its image's {ir_field}")
            for target in sorted(wanted & have):
                trace.links.append((inst.ref, rel.stereotype,
                                    f"{rel.target}:{target}", ir_field))


def _resolved_links(model: Instances, ir: Any, trace: Trace) -> None:
    """Role grants — the agent's own and its teams' — arrive as
    capabilities on each player: the IR holds the model's effective set."""
    for agent in ir.agents:
        effective = model.spec.effective_capabilities(agent.id)
        have = _ids(agent.capabilities)
        missing = effective - have
        if missing:
            trace.gaps.append(
                f"agent:{agent.id}: holds {sorted(missing)} in the model "
                "(directly or through a role), not among its capabilities")
        for cap in sorted(effective & have):
            trace.links.append((f"agent:{agent.id}", "holds",
                                f"capability:{cap}", "capabilities"))


def _plugins_resolve(model: Instances, ir: Any, trace: Trace) -> None:
    """What a plugin provides arrives on each agent that loads it; what it
    requires is held there."""
    plugins = {p.id: p for p in model.spec.plugins}
    for agent in ir.agents:
        for pid in _ids(agent.plugins):
            plugin = plugins.get(pid)
            if plugin is None:
                continue
            for field_, have, verb in (
                    ("provides_skills", _ids(agent.skills), "provides"),
                    ("provides_tools", _ids(agent.tools), "provides"),
                    ("requires_capabilities", _ids(agent.capabilities),
                     "requires")):
                wanted = set(getattr(plugin, field_))
                missing = wanted - have
                if missing:
                    trace.gaps.append(
                        f"agent:{agent.id}: plugin {pid} {verb} "
                        f"{sorted(missing)}, not in its {field_.split('_')[-1]}")
                for target in sorted(wanted & have):
                    trace.links.append((f"plugin:{pid}", verb,
                                        f"agent:{agent.id}",
                                        field_.split("_")[-1]))


def _backward(model: Instances, ir: Any, trace: Trace) -> None:
    """Every element in an IR collection traces to the model."""
    for rule in ELEMENTS:
        if rule.mode is not Mode.COLLECTION or not rule.into:
            continue
        known = model.ids(rule.kind) | (model.ids("organization")
                                        if rule.kind == "team" else set())
        for item in _walk(ir, rule.into) or []:
            ident = getattr(item, "id", None)
            if ident and ident not in known and not (
                    rule.kind == "organization"):
                trace.gaps.append(f"IR {rule.into}[{ident}] has no model "
                                  f"source")


def trace(spec: spec_model.SystemSpec, ir: Any) -> Trace:
    """Check a compiled IR against the transformation, both ways."""
    model = collect(spec)
    out = Trace()
    images = _images(model, ir, out)
    _forward_links(model, ir, images, out)
    _resolved_links(model, ir, out)
    _plugins_resolve(model, ir, out)
    _backward(model, ir, out)
    # Two rules share `teams`; a team found by one is not a gap for the other.
    out.gaps = sorted(set(out.gaps))
    return out


# -- model to text: the generated artifacts ----------------------------------

def trace_targets(ir: Any, files: Iterable[Any], target: str) -> list[str]:
    """Every agent and every workflow in the IR reaches the target's
    artifacts — by name, in a path or in content. Returns the gaps."""
    text = "\n".join(f"{f.path}\n{f.content}" for f in files)
    gaps = []
    for agent in ir.agents:
        if agent.id not in text:
            gaps.append(f"{target}: agent {agent.id} is in no artifact")
    for wf in ir.workflows:
        if wf.id not in text:
            gaps.append(f"{target}: workflow {wf.id} is in no artifact")
    return gaps


# -- the specification, as a document ----------------------------------------

def describe() -> str:
    names = {s.kind: s.name for s in PROFILE.stereotypes}
    lines = ["# The transformation, specified", "",
             "Generated by `orgagents metamodel transformation` from "
             "`orgagents/metamodel/transformation.py` (ADR-0104). What every "
             "model element becomes in the IR, and where every link is "
             "carried. `orgagents metamodel trace <spec>` checks a compile "
             "against it, both ways.", "",
             "## Elements", "",
             "| Stereotype | Becomes | In the IR |", "|---|---|---|"]
    for r in ELEMENTS:
        lines.append(f"| «{names.get(r.kind, r.kind)}» | {r.mode.value} | "
                     f"{r.into or '—'}{' — ' + r.doc if r.doc else ''} |")
    lines += ["", "## Derived in the IR", "", "| IR collection | From |",
              "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in DERIVED.items()]
    lines += ["", "## Links carried under another name", "",
              "| Relationship | IR field |", "|---|---|"]
    lines += [f"| {r.source}.{r.field} | {r.ir_field} |" for r in LINKS]
    lines += ["", "## Links resolved rather than carried", "",
              "| Relationship | How it is checked |", "|---|---|"]
    lines += [f"| {s}.{f} | {v} |" for (s, f), v in RESOLVED_LINKS.items()]
    lines += ["", "Every other relationship is carried under its own field "
              "name on its owner's image.", ""]
    return "\n".join(lines)
