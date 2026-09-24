#!/usr/bin/env python3
"""AYC, running in Docker: purchase to pay, with the controls in the way.

    python examples/ayc/local_stack.py up     # first
    python examples/ayc/end_to_end_local.py

`end_to_end_operations.py` runs AYC's coordination in one process. This runs
against the *stack* — twelve agent containers on LangChain deep agents with
the stub model, five mock systems, each on its own network — and walks one
purchase from reorder to payment, the way ADR-0109 says it should hold:

  1. every agent's worker answers;
  2. the buyer checks stock in Fishbowl and raises a purchase order — which
     stops for approval, because the design says raising a PO is supervised;
     the COO (the buyer's named approver) releases that one call, and it goes;
  3. the buyer receives the goods, again under approval;
  4. the supplier's invoice arrives in accounting;
  5. the buyer tries to pay it and cannot: it holds no payment tool, and its
     container cannot even route to the accounting system;
  6. accounts payable pays an invoice with no purchase order behind it, and
     the accounting system's three-way match refuses — the enforcement the
     spec names as authoritative for `purchasing_and_payment`;
  7. accounts payable pays the matched invoice, under approval; it goes;
  8. accounts payable tries to raise a purchase order and cannot;
  9. the dock's Fishbowl credential cannot adjust a count
     (`inventory_and_fulfillment`), even called directly from the warehouse
     container, because Fishbowl itself tells the two hands apart;
 10. somebody the design does not name tries to approve, and is refused;
 11. and the doors are shut (ADR-0114): nobody approves without signing in, a
     worker answers nobody without its service token, a forged release is
     refused, and every grant and its use are in the worker's audit log.

Approvals are made as a signed-in person: this script signs in to the chat
with the passcode `local_stack.py` generated into generated/local/.env.

Every step talks to the running stack through the chat backend (the same
route a person in the chat window takes) or reads a mock's state; nothing is
simulated in this process. Exit status 0 means every expectation held.
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


def _utf8_console() -> None:
    """Print box-drawing and arrows on a Windows console without PYTHONUTF8=1.

    A cp1252 console cannot encode them, and a traceback after the work is
    done hides whether it passed. Reconfigured, not replaced, so a stream that
    is not a text console (a pipe under a test runner) is left as it is.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

#: Which copy of the stack: `local_stack.py --project/--port-base` sets these.
PROJECT = os.environ.get("AYC_PROJECT", "ayc-local")
BASE = int(os.environ.get("AYC_PORT_BASE", "18000"))
CHAT = f"http://127.0.0.1:{BASE + 80}"
ENV = Path(__file__).resolve().parent / "generated" / "local" / ".env"
#: One browser: the chat's session cookie lives here.
BROWSER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
MOCK = {"shopify": BASE + 101, "fishbowl": BASE + 102, "accounting": BASE + 103,
        "cms": BASE + 104, "deploy_pipeline": BASE + 105}

failures: list[str] = []


def http(url: str, body: dict | None = None, opener=None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with (opener or BROWSER).open(req, timeout=180) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def passcode(person: str) -> str:
    name = "CHAT_PASSCODE_" + "".join(c if c.isalnum() else "_" for c in person).upper()
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    raise SystemExit(f"no {name} in {ENV}; run `local_stack.py env`")


def sign_in(person: str) -> None:
    status, body = http(f"{CHAT}/api/login", {"person": person,
                                              "passcode": passcode(person)})
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
    print(f"  {agent} → {reply['output'].splitlines()[0] if reply['output'] else reply['state']}")
    for c in reply.get("tool_calls", []):
        r = c["result"]
        verdict = "ok" if c["ok"] else ("needs approval" if isinstance(r, dict)
                                        and r.get("requires_approval") else "refused")
        why = "" if c["ok"] else f": {r.get('error') if isinstance(r, dict) else r}"
        print(f"      {c['tool']} {json.dumps(c['arguments'])} — {verdict}{why[:160]}")
    return reply


def call(agent: str, tool: str, args: dict) -> dict:
    """One tool call; the first entry in the reply's tool calls."""
    reply = say(agent, f"call {tool} {json.dumps(args)}")
    calls = reply.get("tool_calls") or [{}]
    return calls[0]


def approve(agent: str, tool: str, args: dict, approver: str) -> tuple[int, dict]:
    """Sign in as the approver, then release: the approver is who is signed
    in, never a name in the request (ADR-0114)."""
    sign_in(approver)
    status, body = http(f"{CHAT}/api/approve", {"agent": agent, "tool": tool,
                                                 "arguments": args})
    print(f"  approval by {approver} for {agent}.{tool}: "
          f"{'granted' if status == 200 else body.get('error')}")
    return status, body


def supervised(agent: str, tool: str, args: dict, approver: str) -> dict:
    """Ask, be stopped for approval, get it, ask again."""
    first = call(agent, tool, args)
    stopped = isinstance(first.get("result"), dict) and first["result"].get("requires_approval")
    expect(bool(stopped), f"{agent}: {tool} stopped for a person's approval first")
    approve(agent, tool, args, approver)
    return call(agent, tool, args)


def state(system: str) -> dict:
    return http(f"http://127.0.0.1:{MOCK[system]}/state")[1]


def in_container(agent: str, code: str, service: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", f"{PROJECT}-{service or 'agent-' + agent}-1", "python", "-c", code],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60)


def from_chat(path: str, body: dict | None, token: str) -> int:
    """Call ap_agent's worker from the chat container, the one place on the
    control network that can reach it, with the token given ('' = none)."""
    code = (
        "import json, os, urllib.request, urllib.error\n"
        f"body = {json.dumps(json.dumps(body)) if body is not None else 'None'}\n"
        f"tok = {token!r}\n"
        "tok = os.environ[tok[1:]] if tok.startswith('$') else tok\n"
        "h = {'Content-Type': 'application/json'}\n"
        "if tok: h['Authorization'] = 'Bearer ' + tok\n"
        f"r = urllib.request.Request('http://agent-ap_agent:8000{path}', "
        "data=body.encode() if body else None, headers=h, "
        "method='POST' if body else 'GET')\n"
        "try:\n"
        "    with urllib.request.urlopen(r, timeout=30) as resp: print(resp.status); "
        "print(resp.read().decode())\n"
        "except urllib.error.HTTPError as e: print(e.code)\n")
    out = in_container("", code, service="chat")
    lines = out.stdout.strip().splitlines()
    from_chat.last = "\n".join(lines[1:])
    return int(lines[0]) if lines and lines[0].isdigit() else -1


def main() -> int:
    _utf8_console()
    banner("0", "The mock systems go back to their seed; sign in")
    sign_in("p_buyer")
    print("  signed in to the chat as p_buyer")
    for system, port in MOCK.items():
        code, _ = http(f"http://127.0.0.1:{port}/admin/reset", {})
        expect(code == 200, f"{system} reset")

    banner("1", "Every agent's worker answers")
    status = http(f"{CHAT}/api/status")[1]
    for agent, up in status.items():
        expect(up, f"{agent} is up")

    banner("2", "The buyer checks stock and raises a purchase order")
    sku, supplier = "AYC-CH-001", "SUP-MAL"
    stock = call("buyer_agent", "fishbowl__stock_check", {"sku": sku})
    part = stock["result"]["stock"][0]
    expect(stock["ok"] and part["on_hand"] < part["reorder_point"],
           f"{sku}: {part['on_hand']} on hand, below its reorder point of "
           f"{part['reorder_point']}")
    po_args = {"supplier_id": supplier, "sku": sku, "quantity": 12,
               "unit_cost": part["unit_cost"]}
    po = supervised("buyer_agent", "fishbowl__purchase_ordering", po_args, "p_coo")
    expect(po["ok"], "the purchase order was raised once the COO approved it")
    order = po["result"]["purchase_order"]
    expect(order["raised_by"] == "buyer_agent",
           f"Fishbowl records {order['po_number']} as raised by buyer_agent")

    banner("3", "The buyer receives the goods")
    received = supervised("buyer_agent", "fishbowl__goods_receiving",
                          {"po_number": order["po_number"]}, "p_coo")
    expect(received["ok"] and received["result"]["purchase_order"]["status"] == "received",
           f"{order['po_number']} received; {sku} on hand is now "
           f"{received['result'].get('on_hand')}")

    banner("4", "The supplier's invoice arrives in accounting")
    fb_po = next(p for p in state("fishbowl")["purchase_orders"]
                 if p["po_number"] == order["po_number"])
    status_, inv = http(f"http://127.0.0.1:{MOCK['accounting']}/admin/supplier-invoice", {
        "supplier_id": supplier, "po_number": fb_po["po_number"],
        "amount": fb_po["amount"], "received": fb_po["status"] == "received",
        "po_raised_by": fb_po["raised_by"]})
    invoice = inv["invoice"]
    print(f"  {invoice['id']}: {invoice['amount']} for {invoice['po_number']}")

    banner("5", "The buyer cannot pay it")
    tried = call("buyer_agent", "accounting__invoice_payment",
                 {"invoice_id": invoice["id"], "amount": invoice["amount"]})
    expect(not tried.get("ok"), "the buyer holds no payment tool; the call went nowhere")
    probe = in_container("buyer_agent",
                         "import urllib.request; urllib.request.urlopen("
                         "'http://mcp-accounting:8080/healthz', timeout=5)")
    expect(probe.returncode != 0,
           "the buyer's container cannot route to the accounting system at all")
    probe = in_container("ap_agent",
                         "import urllib.request; urllib.request.urlopen("
                         "'http://mcp-accounting:8080/healthz', timeout=5)")
    expect(probe.returncode == 0, "accounts payable's container can")

    banner("6", "Payment without a purchase order: the three-way match refuses")
    orphan = supervised("ap_agent", "accounting__invoice_payment",
                        {"invoice_id": "SINV-7790", "amount": 980}, "p_coo")
    expect(not orphan["ok"] and "no match" in str(orphan["result"]),
           "the accounting system refused SINV-7790, which names no order")

    banner("7", "Accounts payable pays the matched invoice")
    paid = supervised("ap_agent", "accounting__invoice_payment",
                      {"invoice_id": invoice["id"], "amount": invoice["amount"]}, "p_coo")
    expect(paid["ok"], f"{invoice['id']} paid")
    ledger = next(p for p in state("accounting")["payables"] if p["id"] == invoice["id"])
    expect(ledger.get("paid_by") == "ap_agent" and fb_po["raised_by"] == "buyer_agent",
           "raised by buyer_agent, paid by ap_agent: two hands, as the separation says")

    banner("8", "Accounts payable cannot raise a purchase order")
    tried = call("ap_agent", "fishbowl__purchase_ordering", po_args)
    expect(not tried.get("ok"), "ap_agent holds no purchasing tool")

    banner("9", "The dock's credential cannot adjust a count, even called directly")
    probe = in_container("warehouse_agent", (
        "import os, json\n"
        "from orgagents.harness.mcp import HttpMCPClient\n"
        "c = HttpMCPClient('http://mcp-fishbowl:8080/mcp', agent_id='warehouse_agent')\n"
        "c.grant(['inventory_adjustment'], os.environ['FISHBOWL_DOCK_TOKEN'])\n"
        "print(json.dumps(c.call_tool('inventory_adjustment', "
        "{'sku': 'AYC-TR-605', 'counted': 1, 'reason': 'shrinkage'})))"))
    out = probe.stdout.strip()
    print(f"  Fishbowl said: {out[:200] or probe.stderr[-200:]}")
    expect('"ok": false' in out and "does not permit" in out,
           "Fishbowl refused the dock credential for an inventory adjustment")

    banner("10", "Only a named approver may approve")
    status_, body = approve("ap_agent", "accounting__invoice_payment",
                            {"invoice_id": "SINV-7790", "amount": 980}, "p_buyer")
    expect(status_ == 403, "the buyer's owner is not an approver for accounts payable")

    banner("11", "The doors are shut (ADR-0114)")
    anonymous = urllib.request.build_opener()
    code, _ = http(f"{CHAT}/api/approve", {"agent": "ap_agent",
                   "tool": "accounting__invoice_payment", "arguments": {}}, anonymous)
    expect(code == 401, "nobody approves through the chat without signing in")
    code, _ = http(f"{CHAT}/api/chat", {"agent": "ap_agent", "message": "hi"}, anonymous)
    expect(code == 401, "nobody talks to an agent through the chat without signing in")
    expect(from_chat("/run", {"prompt": "hi"}, "") == 401,
           "a worker's /run answers 401 without its service token")
    expect(from_chat("/run", {"prompt": "hi"}, "$ORGAGENTS_WORKER_TOKEN_BUYER_AGENT") == 401,
           "and 401 with another worker's token")
    expect(from_chat("/approve", {"tool": "accounting__invoice_payment",
                                  "arguments": {"invoice_id": "SINV-7790", "amount": 980},
                                  "token": "e30.forged"},
                     "$ORGAGENTS_WORKER_TOKEN_AP_AGENT") == 403,
           "a forged, unsigned release is refused even with the right service token")
    expect(from_chat("/audit", None, "$ORGAGENTS_WORKER_TOKEN_AP_AGENT") == 200,
           "the worker's audit log is readable with its token")
    events = json.loads(from_chat.last or "{}").get("events", [])
    kinds = [e["event"] for e in events]
    expect(kinds.count("approval_granted") >= 2 and kinds.count("approval_consumed") >= 2
           and all(e.get("approver") == "p_coo" for e in events),
           f"ap_agent's audit log holds each grant and its use, by p_coo ({len(events)} events)")

    banner("audit", "What the systems saw, and from whom")
    for system in ("fishbowl", "accounting"):
        for row in state(system)["audit"]:
            print(f"  {system:<10} {row['agent']:<16} {row['tool']:<22} "
                  f"{'ok' if row.get('ok') else 'refused: ' + row.get('error', '')[:60]}")

    print(f"\n{'✓ every expectation held' if not failures else f'✗ {len(failures)} failed'}")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
