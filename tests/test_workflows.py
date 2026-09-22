from orgagents.models import WorkflowRef
from orgagents.workflows.engine import WorkflowEngine


def _graph():
    return WorkflowRef(
        name="score-and-branch",
        graph={
            "entry": "fetch",
            "nodes": [
                {"id": "fetch", "kind": "tool", "tool": "get_rows",
                 "args": {"limit": 3}, "output": "rows"},
                {"id": "score", "kind": "transform", "expr": "len(rows)", "output": "n"},
                {"id": "gate", "kind": "branch",
                 "cases": [{"when": "n >= 3", "to": "publish"}], "default": "review"},
                {"id": "publish", "kind": "tool", "tool": "publish",
                 "args": {"count": "{{ n }}"}, "output": "done"},
                {"id": "review", "kind": "human", "output": "approval"},
            ],
            "edges": [{"from": "fetch", "to": "score"}, {"from": "score", "to": "gate"},
                      {"from": "publish", "to": "END"}, {"from": "review", "to": "END"}],
        },
    )


def test_branch_taken_and_args_rendered():
    calls = []
    engine = WorkflowEngine(tool_caller=lambda n, a: (calls.append((n, a)) or
                                                      [1, 2, 3]))
    res = engine.run(_graph())
    assert res.ok and res.path == ["fetch", "score", "gate", "publish"]
    assert calls[-1] == ("publish", {"count": 3})


def test_human_node_interrupts():
    engine = WorkflowEngine(tool_caller=lambda n, a: [1])
    res = engine.run(_graph())
    assert res.interrupted_at == "review" and not res.ok


def test_library_workflows_are_seeded(platform):
    names = {w.name for w in platform.store.list("workflows", WorkflowRef)}
    assert {"delegate-and-review", "governed-data-request", "cross-team-request"} <= names


# -- a declared edge that never ran -----------------------------------------


def _linear_graph(edges):
    return {
        "entry": "a",
        "nodes": [
            {"id": "a", "kind": "transform", "output": "x", "expr": "1"},
            {"id": "b", "kind": "transform", "output": "y", "expr": "2"},
            {"id": "c", "kind": "transform", "output": "z", "expr": "3"},
        ],
        "edges": edges,
    }


def test_a_node_with_two_ways_out_is_refused_not_silently_pruned():
    """Two edges from one node used to collapse into a dict keyed by source.

    The second edge won, the first was never taken, and the run reported
    success — a declared step that simply did not happen, with nothing
    anywhere saying so. Parallel steps are a real gap and this refusal names
    it; a graph whose edges are quietly discarded is the worse failure,
    because it looks like it worked.
    """
    from orgagents.models import WorkflowRef
    from orgagents.workflows.engine import WorkflowEngine

    result = WorkflowEngine().run(WorkflowRef(id="w", name="w", graph=_linear_graph([
        {"from": "a", "to": "b"},
        {"from": "a", "to": "c"},
        {"from": "b", "to": "END"},
        {"from": "c", "to": "END"},
    ])))
    assert result.error is not None
    assert "'a' declares 2 outgoing edges" in result.error
    assert "parallel steps are not supported" in result.error
    assert result.path == [], "nothing should have run"


def test_a_branch_may_declare_several_ways_out():
    """A branch carries its own cases, so it is the one node that chooses."""
    from orgagents.models import WorkflowRef
    from orgagents.workflows.engine import WorkflowEngine

    graph = _linear_graph([{"from": "a", "to": "route"}])
    graph["nodes"].append({
        "id": "route", "kind": "branch",
        "cases": [{"when": "x == 1", "to": "b"}], "default": "c",
    })
    graph["edges"] += [{"from": "route", "to": "b"}, {"from": "route", "to": "c"},
                       {"from": "b", "to": "END"}, {"from": "c", "to": "END"}]
    result = WorkflowEngine().run(WorkflowRef(id="w", name="w", graph=graph))
    assert result.error is None
    assert result.path == ["a", "route", "b"]


def test_a_cycle_runs_and_its_bound_says_what_it_was():
    """Cycles are legal — a review loop that runs until it passes is a real
    process. The step bound is what stops one running forever, and it names
    itself, because "step limit exceeded" alone cannot tell a runaway loop
    from a graph that is simply long.
    """
    from orgagents.models import WorkflowRef
    from orgagents.workflows.engine import WorkflowEngine

    graph = {
        "entry": "a",
        "nodes": [
            {"id": "a", "kind": "transform", "output": "n",
             "expr": "n + 1"},
            {"id": "gate", "kind": "branch",
             "cases": [{"when": "n < 3", "to": "a"}], "default": "END"},
        ],
        "edges": [{"from": "a", "to": "gate"}],
    }
    result = WorkflowEngine().run(WorkflowRef(id="w", name="w", graph=graph),
                                  state={"n": 0})
    assert result.error is None
    assert result.steps > 2, "the cycle did not go round"

    runaway = {
        "entry": "a",
        "nodes": [{"id": "a", "kind": "transform", "output": "x", "expr": "1"}],
        "edges": [{"from": "a", "to": "a"}],
    }
    out = WorkflowEngine().run(WorkflowRef(id="w", name="w", graph=runaway),
                               max_steps=10)
    assert "max_steps is 10" in out.error
    assert out.steps == 10
