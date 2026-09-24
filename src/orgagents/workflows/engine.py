"""Encoded agentic workflows.

Workflows are declared as data (nodes + edges) so the designer UI can render
and edit them, and so governance can diff them. At execution time the engine
compiles the declaration onto LangGraph when it is installed; otherwise it runs
the same semantics with a built-in interpreter, so the platform has no hard
dependency on LangGraph for tests or local development.

Node kinds
----------
``tool``      call a harness tool
``agent``     delegate to another agent (a sub-agent run)
``workflow``  invoke a nested workflow
``human``     interrupt and wait for the human counterpart
``branch``    evaluate a condition and pick the next node
``transform`` apply a pure python expression to the state
``fork``      run every way out (ADR-0110); each branch sees the state at
              the fork, and their writes are merged at the join
``join``      where a fork's branches meet
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..ids import new_id
from ..models import WorkflowRef

try:  # pragma: no cover - exercised only when LangGraph is installed
    from langgraph.graph import END, StateGraph

    HAS_LANGGRAPH = True
except Exception:  # pragma: no cover
    HAS_LANGGRAPH = False
    StateGraph = None  # type: ignore
    END = "__end__"  # type: ignore


@dataclass
class WorkflowResult:
    workflow_id: str
    run_id: str
    state: dict[str, Any]
    path: list[str] = field(default_factory=list)
    interrupted_at: Optional[str] = None
    error: Optional[str] = None
    #: How many nodes actually ran, so a cycle's cost is visible.
    steps: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and self.interrupted_at is None


class AmbiguousEdges(ValueError):
    """A node declares more than one way out and nothing chooses between them.

    Two edges leaving one node mean different things in different engines: to
    LangGraph it is a fan-out that runs both branches as a parallel superstep,
    to this interpreter it is a single `next` that can only be one of them.
    They were being collapsed into a dict keyed by source, so the second edge
    silently won and the first was never taken — a declared step that simply
    did not run, with nothing anywhere saying so.

    Parallel work is drawn, not implied (ADR-0110): a `fork` is the node that
    means "every way out runs", and a `join` is where they meet. Any other
    node with two ways out is still refused rather than guessed at.
    """

    def __init__(self, node_id: str, targets: list[str]) -> None:
        self.node_id = node_id
        self.targets = targets
        super().__init__(
            f"node '{node_id}' declares {len(targets)} outgoing edges "
            f"({', '.join(targets)}) and is not a branch or a fork, so nothing "
            "decides what they mean. Route with a 'branch' node to take one "
            "of them, or start them with a 'fork' and meet them at a 'join' "
            "to run them all."
        )


#: Nodes that may declare several ways out: a branch chooses one, a fork
#: takes every one.
_MANY_WAYS_OUT = ("branch", "fork")


def _edges_out(graph: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        out.setdefault(edge["from"], []).append(edge["to"])
    return out


def _resolve_edges(graph: dict[str, Any], nodes: dict[str, dict]) -> dict[str, str]:
    """One outgoing edge per node, or a refusal naming the node.

    A `branch` node is exempt: it carries its own `cases`, and its entry here
    is only the fallback when no case matches. A `fork` is exempt because all
    of its ways out run; they are read with `_edges_out`.
    """
    resolved: dict[str, str] = {}
    for source, targets in _edges_out(graph).items():
        kind = (nodes.get(source) or {}).get("kind")
        if len(targets) > 1 and kind not in _MANY_WAYS_OUT:
            raise AmbiguousEdges(source, targets)
        resolved[source] = targets[0]
    return resolved


class WorkflowEngine:
    """Compiles and runs declarative workflow graphs."""

    def __init__(
        self,
        *,
        tool_caller: Optional[Callable[[str, dict], Any]] = None,
        agent_caller: Optional[Callable[[str, dict], Any]] = None,
        human_caller: Optional[Callable[[str, dict], Any]] = None,
        workflows: Optional[dict[str, WorkflowRef]] = None,
        workflow_caller: Optional[Callable[[str, dict, dict], Any]] = None,
    ) -> None:
        self.tool_caller = tool_caller or (lambda name, args: {"tool": name, "args": args})
        self.agent_caller = agent_caller or (lambda aid, args: {"agent": aid, "args": args})
        self.human_caller = human_caller
        self.workflows = workflows or {}
        # How a step reaches a workflow whose body lives in an engine
        # (ADR-0110): `(workflow_id, args, step) -> result`. The runtime hands
        # in the ADR-0056 invoker, so the call is egress and governed as such.
        self.workflow_caller = workflow_caller

    # -- execution ---------------------------------------------------------

    def run(
        self, ref: WorkflowRef, state: Optional[dict[str, Any]] = None, *, max_steps: int = 100
    ) -> WorkflowResult:
        if getattr(ref, "body", "graph") == "external":
            return WorkflowResult(
                ref.id, new_id("run"), state or {},
                error=f"workflow '{ref.id}' is built in an engine (body: "
                      "external); it runs through its binding, not here")
        graph = ref.graph or {}
        nodes: dict[str, dict] = {n["id"]: n for n in graph.get("nodes", [])}
        if not nodes:
            return WorkflowResult(ref.id, new_id("run"), state or {}, error="empty graph")
        result = WorkflowResult(ref.id, new_id("run"), dict(state or {}))
        try:
            edges = _resolve_edges(graph, nodes)
        except AmbiguousEdges as e:
            result.error = str(e)
            return result
        forks = {nid: targets for nid, targets in _edges_out(graph).items()
                 if (nodes.get(nid) or {}).get("kind") == "fork"}
        entry = graph.get("entry") or graph["nodes"][0]["id"]
        self._walk(ref, entry, nodes, edges, forks, result, result.state,
                   max_steps=max_steps, in_branch=False)
        if result.steps >= max_steps and result.error is None \
                and result.interrupted_at is None:
            # A cycle is legal — a review loop that runs until it passes is a
            # real process — so this bound is what stops one running forever.
            # It says which bound it was, because "step limit exceeded" alone
            # leaves a reader unable to tell a runaway loop from a long graph.
            result.error = (
                f"step limit exceeded: {result.steps} steps, and max_steps is "
                f"{max_steps}. A cycle in this graph is not converging, or "
                "the graph is longer than the bound allows."
            )
        return result

    def _walk(
        self, ref: WorkflowRef, current: str, nodes: dict[str, dict],
        edges: dict[str, str], forks: dict[str, list[str]],
        result: WorkflowResult, state: dict[str, Any], *,
        max_steps: int, in_branch: bool,
    ) -> Optional[str]:
        """Run from `current` until END, or — inside a fork's branch — until
        the join the branch arrives at, which is returned. None means the run
        stopped (an error, a pause, or the step bound)."""
        while current and current != "END":
            if result.steps >= max_steps:
                return None
            node = nodes.get(current)
            if node is None:
                result.error = f"unknown node '{current}'"
                return None
            kind = node["kind"]
            if kind == "join" and in_branch:
                return current
            result.steps += 1
            result.path.append(current)
            if kind == "human" or current in ref.interrupt_before:
                if self.human_caller is None:
                    result.interrupted_at = current
                    return None
                state[node.get("output", current)] = self.human_caller(current, state)
                current = edges.get(current, "END")
                continue
            if kind == "join":
                # Reached outside a branch: the fork that began it has
                # already waited for every branch, so it simply passes.
                current = edges.get(current, "END")
                continue
            if kind == "fork":
                current = self._fork(ref, node, nodes, edges, forks, result,
                                     state, max_steps=max_steps)
                if current is None:
                    return None
                continue
            try:
                current = self._execute(node, state, edges)
            except Exception as e:
                result.error = f"{current}: {type(e).__name__}: {e}"
                return None
        return "END"

    def _fork(
        self, ref: WorkflowRef, node: dict, nodes: dict[str, dict],
        edges: dict[str, str], forks: dict[str, list[str]],
        result: WorkflowResult, state: dict[str, Any], *, max_steps: int,
    ) -> Optional[str]:
        """Run every branch of a fork and wait for them at their join.

        Each branch starts from the state as it was at the fork and cannot
        see what a sibling writes; what they write is merged at the join, and
        two branches writing the same key is refused rather than one silently
        winning. Branches run one after another here: this interpreter is the
        reference semantics, not a scheduler (ADR-0110).
        """
        fork_id = node["id"]
        joins: set[Optional[str]] = set()
        writes: dict[str, str] = {}
        merged: dict[str, Any] = {}
        for target in forks.get(fork_id, []):
            branch = dict(state)
            arrived = self._walk(ref, target, nodes, edges, forks, result,
                                 branch, max_steps=max_steps, in_branch=True)
            if arrived is None:
                return None
            joins.add(arrived)
            for key, value in branch.items():
                if key in state and state[key] is value:
                    continue
                if key in writes:
                    result.error = (
                        f"{fork_id}: branches '{writes[key]}' and '{target}' "
                        f"both write '{key}', so which one the join keeps "
                        "would be an accident of ordering")
                    return None
                writes[key] = target
                merged[key] = value
        if len(joins) != 1 or "END" in joins:
            result.error = (
                f"{fork_id}: the branches of this fork do not meet at one join "
                f"(they reach {', '.join(sorted(str(j) for j in joins))})")
            return None
        join = joins.pop()
        state.update(merged)
        result.steps += 1
        result.path.append(join)
        return edges.get(join, "END")

    def _execute(self, node: dict, state: dict[str, Any], edges: dict[str, str]) -> str:
        kind = node["kind"]
        nid = node["id"]
        if kind == "tool":
            state[node.get("output", nid)] = self.tool_caller(
                node["tool"], _render(node.get("args", {}), state)
            )
        elif kind == "agent":
            state[node.get("output", nid)] = self.agent_caller(
                node["agent"], _render(node.get("args", {}), state)
            )
        elif kind == "workflow":
            sub = self.workflows.get(node["workflow"])
            args = _render(node.get("args", {}), state)
            if sub is not None and getattr(sub, "body", "graph") == "external":
                # The body lives in an engine: the step calls it through the
                # binding, and what comes back is the step's output.
                if self.workflow_caller is None:
                    raise RuntimeError(
                        f"workflow '{node['workflow']}' is built in an engine "
                        "and no engine invoker was provided to call it")
                state[node.get("output", nid)] = self.workflow_caller(
                    node["workflow"], args, node)
                return edges.get(nid, "END")
            if sub is None:
                raise KeyError(f"nested workflow '{node['workflow']}' not registered")
            nested = self.run(sub, args)
            state[node.get("output", nid)] = nested.state
            if nested.error:
                raise RuntimeError(nested.error)
        elif kind == "transform":
            state[node.get("output", nid)] = _safe_eval(node["expr"], state)
        elif kind == "branch":
            for case in node.get("cases", []):
                if _safe_eval(case["when"], state):
                    return case["to"]
            return node.get("default", edges.get(nid, "END"))
        else:
            raise ValueError(f"unsupported node kind '{kind}'")
        return edges.get(nid, "END")

    # -- LangGraph compilation --------------------------------------------

    def compile_langgraph(self, ref: WorkflowRef) -> Any:
        """Compile the declaration into a real LangGraph `StateGraph`.

        Raises if LangGraph is not installed; `run` never requires it.
        """
        if not HAS_LANGGRAPH:
            raise RuntimeError("langgraph is not installed; pip install 'orgagents[langgraph]'")
        graph = ref.graph or {}
        builder = StateGraph(dict)
        nodes = {n["id"]: n for n in graph.get("nodes", [])}
        parallel = sorted(n for n, v in nodes.items() if v.get("kind") in ("fork", "join"))
        if parallel:
            # Compiling a fork to a LangGraph superstep is ADR-0110's later
            # milestone; refusing is better than a fan-out whose merge rules
            # differ from the interpreter's.
            raise NotImplementedError(
                f"workflow '{ref.id}' forks or joins at {', '.join(parallel)}; "
                "compiling parallel steps to LangGraph is not supported yet, "
                "run it with WorkflowEngine.run")
        # The same resolution as the interpreter, so the two engines cannot
        # disagree about what a graph means. LangGraph would happily fan out
        # where the interpreter takes one edge, which is exactly the kind of
        # divergence that makes a local run stop predicting the deployed one.
        edges = _resolve_edges(graph, nodes)

        def make(node: dict) -> Callable[[dict], dict]:
            def fn(state: dict) -> dict:
                out = dict(state)
                self._execute(node, out, edges)
                return out

            return fn

        for nid, node in nodes.items():
            builder.add_node(nid, make(node))
        entry = graph.get("entry") or next(iter(nodes))
        builder.set_entry_point(entry)
        for nid in nodes:
            target = edges.get(nid)
            if nodes[nid]["kind"] == "branch":
                cases = nodes[nid].get("cases", [])
                builder.add_conditional_edges(
                    nid,
                    lambda s, _c=cases, _d=nodes[nid].get("default", END): next(
                        (c["to"] for c in _c if _safe_eval(c["when"], s)), _d
                    ),
                )
            else:
                builder.add_edge(nid, target if target and target != "END" else END)
        return builder.compile(interrupt_before=ref.interrupt_before or None)


# --------------------------------------------------------------------------
# Small, deliberately restricted expression support
# --------------------------------------------------------------------------

_SAFE_BUILTINS = {
    "len": len, "sum": sum, "min": min, "max": max, "any": any, "all": all,
    "sorted": sorted, "abs": abs, "round": round, "str": str, "int": int,
    "float": float, "bool": bool, "list": list, "dict": dict, "set": set,
}


def _safe_eval(expr: str, state: dict[str, Any]) -> Any:
    """Evaluate a workflow expression against the state, with no builtins."""
    return eval(expr, {"__builtins__": _SAFE_BUILTINS}, dict(state))  # noqa: S307


def _render(args: Any, state: dict[str, Any]) -> Any:
    """Substitute ``{{ expr }}`` placeholders in node arguments from state."""
    if isinstance(args, dict):
        return {k: _render(v, state) for k, v in args.items()}
    if isinstance(args, list):
        return [_render(v, state) for v in args]
    if isinstance(args, str) and args.startswith("{{") and args.endswith("}}"):
        return _safe_eval(args[2:-2].strip(), state)
    return args
