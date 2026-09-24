#!/usr/bin/env python3
"""A mock of one of AYC's backing systems, as an MCP server (ADR-0109).

    MOCK_SYSTEM=fishbowl python server.py        # listens on :8080

One file serves all five systems in `ayc.local.binding.yaml` — Shopify,
Fishbowl, the accounting system, the CMS and the deploy pipeline — chosen by
`MOCK_SYSTEM`, each starting from `seed/<system>.json`. Nothing real is ever
reached: the state is in memory, and a restart puts the seed back.

It speaks the part of MCP's streamable-HTTP transport a tool call needs
(`initialize`, `tools/list`, `tools/call` on `POST /mcp`, JSON responses), with
the standard library only, so the image is `python:3.11-slim` and a file.

Two things make it more than a canned responder, because they are the two
things the design says the *application* enforces (ADR-0071):

* **Credentials.** A seed may say which credential may call which tool. The
  calling agent presents the credential of the capability it is using; a call
  under the wrong one is refused here, whatever the agent's own harness
  thought. That is "the same server under different credentials, so the
  downstream system can tell them apart" — Fishbowl's cycle-count approval.
* **The three-way match.** The accounting mock pays an invoice only against an
  order that exists and was received, for the invoiced amount, and never when
  the payer is the hand that raised the order.

Besides `/mcp` there is `GET /healthz`, `GET /state` (everything, including an
audit log of every call and who made it), and `POST /admin/<action>` for the
things a *system* does rather than an agent — the supplier's invoice arriving.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

SYSTEM = os.environ.get("MOCK_SYSTEM", "")
PORT = int(os.environ.get("MOCK_PORT", "8080"))
SEED_DIR = Path(os.environ.get("MOCK_SEED_DIR", Path(__file__).parent / "seed"))

LOCK = threading.Lock()
STATE: dict[str, Any] = {}


class Refused(Exception):
    """The system says no. Returned to the agent as a tool error."""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def next_id(prefix: str, counter: str) -> str:
    STATE["counters"][counter] = STATE["counters"].get(counter, 0) + 1
    return f"{prefix}{STATE['counters'][counter]}"


def one(rows: list[dict], key: str, value: Any, what: str) -> dict:
    for row in rows:
        if row.get(key) == value:
            return row
    raise Refused(f"no {what} '{value}'")


def money(value: Any, what: str = "amount") -> float:
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        raise Refused(f"{what} must be a number") from None
    if amount <= 0:
        raise Refused(f"{what} must be positive")
    return amount


# -- tools, per system --------------------------------------------------------
#
# Each tool is named after the capability it serves, which is what the binding's
# `tools:` option names. `ctx` carries the calling agent and credential.

def shopify_tools() -> dict[str, tuple[Callable, dict, str]]:
    def product_publishing(ctx, sku: str, title: str = "", price: Any = None):
        product = one(STATE["products"], "sku", sku, "product")
        if title:
            product["title"] = title
        if price is not None:
            product["price"] = money(price, "price")
        product["status"] = "active"
        product["published_by"] = ctx["agent"]
        return {"ok": True, "product": product}

    def price_update(ctx, sku: str, price: Any):
        product = one(STATE["products"], "sku", sku, "product")
        was, product["price"] = product["price"], money(price, "price")
        return {"ok": True, "sku": sku, "was": was, "now": product["price"]}

    def order_query(ctx, order_id: str = "", status: str = "", customer: str = ""):
        rows = [o for o in STATE["orders"]
                if (not order_id or o["id"] == order_id)
                and (not status or o["status"] == status)
                and (not customer or customer.lower() in o["customer"].lower())]
        return {"ok": True, "orders": rows}

    def refund_processing(ctx, order_id: str, amount: Any, reason: str = ""):
        order = one(STATE["orders"], "id", order_id, "order")
        amount = money(amount)
        left = round(order["total"] - order.get("refunded", 0), 2)
        if amount > left:
            raise Refused(f"refund {amount} exceeds the {left} left on {order_id}")
        order["refunded"] = round(order.get("refunded", 0) + amount, 2)
        refund = {"id": next_id("RF-", "refund"), "order_id": order_id,
                  "amount": amount, "reason": reason, "issued_by": ctx["agent"],
                  "at": now()}
        STATE["refunds"].append(refund)
        return {"ok": True, "refund": refund}

    def promotion_run(ctx, code: str, percent_off: Any, skus: list | None = None,
                      ends_on: str = ""):
        pct = float(percent_off)
        if not 0 < pct <= 50:
            raise Refused("percent_off must be between 0 and 50")
        promo = {"code": code.upper(), "percent_off": pct, "skus": skus or [],
                 "ends_on": ends_on, "created_by": ctx["agent"], "at": now()}
        STATE["promotions"].append(promo)
        return {"ok": True, "promotion": promo}

    return {
        "product_publishing": (product_publishing, {
            "sku": "string", "title": "string", "price": "number"}, ["sku"],
            "Publish a product listing on the storefront."),
        "price_update": (price_update, {"sku": "string", "price": "number"},
                         ["sku", "price"], "Change a product's list price."),
        "order_query": (order_query, {"order_id": "string", "status": "string",
                                      "customer": "string"}, [],
                        "Look up storefront orders."),
        "refund_processing": (refund_processing, {
            "order_id": "string", "amount": "number", "reason": "string"},
            ["order_id", "amount"], "Refund all or part of an order."),
        "promotion_run": (promotion_run, {
            "code": "string", "percent_off": "number", "skus": "array",
            "ends_on": "string"}, ["code", "percent_off"],
            "Create a discount code."),
    }


def fishbowl_tools() -> dict[str, tuple]:
    def stock_check(ctx, sku: str = ""):
        rows = [p for p in STATE["parts"] if not sku or p["sku"] == sku]
        if sku and not rows:
            raise Refused(f"no part '{sku}'")
        return {"ok": True, "stock": rows}

    def purchase_ordering(ctx, supplier_id: str, sku: str, quantity: Any,
                          unit_cost: Any):
        supplier = one(STATE["suppliers"], "id", supplier_id, "supplier")
        part = one(STATE["parts"], "sku", sku, "part")
        qty = int(quantity)
        if qty <= 0:
            raise Refused("quantity must be positive")
        cost = money(unit_cost, "unit_cost")
        po = {"po_number": next_id("PO-", "po"), "supplier_id": supplier["id"],
              "supplier": supplier["name"], "sku": sku, "quantity": qty,
              "unit_cost": cost, "amount": round(qty * cost, 2),
              "status": "issued", "raised_by": ctx["agent"], "received": 0,
              "at": now()}
        STATE["purchase_orders"].append(po)
        part["on_order"] += qty
        return {"ok": True, "purchase_order": po}

    def goods_receiving(ctx, po_number: str, quantity: Any = None):
        po = one(STATE["purchase_orders"], "po_number", po_number, "purchase order")
        if po["status"] == "received":
            raise Refused(f"{po_number} is already received")
        qty = int(quantity) if quantity is not None else po["quantity"] - po["received"]
        part = one(STATE["parts"], "sku", po["sku"], "part")
        po["received"] += qty
        po["status"] = "received" if po["received"] >= po["quantity"] else "partial"
        po["received_by"] = ctx["agent"]
        part["on_hand"] += qty
        part["on_order"] = max(0, part["on_order"] - qty)
        return {"ok": True, "purchase_order": po, "on_hand": part["on_hand"]}

    def inventory_adjustment(ctx, sku: str, counted: Any, reason: str = ""):
        part = one(STATE["parts"], "sku", sku, "part")
        counted = int(counted)
        adj = {"id": next_id("ADJ-", "adj"), "sku": sku, "was": part["on_hand"],
               "counted": counted, "delta": counted - part["on_hand"],
               "reason": reason, "by": ctx["agent"], "at": now()}
        part["on_hand"] = counted
        STATE["adjustments"].append(adj)
        return {"ok": True, "adjustment": adj}

    def order_fulfillment(ctx, order_id: str):
        order = one(STATE["sales_orders"], "id", order_id, "sales order")
        if order["status"] == "shipped":
            raise Refused(f"{order_id} has already shipped")
        for line in order["lines"]:
            part = one(STATE["parts"], "sku", line["sku"], "part")
            if part["on_hand"] < line["quantity"]:
                raise Refused(f"{line['sku']}: {part['on_hand']} on hand, "
                              f"{line['quantity']} ordered")
        for line in order["lines"]:
            one(STATE["parts"], "sku", line["sku"], "part")["on_hand"] -= line["quantity"]
        order["status"] = "shipped"
        order["shipped_by"] = ctx["agent"]
        order["shipped_at"] = now()
        return {"ok": True, "sales_order": order}

    return {
        "stock_check": (stock_check, {"sku": "string"}, [],
                        "On hand, allocated and on order, per part."),
        "purchase_ordering": (purchase_ordering, {
            "supplier_id": "string", "sku": "string", "quantity": "integer",
            "unit_cost": "number"}, ["supplier_id", "sku", "quantity", "unit_cost"],
            "Raise a purchase order with a supplier."),
        "goods_receiving": (goods_receiving, {"po_number": "string",
                                              "quantity": "integer"},
                            ["po_number"], "Receive goods against a purchase order."),
        "inventory_adjustment": (inventory_adjustment, {
            "sku": "string", "counted": "integer", "reason": "string"},
            ["sku", "counted"], "Set on-hand to a cycle count."),
        "order_fulfillment": (order_fulfillment, {"order_id": "string"},
                              ["order_id"], "Pick, pack and ship a sales order."),
    }


def accounting_tools() -> dict[str, tuple]:
    def invoice_payment(ctx, invoice_id: str, amount: Any):
        inv = one(STATE["payables"], "id", invoice_id, "supplier invoice")
        amount = money(amount)
        if inv["status"] == "paid":
            raise Refused(f"{invoice_id} is already paid")
        # The three-way match (separation 'purchasing_and_payment' names this
        # as its authoritative enforcement): order, receipt, invoice.
        if not inv.get("po_number"):
            raise Refused(f"{invoice_id} names no purchase order; no match, no payment")
        if not inv.get("received"):
            raise Refused(f"{inv['po_number']} has not been received; "
                          "no receipt, no payment")
        if abs(amount - inv["amount"]) > 0.005:
            raise Refused(f"{amount} does not match the invoiced {inv['amount']}")
        if inv.get("po_raised_by") and inv["po_raised_by"] == ctx["agent"]:
            raise Refused(f"{ctx['agent']} raised {inv['po_number']} and may not "
                          "pay it (purchasing_and_payment)")
        inv.update(status="paid", paid_by=ctx["agent"], paid_at=now())
        payment = {"id": next_id("PAY-", "pay"), "invoice_id": invoice_id,
                   "supplier_id": inv["supplier_id"], "amount": amount,
                   "by": ctx["agent"], "at": now()}
        STATE["payments"].append(payment)
        return {"ok": True, "payment": payment, "invoice": inv}

    def receivable_management(ctx, customer_id: str, action: str, amount: Any,
                              reference: str = ""):
        acct = one(STATE["receivables"], "customer_id", customer_id, "customer account")
        amount = money(amount)
        if action == "record":
            acct["balance"] = round(acct["balance"] + amount, 2)
        elif action in ("receipt", "write_off"):
            if amount > acct["balance"]:
                raise Refused(f"{amount} exceeds the {acct['balance']} balance")
            acct["balance"] = round(acct["balance"] - amount, 2)
        else:
            raise Refused("action is one of record, receipt, write_off")
        entry = {"customer_id": customer_id, "action": action, "amount": amount,
                 "reference": reference, "by": ctx["agent"], "at": now()}
        acct.setdefault("entries", []).append(entry)
        return {"ok": True, "account": acct}

    return {
        "invoice_payment": (invoice_payment, {"invoice_id": "string",
                                              "amount": "number"},
                            ["invoice_id", "amount"],
                            "Pay a supplier invoice against a three-way match."),
        "receivable_management": (receivable_management, {
            "customer_id": "string", "action": "string", "amount": "number",
            "reference": "string"}, ["customer_id", "action", "amount"],
            "Record, receipt or write off a customer receivable."),
    }


def cms_tools() -> dict[str, tuple]:
    def content_publishing(ctx, slug: str, title: str, body: str = ""):
        page = next((p for p in STATE["pages"] if p["slug"] == slug), None)
        if page is None:
            page = {"slug": slug}
            STATE["pages"].append(page)
        page.update(title=title, body=body, status="published",
                    published_by=ctx["agent"], at=now())
        return {"ok": True, "page": page}

    return {"content_publishing": (content_publishing, {
        "slug": "string", "title": "string", "body": "string"},
        ["slug", "title"], "Publish or update a page.")}


def deploy_pipeline_tools() -> dict[str, tuple]:
    def software_deploy(ctx, service: str, version: str, change_ticket: str = ""):
        svc = one(STATE["services"], "name", service, "service")
        if not change_ticket:
            raise Refused("a production deploy needs a change ticket")
        run = {"id": next_id("DEP-", "deploy"), "service": service,
               "from": svc["version"], "to": version,
               "change_ticket": change_ticket, "by": ctx["agent"], "at": now()}
        svc["version"] = version
        STATE["deployments"].append(run)
        return {"ok": True, "deployment": run}

    return {"software_deploy": (software_deploy, {
        "service": "string", "version": "string", "change_ticket": "string"},
        ["service", "version"], "Deploy a version of a service to production.")}


TOOLSETS = {
    "shopify": shopify_tools, "fishbowl": fishbowl_tools,
    "accounting": accounting_tools, "cms": cms_tools,
    "deploy_pipeline": deploy_pipeline_tools,
    # The tenant's flow engine (ADR-0110). It has no MCP tools: an agent
    # reaches it through the workflow engine invoker, as egress.
    "langflow": lambda: {},
}


# -- the flow engine: invoice capture (ADR-0110) ------------------------------
#
# A stand-in for the flow engine's run API, `POST /api/v1/run/<flow>`, so the
# workstation stack need not pull the engine's image. It answers the one flow
# the design binds, invoice capture, from the seed's supplier invoices: given a
# purchase order it returns the invoice that quotes it. What is *inside* the
# flow is the engine's business; the spec only declares its interface.

def run_flow(flow: str, body: dict, caller: str) -> tuple[int, dict]:
    spec = STATE.get("flows", {}).get(flow)
    entry = {"at": now(), "agent": caller or "unknown", "flow": flow,
             "inputs": body.get("inputs", {})}
    if spec is None:
        entry.update(ok=False, error="no such flow")
        STATE["audit"].append(entry)
        return 404, {"error": f"no flow '{flow}'"}
    inputs = body.get("inputs") or {}
    po = inputs.get("purchase_order")
    number = po.get("po_number") or po.get("id") if isinstance(po, dict) else str(po or "")
    invoice = next((dict(i) for i in STATE.get("invoices", [])
                    if i.get("po_number") and i["po_number"] in str(number)), None)
    if invoice is None:
        invoice = {"id": next_id("SINV-", "invoice"), "po_number": str(number or ""),
                   "supplier_id": "SUP-NOR", "amount": 0.0,
                   "note": "no supplier invoice quotes this order yet"}
    entry.update(ok=True, outputs={"invoice": invoice})
    STATE["audit"].append(entry)
    print(json.dumps({"system": SYSTEM, **entry}), flush=True)
    return 200, {"flow": flow, "outputs": {"invoice": invoice}}


# -- admin: what systems do, not agents ---------------------------------------

def admin_supplier_invoice(body: dict) -> dict:
    """A supplier's invoice arrives, carrying the order and receipt it matches."""
    inv = {"id": body.get("id") or next_id("SINV-", "sinv"),
           "supplier_id": body["supplier_id"], "po_number": body.get("po_number", ""),
           "amount": money(body["amount"]), "received": bool(body.get("received")),
           "po_raised_by": body.get("po_raised_by", ""), "status": "open",
           "at": now()}
    STATE["payables"].append(inv)
    return {"ok": True, "invoice": inv}


ADMIN = {"accounting": {"supplier-invoice": admin_supplier_invoice}}


# -- the MCP transport ----------------------------------------------------------

def schema(props: dict[str, str], required: list[str]) -> dict:
    return {"type": "object",
            "properties": {k: {"type": v} for k, v in props.items()},
            "required": required}


def credential_of(token: str) -> str:
    for name in STATE.get("credentials", {}):
        if token and os.environ.get(name) == token:
            return name
    return ""


def call_tool(name: str, args: dict, agent: str, token: str) -> tuple[bool, dict]:
    tools = TOOLSETS[SYSTEM]()
    if name not in tools:
        return False, {"ok": False, "error": f"{SYSTEM} has no tool '{name}'"}
    credential = credential_of(token)
    scoped = STATE.get("credentials", {})
    ctx = {"agent": agent or "unknown", "credential": credential}
    entry = {"at": now(), "agent": ctx["agent"], "credential": credential,
             "tool": name, "arguments": args}
    # A tool any credential is scoped to needs one that covers it.
    refusal = ""
    if any(name in tools_ for tools_ in scoped.values()):
        if not credential:
            refusal = (f"'{name}' needs a credential, and none that this system "
                       "issued was presented")
        elif name not in scoped[credential]:
            refusal = f"credential {credential} does not permit '{name}'"
    if refusal:
        entry.update(ok=False, error=refusal)
        STATE["audit"].append(entry)
        print(json.dumps({"system": SYSTEM, **entry}), flush=True)
        return False, {"ok": False, "error": f"{SYSTEM}: {refusal}"}
    fn = tools[name][0]
    try:
        with LOCK:
            result = fn(ctx, **args)
        entry["ok"] = True
        return True, result
    except Refused as exc:
        entry.update(ok=False, error=str(exc))
        return False, {"ok": False, "error": f"{SYSTEM}: {exc}"}
    except TypeError as exc:
        entry.update(ok=False, error=str(exc))
        return False, {"ok": False, "error": f"{SYSTEM}: bad arguments: {exc}"}
    finally:
        STATE["audit"].append(entry)
        print(json.dumps({"system": SYSTEM, **entry}), flush=True)


def rpc(message: dict, agent: str, token: str) -> dict | None:
    method, mid = message.get("method"), message.get("id")
    if mid is None:
        return None                                   # a notification
    if method == "initialize":
        result = {"protocolVersion": message.get("params", {}).get(
                      "protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": f"ayc-mock-{SYSTEM}", "version": "0.1.0"}}
    elif method == "tools/list":
        result = {"tools": [
            {"name": n, "description": d, "inputSchema": schema(p, r)}
            for n, (_, p, r, d) in TOOLSETS[SYSTEM]().items()]}
    elif method == "tools/call":
        params = message.get("params", {})
        ok, data = call_tool(params.get("name", ""), params.get("arguments") or {},
                             agent, token)
        result = {"content": [{"type": "text", "text": json.dumps(data)}],
                  "structuredContent": data, "isError": not ok}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"no method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


class Handler(BaseHTTPRequestHandler):
    server_version = "ayc-mock/0.1"

    def log_message(self, *_: Any) -> None:            # the audit line is the log
        pass

    def _send(self, status: int, body: Any, headers: dict | None = None) -> None:
        data = json.dumps(body, indent=2).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send(200, {"ok": True, "system": SYSTEM})
        elif self.path == "/state":
            with LOCK:
                self._send(200, STATE)
        elif SYSTEM == "langflow" and self.path.startswith("/flow/"):
            # Where *Open in* lands: the flow as the engine holds it.
            flow = STATE.get("flows", {}).get(self.path[len("/flow/"):])
            self._send(200 if flow else 404, flow or {"error": "no such flow"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, {"error": "body is not JSON"})
            return
        if self.path == "/mcp":
            agent = self.headers.get("X-Orgagents-Agent", "")
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            session = self.headers.get("Mcp-Session-Id") or f"{SYSTEM}-{time.time_ns()}"
            reply = rpc(body, agent, token)
            if reply is None:
                self._send(202, None, {"Mcp-Session-Id": session})
            else:
                self._send(200, reply, {"Mcp-Session-Id": session})
        elif SYSTEM == "langflow" and self.path.startswith("/api/v1/run/"):
            with LOCK:
                status, reply = run_flow(self.path[len("/api/v1/run/"):], body,
                                         self.headers.get("X-Orgagents-Agent", ""))
            self._send(status, reply)
        elif self.path == "/admin/reset":
            load_seed()
            self._send(200, {"ok": True})
        elif self.path.startswith("/admin/"):
            action = ADMIN.get(SYSTEM, {}).get(self.path[len("/admin/"):])
            if action is None:
                self._send(404, {"error": f"{SYSTEM} has no admin action {self.path}"})
                return
            try:
                with LOCK:
                    self._send(200, action(body))
            except (KeyError, Refused) as exc:
                self._send(400, {"ok": False, "error": str(exc)})
        else:
            self._send(404, {"error": "not found"})


def load_seed() -> None:
    seed = json.loads((SEED_DIR / f"{SYSTEM}.json").read_text(encoding="utf-8"))
    with LOCK:
        STATE.clear()
        STATE.update(copy.deepcopy(seed))
        STATE.setdefault("counters", {})
        STATE["audit"] = []


def main() -> int:
    if SYSTEM not in TOOLSETS:
        print(f"MOCK_SYSTEM must be one of {sorted(TOOLSETS)}", file=sys.stderr)
        return 2
    load_seed()
    missing = [n for n in STATE.get("credentials", {}) if not os.environ.get(n)]
    if missing:
        print(f"warning: credentials not set, their tools will refuse: {missing}",
              flush=True)
    print(f"ayc mock {SYSTEM} on :{PORT} "
          f"({len(TOOLSETS[SYSTEM]())} tools)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
