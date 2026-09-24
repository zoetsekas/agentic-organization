"""The governed workflow and the step an engine builds (ADR-0110).

Two levels, one seam: the process is drawn and validated here — who owns each
step, where a person approves, what runs in parallel — and a step whose body
lives in an engine is a `workflow` step calling a workflow with `body:
external`, known only by its interface. These tests hold:

* fork/join validate and execute;
* an external workflow without a binding or an interface is an error;
* interface capabilities and data classes are checked against the calling
  step's owner;
* no engine name enters `src/orgagents/spec/` (also held for every file there
  by tests/test_workflow_engines.py);
* the AYC purchase-to-pay workflow validates, compiles for `local`, and runs
  its engine-bound step through the egress path.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from orgagents.compiler import compile_system
from orgagents.compiler.engine import CompileError
from orgagents.compiler.ir import build_ir
from orgagents.models import WorkflowRef
from orgagents.platform import Platform
from orgagents.runtime.loader import load_system
from orgagents.spec import load_binding, load_spec
from orgagents.spec.binding import Binding, TargetBinding, WorkflowBinding
from orgagents.spec.model import SystemSpec
from orgagents.spec.validate import validate_spec, workflow_binding_findings
from orgagents.workflows.engine import WorkflowEngine

ROOT = Path(__file__).resolve().parents[1]
AYC = ROOT / "examples" / "ayc"
SPEC_DIR = ROOT / "src" / "orgagents" / "spec"


def _spec(workflows: list[dict], **org) -> SystemSpec:
    base = {
        "metadata": {"name": "p2p"},
        "organization": {
            "id": "co", "name": "Co", "leader": "buyer",
            "members": [
                {"id": "buyer", "name": "buyer", "roles": ["buying"],
                 "mandate": {"decisions": []}},
                {"id": "payer", "name": "payer", "roles": ["paying"]},
            ],
            "data_classes": [
                {"id": "supplier_data", "scope": "public"},
                {"id": "ledger", "scope": "public"},
            ],
            "capabilities": [
                {"id": "ordering", "action": "query", "data_classes": ["supplier_data"]},
                {"id": "paying", "action": "query", "data_classes": ["ledger"]},
            ],
            "role_definitions": [
                {"id": "buying", "title": "Buyer", "capabilities": ["ordering"]},
                {"id": "paying", "title": "Payer", "capabilities": ["paying"]},
            ],
            "people": [{"id": "p_boss", "name": "Boss"}],
            "workflows": workflows,
            **org,
        },
    }
    return SystemSpec.model_validate(base)


def _codes(spec: SystemSpec) -> set[str]:
    return {f.code for f in validate_spec(spec) if f.code.startswith("workflow_")}


def _parallel(extra_nodes=(), extra_edges=(), drop=()) -> dict:
    nodes = [
        {"id": "start", "kind": "transform", "expr": "1", "output": "po"},
        {"id": "split", "kind": "fork"},
        {"id": "left", "kind": "transform", "expr": "po + 1", "output": "receipt"},
        {"id": "right", "kind": "transform", "expr": "po + 2", "output": "invoice"},
        {"id": "meet", "kind": "join"},
        {"id": "pay", "kind": "transform", "expr": "receipt + invoice", "output": "paid"},
        *extra_nodes,
    ]
    edges = [
        {"from": "start", "to": "split"},
        {"from": "split", "to": "left"},
        {"from": "split", "to": "right"},
        {"from": "left", "to": "meet"},
        {"from": "right", "to": "meet"},
        {"from": "meet", "to": "pay"},
        {"from": "pay", "to": "END"},
        *extra_edges,
    ]
    nodes = [n for n in nodes if n["id"] not in drop]
    edges = [e for e in edges if e["from"] not in drop and e["to"] not in drop]
    return {"entry": "start", "nodes": nodes, "edges": edges}


# -- fork and join -------------------------------------------------------------

def test_a_fork_that_meets_at_a_join_is_sound():
    assert not _codes(_spec([{"id": "w", "graph": _parallel()}]))


def test_a_fork_whose_branches_never_join_is_an_error():
    graph = _parallel(drop=("meet", "pay"))
    graph["edges"] += [{"from": "left", "to": "END"}, {"from": "right", "to": "END"}]
    assert "workflow_fork_without_join" in _codes(_spec([{"id": "w", "graph": graph}]))


def test_a_join_nothing_forks_into_is_an_error():
    graph = {"entry": "a", "nodes": [
        {"id": "a", "kind": "transform", "expr": "1"},
        {"id": "meet", "kind": "join"}],
        "edges": [{"from": "a", "to": "meet"}, {"from": "meet", "to": "END"}]}
    assert "workflow_join_without_fork" in _codes(_spec([{"id": "w", "graph": graph}]))


def test_fork_and_join_execute_every_branch_and_merge_at_the_join():
    ref = WorkflowRef(id="w", name="w", graph=_parallel())
    result = WorkflowEngine().run(ref)
    assert result.ok, result.error
    assert result.path == ["start", "split", "left", "right", "meet", "pay"]
    assert result.state["receipt"] == 2 and result.state["invoice"] == 3
    assert result.state["paid"] == 5


def test_two_branches_writing_one_key_are_refused_not_raced():
    graph = _parallel()
    for node in graph["nodes"]:
        if node["id"] == "right":
            node["output"] = "receipt"
    result = WorkflowEngine().run(WorkflowRef(id="w", name="w", graph=graph))
    assert result.error and "both write 'receipt'" in result.error


def test_a_human_step_must_say_who_approves():
    graph = {"entry": "ok", "nodes": [{"id": "ok", "kind": "human"}],
             "edges": [{"from": "ok", "to": "END"}]}
    spec = _spec([{"id": "w", "graph": graph}])
    found = [f for f in validate_spec(spec) if f.code == "workflow_approval_without_approver"]
    assert found and found[0].severity == "warning"
    spec.metadata.environment = "production"
    found = [f for f in validate_spec(spec) if f.code == "workflow_approval_without_approver"]
    assert found and found[0].severity == "error"
    graph["nodes"][0]["person"] = "p_boss"
    assert not _codes(_spec([{"id": "w", "graph": graph}]))
    graph["nodes"][0]["person"] = "p_nobody"
    assert "workflow_step_owner_unknown" in _codes(_spec([{"id": "w", "graph": graph}]))


# -- the seam: body external and its interface -----------------------------------

def _caller(owner: str, workflow: str = "capture") -> dict:
    return {"id": "p2p", "graph": {
        "entry": "call", "nodes": [{"id": "call", "kind": "workflow",
                                    "workflow": workflow, "owner": owner}],
        "edges": [{"from": "call", "to": "END"}]}}


def test_an_external_workflow_without_an_interface_is_an_error():
    spec = _spec([{"id": "capture", "body": "external"}])
    assert "workflow_external_without_interface" in _codes(spec)
    # ...and holds no graph of ours, so none of the graph rules fire on it.
    assert "workflow_without_nodes" not in _codes(spec)


def test_an_external_workflow_without_a_binding_is_an_error():
    spec = _spec([{"id": "capture", "body": "external",
                   "interface": {"outputs": ["invoice"]}}])
    unbound = TargetBinding(target="local")
    assert [f.code for f in workflow_binding_findings(spec, unbound)] == [
        "workflow_external_unbound"]
    native = TargetBinding(target="local", workflows=[
        WorkflowBinding(workflow="capture", engine="native")])
    assert workflow_binding_findings(spec, native)
    bound = TargetBinding(target="local", workflows=[WorkflowBinding(
        workflow="capture", engine="flowserver", flow="capture")])
    assert not workflow_binding_findings(spec, bound)
    # A target whose binding was written and does not bind it is refused...
    with pytest.raises(CompileError, match="workflow_external_unbound"):
        compile_system(spec, targets=["local"], write=False,
                       binding=Binding(targets=[unbound]))
    # ...and a target compiled on the platform default, where nothing was
    # chosen, is told so and reports the workflow as not carried.
    [result] = compile_system(spec, targets=["local"], write=False)
    assert [f.severity for f in result.findings
            if f.code == "workflow_external_unbound"] == ["warning"]


def test_the_interface_is_checked_against_the_calling_steps_owner():
    capture = {"id": "capture", "body": "external",
               "interface": {"tools": ["paying"], "receives_data_classes": ["ledger"]}}
    # The payer holds `paying` and may send the ledger.
    assert not _codes(_spec([capture, _caller("payer")]))
    # The buyer holds neither: the step is refused on both counts.
    codes = _codes(_spec([capture, _caller("buyer")]))
    assert {"workflow_step_owner_lacks_capability",
            "workflow_step_sends_unheld_data"} <= codes
    # An owner that is not in the design is named as such.
    assert "workflow_step_owner_unknown" in _codes(_spec([capture, _caller("ghost")]))


def test_no_engine_name_enters_the_spec_layer():
    for path in SPEC_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in ("langflow", "langgraph", "langchain"):
            assert not re.search(rf"\b{name}\b", text, re.IGNORECASE), (path.name, name)


# -- AYC: purchase-to-pay ----------------------------------------------------------

@pytest.fixture(scope="module")
def ayc():
    return load_spec(str(AYC / "ayc.system.yaml"))


def _local():
    return next(t for t in load_binding(str(AYC / "ayc.local.binding.yaml")).targets
                if t.target == "local")


def test_ayc_purchase_to_pay_validates_and_keeps_the_separations(ayc):
    errors = [f for f in validate_spec(ayc) if f.severity == "error"]
    assert not errors, errors
    p2p = next(w for w in ayc.workflows if w.id == "purchase_to_pay")
    kinds = {n.id: n.kind.value for n in p2p.graph.nodes}
    assert kinds["in_parallel"] == "fork" and kinds["both_done"] == "join"
    assert kinds["coo_approval"] == "human"
    owners = {n.id: n.owner or n.agent for n in p2p.graph.nodes}
    # The hand that raises the order never pays it.
    assert owners["raise_po"] == "buyer_agent" and owners["match_and_pay"] == "ap_agent"
    assert owners["capture_invoice"] == "ap_agent"
    capture = next(w for w in ayc.workflows if w.id == "invoice_capture")
    assert capture.body.value == "external" and not capture.graph


def test_ayc_purchase_to_pay_compiles_for_local(ayc, tmp_path):
    [result] = compile_system(ayc, targets=["local"], out_dir=tmp_path,
                              binding=load_binding(str(AYC / "ayc.local.binding.yaml")),
                              write=False)
    assert {w.id for w in result.ir.workflows} >= {"purchase_to_pay", "invoice_capture"}
    assert result.ir.binding.workflow_binding("invoice_capture").engine == "langflow"


def test_ayc_purchase_to_pay_runs_its_engine_bound_step_through_egress(ayc, tmp_path):
    ir = build_ir(ayc, target="local", binding=_local())
    platform = Platform(str(tmp_path / "p2p.db"), configure_logs=False)
    load_system(platform, ir)
    runtime = platform.runtime
    sent = []

    def transport(url, payload, secret_ref):
        sent.append({"url": url, "payload": payload, "secret_ref": secret_ref})
        return {"outputs": {"invoice": {"po_number": "PO-1040", "amount": 2440.0}}}

    runtime.workflow_transport = transport
    coo = platform.org.agent("coo_agent")
    session = platform.sessions.create(coo.id)
    ref = platform.store.get("workflows", "purchase_to_pay", WorkflowRef)
    engine = WorkflowEngine(
        agent_caller=lambda agent_id, args: {"by": agent_id, **args},
        human_caller=lambda step, state: {"approved_by": "p_coo"},
        workflows=runtime._callable_workflows(coo, ref),
        workflow_caller=runtime.external_workflow_caller(coo, session.id),
    )
    result = engine.run(ref, {})
    assert result.ok, result.error
    assert result.path == ["raise_po", "coo_approval", "in_parallel", "receive_goods",
                           "capture_invoice", "both_done", "match_and_pay"]
    assert result.state["invoice"]["outputs"]["invoice"]["po_number"] == "PO-1040"
    # It left through the engine invoker: to the bound flow, with the engine's
    # own credential, and logged as outside the agent's sandbox.
    [call] = sent
    assert call["url"] == "http://flows.internal:7860/api/v1/run/invoice-capture"
    assert call["secret_ref"] == "LANGFLOW_SECRET_KEY"
    logged = [e.payload for e in platform.sessions.events(session.id)
              if e.type == "workflow"]
    assert logged and logged[-1]["outside_agent_sandbox"] is True
    assert logged[-1]["engine"] == "langflow"


def test_an_engine_bound_step_is_refused_when_its_owner_cannot_reach_the_engine(
        ayc, tmp_path):
    binding = _local()
    wb = binding.workflow_binding("invoice_capture")
    wb.endpoint = "http://flows.elsewhere.example/api/v1/run"
    ir = build_ir(ayc, target="local", binding=binding)
    platform = Platform(str(tmp_path / "p2p.db"), configure_logs=False)
    load_system(platform, ir)
    platform.runtime.workflow_transport = lambda *a: pytest.fail("must not be sent")
    coo = platform.org.agent("coo_agent")
    call = platform.runtime.external_workflow_caller(
        coo, platform.sessions.create(coo.id).id)
    with pytest.raises(RuntimeError, match="egress_not_allowlisted"):
        call("invoice_capture", {"purchase_order": "PO-1040"}, {"owner": "ap_agent"})
