#!/usr/bin/env python3
"""AYC chat: talk to any of AYC's agents as a person would (ADR-0109).

    CHAT_IR=../generated/local/system.ir.json python server.py   # :8080

A page and a small backend. The page lists the agents in the compiled system;
the backend routes a message to **that agent's own worker container** and
hands back its reply, every tool call it made against the mock systems, and
any refusal — a mandate, a missing grant, an approval gate, a separation.

The backend is a router and, since ADR-0114, a small identity provider. It
holds no backing-system credential, joins only the control network the workers
answer on, and cannot route to a backing system: it can ask an agent to act,
and it cannot act for one. What it does hold:

* each worker's **service token**, because a worker answers nobody without
  one — and no agent holds another's;
* the approval issuer's **Ed25519 private key** -- the only copy; workers
  hold the public key and can verify, never sign -- because it is the issuer
  of approvals: a person
  signs in here (pick yourself from the design's people, give your passcode),
  and when you release a stopped call this signs *that* release — you, that
  agent, that tool, exactly those arguments, for five minutes, once. The
  worker checks the signature and still decides whether the design names you.

Sign-in here is a local demo identity provider: passcodes are generated per
checkout into the stack's git-ignored `.env` and printed by
`local_stack.py up` / `passcodes`. A real deployment signs people in through
the designer's OIDC (ADR-0047) and issues approvals from that identity.

The standard library plus `cryptography` (for Ed25519), so the image is
`python:3.11-slim`, one pip install and three files.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PORT = int(os.environ.get("CHAT_PORT", "8080"))
IR_PATH = Path(os.environ.get("CHAT_IR", "/app/system.ir.json"))
WORKER_URL = os.environ.get("CHAT_WORKER_URL", "http://agent-{agent}:8000")
STATIC = Path(__file__).parent / "static"
#: Links the page shows to each mock's state, as the *host* reaches them.
SYSTEM_LINKS = os.environ.get("CHAT_SYSTEM_LINKS", "")
#: The issuer's private key, `kid:base64url(32 bytes)` (ADR-0114). Only this
#: service has it; every worker has the matching public key.
APPROVAL_SIGNING_KEY = os.environ.get("ORGAGENTS_APPROVAL_SIGNING_KEY", "")
SESSION_COOKIE = "ayc_session"
SESSION_SECONDS = 8 * 3600
APPROVAL_TTL = 300
_sessions: dict[str, tuple[str, float]] = {}
_sessions_lock = threading.Lock()

TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css",
         ".js": "text/javascript", ".svg": "image/svg+xml"}


def compiled() -> dict[str, Any]:
    return json.loads(IR_PATH.read_text(encoding="utf-8"))


def roster() -> list[dict[str, Any]]:
    """The agents as the design names them, before asking any worker."""
    ir = compiled()
    teams = {t["id"]: t for t in ir.get("teams", [])}
    out = []
    for a in ir["agents"]:
        team = teams.get(a.get("team_id"), {})
        out.append({
            "id": a["id"],
            "name": a.get("name") or a["id"],
            "description": a.get("description", ""),
            "team": team.get("name") or a.get("team_id", ""),
            "team_path": a.get("team_path", []),
            "reports_to": a.get("reports_to"),
            "placements": a.get("placements", []),
            "mandate": (a.get("mandate") or {}).get("decisions", []),
        })
    return out


def _suffix(ident: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in ident).upper()


def people() -> list[dict[str, str]]:
    """Who may sign in: the design's people, and only those with a passcode."""
    return [{"id": p["id"], "name": p.get("name") or p["id"],
             "position": p.get("position", "")}
            for p in compiled().get("people", [])
            if os.environ.get(f"CHAT_PASSCODE_{_suffix(p['id'])}")]


def check_passcode(person: str, passcode: str) -> bool:
    expected = os.environ.get(f"CHAT_PASSCODE_{_suffix(person)}", "")
    return bool(expected) and hmac.compare_digest(passcode.encode(), expected.encode())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def issue_approval(agent: str, tool: str, arguments: dict, approver: str) -> str:
    """Sign one person's release of one call with the issuer's Ed25519 key
    (ADR-0114). The same format as
    orgagents.security.service_auth.issue_approval, repeated here because this
    image does not carry the platform; a test holds the two together."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    kid, _, raw = APPROVAL_SIGNING_KEY.partition(":")
    key = Ed25519PrivateKey.from_private_bytes(
        base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    canonical = json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"),
                           default=str)
    claims = {"agent": agent, "aud": agent, "tool": tool,
              "args": hashlib.sha256(canonical.encode()).hexdigest(),
              "approver": approver, "exp": int(time.time() + APPROVAL_TTL),
              "nonce": secrets.token_urlsafe(12), "kid": kid}
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{_b64(key.sign(body.encode()))}"


def worker(agent: str, path: str, body: dict | None = None,
           timeout: float = 120.0) -> tuple[int, Any]:
    known = {a["id"] for a in compiled()["agents"]}
    if agent not in known:
        return 404, {"error": f"no agent '{agent}' in AYC"}
    url = WORKER_URL.format(agent=agent) + path
    data = json.dumps(body).encode() if body is not None else None
    # This worker's own token, and no other's (ADR-0114).
    token = os.environ.get(f"ORGAGENTS_WORKER_TOKEN_{_suffix(agent)}", "")
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}")
        except ValueError:
            detail = {}
        return e.code, {"error": detail.get("detail") or detail.get("error") or str(e)}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 502, {"error": f"{agent}'s worker did not answer: {e}"}


def trace(trace_id: str) -> list[dict[str, Any]]:
    """One conversation's agent-to-agent hops (ADR-0118): each worker's part
    of the trace, in time order. Every worker records what it sent, received
    and refused under the trace id of the run that started the chain."""
    safe = "".join(c for c in trace_id if c.isalnum() or c in "-_")
    ids = [a["id"] for a in roster()]
    with ThreadPoolExecutor(max_workers=len(ids) or 1) as pool:
        parts = list(pool.map(lambda i: worker(i, f"/bus/trace/{safe}", timeout=5), ids))
    hops = [e for status, body in parts if status == 200
            for e in (body.get("events") or [])]
    return sorted(hops, key=lambda e: e.get("at") or 0)


class Handler(BaseHTTPRequestHandler):
    server_version = "ayc-chat/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if "/healthz" not in (args[0] if args else ""):
            sys.stderr.write("chat: " + fmt % args + "\n")

    def _json(self, status: int, body: Any, cookie: str | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if cookie is not None:
            # HttpOnly: no script reads it; SameSite=Strict: no other site's
            # page can make a signed-in browser approve something.
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}={cookie}; Path=/; "
                             f"HttpOnly; SameSite=Strict; Max-Age="
                             f"{SESSION_SECONDS if cookie else 0}")
        self.end_headers()
        self.wfile.write(data)

    def _person(self) -> str | None:
        """The signed-in person, from the session cookie, or None."""
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE and value:
                with _sessions_lock:
                    found = _sessions.get(value)
                    if found and found[1] > time.time():
                        return found[0]
                    _sessions.pop(value, None)
        return None

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/healthz":
            self._json(200, {"ok": True})
        elif path == "/api/people":
            self._json(200, {"people": people()})
        elif path == "/api/me":
            person = self._person()
            known = {p["id"]: p for p in people()}
            self._json(200, {"person": known.get(person) if person else None})
        elif path == "/api/agents":
            self._json(200, {"system": compiled().get("name"),
                             "agents": roster(),
                             "systems": [s for s in SYSTEM_LINKS.split(",") if s]})
        elif path == "/api/status":
            ids = [a["id"] for a in roster()]
            with ThreadPoolExecutor(max_workers=len(ids) or 1) as pool:
                states = list(pool.map(lambda i: worker(i, "/healthz", timeout=3), ids))
            self._json(200, {i: s == 200 for i, (s, _) in zip(ids, states)})
        elif path.startswith("/api/trace/"):
            self._json(200, {"hops": trace(path.rsplit("/", 1)[-1])})
        elif path.startswith("/api/agents/"):
            status, body = worker(path.rsplit("/", 1)[-1], "/agent", timeout=10)
            self._json(status, body)
        else:
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (STATIC / name).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                self._json(404, {"error": "not found"})
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", TYPES.get(target.suffix, "text/plain"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": "body is not JSON"})
            return
        agent = body.get("agent", "")
        if self.path == "/api/login":
            person = str(body.get("person", ""))
            if not check_passcode(person, str(body.get("passcode", ""))):
                self._json(401, {"error": "that person and passcode do not match"})
                return
            sid = secrets.token_urlsafe(24)
            with _sessions_lock:
                _sessions[sid] = (person, time.time() + SESSION_SECONDS)
            self._json(200, {"person": person}, cookie=sid)
            return
        if self.path == "/api/logout":
            for part in (self.headers.get("Cookie") or "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == SESSION_COOKIE:
                    with _sessions_lock:
                        _sessions.pop(value, None)
            self._json(200, {"ok": True}, cookie="")
            return
        # Everything below acts through an agent, so it needs a person.
        person = self._person()
        if person is None:
            self._json(401, {"error": "sign in first"})
            return
        if self.path == "/api/chat":
            # Long enough for a delegation chain to come back (ADR-0118).
            status, reply = worker(agent, "/run", {
                "prompt": body.get("message", ""),
                "created_by": f"chat:{person}",
            }, timeout=float(os.environ.get("CHAT_RUN_TIMEOUT", "300")))
            self._json(status, reply)
        elif self.path == "/api/approve":
            # The approver is the signed-in person — never a field in the
            # request. The worker checks the signature and whether the design
            # names this person as the agent's approver.
            tool = body.get("tool", "")
            arguments = body.get("arguments") or {}
            if not APPROVAL_SIGNING_KEY:
                self._json(503, {"error": "this chat is not configured to issue approvals"})
                return
            status, reply = worker(agent, "/approve", {
                "tool": tool, "arguments": arguments,
                "token": issue_approval(agent, tool, arguments, person),
            }, timeout=15)
            self._json(status, reply)
        else:
            self._json(404, {"error": "not found"})


def main() -> int:
    if not IR_PATH.exists():
        print(f"no compiled system at {IR_PATH}", file=sys.stderr)
        return 2
    print(f"ayc chat on :{PORT} ({len(roster())} agents)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
