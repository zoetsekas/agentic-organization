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

    Refusing is not the end state; parallel workflow steps are a real gap and
    named as one. But a graph whose declared edges are quietly discarded is the
    worse failure, because the run looks like a success.
    """

    def __init__(self, node_id: str, targets: list[str]) -> None:
        self.node_id = node_id
        self.targets = targets
        super().__init__(
            f"node '{node_id}' declares {len(targets)} outgoing edges "
            f"({', '.join(targets)}) and is not a branch, so nothing chooses "
            "between them. Route with a 'branch' node, or split the work "
            "across separate workflows: parallel steps are not supported."
        )


def _resolve_edges(graph: dict[str, Any], nodes: dict[str, dict]) -> dict[str, str]:
    """One outgoing edge per node, or a refusal naming the node.

    A `branch` node is exempt: it carries its own `cases`, and its entry here
    is only the fallback when no case matches.
    """
    out: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        out.setdefault(edge["from"], []).append(edge["to"])
    resolved: dict[str, str] = {}
    for source, targets in out.items():
        kind = (nodes.get(source) or {}).get("kind")
        if len(targets) > 1 and kind != "branch":
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
    ) -> None:
        self.tool_caller = tool_caller or (lambda name, args: {"tool": name, "args": args})
        self.agent_caller = agent_caller or (lambda aid, args: {"agent": aid, "args": args})
        self.human_caller = human_caller
        self.workflows = workflows or {}

    # -- execution ---------------------------------------------------------

    def run(
        self, ref: WorkflowRef, state: Optional[dict[str, Any]] = None, *, max_steps: int = 100
    ) -> WorkflowResult:
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
        current = graph.get("entry") or graph["nodes"][0]["id"]

        steps = 0
        while current and current != "END" and steps < max_steps:
            steps += 1
            node = nodes.get(current)
            if node is None:
                result.error = f"unknown node '{current}'"
                return result
            result.path.append(current)
            if node["kind"] == "human" or current in ref.interrupt_before:
                if self.human_caller is None:
                    result.interrupted_at = current
                    return result
                result.state[node.get("output", current)] = self.human_caller(
                    current, result.state
                )
                current = edges.get(current, "END")
                continue
            try:
                current = self._execute(node, result, edges)
            except Exception as e:
                result.error = f"{current}: {type(e).__name__}: {e}"
                return result
        result.steps = steps
        if steps >= max_steps:
            # A cycle is legal — a review loop that runs until it passes is a
            # real process — so this bound is what stops one running forever.
            # It says which bound it was, because "step limit exceeded" alone
            # leaves a reader unable to tell a runaway loop from a long graph.
            result.error = (
                f"step limit exceeded: {steps} steps, and max_steps is "
                f"{max_steps}. A cycle in this graph is not converging, or "
                "the graph is longer than the bound allows."
            )
        return result

    def _execute(self, node: dict, result: WorkflowResult, edges: dict[str, str]) -> str:
        kind = node["kind"]
        nid = node["id"]
        state = result.state
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
            if sub is None:
                raise KeyError(f"nested workflow '{node['workflow']}' not registered")
            nested = self.run(sub, _render(node.get("args", {}), state))
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
        # The same resolution as the interpreter, so the two engines cannot
        # disagree about what a graph means. LangGraph would happily fan out
        # where the interpreter takes one edge, which is exactly the kind of
        # divergence that makes a local run stop predicting the deployed one.
        edges = _resolve_edges(graph, nodes)

        def make(node: dict) -> Callable[[dict], dict]:
            def fn(state: dict) -> dict:
                r = WorkflowResult(ref.id, "inline", dict(state))
                self._execute(node, r, edges)
                return r.state

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
