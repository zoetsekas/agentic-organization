"""AYC on a workstation: the local binding, the stack it compiles to, and the
pieces that make that stack run (ADR-0109).

The generated stack was parsed and never started before this; these tests hold
the things that starting it depended on:

* the local binding passes the phase gate, separations included;
* each backing system sits on a network of its own, and an agent is attached
  to exactly the systems it holds a capability on — the buyer cannot route to
  the ledger, accounts payable cannot route to Fishbowl;
* each agent carries only its own capabilities' credentials;
* every agent container runs a command the CLI actually has;
* the committed output under examples/ayc/generated/local is current;
* Docker's own parser accepts the stack with the workstation overlay;
* the stub model drives a real deep agents loop, through the harness, to an
  MCP server — a supervised call stops for approval, an approval releases one
  call with those arguments, a missing grant is refused, and a system refuses
  a credential that does not cover the tool.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from orgagents.cli import main as cli_main
from orgagents.compiler import compile_system
from orgagents.compiler.ir import build_ir
from orgagents.harness.builder import ApprovalGrants
from orgagents.phases import review
from orgagents.spec import load_binding, load_spec

ROOT = Path(__file__).resolve().parents[1]
AYC = ROOT / "examples" / "ayc"
SPEC = AYC / "ayc.system.yaml"
BINDING = AYC / "ayc.local.binding.yaml"
COMMITTED = AYC / "generated" / "local"


def _target():
    return next(t for t in load_binding(str(BINDING)).targets if t.target == "local")


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("ayc-local")
    compile_system(load_spec(str(SPEC)), targets=["local"], out_dir=out,
                   binding=load_binding(str(BINDING)))
    return out / "local"


@pytest.fixture(scope="module")
def compose(generated) -> dict:
    return yaml.safe_load((generated / "docker-compose.yaml").read_text(encoding="utf-8"))


# -- the binding ---------------------------------------------------------------

def test_the_local_binding_passes_the_phase_gate():
    report = review(load_spec(str(SPEC)), binding=load_binding(str(BINDING)),
                    target="local")
    failed = [c.title for c in report.checks if c.status == "fail"]
    assert not failed, failed


def test_the_binding_keeps_the_design_runtime_and_stubs_only_the_model():
    target = _target()
    assert target.runtime.adapter == "langchain_deepagents"
    assert target.model.provider == "stub"


# -- the stack -----------------------------------------------------------------

AGENT_SERVERS = {
    "buyer_agent": {"fishbowl"},
    "warehouse_agent": {"fishbowl", "shopify"},
    "inventory_agent": {"fishbowl"},
    "ap_agent": {"accounting"},
    "ar_agent": {"accounting"},
    "software_agent": {"deploy_pipeline"},
    "ecommerce_agent": {"shopify", "fishbowl"},
    "cs_agent": {"shopify"},
    "marketing_agent": {"shopify", "cms"},
    "ceo_agent": set(),
}


@pytest.mark.parametrize("agent,servers", sorted(AGENT_SERVERS.items()))
def test_an_agent_is_on_exactly_the_system_networks_it_holds_a_capability_on(
        compose, agent, servers):
    nets = {n for n in compose["services"][f"agent-{agent}"]["networks"]
            if n.startswith("srv-")}
    assert nets == {f"srv-{s}" for s in servers}


def test_a_backing_system_is_on_its_own_network_and_not_on_control(compose):
    for server in ("shopify", "fishbowl", "accounting", "cms", "deploy_pipeline"):
        service = compose["services"][f"mcp-{server}"]
        assert service["networks"] == [f"srv-{server}"]
        assert compose["networks"][f"srv-{server}"] == {"internal": True}


def test_the_two_sides_of_purchasing_and_payment_cannot_route_to_each_other(compose):
    buyer = set(compose["services"]["agent-buyer_agent"]["networks"])
    ap = set(compose["services"]["agent-ap_agent"]["networks"])
    assert "srv-accounting" not in buyer
    assert "srv-fishbowl" not in ap


def test_each_agent_carries_only_its_own_fishbowl_credential(compose):
    def creds(agent):
        env = compose["services"][f"agent-{agent}"]["environment"]
        return {k for k in env if k.startswith("FISHBOWL_")}

    assert creds("warehouse_agent") == {"FISHBOWL_DOCK_TOKEN"}
    assert creds("inventory_agent") == {"FISHBOWL_AUDIT_TOKEN", "FISHBOWL_READ_TOKEN"}
    assert creds("buyer_agent") == {"FISHBOWL_PURCHASING_TOKEN", "FISHBOWL_READ_TOKEN"}
    assert creds("ap_agent") == set()


def test_every_agent_container_runs_a_command_the_cli_has(compose, capsys):
    for name, service in compose["services"].items():
        if not name.startswith("agent-"):
            continue
        assert service["command"][:2] == ["orgagents", "worker"]
    with pytest.raises(SystemExit) as done:
        cli_main(["worker", "--help"])
    assert done.value.code == 0
    assert "agent_id" in capsys.readouterr().out


def test_an_internal_channel_gets_no_bridge(compose):
    assert not [s for s in compose["services"] if s.startswith("channel-")]


def test_infrastructure_says_when_it_is_ready(compose):
    for name in ("state", "nats", "artifacts"):
        assert compose["services"][name].get("healthcheck"), name
    assert "--console-address" not in compose["services"]["artifacts"]["command"]


def test_the_runtime_image_builds_without_a_published_package(generated):
    dockerfile = (generated / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements.txt wheel[s] /wheels/" in dockerfile
    assert "orgagents[langgraph]" in (generated / "requirements.txt").read_text(encoding="utf-8")


def test_the_committed_stack_is_current(generated):
    for name in ("docker-compose.yaml", "Dockerfile", "requirements.txt"):
        assert (COMMITTED / name).read_text(encoding="utf-8") == \
            (generated / name).read_text(encoding="utf-8"), (
                f"examples/ayc/generated/local/{name} is stale; run "
                "`python examples/ayc/local_stack.py generate`")


@pytest.mark.skipif(shutil.which("docker") is None, reason="no docker CLI")
def test_docker_accepts_the_stack_with_the_workstation_overlay():
    out = subprocess.run(
        ["docker", "compose", "-p", "ayc-validation", "-f", "docker-compose.yaml",
         "-f", "overlays/20-ayc-workstation.yaml", "config", "-q", "--no-interpolate"],
        capture_output=True, text=True, cwd=str(COMMITTED))
    assert out.returncode == 0, out.stderr


# -- the pieces that run it ----------------------------------------------------

def test_the_stub_model_reads_calls_and_nothing_else():
    from orgagents.runtime.stub_model import parse_calls

    calls = parse_calls('Please reorder.\ncall fishbowl__stock_check {"sku": "X"}\n'
                        'call cms__content_publishing\nnot a call {"a": 1}')
    assert calls == [{"name": "fishbowl__stock_check", "args": {"sku": "X"}},
                     {"name": "cms__content_publishing", "args": {}}]


def test_an_approval_is_for_one_call_with_those_arguments():
    grants = ApprovalGrants()
    grants.grant("ap", "pay", {"invoice": "A", "amount": 10}, "p_coo")
    assert grants.consume("ap", "pay", {"invoice": "A", "amount": 100}) is None
    assert grants.consume("ap", "pay", {"amount": 10, "invoice": "A"})
    assert grants.consume("ap", "pay", {"invoice": "A", "amount": 10}) is None


def _load_mock():
    spec = importlib.util.spec_from_file_location("ayc_mock", AYC / "mocks" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fishbowl(monkeypatch):
    """The Fishbowl mock, in a thread, on a free port."""
    tokens = {"FISHBOWL_READ_TOKEN": "r", "FISHBOWL_PURCHASING_TOKEN": "p",
              "FISHBOWL_AUDIT_TOKEN": "a", "FISHBOWL_DOCK_TOKEN": "d"}
    for k, v in tokens.items():
        monkeypatch.setenv(k, v)
    mock = _load_mock()
    mock.SYSTEM = "fishbowl"
    mock.load_seed()
    server = ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mcp", mock
    server.shutdown()


def test_a_system_refuses_a_credential_that_does_not_cover_the_tool(fishbowl):
    from orgagents.harness.mcp import HttpMCPClient

    url, mock = fishbowl
    client = HttpMCPClient(url, agent_id="warehouse_agent")
    client.grant(["inventory_adjustment"], "d")          # the dock's token
    out = client.call_tool("inventory_adjustment", {"sku": "AYC-TR-605", "counted": 1})
    assert out["ok"] is False and "does not permit" in out["error"]
    assert mock.STATE["audit"][-1]["agent"] == "warehouse_agent"


def test_the_stub_model_drives_deep_agents_to_the_system_and_the_controls_hold(
        fishbowl, tmp_path):
    pytest.importorskip("deepagents")
    from orgagents.runtime import worker

    url, mock = fishbowl
    target = _target()
    for server in target.servers:
        if server.id == "fishbowl":
            server.url = url
    ir = build_ir(load_spec(str(SPEC)), target="local", binding=target)
    ir_path = tmp_path / "system.ir.json"
    ir_path.write_text(json.dumps(ir.model_dump(mode="json")), encoding="utf-8")
    platform = worker.build_worker("buyer_agent", ir_path=str(ir_path),
                                   db=str(tmp_path / "buyer.db"))
    assert platform.org.agent("buyer_agent").harness.runtime.value == "langchain_deepagents"

    args = {"supplier_id": "SUP-MAL", "sku": "AYC-CH-001", "quantity": 12,
            "unit_cost": 212}
    prompt = ("call fishbowl__stock_check {\"sku\": \"AYC-CH-001\"}\n"
              f"call fishbowl__purchase_ordering {json.dumps(args)}\n"
              "call accounting__invoice_payment {\"invoice_id\": \"X\", \"amount\": 1}")
    first = platform.runtime.run("buyer_agent", prompt)
    by_tool = {c["tool"]: c for c in first.tool_calls}
    assert by_tool["fishbowl__stock_check"]["ok"]
    assert by_tool["fishbowl__stock_check"]["result"]["stock"][0]["sku"] == "AYC-CH-001"
    held = by_tool["fishbowl__purchase_ordering"]["result"]
    assert held["requires_approval"] and held["decision"] == "raise_po"
    assert not by_tool["accounting__invoice_payment"]["ok"]      # never granted

    platform.harness.approvals.grant("buyer_agent", "fishbowl__purchase_ordering",
                                     args, "p_coo")
    second = platform.runtime.run("buyer_agent",
                                  f"call fishbowl__purchase_ordering {json.dumps(args)}")
    po = second.tool_calls[0]
    assert po["ok"], po
    assert po["result"]["purchase_order"]["raised_by"] == "buyer_agent"
    assert mock.STATE["purchase_orders"][-1]["quantity"] == 12   # arguments arrived
