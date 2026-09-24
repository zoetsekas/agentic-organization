"""One agent, one process: what `orgagents worker <agent>` runs (ADR-0109).

The local target has always generated one container per agent with the command
``orgagents worker <agent>``, and there was no such command, so the generated
stack could be parsed and never started. This is that command.

A worker is deliberately narrow:

* it loads the compiled system (``system.ir.json``) into a platform of its
  own, on its own database, so the org chart, mandates and separations it
  enforces are the compiled ones and nothing else;
* it mounts **only this agent's** backing systems, over HTTP, each with the
  credential of the capability it belongs to — and it can only reach the ones
  its container's networks route to, which the local target decides;
* it runs **only this agent**. Asked to run another it refuses, because a
  worker that runs whoever it is asked to is every agent at once, on one
  agent's network and credentials.

It answers on HTTP: ``GET /healthz``, ``GET /agent`` (who this is, what it
holds, who approves for it), ``POST /run`` (a prompt in, the reply and every
tool call out), ``POST /approve`` (a named approver releases one call, by a
signed token; ADR-0114) and ``POST /workflow`` (run a workflow the agent may
run; ADR-0110). All but ``/healthz`` need the worker's service token.
Delegation to *another* agent's container goes over the bus (ADR-0118):
`attach_bus` replaces the in-process `delegate`/`send_message` with ones that
publish to NATS under this agent's own broker credentials, consumes this
agent's inbox, and runs the agent on what it accepts. The edges it may use are
the ones compiled into its manifest (`agents/<id>.json`, key `links`), never
worked out here from the whole design. ``GET /bus/trace/<id>`` and
``GET /bus/events`` show what this worker sent, received and refused.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..models import MCPServerRef

DEFAULT_IR = "/app/system.ir.json"


# Module level, not inside `create_app`: FastAPI resolves annotations by name,
# and a class local to a function is not a name it can find.
class RunRequest(BaseModel):
    prompt: str
    agent_id: str = ""
    created_by: str = "worker"


class WorkflowRequest(BaseModel):
    workflow: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class ApproveRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # A signed release (ADR-0114). There is no `approver` field: who approved
    # is a claim inside the token, vouched for by the issuer that
    # authenticated the person, never a name the caller types.
    token: str


def _secret(ref: MCPServerRef) -> Optional[str]:
    """Resolve a server credential by name from the environment (ADR-0015)."""
    name = ref.secret_refs.get("token")
    return os.environ.get(name) if name else None


def http_transport(url: str, payload: Any, secret_ref: Optional[str]) -> Any:
    """POST a workflow run to an out-of-process engine (ADR-0056).

    Only reached after `call_endpoint` has checked tenant, egress, data
    classes, credential and approval. The credential is the engine's own,
    resolved by name from this process's environment; an agent container is
    not given one, so a real engine that requires it refuses — which is the
    engine authenticating as itself, not a fault here.
    """
    import urllib.request

    headers = {"content-type": "application/json"}
    key = os.environ.get(secret_ref) if secret_ref else None
    if key:
        headers["x-api-key"] = key
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        body = resp.read().decode("utf-8")
    try:
        return json.loads(body)
    except ValueError:
        return body


def build_worker(agent_id: str, *, ir_path: str = DEFAULT_IR, db: str = "",
                 base_url: str = "http://localhost:8000") -> Any:
    """The platform a worker runs on, with this agent's servers mounted."""
    from ..platform import Platform
    from .loader import load_system

    ir = json.loads(Path(ir_path).read_text(encoding="utf-8"))
    db = db or os.environ.get("ORGAGENTS_DB") or f"/tmp/{agent_id}.db"
    platform = Platform(db, base_url=base_url)
    load_system(platform, ir)
    agent = platform.org.agent(agent_id)
    if agent is None:
        known = ", ".join(a["id"] for a in ir.get("agents", []))
        raise SystemExit(f"no agent '{agent_id}' in the compiled system ({known})")
    for ref in agent.harness.mcp_servers:
        if ref.transport in ("http", "sse") and ref.url:
            platform.registry.mount_http(ref, token=_secret(ref), agent_id=agent_id)
    # An external workflow step leaves this container through the engine
    # invoker, and needs a way out (ADR-0110). The container's networks still
    # decide what it can reach.
    platform.runtime.workflow_transport = http_transport
    platform.compiled_agent = next(a for a in ir["agents"] if a["id"] == agent_id)
    return platform


def approvers(platform: Any) -> list[dict[str, str]]:
    """The people the design names as this agent's approvers."""
    humans = (getattr(platform, "compiled_agent", None) or {}).get("humans", [])
    return [{"person": h.get("person", ""), "name": h.get("name", ""),
             "title": h.get("role_title", "")}
            for h in humans if "approver" in (h.get("roles") or [])]


def describe(platform: Any, agent_id: str) -> dict[str, Any]:
    """Who this worker is, in the terms a person talking to it cares about."""
    agent = platform.org.agent(agent_id)
    tools = sorted(platform.harness.build(agent)) if agent else []
    unit = platform.org.unit(agent.org_unit_id) if agent and \
        hasattr(platform.org, "unit") and agent.org_unit_id else None
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "team": getattr(unit, "name", None) or agent.org_unit_id,
        "mandate": list(agent.mandate),
        "runtime": getattr(agent.harness.runtime, "value", str(agent.harness.runtime)),
        "model": f"{agent.harness.model.provider}:{agent.harness.model.model}",
        "servers": sorted({r.name for r in agent.harness.mcp_servers}),
        "tools": tools,
        "human": agent.human.display_name if agent.human else None,
        "approvers": approvers(platform),
    }


def audit_sink(agent_id: str, path: str = "") -> Any:
    """Where a worker writes its approval events (ADR-0114): one JSON line
    per event, to a file on the container's writable scratch space, and to
    the structured log so `docker logs` carries it off the container."""
    from ..observability import log_event

    state = os.environ.get("ORGAGENTS_STATE_DIR", "")
    target = Path(path or os.environ.get("ORGAGENTS_AUDIT_LOG")
                  or (f"{state}/{agent_id}-audit.jsonl" if state
                      else f"/tmp/{agent_id}-audit.jsonl"))

    def write(event: str, entry: dict[str, Any]) -> None:
        log_event(event, **entry)
        try:
            with target.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass  # the log line above is still the record

    return write


def nonce_store(agent_id: str, path: str = "") -> Any:
    """The used-release nonces, on the worker's persistent state volume
    (ORGAGENTS_STATE_DIR) so a restart does not forget them (ADR-0114);
    memory only when there is none."""
    from ..security.service_auth import NonceStore

    state = os.environ.get("ORGAGENTS_STATE_DIR", "")
    target = path or (f"{state}/{agent_id}-nonces.jsonl" if state else "")
    return NonceStore(target or None)


def load_links(agent_id: str, manifest: str = "") -> Optional[dict[str, Any]]:
    """This agent's compiled bus edges, from its manifest (ADR-0118)."""
    path = Path(manifest or os.environ.get("ORGAGENTS_MANIFEST")
                or f"/app/agents/{agent_id}.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    links = data.get("links")
    return links if isinstance(links, dict) and links.get("agent") == agent_id else None


def attach_bus(platform: Any, agent_id: str, links: dict[str, Any], *,
               transport: Any = None, env: Optional[dict[str, str]] = None,
               audit: Any = None) -> Any:
    """Put this agent on the bus (ADR-0118) and return its messenger.

    * a runtime tool hook swaps the in-process delegation and messaging tools
      for the bus ones, bound to the run's hop (trace, chain, depth), and
      refuses any tool whose decision is separated from one an upstream
      principal in that chain holds;
    * received tasks and messages run as this agent, on a thread of the
      messenger's, with the hop they arrived with.

    `transport` is injected in tests; otherwise it comes from the environment
    the local target generates (`ORGAGENTS_BUS=nats`, URL, user, password).
    """
    import threading

    from .agent_bus import (AgentMessenger, HopContext, LinkPolicy, connect_from_env,
                            render_task, separation_guard)

    policy = LinkPolicy(links)
    current = threading.local()
    messenger = AgentMessenger(policy, transport,
                               audit=audit or audit_sink(agent_id))

    def run_task(ctx: HopContext, sender: str, kind: str, text: str,
                 inputs: dict[str, Any]) -> dict[str, Any]:
        current.ctx = ctx
        try:
            result = platform.runtime.run(agent_id, render_task(kind, sender, text, inputs),
                                          created_by=f"agent:{sender}")
        finally:
            current.ctx = None
        return {"state": result.state.value, "output": result.output,
                "error": result.error, "session_id": result.session_id}

    messenger.run_task = run_task

    def hook(agent: Any, session_id: str, tools: dict[str, Any]) -> dict[str, Any]:
        if agent.id != agent_id:
            return tools
        ctx = getattr(current, "ctx", None)
        ctx = HopContext(trace_id=ctx.trace_id, chain=list(ctx.chain), depth=ctx.depth,
                         session_id=session_id, handle=ctx.handle) if ctx else \
            HopContext(trace_id=session_id, chain=[agent_id], session_id=session_id)
        platform.runtime.sessions.log(session_id, "bus_hop", actor=agent_id, payload={
            "trace_id": ctx.trace_id, "chain": ctx.chain, "depth": ctx.depth,
            "handle": ctx.handle})
        # The in-process versions would run another agent inside this
        # container, under this agent's identity: gone, not merely unused.
        # (A sub-agent is a tool of this agent, ADR-0027, and stays.)
        for name in ("assign", "check", "gather", "read_inbox"):
            tools.pop(name, None)
        tools.update(messenger.tools(ctx))
        current.hop = ctx
        return tools

    def guard(agent: Any, session_id: str, tools: dict[str, Any]) -> dict[str, Any]:
        # After the policy wrapper, so a separated call is refused before it
        # can even stop for approval: no approver can release it.
        ctx = getattr(current, "hop", None)
        if agent.id != agent_id or ctx is None:
            return tools
        return separation_guard(
            policy, ctx, lambda n: platform.harness.decision_class(agent, n), tools,
            record=messenger.record)

    platform.runtime.tool_hooks.append(hook)
    platform.runtime.tool_guards.append(guard)
    if transport is None:
        messenger.transport = connect_from_env(policy, dict(env if env is not None
                                                            else os.environ),
                                               messenger.on_delivery)
    platform.messenger = messenger
    return messenger


def create_app(platform: Any, agent_id: str, *, service_token: Optional[str] = None,
               approval_public_keys: Optional[str] = None,
               nonces: Any = None) -> Any:
    """The worker's HTTP face (ADR-0109), authenticated (ADR-0114).

    Every route but `/healthz` needs this worker's service token as a bearer:
    only the platform and the chat surface hold it, and no agent holds
    another's, so an agent that can route to a neighbour still cannot make it
    act. There is no default token; a worker without one refuses to start.
    """
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse

    from ..security.service_auth import (ApprovalTokenError, bearer_matches,
                                         parse_public_keys, verify_approval)

    token = service_token if service_token is not None else \
        os.environ.get("ORGAGENTS_WORKER_TOKEN", "")
    if not token:
        raise SystemExit(f"worker {agent_id}: ORGAGENTS_WORKER_TOKEN is not set; "
                         "a worker with no token would run for anyone who can reach it")
    # Public keys only (ADR-0114 v1.1): this worker can check a release and
    # can never sign one. `kid:key,kid:key`, so the issuer can rotate.
    keys = parse_public_keys(approval_public_keys if approval_public_keys is not None
                             else os.environ.get("ORGAGENTS_APPROVAL_PUBLIC_KEYS", ""))
    if platform.harness.approvals.audit is None:
        platform.harness.approvals.audit = audit_sink(agent_id)
    #: Nonces of approvals already used, until they would have expired anyway;
    #: persisted, so a restart does not reopen the replay window.
    seen = nonces if nonces is not None else nonce_store(agent_id)

    app = FastAPI(title=f"orgagents worker: {agent_id}")

    @app.middleware("http")
    async def require_service_token(request: Request, call_next: Any) -> Any:
        if request.url.path != "/healthz" and not bearer_matches(
                request.headers.get("authorization", ""), token):
            return JSONResponse({"detail": f"{agent_id}'s worker needs its service token"},
                                status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "agent": agent_id}

    @app.get("/agent")
    def agent() -> dict[str, Any]:
        return describe(platform, agent_id)

    @app.get("/audit")
    def audit() -> dict[str, Any]:
        """The approval events this worker has recorded, newest last."""
        return {"agent": agent_id, "events": list(platform.harness.approvals.events)}

    @app.get("/bus/events")
    def bus_events() -> dict[str, Any]:
        """What this worker sent, received and refused over the bus."""
        messenger = getattr(platform, "messenger", None)
        return {"agent": agent_id, "connected": _bus_connected(messenger),
                "events": list(messenger.events) if messenger else []}

    @app.get("/bus/trace/{trace_id}")
    def bus_trace(trace_id: str) -> dict[str, Any]:
        """This worker's part of one trace (ADR-0118)."""
        messenger = getattr(platform, "messenger", None)
        return {"agent": agent_id,
                "events": messenger.trace(trace_id) if messenger else []}

    @app.post("/approve")
    def approve(req: ApproveRequest) -> dict[str, Any]:
        """A named approver releases exactly one call (ADR-0109, ADR-0114).

        The release is a token signed for this agent, this tool and exactly
        these arguments by an issuer that authenticated the person; a name in
        a request field is not an identity. The person must still be one the
        design names as this agent's approver, the token is good once, and the
        mandate is still checked when the call is made, so approving never
        widens it.
        """
        try:
            claims = verify_approval(keys, req.token, agent_id=agent_id,
                                     tool=req.tool, arguments=req.arguments)
        except ApprovalTokenError as e:
            raise HTTPException(403, f"approval refused: {e}") from e
        if claims["nonce"] in seen:
            raise HTTPException(403, "approval refused: this release was already used")
        approver = claims["approver"]
        allowed = approvers(platform)
        if not any(approver in (a["person"], a["name"]) for a in allowed):
            names = ", ".join(f"{a['name']} ({a['person']})" for a in allowed) or "nobody"
            raise HTTPException(
                403, f"'{approver}' is not an approver for {agent_id}; "
                     f"the design names {names}")
        if not seen.use(claims["nonce"], float(claims["exp"])):
            raise HTTPException(403, "approval refused: this release was already used")
        record = platform.harness.approvals.grant(agent_id, req.tool, req.arguments,
                                                  approver, nonce=claims["nonce"])
        return {"ok": True, "approval": record}

    @app.post("/workflow")
    def workflow(req: WorkflowRequest) -> dict[str, Any]:
        """Run a workflow this agent may run (ADR-0110). A step bound to an
        engine is invoked through the binding, as egress; a human step
        pauses the run and says where."""
        me = platform.org.agent(agent_id)
        session = platform.sessions.create(agent_id)
        out = platform.runtime._workflow_tools(me, session.id)["run_workflow"](
            req.workflow, req.inputs)
        events = [e.payload for e in platform.sessions.events(session.id)
                  if e.type == "workflow"]
        return {"agent": agent_id, "session_id": session.id, **out,
                "events": events}

    @app.post("/run")
    def run(req: RunRequest) -> dict[str, Any]:
        if req.agent_id and req.agent_id != agent_id:
            raise HTTPException(
                403, f"this worker runs '{agent_id}' only; '{req.agent_id}' has "
                     "a worker of its own")
        result = platform.runtime.run(agent_id, req.prompt, created_by=req.created_by)
        return {
            "agent": agent_id,
            "session_id": result.session_id,
            "state": result.state.value,
            "output": result.output,
            "error": result.error,
            "tool_calls": result.tool_calls,
            # The run is the root of its trace; every hop it causes carries
            # this id (ADR-0118).
            "trace_id": result.session_id,
        }

    return app


def _bus_connected(messenger: Any) -> bool:
    transport = getattr(messenger, "transport", None)
    connected = getattr(transport, "connected", None)
    return bool(connected.is_set()) if connected is not None else transport is not None


def serve(agent_id: str, *, host: str = "0.0.0.0", port: int = 8000,
          ir_path: str = DEFAULT_IR, db: str = "") -> int:
    import uvicorn

    platform = build_worker(agent_id, ir_path=ir_path, db=db)
    links = load_links(agent_id)
    if links is not None and os.environ.get("ORGAGENTS_BUS", "in_process") == "nats":
        attach_bus(platform, agent_id, links)
        print(f"worker {agent_id} on the bus as '{links['user']}', reaching "
              f"{', '.join(links['outbound']) or 'nobody'}", flush=True)
    print(f"worker {agent_id} listening on {host}:{port}", flush=True)
    uvicorn.run(create_app(platform, agent_id), host=host, port=port,
                log_level="warning")
    return 0
