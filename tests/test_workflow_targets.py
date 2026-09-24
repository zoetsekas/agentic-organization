"""A workflow reaches the target, or the target says it did not (ADR-0096).

One test per line of that ADR's Verification section. Before this, a design
could declare a process graph, have it validate and resolve into the IR, and
then compile to a stack with no trace of it — and a `CONFORMANCE.md` whose
whole job is naming what did not survive would not mention it.
"""
from __future__ import annotations

import ast
import pathlib
import tempfile

import pytest

from orgagents.compiler.base import register_builtin_targets
from orgagents.compiler.engine import compile_system
from orgagents.spec.loader import load_binding, load_spec
from orgagents.spec.validate import errors, validate_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_TARGETS = ["langgraph", "adk", "maf"]


def _emit(target: str) -> dict[str, str]:
    register_builtin_targets()
    spec = load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml")
    binding = load_binding(ROOT / "examples" / "ayc" / "ayc.binding.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(spec, targets=[target],
                                out_dir=pathlib.Path(tmp), binding=binding)[0]
    return {f.path: f.content for f in result.files}


# -- the rule: emit it, or name it ------------------------------------------


@pytest.mark.parametrize("target", AGENT_TARGETS)
def test_every_target_accounts_for_every_declared_workflow(target):
    """Silence is the one option removed.

    The absence was unmentioned in the document whose purpose is naming
    absences, which is the worst place for one: a reader trusts it to be
    complete.
    """
    conformance = _emit(target)["CONFORMANCE.md"]
    assert "Encoded workflows" in conformance
    assert "`listing_readiness`" in conformance, \
        "a declared workflow is not named in the conformance report"


# -- LangGraph carries them -------------------------------------------------


def test_langgraph_emits_a_module_per_workflow():
    emitted = _emit("langgraph")
    assert "graphs/workflows/listing_readiness.py" in emitted
    assert "graphs/workflows/__init__.py" in emitted


def test_the_emitted_graph_is_the_declared_graph():
    """The shape is the claim, so the shape must be exact."""
    module = _emit("langgraph")["graphs/workflows/listing_readiness.py"]
    ast.parse(module)
    assert 'builder.add_node("stock", stock)' in module
    assert 'builder.add_node("draft", draft)' in module
    assert 'builder.add_edge(START, "stock")' in module
    assert 'builder.add_edge("stock", "draft")' in module
    assert 'builder.add_edge("draft", END)' in module


def test_node_bodies_raise_rather_than_looking_finished():
    """A `tool` node names a tool this stack may not hold, and a `transform`
    node is an expression for our own evaluator. A body that looked finished
    and quietly was not would be the worse failure."""
    module = _emit("langgraph")["graphs/workflows/listing_readiness.py"]
    assert module.count("NotImplementedError") == 2
    assert "calls tool 'stock_check'" in module
    assert "delegates to agent 'ecommerce_agent'" in module


def test_langgraph_says_the_shape_is_exact_and_the_bodies_are_not():
    conformance = _emit("langgraph")["CONFORMANCE.md"]
    assert "graph shape is exact" in conformance
    assert "bodies are not" in conformance
    assert "| Encoded workflows | emitted |" in conformance


# -- ADK and MAF decline to approximate -------------------------------------


@pytest.mark.parametrize("target", ["adk", "maf"])
def test_a_target_that_cannot_carry_them_says_so_and_emits_nothing(target):
    """Both frameworks have workflow constructs that compose *agents*. Our
    nodes are tool calls, transforms, branch predicates and interrupts.
    Mapping one onto the other would run, would not be the declared process,
    and would read as if it were."""
    emitted = _emit(target)
    conformance = emitted["CONFORMANCE.md"]
    assert "| Encoded workflows | **not carried** |" in conformance
    assert "listing_readiness" in conformance
    assert not [p for p in emitted if "/workflows/" in p], \
        "a target that declared it carries nothing emitted a workflow anyway"


def test_adk_names_the_constructs_it_declined_to_use():
    conformance = _emit("adk")["CONFORMANCE.md"]
    assert "SequentialAgent" in conformance
    assert "reads as if it were" in conformance


# -- graph shape is validated ------------------------------------------------


def _spec_with_graph(graph: dict):
    from spec_fixtures import BASE
    from spec_fixtures import org as _org
    return _org({**BASE, "workflows": [
        {"id": "wf", "name": "wf", "graph": graph}]})


def test_a_node_nothing_reaches_is_refused():
    """The defect this found in two shipped examples: a declared step that
    never ran, and nothing said so."""
    found = errors(validate_spec(_spec_with_graph({
        "entry": "a",
        "nodes": [{"id": "a", "kind": "transform", "expr": "1"},
                  {"id": "orphan", "kind": "transform", "expr": "2"}],
    })))
    unreachable = [f for f in found if f.code == "workflow_node_unreachable"]
    assert unreachable
    assert "'orphan'" in unreachable[0].message
    assert "A step that cannot run is not a step" in unreachable[0].message


def test_an_edge_to_a_node_that_does_not_exist_is_refused():
    codes = {f.code for f in errors(validate_spec(_spec_with_graph({
        "entry": "a",
        "nodes": [{"id": "a", "kind": "transform", "expr": "1"}],
        "edges": [{"from": "a", "to": "ghost"}],
    })))}
    assert "workflow_edge_to_unknown" in codes


def test_a_branch_case_pointing_nowhere_is_refused():
    codes = {f.code for f in errors(validate_spec(_spec_with_graph({
        "entry": "a",
        "nodes": [{"id": "a", "kind": "branch",
                   "cases": [{"when": "True", "to": "ghost"}]}],
    })))}
    assert "workflow_branch_target_unknown" in codes


def test_an_entry_that_is_not_a_node_is_refused():
    codes = {f.code for f in errors(validate_spec(_spec_with_graph({
        "entry": "nowhere",
        "nodes": [{"id": "a", "kind": "transform", "expr": "1"}],
    })))}
    assert "workflow_entry_unknown" in codes


def test_a_sound_graph_passes():
    assert not [f for f in errors(validate_spec(_spec_with_graph({
        "entry": "a",
        "nodes": [{"id": "a", "kind": "transform", "expr": "1"},
                  {"id": "b", "kind": "transform", "expr": "2"}],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "END"}],
    }))) if f.code.startswith("workflow_")]


def test_every_shipped_example_has_a_sound_graph():
    """Two of them did not, until this rule existed."""
    for path in sorted(ROOT.glob("examples/*/*.system.yaml")):
        bad = [f.code for f in errors(validate_spec(load_spec(path)))
               if f.code.startswith("workflow_")]
        assert not bad, f"{path.name}: {bad}"
