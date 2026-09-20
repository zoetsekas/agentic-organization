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
