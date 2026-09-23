"""The designer, specified as gestures on the model (ADR-0103).

The designer is a view of the model. Every gesture a person makes on the
canvas — dropping a component, dragging one box onto another, drawing an edge,
pressing Delete, editing a property — is exactly one operation of
`orgagents.metamodel.operations`, sent as one request. The designer adds no
rules of its own: what is accepted, what else changes, and what is refused
are the model's answers, which the canvas shows.

The catalogue is derived from the profile, so a relationship added to the
metamodel is a gesture without a line of designer code. `catalogue()` renders
it as the designer's specification (`docs/designer/gestures.md`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from ..metamodel import (NON_PALETTE, PROFILE, Draw, RelKind, Shape,
                         _concrete, specialisations)


@dataclass(frozen=True)
class Gesture:
    id: str
    #: What the person does, in the canvas's own words.
    does: str
    #: The operation request it sends, with `<source>`/`<target>`/`<new>`
    #: standing for the ids the gesture supplies.
    request: dict[str, Any]
    #: What the canvas shows when the model accepts it.
    shows: str
    #: What the canvas shows when the model refuses it.
    refused: str = ("the change is not made; the status line names the "
                    "constraint and the element that breaks it")
    source: str = ""
    target: str = ""
    kinds: tuple[str, ...] = field(default_factory=tuple)


def _palette(kind: str) -> bool:
    return kind not in NON_PALETTE and kind != "note"


def _name(kind: str) -> str:
    st = PROFILE.stereotype(kind)
    return st.name if st else kind


#: How each UML kind is drawn — the presentation half of the specification.
PRESENTATION = {
    RelKind.COMPOSITION: "a solid edge with a filled diamond at the whole",
    RelKind.ASSOCIATION: "a solid edge, arrow at the navigable end, labelled "
                         "with its stereotype",
    RelKind.USAGE: "a dashed edge labelled «use»",
    RelKind.REALIZATION: "a dashed edge with a hollow triangle",
    RelKind.DEPLOYMENT: "the worker drawn inside the environment's box",
    RelKind.DEPENDENCY: "a dashed edge with an open arrow",
}
DRAWN_AS = {
    Draw.EDGE: "an edge",
    Draw.NEST: "nesting: the target box inside the source box",
    Draw.INLINE: "a chip inside the holder's box while it has one holder, an "
                 "edge once shared",
    Draw.NONE: "not drawn; shown in Properties",
}


def gestures() -> list[Gesture]:
    out: list[Gesture] = []
    concrete = [r for r in _concrete(PROFILE)
                if _palette(r.source) and _palette(r.target)]

    # -- creating: from the palette ------------------------------------------
    for st in PROFILE.stereotypes:
        if not _palette(st.kind):
            continue
        wholes = sorted({r.source for r in concrete
                         if r.shape is Shape.PART and r.target == st.kind})
        if wholes:
            out.append(Gesture(
                f"create:{st.kind}",
                f"drop «{st.name}» from the palette onto a "
                f"{' or '.join('«%s»' % _name(w) for w in wholes)}",
                {"op": "create", "kind": st.kind, "id": "<new>",
                 "owner": "<target>"},
                f"a new «{st.name}» box inside the whole it was dropped on",
                kinds=(st.kind,), target="|".join(wholes)))
        else:
            out.append(Gesture(
                f"create:{st.kind}",
                f"drop «{st.name}» from the palette onto the canvas",
                {"op": "create", "kind": st.kind, "id": "<new>"},
                f"a new «{st.name}» box where it was dropped",
                kinds=(st.kind,)))

    # -- composing: drag an existing part onto a whole ------------------------
    for r in concrete:
        if r.shape is Shape.PART:
            out.append(Gesture(
                f"compose:{r.source}-{r.target}",
                f"drag an existing «{_name(r.target)}» onto a "
                f"«{_name(r.source)}»",
                {"op": "link", "source": {"kind": r.source, "id": "<target>"},
                 "target": {"kind": r.target, "id": "<source>"},
                 "relationship": r.stereotype},
                f"the «{_name(r.target)}» moves: it is a part of the new "
                f"«{_name(r.source)}» and no longer of its old one — a part "
                "has one whole. Its associations go with it",
                source=r.target, target=r.source))

    # -- deploying: drag into and out of an environment box ------------------
    for r in concrete:
        if r.kind is RelKind.DEPLOYMENT:
            out.append(Gesture(
                f"deploy:{r.source}",
                f"drag a «{_name(r.source)}» into an «{_name(r.target)}» box",
                {"op": "link", "source": {"kind": r.source, "id": "<source>"},
                 "target": {"kind": r.target, "id": "<target>"},
                 "relationship": r.stereotype},
                "the box is drawn inside the environment; the deployment's "
                "own settings are edited on the nested box",
                source=r.source, target=r.target))
            out.append(Gesture(
                f"undeploy:{r.source}",
                f"drag a «{_name(r.source)}» out of an «{_name(r.target)}» "
                "box",
                {"op": "unlink", "source": {"kind": r.source, "id": "<source>"},
                 "target": {"kind": r.target, "id": "<target>"},
                 "relationship": r.stereotype},
                "the box is drawn outside; the status line says it is no "
                "longer deployed there, and Undo restores it",
                source=r.source, target=r.target))

    # -- drawing: an edge between two boxes ----------------------------------
    pairs: dict[tuple[str, str], list] = {}
    for r in concrete:
        if r.shape is Shape.PART or r.kind is RelKind.DEPLOYMENT or \
                not r.linkable:
            continue
        pairs.setdefault((r.source, r.target), []).append(r)
    for (src, tgt), rels in sorted(pairs.items()):
        for r in rels:
            choose = len(rels) > 1
            both = r.kind is not RelKind.DEPENDENCY and r.shape is not \
                Shape.RECORD and src != tgt
            does = (f"draw an edge from a «{_name(src)}» to a «{_name(tgt)}»"
                    + (" (or the other way)" if both else "")
                    + (f", choosing '{r.label}'" if choose else "")
                    + (f", choosing its kind ({', '.join(r.choices)})"
                       if r.choices else ""))
            out.append(Gesture(
                f"draw:{src}-{r.field}-{tgt}",
                does,
                {"op": "link", "source": {"kind": src, "id": "<source>"},
                 "target": {"kind": tgt, "id": "<target>"},
                 "relationship": r.stereotype,
                 **({"attrs": {"kind": "<chosen>"}} if r.choices else {})},
                f"{PRESENTATION.get(r.kind, 'an edge')}, drawn as "
                f"{DRAWN_AS[r.draw]}",
                source=src, target=tgt))

    # -- removing ------------------------------------------------------------
    out.append(Gesture(
        "unlink:edge", "select an edge that is not a composition and press "
        "Delete",
        {"op": "unlink", "source": {"kind": "<kind>", "id": "<source>"},
         "target": {"kind": "<kind>", "id": "<target>"},
         "relationship": "<edge's relationship>"},
        "the edge disappears; both boxes remain",
        refused="a composition edge cannot be removed alone — the part "
                "would have no whole; the status line says to move it or "
                "delete it"))
    out.append(Gesture(
        "delete:node", "select a box and press Delete",
        {"op": "delete", "kind": "<kind>", "id": "<source>"},
        "the box, its parts and every link to it disappear; the status line "
        "lists each link destroyed, and Undo restores them all",
        refused="nothing is removed; the status line names what still "
                "requires it (a trigger that must fire it, a step that "
                "calls it)"))

    # -- Properties ----------------------------------------------------------
    out.append(Gesture(
        "properties:edit", "change a field in Properties",
        {"op": "update", "kind": "<kind>", "id": "<source>",
         "attrs": {"<field>": "<value>"}},
        "the field shows the new value",
        refused="the field returns to its value; the status line names the "
                "constraint. A field that is a drawn relationship is not "
                "editable here — it is drawn"))
    out.append(Gesture(
        "properties:leader", "tick 'Leads its team' on an agent",
        {"op": "set_leader", "team": "<agent's team>", "agent": "<source>"},
        "the agent is marked as leader; the previous leader is not",
        source="agent", target="team"))
    return [replace(g, does=_art(g.does), shows=_art(g.shows)) for g in out]


def _art(text: str) -> str:
    return re.sub(r"\b([Aa]) «([AEIOU])", lambda m: f"{m.group(1)}n «"
                  f"{m.group(2)}", text)


def catalogue() -> str:
    lines = ["# The designer, specified", "",
             "Generated by `orgagents designer gestures` from "
             "`orgagents/designer/gestures.py` (ADR-0103). Every gesture is "
             "one model operation; what is accepted, what else changes and "
             "what is refused is the model's answer (ADR-0102), not the "
             "canvas's.", "",
             "## How relationships are drawn", "",
             "| UML kind | Drawn as |", "|---|---|"]
    for kind, text in PRESENTATION.items():
        lines.append(f"| {kind.value} | {text} |")
    groups = [("Creating", "create:"), ("Composing", "compose:"),
              ("Deploying", ("deploy:", "undeploy:")),
              ("Drawing", "draw:"), ("Removing", ("unlink:", "delete:")),
              ("Properties", "properties:")]
    all_g = gestures()
    for title, prefix in groups:
        lines += ["", f"## {title}", "",
                  "| Gesture | Operation | Accepted | Refused |",
                  "|---|---|---|---|"]
        for g in all_g:
            if g.id.startswith(prefix):
                req = g.request["op"] + (
                    f" «{g.request['relationship']}»"
                    if g.request.get("relationship") else "")
                lines.append(f"| {g.does} | `{req}` | {g.shows} | "
                             f"{g.refused} |")
    return "\n".join(lines) + "\n"


def evaluate(spec: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """The model's answer to one gesture on a draft: accepted with the new
    draft and what else changed, or refused with the violations. The draft
    comes back complete — every field with its value, defaults included —
    because the canvas reads a flow's kind or a unit link's kind whether or
    not the author wrote it; only empty (None) values are left out."""
    from ..metamodel.operations import apply
    from ..spec.model import SystemSpec

    result = apply(SystemSpec.model_validate(spec), request)
    answer: dict[str, Any] = {
        "accepted": result.accepted,
        "effects": result.effects,
        "violations": [{"constraint": v.constraint, "element": v.element,
                        "message": v.message} for v in result.violations],
        "incomplete": [{"constraint": v.constraint, "element": v.element,
                        "message": v.message} for v in result.incomplete],
    }
    if result.accepted:
        answer["spec"] = result.spec.model_dump(mode="json", by_alias=True,
                                                exclude_none=True)
    return answer
