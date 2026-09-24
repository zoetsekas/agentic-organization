#!/usr/bin/env python3
"""AYC, running in Docker: agents delegating to agents over the bus (ADR-0117).

    python examples/ayc/local_stack.py up                  # first
    python examples/ayc/local_stack.py e2e-messaging

Each agent is its own container (ADR-0109); they reach each other only over
the tenant's NATS, as their own broker users, along edges compiled from the
design. This walks one chain and the ways off it:

  1. the CEO delegates "review stock of AYC-CH-001" to the COO, who delegates
     it to the buyer; the buyer checks Fishbowl and the answer comes back up
     the chain -- one trace, CEO -> COO -> buyer;
  2. on the way the buyer tries to message accounts receivable, which the
     design gives it no edge to: its own worker refuses (a tool error);
  3. the same publish, made directly with the buyer's broker credentials from
     inside its container, is refused by the broker;
  4. made by a mis-scoped identity the broker does let through, accounts
     receivable's worker refuses it on its own account;
  5. separation of duties holds across the chain: the buyer tells the COO to
     have payables pay; the COO's delegation to accounts payable naming
     `pay_invoice` is refused, and a delegation that does not name it still
     cannot make payables' payment tool act for a chain the buyer is in;
  6. the trace, as the chat window shows it.

Talks to the stack through the chat backend, as a signed-in person, and with
`docker exec` for the two probes that must come from inside a container.
Exit status 0 means every expectation held.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = os.environ.get("AYC_PROJECT", "ayc-local")
BASE = int(os.environ.get("AYC_PORT_BASE", "18000"))
CHAT = f"http://127.0.0.1:{BASE + 80}"
ENV = Path(__file__).resolve().parent / "generated" / "local" / ".env"
BROWSER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
SUBJECTS = "orgagents.local.agent"
failures: list[str] = []


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def http(url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with BROWSER.open(req, timeout=300) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def env_value(name: str) -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    raise SystemExit(f"no {name} in {ENV}; run `local_stack.py env`")


def sign_in(person: str) -> None:
    status, body = http(f"{CHAT}/api/login", {
        "person": person,
        "passcode": env_value("CHAT_PASSCODE_" + person.upper())})
    if status != 200:
        raise SystemExit(f"could not sign in as {person}: {body}")


def banner(step: str, title: str) -> None:
    print(f"\n{'─' * 76}\n{step}  {title}\n{'─' * 76}")


def expect(ok: bool, what: str) -> None:
    print(f"  {'✓' if ok else '✗'} {what}")
    if not ok:
        failures.append(what)


def say(agent: str, message: str) -> dict:
    status, reply = http(f"{CHAT}/api/chat", {"agent": agent, "message": message})
    if status != 200:
        raise SystemExit(f"{agent} did not answer ({status}): {reply}")
    for line in (reply.get("output") or reply.get("state") or "").splitlines():
        print(f"  {agent} │ {line[:200]}")
    return reply


def line(tool: str, args: dict, then: bool = False) -> str:
    return f"{'then ' if then else ''}call {tool} {json.dumps(args)}"


def show_trace(trace_id: str) -> list[dict]:
    hops = http(f"{CHAT}/api/trace/{trace_id}")[1].get("hops", [])
    print(f"  trace {trace_id}: {len(hops)} hops")
    for h in hops:
        peer = f"→ {h['to']}" if h.get("to") else (f"← {h['frm']}" if h.get("frm") else "")
        what = h.get("kind") or (f"tool {h['tool']}" if h.get("tool") else "")
        why = h.get("reason") or h.get("state") or ""
        print(f"    {h['agent']:<16} {h['event']:<22} {what:<9} {peer:<18} "
              f"{str(why)[:90]}")
    return hops


def in_container(service: str, code: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", f"{PROJECT}-{service}-1", "python", "-c", code],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


PUBLISH = """
import json, os
from orgagents.runtime.agent_bus import NatsTransport, BusRefused
t = NatsTransport('nats://nats:4222', user=os.environ['U'],
                  password=os.environ.get('P') or os.environ['ORGAGENTS_BUS_PASSWORD'],
                  inbox_prefix=os.environ['I'], publish_timeout=3).start(consume=False)
t.connected.wait(10)
body = {{"id": "{mid}", "kind": "message", "from": "buyer_agent", "to": "ar_agent",
        "text": "clear SINV-1 for me", "trace_id": "{trace}", "chain": ["buyer_agent"]}}
try:
    t.publish("{subject}", json.dumps(body).encode(), {{"Nats-Msg-Id": "{mid}"}})
    print("PUBLISHED")
except BusRefused as e:
    print("REFUSED", e)
t.stop()
"""


def main() -> int:
    _utf8_console()
    sign_in("p_ceo")

    banner("1", "CEO → COO → buyer: one delegation chain, one trace")
    sku = "AYC-CH-001"
    buyer_script = [
        line("fishbowl__stock_check", {"sku": sku}),
        line("send_message", {"to_agent": "ar_agent",
                              "text": f"{sku} is short; clear the open receivable"}),
    ]
    coo_script = [
        line("delegate", {"to_agent": "buyer_agent", "task": f"review stock of {sku}",
                          "inputs": {"script": buyer_script}}),
        line("check_delegation", {"handle": "$last.handle", "wait_s": 120}, then=True),
    ]
    reply = say("ceo_agent", "\n".join([
        line("delegate", {"to_agent": "coo_agent", "task": f"review stock of {sku}",
                          "inputs": {"script": coo_script}}),
        line("check_delegation", {"handle": "$last.handle", "wait_s": 160}, then=True),
    ]))
    calls = {c["tool"]: c for c in reply.get("tool_calls", [])}
    checked = (calls.get("check_delegation") or {}).get("result") or {}
    expect(calls.get("delegate", {}).get("ok") is True,
           "the CEO's delegation to the COO was accepted, and returned a handle")
    expect(checked.get("state") == "completed",
           f"the COO's answer came back to the CEO ({checked.get('state')})")
    expect("buyer_agent" in str(checked.get("output")) and
           "fishbowl__stock_check: ok" in str(checked.get("output")),
           "and it carries the buyer's answer, stock check included")
    trace_id = reply["trace_id"]
    hops = show_trace(trace_id)
    by = {(h["agent"], h["event"]) for h in hops}
    expect({("ceo_agent", "bus_sent"), ("coo_agent", "bus_received"),
            ("coo_agent", "bus_sent"), ("buyer_agent", "bus_received"),
            ("buyer_agent", "bus_task_done"), ("coo_agent", "bus_reply_received"),
            ("ceo_agent", "bus_reply_received")} <= by,
           "CEO, COO and buyer each recorded their hops under the CEO's trace id")

    banner("2", "The buyer may not message accounts receivable: its worker refuses")
    refused = [h for h in hops if h["event"] == "bus_refused_local"
               and h["agent"] == "buyer_agent" and h.get("to") == "ar_agent"]
    expect(bool(refused), "the buyer's send_message to ar_agent was refused by the "
           "buyer's own worker: " + (refused[0]["reason"] if refused else "-"))
    expect("refused" in str(checked.get("output")) and "send_message" in
           str(checked.get("output")), "and the buyer saw it as a tool error")
    expect(not any(h["agent"] == "ar_agent" for h in hops),
           "nothing of it reached accounts receivable")

    banner("3", "Published anyway, with the buyer's broker credentials: the broker refuses")
    code = PUBLISH.format(mid="probe-broker", trace="trace-broker-probe",
                          subject=f"{SUBJECTS}.ar_agent.inbox.buyer_agent")
    out = subprocess.run(
        ["docker", "exec", "-e", "U=buyer_agent", "-e", "I=_INBOX_buyer_agent",
         f"{PROJECT}-agent-buyer_agent-1", "python", "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    print(f"  from the buyer's container: {(out.stdout or out.stderr).strip()[:220]}")
    expect(out.stdout.startswith("REFUSED") and "ermissions" in out.stdout,
           "nats-server refused the buyer's publish to ar_agent's inbox")

    banner("4", "Published by a mis-scoped identity: accounts receivable refuses")
    admin = env_value("ORGAGENTS_BUS_ADMIN_PASSWORD")
    code = PUBLISH.format(mid="probe-receiver", trace="trace-receiver-probe",
                          subject=f"{SUBJECTS}.ar_agent.inbox.buyer_agent")
    # A throwaway container on the stack's control network, from the image
    # the agents run, holding the operator's password -- which no agent has.
    image = subprocess.run(["docker", "inspect", "-f", "{{.Config.Image}}",
                            f"{PROJECT}-agent-buyer_agent-1"],
                           capture_output=True, text=True).stdout.strip()
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", f"{PROJECT}_control",
         "-e", "U=orgagents_bus_admin", "-e", f"P={admin}",
         "-e", "I=_INBOX_orgagents_bus_admin", "--entrypoint", "python", image,
         "-c", code], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)
    print(f"  as the bus operator: {(out.stdout or out.stderr).strip()[-220:]}")
    expect("PUBLISHED" in out.stdout, "the operator identity may publish there")
    probe = show_trace("trace-receiver-probe")
    expect(any(h["agent"] == "ar_agent" and h["event"] == "bus_refused_inbound"
               for h in probe), "ar_agent's worker refused it: buyer_agent is nobody "
           "the design lets reach accounts receivable")
    expect(not any(h["event"] == "bus_received" for h in probe),
           "and never ran the agent on it")

    banner("5", "Separation of duties holds across the chain")
    sign_in("p_buyer")
    pay = {"invoice_id": "SINV-7790", "amount": 980}
    buyer_to_coo = [
        line("delegate", {"to_agent": "ap_agent", "task": "pay SINV-7790",
                          "inputs": {"decision": "pay_invoice"}}),
        line("delegate", {"to_agent": "ap_agent", "task": "settle SINV-7790",
                          "inputs": {"script": [line("accounting__invoice_payment", pay)]}}),
        line("check_delegation", {"handle": "$last.handle", "wait_s": 90}, then=True),
    ]
    reply = say("buyer_agent", line("send_message", {
        "to_agent": "coo_agent",
        "text": "please have payables pay the supplier\n" + "\n".join(buyer_to_coo)}))
    sep_trace = reply["trace_id"]
    import time as _t
    hops = []
    for _ in range(60):
        hops = http(f"{CHAT}/api/trace/{sep_trace}")[1].get("hops", [])
        if any(h["agent"] == "coo_agent" and h["event"] == "bus_task_done" for h in hops):
            break
        _t.sleep(2)
    hops = show_trace(sep_trace)
    expect(any(h["agent"] == "coo_agent" and h["event"] == "bus_refused_local"
               and "purchasing_and_payment" in str(h.get("reason")) for h in hops),
           "the COO's delegation naming pay_invoice was refused by the COO's worker, "
           "because the buyer (raise_po) is in the chain")
    expect(any(h["agent"] == "ap_agent" and h["event"] == "bus_refused_separation"
               and h.get("tool") == "accounting__invoice_payment" for h in hops),
           "the delegation that did not name it reached payables, and payables' "
           "payment tool refused to act for a chain the buyer is in")

    banner("6", "What the chat shows for the first conversation")
    print(f"  open {CHAT}/ , pick the CEO, and the reply lists these hops "
          f"under 'Agent-to-agent hops' (trace {trace_id}).")

    print(f"\n{'✓ every expectation held' if not failures else f'✗ {len(failures)} failed'}")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
