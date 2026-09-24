"""Agent-to-agent messaging and delegation over the bus (ADR-0117).

Unit tests, no broker: the edges compiled from the AYC design, the broker
permissions rendered from the same edges, and both sides of the checks --
sender and receiver -- over a fake transport that also enforces the broker's
publish permissions, so all three refusals are exercised. Separation of duties
across a delegation chain. The stub model's multi-turn scripts.

`test_agent_bus_nats.py` runs the same against a real nats-server.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from orgagents.compiler import links as L
from orgagents.compiler.ir import build_ir
from orgagents.runtime.agent_bus import (AgentMessenger, BusRefused, HopContext,
                                         LinkPolicy, render_task, separation_guard)
from orgagents.spec import load_binding, load_spec

ROOT = Path(__file__).resolve().parents[1]
AYC = ROOT / "examples" / "ayc"


@pytest.fixture(scope="module")
def ir():
    binding = load_binding(str(AYC / "ayc.local.binding.yaml"))
    target = next(t for t in binding.targets if t.target == "local")
    return build_ir(load_spec(str(AYC / "ayc.system.yaml")), target="local", binding=target)


@pytest.fixture(scope="module")
def links(ir):
    return L.agent_links(ir)


def kinds(links, src, dst):
    edge = links[src]["outbound"].get(dst)
    return sorted({(g["kind"], g["via"]) for g in edge["grants"]}) if edge else []


# -- the edges come from the design, and only from it ---------------------------

def test_a_leader_delegates_to_its_members_and_sub_team_leaders(links):
    assert kinds(links, "coo_agent", "buyer_agent") == [("delegate", "leads:operations")]
    assert kinds(links, "ceo_agent", "coo_agent") == [("delegate", "leads:ayc")]
    assert kinds(links, "ap_agent", "ar_agent") == [("delegate", "leads:finance")]


def test_a_member_may_message_its_leader_and_never_delegate_up(links):
    assert kinds(links, "buyer_agent", "coo_agent") == [("message", "reports_to")]
    policy = LinkPolicy(links["buyer_agent"])
    assert policy.may_send("coo_agent", "message")[0]
    ok, why = policy.may_send("coo_agent", "delegate")
    assert not ok and "not delegate" in why


def test_flows_give_exactly_their_kind(links):
    # buyer -> ap is a notify flow: may inform, may not hand work over.
    assert kinds(links, "buyer_agent", "ap_agent") == [("message", "flow:notify")]
    assert kinds(links, "ecommerce_agent", "inventory_agent")[0] == ("delegate",
                                                                     "mission:peak_season_readiness")
    assert ("message", "flow:consult") in kinds(links, "ecommerce_agent", "inventory_agent")


def test_unit_links_give_messages_and_partners_with_gives_nothing(links):
    assert kinds(links, "ap_agent", "buyer_agent") == [
        ("message", "unit_link:oversees:finance->purchasing")]
    assert kinds(links, "ecommerce_agent", "software_agent") == [
        ("message", "unit_link:serves:it->ecommerce")]
    # ecommerce partners_with marketing: inert. What there is comes from elsewhere.
    vias = [v for _, v in kinds(links, "ecommerce_agent", "marketing_agent")]
    assert vias and not any(v.startswith("unit_link") for v in vias)
    assert not any(v.startswith("unit_link:partners_with")
                   for a in links.values() for e in a["outbound"].values()
                   for v in (g["via"] for g in e["grants"]))


def test_what_the_design_does_not_say_is_not_an_edge(links):
    assert "ar_agent" not in links["buyer_agent"]["outbound"]
    assert "buyer_agent" not in links["ar_agent"]["inbound"]
    assert links["ar_agent"]["outbound"] == {"ap_agent": {"grants": [
        {"kind": "message", "via": "reports_to"}]}}


def test_inbound_is_the_mirror_of_outbound(links):
    for src, cfg in links.items():
        for dst, edge in cfg["outbound"].items():
            assert links[dst]["inbound"][src] == edge


def test_a_mission_edge_carries_its_window(links):
    grant = links["inventory_agent"]["outbound"]["ecommerce_agent"]["grants"][0]
    assert grant["via"] == "mission:peak_season_readiness"
    assert (grant["starts_on"], grant["ends_on"]) == ("2026-10-01", "2026-12-15")
    policy = LinkPolicy(links["inventory_agent"])
    assert not policy.may_send("ecommerce_agent", "delegate", on=date(2026, 9, 1))[0]
    assert policy.may_send("ecommerce_agent", "delegate", on=date(2026, 11, 1))[0]
    assert not policy.may_send("ecommerce_agent", "delegate", on=date(2027, 1, 1))[0]


def test_the_config_holds_only_separated_holdings_not_the_design(links):
    cfg = links["coo_agent"]
    assert set(cfg) == {"agent", "subject_prefix", "stream", "consumer", "user",
                        "inbox_prefix", "max_depth", "outbound", "inbound",
                        "separations", "separated_holdings"}
    assert cfg["separated_holdings"]["buyer_agent"] == ["raise_po"]
    assert "coo_agent" not in cfg["separated_holdings"]


def test_the_generated_manifest_carries_the_links(tmp_path, ir):
    from orgagents.compiler.targets.local import LocalTarget

    files = {f.path: f.content for f in LocalTarget().generate(ir)}
    manifest = json.loads(files["agents/buyer_agent.json"])
    assert manifest["links"]["agent"] == "buyer_agent"
    assert "ar_agent" not in manifest["links"]["outbound"]
    conf = files["nats/nats.conf"]
    assert "$ORGAGENTS_BUS_PASSWORD_BUYER_AGENT" in conf and "password: \"" not in conf


# -- the broker is told the same thing -------------------------------------------

def test_broker_permissions_are_the_edges(ir, links):
    perms = L.nats_permissions(ir, "buyer_agent", links)
    pub = set(perms["publish"])
    assert "orgagents.local.agent.coo_agent.inbox.buyer_agent" in pub
    assert "orgagents.local.agent.ap_agent.inbox.buyer_agent" in pub
    assert not any(".ar_agent." in s for s in pub)
    # As itself only: never another agent's name as the sender.
    assert all(s.endswith(".buyer_agent") for s in pub if ".inbox." in s)
    # Replies to those who may reach it: coo (leads) and ap (oversees).
    assert "orgagents.local.agent.coo_agent.reply.buyer_agent" in pub
    assert perms["subscribe"] == ["_INBOX_buyer_agent.>"]
    assert not any(s.startswith("orgagents.") for s in perms["subscribe"])


# -- both sides, over a fake transport that enforces the broker's permissions ----

def _match(pattern: str, subject: str) -> bool:
    p, s = pattern.split("."), subject.split(".")
    for i, tok in enumerate(p):
        if tok == ">":
            return len(s) > i
        if i >= len(s) or (tok != "*" and tok != s[i]):
            return False
    return len(p) == len(s)


class FakeBroker:
    """Routes a publish to the addressed agent's messenger, synchronously,
    and refuses one its user may not publish -- as nats-server would."""

    def __init__(self, ir, links):
        self.ir, self.links = ir, links
        self.messengers: dict[str, AgentMessenger] = {}
        self.log: list[tuple[str, str]] = []

    def transport(self, user: str):
        broker = self
        allowed = None if user == "admin" else L.nats_permissions(
            self.ir, user, self.links)["publish"]

        class T:
            def publish(self, subject, data, headers):
                if allowed is not None and not any(_match(a, subject) for a in allowed):
                    raise BusRefused(f"Permissions Violation for Publish to \"{subject}\"")
                broker.log.append((user, subject))
                to = subject.split(".")[3]
                target = broker.messengers.get(to)
                if target is not None:
                    target.on_delivery(subject, data, headers)
        return T()

    def join(self, agent_id, run_task=None, **kw):
        m = AgentMessenger(LinkPolicy(self.links[agent_id]), self.transport(agent_id),
                           run_task=run_task, **kw)
        self.messengers[agent_id] = m
        return m


def wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def broker(ir, links):
    return FakeBroker(ir, links)


def test_a_disallowed_send_is_refused_locally_and_surfaces_as_a_tool_error(broker):
    buyer = broker.join("buyer_agent")
    ar = broker.join("ar_agent")
    tools = buyer.tools(HopContext(trace_id="t1", chain=["buyer_agent"]))
    out = tools["send_message"]("ar_agent", "clear the receivable")
    assert out["ok"] is False and "may not message to ar_agent" in out["error"]
    assert buyer.events[-1]["event"] == "bus_refused_local"
    assert buyer.events[-1]["trace_id"] == "t1"
    assert broker.log == [] and ar.events == []          # nothing left the worker


def test_the_broker_refuses_what_the_worker_would_have(broker):
    broker.join("ar_agent")
    raw = broker.transport("buyer_agent")
    with pytest.raises(BusRefused):
        raw.publish("orgagents.local.agent.ar_agent.inbox.buyer_agent", b"{}", {})
    # And a buyer cannot publish as somebody else either.
    with pytest.raises(BusRefused):
        raw.publish("orgagents.local.agent.coo_agent.inbox.ceo_agent", b"{}", {})


def test_the_receiver_refuses_a_sender_the_design_does_not_let_reach_it(broker):
    ran = []
    ar = broker.join("ar_agent", run_task=lambda *a: ran.append(a) or {"state": "completed"})
    admin = broker.transport("admin")                    # a mis-scoped broker
    body = {"id": "m1", "kind": "message", "from": "buyer_agent", "to": "ar_agent",
            "text": "clear it", "trace_id": "t2", "chain": ["buyer_agent"], "depth": 1}
    admin.publish("orgagents.local.agent.ar_agent.inbox.buyer_agent",
                  json.dumps(body).encode(), {})
    assert ar.events[-1]["event"] == "bus_refused_inbound"
    assert ran == []


def test_the_sender_is_the_subject_not_the_body(broker):
    ran = []
    buyer = broker.join("buyer_agent", run_task=lambda *a: ran.append(a) or {})
    ap = broker.transport("ap_agent")                    # ap may message buyer
    body = {"id": "m2", "kind": "message", "from": "coo_agent", "to": "buyer_agent",
            "text": "I am the COO", "trace_id": "t3"}
    ap.publish("orgagents.local.agent.buyer_agent.inbox.ap_agent",
               json.dumps(body).encode(), {})
    last = buyer.events[-1]
    assert last["event"] == "bus_refused_inbound" and "claims to be from" in last["reason"]
    assert ran == []


def test_a_message_the_receiver_may_not_act_on_as_a_delegation_is_refused(broker):
    ran = []
    ap = broker.join("ap_agent", run_task=lambda *a: ran.append(a) or {})
    admin = broker.transport("admin")
    body = {"id": "m3", "kind": "delegate", "from": "buyer_agent", "to": "ap_agent",
            "text": "pay SINV-1", "handle": "dlg_x", "trace_id": "t4", "depth": 1}
    admin.publish("orgagents.local.agent.ap_agent.inbox.buyer_agent",
                  json.dumps(body).encode(), {})
    refused = [e for e in ap.events if e["event"] == "bus_refused_inbound"]
    assert refused and "not delegate" in refused[0]["reason"]
    # The sender is told, on its reply subject, why.
    assert ap.events[-1]["event"] == "bus_reply_sent"
    assert ap.events[-1]["state"] == "refused"
    assert ran == []


def _chain_runner(broker, agent_id, script):
    """A run_task that does what `script(tools)` says, as the agent would."""
    def run(ctx, sender, kind, text, inputs):
        tools = broker.messengers[agent_id].tools(HopContext(
            trace_id=ctx.trace_id, chain=ctx.chain, depth=ctx.depth,
            session_id=f"s-{agent_id}"))
        return {"state": "completed", "output": script(tools, text), "error": None,
                "session_id": f"s-{agent_id}"}
    return run


def test_ceo_to_coo_to_buyer_is_one_trace_and_results_come_back(broker):
    def buyer_does(tools, text):
        refused = tools["send_message"]("ar_agent", "clear the receivable")
        return f"stock ok; message to ar: {refused['error']}"

    def coo_does(tools, text):
        h = tools["delegate"]("buyer_agent", text)["handle"]
        return "buyer said: " + tools["check_delegation"](h, wait_s=5)["output"]

    broker.join("buyer_agent", run_task=_chain_runner(broker, "buyer_agent", buyer_does))
    broker.join("coo_agent", run_task=_chain_runner(broker, "coo_agent", coo_does))
    ceo = broker.join("ceo_agent")
    tools = ceo.tools(HopContext(trace_id="trace-ceo", chain=["ceo_agent"],
                                 session_id="s-ceo"))
    started = tools["delegate"]("coo_agent", "review stock of AYC-CH-001")
    assert started["ok"] and started["handle"].startswith("dlg_")
    done = tools["check_delegation"](started["handle"], wait_s=10)
    assert done["ok"] and done["state"] == "completed", done
    assert "buyer said: stock ok" in done["output"]
    assert "may not message to ar_agent" in done["output"]
    for agent in ("ceo_agent", "coo_agent", "buyer_agent"):
        events = broker.messengers[agent].trace("trace-ceo")
        assert events, agent
    buyer_received = next(e for e in broker.messengers["buyer_agent"].events
                          if e["event"] == "bus_received")
    assert buyer_received["chain"] == ["ceo_agent", "coo_agent"]
    assert buyer_received["depth"] == 2
    refused = broker.messengers["buyer_agent"].trace("trace-ceo")
    assert any(e["event"] == "bus_refused_local" and e["to"] == "ar_agent" for e in refused)


def test_check_delegation_settles_a_handle_that_never_answers(broker):
    broker.messengers.clear()
    coo = broker.join("coo_agent", handle_deadline_s=0.05)
    tools = coo.tools(HopContext(trace_id="t5", chain=["coo_agent"]))
    h = tools["delegate"]("buyer_agent", "nobody is listening")["handle"]
    assert tools["check_delegation"](h)["state"] == "pending"
    time.sleep(0.1)
    out = tools["check_delegation"](h)
    assert out["state"] == "failed" and "no answer" in out["error"]
    assert tools["check_delegation"]("dlg_nope")["ok"] is False


def test_a_reply_for_a_handle_it_did_not_send_is_refused(broker):
    coo = broker.join("coo_agent")
    buyer = broker.transport("buyer_agent")
    body = {"id": "r1", "kind": "reply", "from": "buyer_agent", "handle": "dlg_forged",
            "state": "completed", "output": "paid", "trace_id": "t6"}
    buyer.publish("orgagents.local.agent.coo_agent.reply.buyer_agent",
                  json.dumps(body).encode(), {})
    assert coo.events[-1]["event"] == "bus_refused_inbound"


# -- separation of duties across delegation --------------------------------------

def test_a_delegation_for_a_separated_decision_is_refused_by_the_sender(broker):
    coo = broker.join("coo_agent")
    # The COO is acting on the buyer's message: the buyer is in the chain.
    tools = coo.tools(HopContext(trace_id="t7", chain=["buyer_agent", "coo_agent"]))
    out = tools["delegate"]("ap_agent", "pay SINV-1", {"decision": "pay_invoice"})
    assert out["ok"] is False and "purchasing_and_payment" in out["error"]
    # Commissioned by the COO alone, the same delegation goes.
    clean = coo.tools(HopContext(trace_id="t8", chain=["coo_agent"]))
    assert clean["delegate"]("ap_agent", "pay SINV-1", {"decision": "pay_invoice"})["ok"]


def test_and_by_the_receiver_whatever_the_sender_did(broker):
    ran = []
    ap = broker.join("ap_agent", run_task=lambda *a: ran.append(a) or {"state": "completed"})
    coo = broker.transport("coo_agent")                  # a sender that skipped its check
    body = {"id": "m9", "kind": "delegate", "from": "coo_agent", "to": "ap_agent",
            "text": "pay SINV-1", "handle": "dlg_9", "decision": "pay_invoice",
            "chain": ["buyer_agent", "coo_agent"], "trace_id": "t9", "depth": 2}
    coo.publish("orgagents.local.agent.ap_agent.inbox.coo_agent",
                json.dumps(body).encode(), {})
    refused = [e for e in ap.events if e["event"] == "bus_refused_inbound"]
    assert refused and "purchasing_and_payment" in refused[0]["reason"]
    assert ran == []


def test_a_tool_constituting_a_separated_decision_is_refused_for_that_chain(links):
    policy = LinkPolicy(links["ap_agent"])
    calls = []
    tools = {"accounting__invoice_payment": lambda **k: calls.append(k) or {"ok": True},
             "accounting__ledger_read": lambda **k: {"ok": True}}
    decisions = {"accounting__invoice_payment": "pay_invoice"}
    ctx = HopContext(trace_id="t10", chain=["buyer_agent", "coo_agent", "ap_agent"])
    guarded = separation_guard(policy, ctx, decisions.get, tools)
    out = guarded["accounting__invoice_payment"](invoice_id="X", amount=1)
    assert out["ok"] is False and "purchasing_and_payment" in out["error"]
    assert calls == []
    assert guarded["accounting__ledger_read"] is tools["accounting__ledger_read"]
    # Run for the COO alone, nothing is replaced.
    plain = separation_guard(policy, HopContext(trace_id="t", chain=["coo_agent", "ap_agent"]),
                             decisions.get, tools)
    assert plain == tools


def test_depth_is_bounded(broker, links):
    coo = broker.join("coo_agent")
    deep = HopContext(trace_id="t11", chain=["coo_agent"],
                      depth=links["coo_agent"]["max_depth"])
    out = coo.tools(deep)["delegate"]("buyer_agent", "again")
    assert out["ok"] is False and "depth" in out["error"]


def test_a_received_task_reads_as_one(links):
    text = render_task("delegate", "coo_agent", "review stock",
                       {"script": ["call x {}"], "sku": "AYC-CH-001"})
    assert text.splitlines() == ["[task delegated from coo_agent] review stock",
                                 "call x {}", 'Inputs: {"sku": "AYC-CH-001"}']


# -- the stub model scripts delegation -------------------------------------------

def test_the_stub_model_reads_later_turns_and_substitutes_the_last_result():
    from orgagents.runtime.stub_model import parse_stages, substitute

    stages = parse_stages('call delegate {"to_agent": "coo_agent", "task": "t"}\n'
                          'then call check_delegation {"handle": "$last.handle", "wait_s": 5}\n'
                          'call send_message {"to_agent": "x", "text": "y"}')
    assert [[c["name"] for c in s] for s in stages] == [
        ["delegate"], ["check_delegation", "send_message"]]
    assert substitute(stages[1][0]["args"], {"handle": "dlg_1"}) == {
        "handle": "dlg_1", "wait_s": 5}


def test_the_stub_model_runs_a_two_turn_script():
    pytest.importorskip("langchain_core")
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from orgagents.runtime.stub_model import stub_chat_model

    model = stub_chat_model("coo")
    human = HumanMessage(content='call delegate {"to_agent": "b", "task": "t"}\n'
                                 'then call check_delegation {"handle": "$last.handle"}')
    first = model._reply([human])
    assert first.tool_calls[0]["name"] == "delegate"
    r1 = ToolMessage(content=json.dumps({"ok": True, "handle": "dlg_7", "state": "pending"}),
                     tool_call_id=first.tool_calls[0]["id"], name="delegate")
    second = model._reply([human, first, r1])
    assert second.tool_calls[0]["args"] == {"handle": "dlg_7"}
    r2 = ToolMessage(content=json.dumps({"ok": True, "handle": "dlg_7", "state": "completed",
                                         "to": "b", "output": "done"}),
                     tool_call_id=second.tool_calls[0]["id"], name="check_delegation")
    final = model._reply([human, first, r1, second, r2])
    assert not final.tool_calls
    assert "check_delegation: ok [dlg_7 completed] <- b: done" in final.content


# -- the worker wires it in ------------------------------------------------------

def test_the_worker_replaces_in_process_delegation_with_the_bus(tmp_path, ir, links):
    from orgagents.runtime import worker

    ir_path = tmp_path / "system.ir.json"
    ir_path.write_text(json.dumps(ir.model_dump(mode="json")), encoding="utf-8")
    platform = worker.build_worker("coo_agent", ir_path=str(ir_path),
                                   db=str(tmp_path / "coo.db"))
    broker = FakeBroker(ir, links)
    messenger = worker.attach_bus(platform, "coo_agent", links["coo_agent"],
                                  transport=broker.transport("coo_agent"),
                                  audit=lambda *a: None)
    hook = platform.runtime.tool_hooks[-1]
    agent = platform.org.agent("coo_agent")
    tools = hook(agent, "sess-1", {"delegate": None, "assign": None, "check": None,
                                   "gather": None, "read_inbox": None,
                                   "send_message": None, "other": "x"})
    assert set(tools) == {"delegate", "send_message", "check_delegation", "other"}
    out = tools["delegate"]("buyer_agent", "review stock")
    assert out["ok"] and broker.log[-1] == (
        "coo_agent", "orgagents.local.agent.buyer_agent.inbox.coo_agent")
    assert messenger.events[-1]["trace_id"] == "sess-1"


def test_load_links_reads_only_this_agents_manifest(tmp_path, links):
    from orgagents.runtime.worker import load_links

    path = tmp_path / "buyer_agent.json"
    path.write_text(json.dumps({"links": links["buyer_agent"]}), encoding="utf-8")
    assert load_links("buyer_agent", str(path))["agent"] == "buyer_agent"
    assert load_links("coo_agent", str(path)) is None


def test_the_messenger_is_thread_safe_under_concurrent_delegations(broker):
    broker.messengers.clear()
    broker.join("buyer_agent", run_task=lambda ctx, *a: {"state": "completed",
                                                           "output": ctx.handle})
    coo = broker.join("coo_agent")
    tools = coo.tools(HopContext(trace_id="t12", chain=["coo_agent"]))
    handles = []
    threads = [threading.Thread(target=lambda: handles.append(
        tools["delegate"]("buyer_agent", "x")["handle"])) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert wait_for(lambda: all(tools["check_delegation"](h)["state"] == "completed"
                                for h in handles))
    assert all(tools["check_delegation"](h)["output"] == h for h in handles)
