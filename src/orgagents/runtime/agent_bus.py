"""Agent-to-agent messaging and delegation between worker containers (ADR-0118).

Each worker is one agent (ADR-0109). This module is what lets two of them talk:

* **three tools** for the agent -- `send_message(to_agent, text)`,
  `delegate(to_agent, task, inputs)` returning a handle (ADR-0093) and
  `check_delegation(handle, wait_s)`;
* **an inbox**: a durable JetStream consumer on the agent's own subjects, whose
  deliveries are decided here and, if accepted, run as the agent;
* **the checks, on both sides.** Who may reach whom is compiled into the
  agent's own config (`orgagents.compiler.links`); the sender refuses an edge
  the design does not have, and the receiver refuses it again, because
  receiving on a subject is not proof the sender was allowed to send
  (ADR-0059 rule 4). Between them sits the broker, configured from the same
  edges, which refuses a publish to an inbox the connection may not reach.

**Who sent it** is the last token of the subject, never a field in the body:
an agent's broker user may publish only to `<to>.inbox.<itself>`, so the
subject is vouched for by the connection that published it. A body that
claims to be from somebody else is refused as a spoof.

**Separation of duties survives delegation** (ADR-0070). Every hop carries the
chain of principals it was commissioned by -- on the NATS bus as a hash chain
of hops each signed by the agent that sent it (ADR-0118 v1.1, `HopKeys`), so
the principals a receiver judges by are proven, not claimed. A delegation that
names a separated decision -- in `inputs.decision` or written in its task text
or inputs -- is refused, by the sender and by the receiver, when anyone in the
chain holds a decision separated from it; and when the receiver runs the work,
any tool constituting such a decision is refused, so "the buyer asks the COO
to have payables pay" is refused however it is phrased.

**Tracing.** Every hop carries the trace id of the run that started it (the
first session's id), the chain and the depth; each side records what it did in
the worker's audit log and in memory, and `/bus/trace/<id>` on each worker
returns its part, which the chat stitches together.

The transport is injected (`Transport`), so everything but the wire is tested
without a broker; `NatsTransport` is the real one, over `nats-py`.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Callable, Optional, Protocol

DELEGATE = "delegate"
MESSAGE = "message"
REPLY = "reply"

#: How long a delegation may stay unanswered before its handle settles as
#: failed (ADR-0093 rule 5: a handle always settles).
DEFAULT_HANDLE_DEADLINE_S = 900.0
#: The longest one `check_delegation` call will block.
MAX_WAIT_S = 170.0


class BusRefused(Exception):
    """The broker refused a publish (a permissions violation)."""


# -- the compiled edges ------------------------------------------------------

def _token(value: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "-" for c in (value or ""))
    return out or "unknown"


def _in_window(grant: dict[str, Any], on: date) -> bool:
    start, end = grant.get("starts_on") or "", grant.get("ends_on") or ""
    try:
        if start and on < date.fromisoformat(start):
            return False
        if end and on > date.fromisoformat(end):
            return False
    except ValueError:
        return False
    return True


@dataclass
class LinkPolicy:
    """One agent's compiled edges, and the checks made against them."""

    config: dict[str, Any]

    @property
    def agent_id(self) -> str:
        return self.config["agent"]

    @property
    def prefix(self) -> str:
        return self.config["subject_prefix"]

    @property
    def max_depth(self) -> int:
        return int(self.config.get("max_depth") or 3)

    def _allows(self, edges: dict[str, Any], other: str, kind: str,
                on: Optional[date]) -> tuple[bool, str]:
        edge = edges.get(other)
        if not edge:
            return False, "the design declares no edge between them"
        on = on or date.today()
        wanted = (DELEGATE,) if kind == DELEGATE else (DELEGATE, MESSAGE)
        live = [g for g in edge.get("grants", []) if g["kind"] in wanted]
        if not live:
            return False, (f"the design lets them {edge['grants'][0]['kind']} "
                           f"({edge['grants'][0]['via']}), not {kind}")
        dated = [g for g in live if _in_window(g, on)]
        if not dated:
            g = live[0]
            return False, (f"{g['via']} held only from {g.get('starts_on') or '-'} "
                           f"to {g.get('ends_on') or '-'}")
        return True, dated[0]["via"]

    def may_send(self, to: str, kind: str, on: Optional[date] = None) -> tuple[bool, str]:
        ok, why = self._allows(self.config.get("outbound", {}), to, kind, on)
        return ok, why if ok else f"{self.agent_id} may not {kind} to {to}: {why}"

    def may_receive(self, frm: str, kind: str, on: Optional[date] = None) -> tuple[bool, str]:
        ok, why = self._allows(self.config.get("inbound", {}), frm, kind, on)
        return ok, why if ok else f"{frm} may not {kind} to {self.agent_id}: {why}"

    def may_reply_to(self, agent: str) -> bool:
        return agent in self.config.get("inbound", {})

    def separation_conflict(self, principals: list[str], decision: str) -> Optional[str]:
        """Why taking `decision` on behalf of these principals would put both
        sides of a separation in one line of command, or None."""
        if not decision:
            return None
        holdings = self.config.get("separated_holdings", {})
        for rule in self.config.get("separations", []):
            if decision not in rule.get("decisions", []):
                continue
            for p in dict.fromkeys(principals):
                other = [d for d in holdings.get(p, [])
                         if d in rule["decisions"] and d != decision]
                if other:
                    return (f"separation '{rule['id']}': {p} holds "
                            f"{', '.join(other)}, so '{decision}' may not be taken "
                            f"on its behalf. {rule.get('reason', '')}".strip())
        return None

    def agent_for_token(self, token: str) -> Optional[str]:
        """The agent a subject token names, among those this one knows."""
        known = set(self.config.get("outbound", {})) | set(self.config.get("inbound", {}))
        return next((a for a in known if _token(a) == token), None)

    # subjects -- spelled as orgagents.bus.SubjectNamespace spells them
    def inbox(self, to: str, frm: str) -> str:
        return f"{self.prefix}.agent.{_token(to)}.inbox.{_token(frm)}"

    def reply(self, to: str, frm: str) -> str:
        return f"{self.prefix}.agent.{_token(to)}.reply.{_token(frm)}"


# -- the hop ------------------------------------------------------------------

@dataclass
class HopContext:
    """What a run knows about the delegation chain it is part of."""

    trace_id: str
    chain: list[str] = field(default_factory=list)   # principals, first to last
    depth: int = 0
    session_id: str = ""
    handle: str = ""                                  # the handle it answers
    hops: list[dict[str, Any]] = field(default_factory=list)   # signed, verified


# -- signed hops (ADR-0118 v1.1) -----------------------------------------------
#
# The broker vouches for the immediate sender only (the subject). What stood
# behind it -- who commissioned the sender -- was the sender's word. Now every
# hop is a record signed by the agent that sent it, with its NKey seed (an
# Ed25519 key only that agent's worker holds):
#
#   {v, kind, task, trace_id, from, to, depth, prev, digest, decisions, iat, exp}
#
# `prev` is the SHA-256 of the previous signed hop, so the chain is a hash
# chain from its origin: nobody along it can drop, reorder or replace an
# earlier hop without the signature of the agent that made it. `digest` binds
# the hop to the text and inputs it carried. A receiver verifies every hop
# against the public keys it was given, and takes the principals from the
# verified chain -- never from a list in the body.
#
# What it cannot stop: an agent may always start a fresh chain as itself. A
# compromised agent can drop what came before *it* only by claiming to act on
# its own account, and separation is then judged against its own holdings.

HOP_VERSION = 1
#: Clock skew tolerated between two workers' `iat` and `now`.
HOP_SKEW_S = 60.0


class HopError(Exception):
    """A hop chain does not prove what it claims; the reason is safe to log."""


def _canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def hop_hash(hop: dict[str, Any]) -> str:
    return hashlib.sha256(_canon(hop)).hexdigest()


def body_digest(kind: str, text: str, inputs: Optional[dict[str, Any]]) -> str:
    return hashlib.sha256(
        _canon({"kind": kind, "text": text or "", "inputs": inputs or {}})).hexdigest()


def parse_bus_public_keys(spec: str) -> dict[str, str]:
    """`agent:U...,agent:U...` -> {agent: public NKey}."""
    out: dict[str, str] = {}
    for part in (spec or "").split(","):
        agent, sep, key = part.strip().rpartition(":")
        if sep and agent and key:
            out[agent] = key
    return out


@dataclass
class HopKeys:
    """This agent's hop-signing seed and every agent's public NKey."""

    seed: str
    public_keys: dict[str, str]

    def sign(self, claims: dict[str, Any]) -> dict[str, Any]:
        import base64

        from ..security import nkey
        sig = nkey.sign(self.seed, _canon(claims))
        return {"claims": claims, "sig": base64.urlsafe_b64encode(sig).decode().rstrip("=")}

    def verify_chain(self, hops: list[dict[str, Any]], *, me: str, sender: str,
                     body: dict[str, Any], now: Optional[float] = None) -> list[str]:
        """The principals of a verified chain, origin first, ending with
        `me`; or HopError."""
        import base64

        from ..security import nkey
        now = _now() if now is None else now
        if not isinstance(hops, list) or not hops:
            raise HopError("the delegation carries no signed hops")
        prev_hash, prev = "", None
        for i, hop in enumerate(hops):
            if not isinstance(hop, dict) or not isinstance(hop.get("claims"), dict):
                raise HopError(f"hop {i + 1} is malformed")
            c = hop["claims"]
            signer = str(c.get("from") or "")
            key = self.public_keys.get(signer)
            if not key:
                raise HopError(f"hop {i + 1} is signed as '{signer}', whose key "
                               f"{me} does not hold")
            try:
                sig = base64.urlsafe_b64decode(str(hop.get("sig", "")) + "=" * (
                    -len(str(hop.get("sig", ""))) % 4))
            except (ValueError, TypeError):
                sig = b""
            if not nkey.verify(key, _canon(c), sig):
                raise HopError(f"hop {i + 1} ({signer} -> {c.get('to')}) is not "
                               f"signed by {signer}")
            if c.get("v") != HOP_VERSION:
                raise HopError(f"hop {i + 1} has an unknown version")
            if c.get("prev", "") != prev_hash:
                raise HopError(f"hop {i + 1} does not follow the hop before it "
                               "(the chain was altered)")
            if prev is not None:
                if signer != prev["to"]:
                    raise HopError(f"hop {i + 1} is from {signer}, but hop {i} "
                                   f"went to {prev['to']}")
                if float(c.get("iat", 0)) > float(prev.get("exp", 0)):
                    raise HopError(f"hop {i + 1} was made after hop {i} expired")
                if c.get("trace_id") != prev.get("trace_id"):
                    raise HopError(f"hop {i + 1} changes the trace")
            prev_hash, prev = hop_hash(hop), c
        last = prev or {}
        if last.get("from") != sender or last.get("to") != me:
            raise HopError(f"the last hop is {last.get('from')} -> {last.get('to')}, "
                           f"not {sender} -> {me}")
        if float(last.get("exp", 0)) < now:
            raise HopError("the last hop has expired")
        if float(last.get("iat", 0)) > now + HOP_SKEW_S:
            raise HopError("the last hop is dated in the future")
        if last.get("task") != body.get("id") or last.get("kind") != body.get("kind") \
                or last.get("trace_id") != body.get("trace_id") \
                or int(last.get("depth", -1)) != int(body.get("depth") or 0):
            raise HopError("the signed hop is for a different message")
        if last.get("digest") != body_digest(str(body.get("kind") or ""),
                                              str(body.get("text") or ""),
                                              body.get("inputs") or {}):
            raise HopError("the text or inputs were changed after they were signed")
        return [str(hops[0]["claims"]["from"])] + [str(h["claims"]["to"]) for h in hops]


def named_decisions(policy: "LinkPolicy", text: str, inputs: Optional[dict[str, Any]]
                    ) -> list[str]:
    """Separated decisions a task names: `inputs.decision`, and any separated
    decision id written in its text or inputs (`pay_invoice`, or `pay
    invoice`). A decision taken without a tool is otherwise invisible to the
    bus; naming it is the one trace it leaves (ADR-0118 v1.1)."""
    import re

    inputs = inputs or {}
    found: list[str] = []
    explicit = str(inputs.get("decision") or "")
    if explicit:
        found.append(explicit)
    haystack = (text or "") + "\n" + json.dumps(
        {k: v for k, v in inputs.items() if k != "decision"}, default=str)
    separated = {d for r in policy.config.get("separations", [])
                 for d in r.get("decisions", [])}
    for d in sorted(separated):
        spelled = [re.escape(d)]
        if "_" in d:
            spelled.append(re.escape(d).replace("_", r"[\s\-]+"))
        if any(re.search(rf"(?<![A-Za-z0-9]){s}(?![A-Za-z0-9])", haystack, re.I)
               for s in spelled) and d not in found:
            found.append(d)
    return found


class Transport(Protocol):
    def publish(self, subject: str, data: bytes, headers: dict[str, str]) -> None: ...


def _now() -> float:
    return time.time()


class AgentMessenger:
    """The agent's side of the bus: its tools, its handles and its inbox."""

    def __init__(self, policy: LinkPolicy, transport: Optional[Transport] = None, *,
                 run_task: Optional[Callable[[HopContext, str, str, str, dict], dict]] = None,
                 audit: Optional[Callable[[str, dict], None]] = None,
                 handle_deadline_s: float = DEFAULT_HANDLE_DEADLINE_S,
                 max_workers: int = 4, hop_keys: Optional[HopKeys] = None) -> None:
        self.policy = policy
        self.transport = transport
        self.run_task = run_task
        self._audit = audit
        # With keys, every hop sent is signed and every hop received must
        # verify (the generated stack; ADR-0118 v1.1). Without, the chain is
        # the sender's word: the in-process bus and older tests only.
        self.hop_keys = hop_keys
        self._seen_tasks: dict[str, float] = {}
        self.handle_deadline_s = handle_deadline_s
        self.handles: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self._cond = threading.Condition()
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix=f"bus-{policy.agent_id}")

    @property
    def me(self) -> str:
        return self.policy.agent_id

    # -- record -------------------------------------------------------------

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        entry = {"event": event, "agent": self.me, "at": _now(), **fields}
        with self._cond:
            self.events.append(entry)
            del self.events[:-2000]
        if self._audit is not None:
            try:
                self._audit(event, entry)
            except Exception:                              # noqa: BLE001
                pass
        return entry

    def trace(self, trace_id: str) -> list[dict[str, Any]]:
        with self._cond:
            return [e for e in self.events if e.get("trace_id") == trace_id]

    # -- sending --------------------------------------------------------------

    def _publish(self, subject: str, body: dict[str, Any]) -> None:
        if self.transport is None:
            raise BusRefused("this worker is not connected to a bus")
        headers = {"Nats-Msg-Id": body["id"], "Orgagents-Trace-Id": body["trace_id"]}
        self.transport.publish(subject, json.dumps(body, default=str).encode(), headers)

    def _refuse(self, event: str, ctx: HopContext, to: str, kind: str, reason: str,
                **extra: Any) -> dict[str, Any]:
        self.record(event, trace_id=ctx.trace_id, to=to, kind=kind, reason=reason,
                    session_id=ctx.session_id, depth=ctx.depth + 1, **extra)
        return {"ok": False, "error": reason, "refused_by": event.split("_", 2)[-1]}

    def _outbound(self, ctx: HopContext, to: str, kind: str, text: str,
                  inputs: Optional[dict[str, Any]], handle: str = "") -> dict[str, Any]:
        ok, why = self.policy.may_send(to, kind)
        if not ok:
            return self._refuse("bus_refused_local", ctx, to, kind, why)
        depth = ctx.depth + 1
        if kind == DELEGATE and depth > self.policy.max_depth:
            return self._refuse("bus_refused_local", ctx, to, kind,
                                f"delegation depth {depth} exceeds this agent's "
                                f"max_delegation_depth of {self.policy.max_depth}")
        chain = list(ctx.chain or [self.me])
        if not chain or chain[-1] != self.me:
            chain.append(self.me)
        decision = str((inputs or {}).get("decision") or "")
        # A delegation is judged by every separated decision it names, in
        # `inputs.decision` or in its text and inputs (ADR-0118 v1.1).
        decisions = named_decisions(self.policy, text, inputs) if kind == DELEGATE \
            else ([decision] if decision else [])
        for d in decisions:
            conflict = self.policy.separation_conflict(chain, d)
            if conflict:
                return self._refuse("bus_refused_local", ctx, to, kind, conflict,
                                    decision=d)
        body = {
            "id": uuid.uuid4().hex, "kind": kind, "from": self.me, "to": to,
            "text": text, "inputs": inputs or {}, "handle": handle,
            "decision": decision, "trace_id": ctx.trace_id, "chain": chain,
            "depth": depth, "parent_session": ctx.session_id, "sent_at": _now(),
        }
        if self.hop_keys is not None:
            prev = ctx.hops[-1] if ctx.hops else None
            now = _now()
            hop = self.hop_keys.sign({
                "v": HOP_VERSION, "kind": kind, "task": body["id"],
                "trace_id": ctx.trace_id, "from": self.me, "to": to, "depth": depth,
                "prev": hop_hash(prev) if prev else "",
                "digest": body_digest(kind, text, inputs or {}),
                "decisions": decisions, "iat": now,
                "exp": now + self.handle_deadline_s,
            })
            body["hops"] = list(ctx.hops) + [hop]
        try:
            self._publish(self.policy.inbox(to, self.me), body)
        except BusRefused as e:
            return self._refuse("bus_refused_broker", ctx, to, kind, str(e))
        except Exception as e:                             # noqa: BLE001
            return self._refuse("bus_send_failed", ctx, to, kind,
                                f"{type(e).__name__}: {e}")
        self.record("bus_sent", trace_id=ctx.trace_id, to=to, kind=kind, via=why,
                    handle=handle, message_id=body["id"], session_id=ctx.session_id,
                    depth=depth, chain=chain, text=text[:200])
        return {"ok": True, "message_id": body["id"], "via": why}

    def tools(self, ctx: HopContext) -> dict[str, Callable[..., Any]]:
        """The agent's bus tools, bound to the run they are handed to."""

        def send_message(to_agent: str, text: str) -> dict[str, Any]:
            """Send a message to another agent the design lets you reach. It
            is delivered to that agent's inbox; no answer comes back."""
            out = self._outbound(ctx, to_agent, MESSAGE, text, None)
            if out["ok"]:
                out["delivered_to"] = to_agent
            return out

        def delegate(to_agent: str, task: str,
                     inputs: Optional[dict[str, Any]] = None) -> dict[str, Any]:
            """Hand a task to an agent you may delegate to. Returns a handle at
            once; collect the result with `check_delegation(handle)`. Put the
            decision the task asks for in `inputs.decision` when there is one."""
            handle = "dlg_" + uuid.uuid4().hex[:16]
            # Held before it is sent: the answer can arrive before `publish`
            # returns, and a reply for a handle we do not hold is refused.
            with self._cond:
                self.handles[handle] = {
                    "handle": handle, "to": to_agent, "task": task,
                    "state": "pending", "output": "", "error": None,
                    "child_session": "", "trace_id": ctx.trace_id,
                    "sent_at": _now(), "session_id": ctx.session_id,
                }
            out = self._outbound(ctx, to_agent, DELEGATE, task, inputs, handle=handle)
            if not out["ok"]:
                with self._cond:
                    self.handles.pop(handle, None)
                return out
            with self._cond:
                state = self.handles[handle]["state"]
            return {"ok": True, "handle": handle, "to": to_agent, "state": state,
                    "via": out["via"]}

        def check_delegation(handle: str, wait_s: float = 0) -> dict[str, Any]:
            """The state of a delegation by its handle, and its output once it
            has settled. `wait_s` blocks up to that many seconds for it."""
            deadline = _now() + max(0.0, min(float(wait_s or 0), MAX_WAIT_S))
            with self._cond:
                while True:
                    held = self.handles.get(handle)
                    if held is None:
                        return {"ok": False, "error": f"no delegation '{handle}' "
                                "was made by this agent"}
                    if held["state"] == "pending" and \
                            _now() - held["sent_at"] > self.handle_deadline_s:
                        held.update(state="failed", error=(
                            f"no answer from {held['to']} within "
                            f"{int(self.handle_deadline_s)}s"))
                    if held["state"] != "pending" or _now() >= deadline:
                        break
                    self._cond.wait(timeout=max(0.05, deadline - _now()))
                out = dict(held)
            return {"ok": out["state"] == "completed", **out}

        return {"send_message": send_message, "delegate": delegate,
                "check_delegation": check_delegation}

    # -- receiving ------------------------------------------------------------

    def on_delivery(self, subject: str, data: bytes,
                    headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
        """Decide on one delivery from this agent's consumer. Never raises."""
        parts = subject.split(".")
        mine = f"{self.policy.prefix}.agent.{_token(self.me)}"
        if not subject.startswith(mine + ".") or len(parts) != len(mine.split(".")) + 2:
            return self.record("bus_refused_inbound", trace_id="", reason=(
                f"subject {subject} is not one of {self.me}'s"), subject=subject)
        box, sender_token = parts[-2], parts[-1]
        try:
            body = json.loads(data.decode() if isinstance(data, bytes) else data)
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except Exception as e:                             # noqa: BLE001
            return self.record("bus_refused_inbound", trace_id="", subject=subject,
                               reason=f"undecodable: {e}")
        trace_id = str(body.get("trace_id") or "")
        sender = self.policy.agent_for_token(sender_token)
        if sender is None:
            return self.record("bus_refused_inbound", trace_id=trace_id, subject=subject,
                               reason=f"'{sender_token}' is nobody {self.me} may "
                                      "hear from")
        # Who sent it is who the broker let publish on this subject.
        if body.get("from") != sender:
            return self.record("bus_refused_inbound", trace_id=trace_id, frm=sender,
                               reason=f"the body claims to be from "
                                      f"'{body.get('from')}' but it was published "
                                      f"as '{sender}'")
        if box == "reply":
            return self._on_reply(sender, body)
        if box != "inbox":
            return self.record("bus_refused_inbound", trace_id=trace_id, frm=sender,
                               reason=f"unknown mailbox '{box}'")
        return self._on_inbox(sender, body)

    def _on_reply(self, sender: str, body: dict[str, Any]) -> dict[str, Any]:
        handle = str(body.get("handle") or "")
        with self._cond:
            held = self.handles.get(handle)
            if held is None or held["to"] != sender:
                pass
            else:
                held.update(state=str(body.get("state") or "failed"),
                            output=body.get("output") or "",
                            error=body.get("error"),
                            child_session=body.get("session_id") or "")
                self._cond.notify_all()
        if held is None or held["to"] != sender:
            return self.record("bus_refused_inbound", trace_id=body.get("trace_id", ""),
                               frm=sender, reason=f"a reply for '{handle}', which "
                               f"{self.me} did not send to {sender}")
        return self.record("bus_reply_received", trace_id=body.get("trace_id", ""),
                           frm=sender, handle=handle, state=held["state"],
                           child_session=held["child_session"])

    def _on_inbox(self, sender: str, body: dict[str, Any]) -> dict[str, Any]:
        kind = body.get("kind")
        trace_id = str(body.get("trace_id") or "")
        handle = str(body.get("handle") or "")
        depth = int(body.get("depth") or 1)
        chain = [str(p) for p in (body.get("chain") or [])]
        # The immediate sender is a principal whatever the body says.
        if not chain or chain[-1] != sender:
            chain.append(sender)
        reason = ""
        if kind not in (DELEGATE, MESSAGE):
            reason = f"unknown kind '{kind}'"
        if not reason:
            ok, why = self.policy.may_receive(sender, kind)
            reason = "" if ok else why
        if not reason and kind == DELEGATE and depth > self.policy.max_depth + 1:
            reason = f"delegation depth {depth} is past any bound this design sets"
        hops: list[dict[str, Any]] = []
        if not reason and self.hop_keys is not None:
            # The principals are the verified chain's, not the body's list.
            try:
                hops = list(body.get("hops") or [])
                chain = self.hop_keys.verify_chain(hops, me=self.me, sender=sender,
                                                   body=body)[:-1]
                task = str(hops[-1]["claims"]["task"])
                with self._cond:
                    now = _now()
                    for t, exp in list(self._seen_tasks.items()):
                        if exp < now:
                            del self._seen_tasks[t]
                    if task in self._seen_tasks:
                        raise HopError(f"hop for task {task} was already delivered "
                                       "(a replay)")
                    self._seen_tasks[task] = float(hops[-1]["claims"]["exp"])
            except HopError as e:
                reason = f"signed chain refused: {e}"
            except (KeyError, TypeError, ValueError) as e:
                reason = f"signed chain refused: malformed ({type(e).__name__})"
        if not reason:
            inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
            if kind == DELEGATE:
                named = named_decisions(self.policy, str(body.get("text") or ""),
                                        {**inputs, **({"decision": body["decision"]}
                                                      if body.get("decision") else {})})
            else:
                named = [str(body.get("decision"))] if body.get("decision") else []
            for d in named:
                reason = self.policy.separation_conflict(chain, d) or ""
                if reason:
                    break
        if reason:
            entry = self.record("bus_refused_inbound", trace_id=trace_id, frm=sender,
                                kind=kind, handle=handle, reason=reason, chain=chain)
            if kind == DELEGATE and handle and self.policy.may_reply_to(sender):
                self._reply(sender, {"handle": handle, "state": "refused",
                                     "error": f"refused by {self.me}: {reason}",
                                     "trace_id": trace_id})
            return entry
        ctx = HopContext(trace_id=trace_id, chain=chain + [self.me], depth=depth,
                         handle=handle, hops=hops)
        entry = self.record("bus_received", trace_id=trace_id, frm=sender, kind=kind,
                            handle=handle, depth=depth, chain=chain,
                            text=str(body.get("text") or "")[:200])
        self._pool.submit(self._run, ctx, sender, kind, body)
        return entry

    def _run(self, ctx: HopContext, sender: str, kind: str, body: dict[str, Any]) -> None:
        try:
            if self.run_task is None:
                result = {"state": "failed", "error": "no agent is bound to this inbox"}
            else:
                result = self.run_task(ctx, sender, kind, str(body.get("text") or ""),
                                       dict(body.get("inputs") or {}))
        except Exception as e:                             # noqa: BLE001
            result = {"state": "failed", "error": f"{type(e).__name__}: {e}"}
        self.record("bus_task_done", trace_id=ctx.trace_id, frm=sender, kind=kind,
                    handle=ctx.handle, state=result.get("state"),
                    session_id=result.get("session_id", ""), error=result.get("error"),
                    output=str(result.get("output") or "")[:400])
        if kind == DELEGATE and ctx.handle:
            self._reply(sender, {"handle": ctx.handle, "trace_id": ctx.trace_id,
                                 **{k: result.get(k) for k in
                                    ("state", "output", "error", "session_id")}})

    def _reply(self, to: str, fields: dict[str, Any]) -> None:
        body = {"id": uuid.uuid4().hex, "kind": REPLY, "from": self.me, "to": to,
                "trace_id": fields.get("trace_id", ""), **fields}
        try:
            self._publish(self.policy.reply(to, self.me), body)
            self.record("bus_reply_sent", trace_id=body["trace_id"], to=to,
                        handle=fields.get("handle"), state=fields.get("state"))
        except Exception as e:                             # noqa: BLE001
            self.record("bus_send_failed", trace_id=body["trace_id"], to=to,
                        kind=REPLY, reason=f"{type(e).__name__}: {e}")


def render_task(kind: str, sender: str, text: str, inputs: dict[str, Any]) -> str:
    """The prompt a received message or task becomes.

    `inputs.script`, a list of lines, is appended verbatim: it is how a
    scripted (stub-model) demo tells the next agent which tools to call. Every
    other input is shown as JSON.
    """
    head = f"[{'task delegated' if kind == DELEGATE else 'message'} from {sender}] {text}"
    rest = dict(inputs or {})
    script = rest.pop("script", None)
    lines = [head]
    if isinstance(script, list):
        lines += [str(s) for s in script]
    if rest:
        lines.append("Inputs: " + json.dumps(rest, sort_keys=True, default=str))
    return "\n".join(lines)


def separation_guard(policy: LinkPolicy, ctx: HopContext,
                     decision_of: Callable[[str], Optional[str]],
                     tools: dict[str, Callable[..., Any]],
                     record: Optional[Callable[..., Any]] = None
                     ) -> dict[str, Callable[..., Any]]:
    """Replace each tool whose decision is separated from one an upstream
    principal holds with a refusal. Only those: the rest are left exactly as
    they were, so their schemas are untouched."""
    upstream = [p for p in ctx.chain if p != policy.agent_id]
    if not upstream:
        return tools
    out = dict(tools)
    for name in tools:
        decision = decision_of(name)
        conflict = policy.separation_conflict(upstream, decision or "")
        if not conflict:
            continue

        def refused(_name: str = name, _why: str = conflict, **_: Any) -> dict[str, Any]:
            if record is not None:
                record("bus_refused_separation", trace_id=ctx.trace_id, tool=_name,
                       reason=_why, chain=ctx.chain)
            return {"ok": False, "error": f"refused: {_why}", "decision": decision_of(_name)}

        refused.__name__ = name
        refused.__doc__ = (f"Refused for this task: {conflict}")
        out[name] = refused
    return out


# -- the wire -----------------------------------------------------------------

class NatsTransport:
    """`nats-py` on a loop of its own, so the synchronous agent loop and the
    FastAPI worker can publish, and a pull consumer can feed the inbox."""

    def __init__(self, url: str, *, user: str, seed: str, inbox_prefix: str,
                 stream: str = "", consumer: str = "",
                 on_delivery: Optional[Callable[[str, bytes, dict[str, str]], Any]] = None,
                 connect_timeout: float = 5.0, publish_timeout: float = 5.0) -> None:
        # `user` names the connection; the identity is the NKey `seed`
        # (ADR-0118 v1.1): the broker holds only its public key.
        self.url, self.user, self.seed = url, user, seed
        self.inbox_prefix = inbox_prefix
        self.stream, self.consumer = stream, consumer
        self.on_delivery = on_delivery
        self.connect_timeout, self.publish_timeout = connect_timeout, publish_timeout
        self.connected = threading.Event()
        self.last_error = ""
        self._violations: list[str] = []
        self._loop: Any = None
        self._nc: Any = None
        self._js: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop = False

    # lifecycle
    def start(self, *, consume: bool = True) -> "NatsTransport":
        import asyncio

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True,
                                        name=f"nats-{self.user}")
        self._thread.start()
        self._task = asyncio.run_coroutine_threadsafe(self._main(consume), self._loop)
        return self

    def stop(self) -> None:
        import asyncio

        self._stop = True
        if self._nc is not None and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._nc.close(), self._loop).result(5)
            except Exception:                              # noqa: BLE001
                pass
        if self._loop is not None:
            if getattr(self, "_task", None) is not None:
                self._task.cancel()
                time.sleep(0.05)
            self._loop.call_soon_threadsafe(self._loop.stop)

    async def _error(self, e: Exception) -> None:
        text = str(e)
        self.last_error = text
        if "permissions violation" in text.lower():
            self._violations.append(text)
            del self._violations[:-50]

    async def _main(self, consume: bool) -> None:
        import asyncio

        import nats

        from ..security.nkey import install_nkeys_shim
        install_nkeys_shim()
        delay = 0.5
        while not self._stop:
            try:
                self._nc = await nats.connect(
                    self.url, nkeys_seed_str=self.seed,
                    inbox_prefix=self.inbox_prefix, name=self.user,
                    connect_timeout=self.connect_timeout, error_cb=self._error,
                    max_reconnect_attempts=-1)
                self._js = self._nc.jetstream()
                break
            except Exception as e:                         # noqa: BLE001
                self.last_error = f"connect: {e}"
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10)
        self.connected.set()
        if not consume or not self.stream or not self.consumer:
            return
        sub = None
        while not self._stop:
            try:
                if sub is None:
                    sub = await self._js.pull_subscribe_bind(self.consumer, self.stream)
                msgs = await sub.fetch(10, timeout=5)
            except asyncio.TimeoutError:
                continue
            except Exception as e:                         # noqa: BLE001
                if type(e).__name__ == "TimeoutError":
                    continue
                self.last_error = f"consume: {e}"
                sub = None
                await asyncio.sleep(2)
                continue
            for m in msgs:
                try:
                    await m.ack()
                except Exception as e:                     # noqa: BLE001
                    self.last_error = f"ack: {e}"
                if self.on_delivery is not None:
                    headers = dict(m.headers or {})
                    await self._loop.run_in_executor(
                        None, self.on_delivery, m.subject, m.data, headers)

    # sending
    def publish(self, subject: str, data: bytes, headers: dict[str, str]) -> None:
        import asyncio

        if not self.connected.wait(self.connect_timeout * 2) or self._js is None:
            raise BusRefused(f"not connected to the bus: {self.last_error}")
        seen = len(self._violations)
        fut = asyncio.run_coroutine_threadsafe(
            self._js.publish(subject, data, headers=headers,
                             timeout=self.publish_timeout), self._loop)
        try:
            fut.result(self.publish_timeout + 2)
        except Exception as e:                             # noqa: BLE001
            # A publish the broker refuses is dropped, not answered: the
            # request times out and the refusal arrives on the error callback.
            new = [v for v in self._violations[seen:] if subject in v]
            if new or any(subject in v for v in self._violations[-5:]):
                raise BusRefused(f"the broker refused it: {(new or self._violations)[-1]}") \
                    from e
            raise


def connect_from_env(policy: LinkPolicy, env: dict[str, str],
                     on_delivery: Callable[[str, bytes, dict[str, str]], Any]
                     ) -> Optional[NatsTransport]:
    """The generated stack's settings (see the local target), or None when
    the bus is the in-process one."""
    if env.get("ORGAGENTS_BUS", "in_process") != "nats":
        return None
    cfg = policy.config
    return NatsTransport(
        env.get("ORGAGENTS_BUS_URL", "nats://nats:4222"),
        user=env.get("ORGAGENTS_BUS_USER") or cfg["user"],
        seed=env.get("ORGAGENTS_BUS_NKEY_SEED", ""),
        inbox_prefix=cfg["inbox_prefix"], stream=cfg["stream"],
        consumer=cfg["consumer"], on_delivery=on_delivery).start()


def hop_keys_from_env(agent_id: str, env: dict[str, str]) -> HopKeys:
    """This worker's seed and every agent's public NKey. A worker on the NATS
    bus without them refuses to start: an unsigned chain is the sender's word
    (ADR-0118 v1.1)."""
    from ..security import nkey

    seed = env.get("ORGAGENTS_BUS_NKEY_SEED", "")
    keys = parse_bus_public_keys(env.get("ORGAGENTS_BUS_PUBLIC_KEYS", ""))
    if not seed or not keys:
        raise SystemExit(f"worker {agent_id}: ORGAGENTS_BUS_NKEY_SEED and "
                         "ORGAGENTS_BUS_PUBLIC_KEYS must be set on the NATS bus")
    try:
        mine = nkey.public_of(seed)
    except nkey.NKeyError as e:
        raise SystemExit(f"worker {agent_id}: ORGAGENTS_BUS_NKEY_SEED is not a seed: {e}")
    if keys.get(agent_id) != mine:
        raise SystemExit(f"worker {agent_id}: its seed is not the key the stack lists "
                         "for it in ORGAGENTS_BUS_PUBLIC_KEYS")
    return HopKeys(seed=seed, public_keys=keys)


def with_context(ctx: HopContext, **changes: Any) -> HopContext:
    return replace(ctx, **changes)


def bus_plan(ir: dict[str, Any]) -> dict[str, Any]:
    """The stream and consumers a compiled system needs, from its IR (the
    same subjects `orgagents.bus.SubjectNamespace` spells)."""
    from ..bus import SubjectNamespace

    tenant = (ir.get("tenant") or {}).get("id") or "local"
    subjects = SubjectNamespace(tenant=tenant)
    return {
        "stream": subjects.stream,
        "subjects": [subjects.agents_wildcard],
        "consumers": {subjects.consumer(a["id"]): subjects.mailbox(a["id"])
                      for a in ir.get("agents", [])},
    }


def bus_init(ir: dict[str, Any], *, url: str, user: str, seed: str,
             attempts: int = 30) -> int:
    """Create (or reconcile) the tenant's stream and a durable pull consumer
    per agent, as the bus operator (its NKey `seed`). Idempotent; run by
    `bus-init`."""
    import asyncio

    import nats
    from nats.js.api import AckPolicy, ConsumerConfig, RetentionPolicy, StorageType, StreamConfig

    from ..security.nkey import install_nkeys_shim

    install_nkeys_shim()
    plan = bus_plan(ir)

    async def main() -> int:
        nc = None
        for i in range(max(1, attempts)):
            try:
                nc = await nats.connect(url, nkeys_seed_str=seed or None, name=user or None,
                                        inbox_prefix=f"_INBOX_{user}" if user else "_INBOX",
                                        connect_timeout=3, max_reconnect_attempts=0)
                break
            except Exception as e:                         # noqa: BLE001
                print(f"bus-init: waiting for the bus ({e})", flush=True)
                await asyncio.sleep(min(1 + i, 5))
        if nc is None:
            print("bus-init: the bus never answered", flush=True)
            return 1
        js = nc.jetstream()
        stream = StreamConfig(name=plan["stream"], subjects=plan["subjects"],
                              retention=RetentionPolicy.LIMITS,
                              storage=StorageType.FILE, max_age=7 * 24 * 3600,
                              duplicate_window=120)
        try:
            await js.add_stream(stream)
        except Exception:                                  # noqa: BLE001
            await js.update_stream(stream)
        for durable, subject in plan["consumers"].items():
            config = ConsumerConfig(durable_name=durable, filter_subject=subject,
                                    ack_policy=AckPolicy.EXPLICIT, ack_wait=60,
                                    max_deliver=5)
            try:
                await js.add_consumer(plan["stream"], config)
            except Exception as e:                         # noqa: BLE001
                print(f"bus-init: consumer {durable}: {e}; recreating", flush=True)
                await js.delete_consumer(plan["stream"], durable)
                await js.add_consumer(plan["stream"], config)
        print(f"bus-init: stream {plan['stream']} on {plan['subjects'][0]}, "
              f"{len(plan['consumers'])} consumers", flush=True)
        await nc.close()
        return 0

    return asyncio.run(main())
