"""Placing the boxes: three layout algorithms (ADR-0100).

There was no layout algorithm in the product. The screenshot script walked the
tree assigning a column per depth and a slot per sibling, which cascades
diagonally — by twelve agents the organisation ran off the right edge of every
capture. A reader's first impression of a design is where its boxes are, and
that was decided by whoever dragged them last.

These live in Python rather than in the canvas for one reason: a layout is a
function from a graph to coordinates, so "no two nodes overlap" and "a child
sits below its parent" are assertions. A layout that runs only in a browser is
a layout nothing checks, and layout is exactly the kind of code that rots
quietly, because a bad result still renders.

Each algorithm is deterministic. Two runs over the same graph give the same
coordinates, or a diagram would move every time somebody opened it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: Assumed node box. The server places boxes it cannot measure, so these are a
#: claim about the canvas's node size rather than a measurement of one — which
#: is why a very long label can still crowd its neighbour (ADR-0100).
NODE_W = 220
NODE_H = 96
GAP_X = 48
GAP_Y = 72


@dataclass
class LayoutNode:
    id: str
    parent: Optional[str] = None
    label: str = ""


@dataclass
class LayoutEdge:
    source: str
    target: str


@dataclass
class LayoutResult:
    positions: dict[str, dict[str, float]] = field(default_factory=dict)
    algorithm: str = ""
    #: Anything the algorithm could not honour, named rather than silently
    #: worked around.
    notes: list[str] = field(default_factory=list)

    @property
    def width(self) -> float:
        return max((p["x"] + NODE_W for p in self.positions.values()), default=0)

    @property
    def height(self) -> float:
        return max((p["y"] + NODE_H for p in self.positions.values()), default=0)


def _ordered(nodes: Iterable[LayoutNode]) -> list[LayoutNode]:
    """Declaration order, which is the only stable order a spec gives us."""
    return list(nodes)


# --------------------------------------------------------------------------
# tree — an organisation
# --------------------------------------------------------------------------


def tree(nodes: Iterable[LayoutNode], edges: Iterable[LayoutEdge] = ()) -> LayoutResult:
    """Parents centred over their children, depth down the page.

    The shape an org chart wants, and the one the diagonal cascade was failing
    to be. Children are packed left to right at their own depth and the parent
    is then centred over the span they occupy, so a wide team pushes its
    siblings aside rather than overlapping them.

    A node whose parent is not in the set is treated as a root. That is the
    common case when a diagram is scoped to one team, and refusing it would
    make a drill-down unlayoutable.
    """
    items = _ordered(nodes)
    by_id = {n.id: n for n in items}
    children: dict[Optional[str], list[str]] = {}
    for node in items:
        parent = node.parent if node.parent in by_id else None
        children.setdefault(parent, []).append(node.id)

    positions: dict[str, dict[str, float]] = {}
    notes: list[str] = []
    cursor = {"x": 0.0}
    seen: set[str] = set()

    def place(node_id: str, depth: int) -> tuple[float, float]:
        """Returns the horizontal span this subtree occupies."""
        if node_id in seen:
            # A cycle cannot happen in a reporting tree and is not trusted to
            # be impossible, because this runs over data somebody typed.
            notes.append(f"'{node_id}' appears under more than one parent; "
                         "drawn once")
            return (0.0, 0.0)
        seen.add(node_id)
        kids = children.get(node_id, [])
        if not kids:
            x = cursor["x"]
            cursor["x"] += NODE_W + GAP_X
            positions[node_id] = {"x": x, "y": depth * (NODE_H + GAP_Y)}
            return (x, x + NODE_W)
        spans = [place(kid, depth + 1) for kid in kids]
        spans = [s for s in spans if s != (0.0, 0.0)]
        left = min(s[0] for s in spans) if spans else cursor["x"]
        right = max(s[1] for s in spans) if spans else cursor["x"] + NODE_W
        centre = left + (right - left - NODE_W) / 2
        positions[node_id] = {"x": centre, "y": depth * (NODE_H + GAP_Y)}
        return (min(left, centre), max(right, centre + NODE_W))

    for root in children.get(None, []):
        place(root, 0)
    # Anything the walk did not reach — an orphan whose parent is itself an
    # orphan, say — still has to land somewhere visible.
    for node in items:
        if node.id not in positions:
            positions[node.id] = {"x": cursor["x"], "y": 0.0}
            cursor["x"] += NODE_W + GAP_X
            notes.append(f"'{node.id}' has no reachable parent; placed at the top")
    return LayoutResult(positions=positions, algorithm="tree", notes=notes)


# --------------------------------------------------------------------------
# layered — a process
# --------------------------------------------------------------------------


def layered(nodes: Iterable[LayoutNode], edges: Iterable[LayoutEdge]) -> LayoutResult:
    """Steps ranked by their longest path from the entry, ranks down the page.

    The shape a pipeline wants. The interesting case is a cycle: a review loop
    that runs until it passes is a legal process (ADR-0096), and ranking naively
    over a back edge would push the loop's target below its own source and draw
    a process that runs upward. So back edges are found first and excluded from
    ranking — they are still drawn, as the loop they are.
    """
    items = _ordered(nodes)
    ids = [n.id for n in items]
    known = set(ids)
    out: dict[str, list[str]] = {i: [] for i in ids}
    for edge in edges:
        if edge.source in known and edge.target in known:
            out[edge.source].append(edge.target)

    # Depth-first ordering tells us which edges point backwards.
    order: dict[str, int] = {}
    state: dict[str, int] = {}
    back: set[tuple[str, str]] = set()
    counter = {"n": 0}

    def visit(node_id: str) -> None:
        state[node_id] = 1
        for target in out[node_id]:
            if state.get(target) == 1:
                back.add((node_id, target))
            elif state.get(target) is None:
                visit(target)
        state[node_id] = 2
        order[node_id] = counter["n"]
        counter["n"] += 1

    entry = ids[0] if ids else None
    if entry:
        visit(entry)
    for node_id in ids:
        if state.get(node_id) is None:
            visit(node_id)

    rank: dict[str, int] = {i: 0 for i in ids}
    forward = [(s, t) for s in ids for t in out[s] if (s, t) not in back]
    # Longest path, relaxed until stable. The graph is small enough that this
    # is cheaper to read than a topological sort with its own edge cases.
    for _ in range(len(ids) + 1):
        changed = False
        for source, target in forward:
            if rank[target] < rank[source] + 1:
                rank[target] = rank[source] + 1
                changed = True
        if not changed:
            break

    rows: dict[int, list[str]] = {}
    for node_id in ids:
        rows.setdefault(rank[node_id], []).append(node_id)

    positions: dict[str, dict[str, float]] = {}
    widest = max((len(r) for r in rows.values()), default=1)
    span = widest * (NODE_W + GAP_X)
    for depth in sorted(rows):
        row = rows[depth]
        width = len(row) * (NODE_W + GAP_X) - GAP_X
        start = (span - GAP_X - width) / 2
        for index, node_id in enumerate(row):
            positions[node_id] = {
                "x": start + index * (NODE_W + GAP_X),
                "y": depth * (NODE_H + GAP_Y),
            }
    notes = [f"'{s}' → '{t}' is a loop back and was not ranked"
             for s, t in sorted(back)]
    return LayoutResult(positions=positions, algorithm="layered", notes=notes)


# --------------------------------------------------------------------------
# grid — a flat collection
# --------------------------------------------------------------------------


def grid(nodes: Iterable[LayoutNode], edges: Iterable[LayoutEdge] = (),
         columns: int = 0) -> LayoutResult:
    """Reading order, packed. For a set with no structure worth honouring.

    A list of policies or data classes has no hierarchy and no flow, and
    pretending otherwise with a tree produces a picture that implies a
    relationship the model does not carry (ADR-0081).
    """
    items = _ordered(nodes)
    if not items:
        return LayoutResult(algorithm="grid")
    if columns < 1:
        # Roughly square, which keeps a long list from becoming a strip.
        columns = max(1, int(len(items) ** 0.5 + 0.999))
    positions = {
        node.id: {
            "x": (index % columns) * (NODE_W + GAP_X),
            "y": (index // columns) * (NODE_H + GAP_Y),
        }
        for index, node in enumerate(items)
    }
    return LayoutResult(positions=positions, algorithm="grid")


ALGORITHMS = {"tree": tree, "layered": layered, "grid": grid}

#: What each diagram kind gets when nobody says. A process is a flow and an
#: organisation is a hierarchy, and defaulting either to the other produces a
#: picture that argues with the model.
DEFAULT_ALGORITHM = {"organisation": "tree", "process": "layered"}


def arrange(nodes: Iterable[LayoutNode], edges: Iterable[LayoutEdge] = (),
            *, algorithm: str = "", kind: str = "organisation") -> LayoutResult:
    """Lay a diagram out, by name or by what the diagram is."""
    name = algorithm or DEFAULT_ALGORITHM.get(kind, "tree")
    if name not in ALGORITHMS:
        raise ValueError(
            f"unknown layout '{name}'; this platform has {sorted(ALGORITHMS)}"
        )
    return ALGORITHMS[name](list(nodes), list(edges))
