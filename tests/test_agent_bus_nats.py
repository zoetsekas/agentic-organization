"""The bus against a real nats-server (ADR-0118).

Runs the AYC design's generated broker configuration -- one user per agent,
subject permissions from the design's edges -- on a real server with
JetStream, creates the stream and consumers the way `orgagents bus-init` does,
and drives the CEO -> COO -> buyer delegation over it:

* the chain is one trace and the result comes back up it;
* the buyer's message to accounts receivable is refused by the buyer's worker;
* published anyway, with the buyer's own credentials, the broker refuses it;
* published by a mis-scoped operator identity, accounts receivable refuses it.

Needs `nats-py` and a `nats-server`: on PATH, or started from the pinned image
when a Docker CLI is available. Skipped, with the reason, otherwise.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

pytest.importorskip("nats", reason="nats-py is not installed (pip install 'orgagents[bus]')")

from orgagents.compiler import links as L  # noqa: E402
from orgagents.compiler.ir import build_ir  # noqa: E402
from orgagents.compiler.targets.local import BUS_IMAGE  # noqa: E402
from orgagents.runtime.agent_bus import (  # noqa: E402
    AgentMessenger,
    BusRefused,
    HopContext,
    LinkPolicy,
    NatsTransport,
    bus_init,
)
from orgagents.spec import load_binding, load_spec  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AYC = ROOT / "examples" / "ayc"
AGENTS = ("ceo_agent", "coo_agent", "buyer_agent", "ar_agent")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    # A Windows-containers daemon (GitHub's windows-latest) answers `docker
    # info` but cannot run the Linux nats image.
    probe = subprocess.run(["docker", "info", "--format", "{{.OSType}}"],
                           capture_output=True, text=True)
    return probe.returncode == 0 and probe.stdout.strip() == "linux"


@pytest.fixture(scope="module")
def ir():
    binding = load_binding(str(AYC / "ayc.local.binding.yaml"))
    target = next(t for t in binding.targets if t.target == "local")
    return build_ir(load_spec(str(AYC / "ayc.system.yaml")), target="local", binding=target)


@pytest.fixture(scope="module")
def server(ir, tmp_path_factory):
    """A nats-server with the generated configuration and fresh passwords."""
    passwords = {L.bus_password_ref(a.id): secrets.token_urlsafe(12) for a in ir.agents}
    passwords[L.BUS_ADMIN_PASSWORD_REF] = secrets.token_urlsafe(12)
    work = tmp_path_factory.mktemp("nats")
    port = _free_port()
    conf = L.nats_config(ir).replace('store_dir: "/data"', 'store_dir: "/tmp/js"')
    binary = shutil.which("nats-server")
    if binary:
        conf = conf.replace("port: 4222", f"port: {port}").replace(
            "http_port: 8222", "http_port: -1").replace(
            'store_dir: "/tmp/js"', f'store_dir: "{(work / "js").as_posix()}"')
        (work / "nats.conf").write_text(conf, encoding="utf-8")
        proc = subprocess.Popen([binary, "-c", str(work / "nats.conf")],
                                env={**os.environ, **passwords},
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stop = proc.terminate
    elif _docker_ok():
        (work / "nats.conf").write_text(conf, encoding="utf-8")
        name = f"orgagents-test-nats-{secrets.token_hex(4)}"
        env_args = [x for k, v in passwords.items() for x in ("-e", f"{k}={v}")]
        started = subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", name, *env_args,
             "-v", f"{work}:/etc/nats-test:ro", "-p", f"127.0.0.1:{port}:4222",
             BUS_IMAGE, "-c", "/etc/nats-test/nats.conf"], capture_output=True, text=True)
        if started.returncode != 0:
            pytest.skip(f"could not start {BUS_IMAGE}: {started.stderr.strip()[:200]}")

        def stop() -> None:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    else:
        pytest.skip("no nats-server on PATH and no usable Docker to start one")
    try:
        if not _wait_port(port):
            pytest.skip("the nats-server did not come up")
        url = f"nats://127.0.0.1:{port}"
        assert bus_init(ir.model_dump(mode="json"), url=url, user=L.BUS_ADMIN_USER,
                        password=passwords[L.BUS_ADMIN_PASSWORD_REF], attempts=20) == 0
        yield url, passwords
    finally:
        stop()


@pytest.fixture(scope="module")
def mesh(ir, server):
    """Four agents' messengers, each on its own connection and consumer."""
    url, passwords = server
    links = L.agent_links(ir)
    messengers: dict[str, AgentMessenger] = {}
    transports = []

    def join(agent_id, run_task=None):
        cfg = links[agent_id]
        m = AgentMessenger(LinkPolicy(cfg), None, run_task=run_task)
        t = NatsTransport(url, user=cfg["user"],
                          password=passwords[L.bus_password_ref(agent_id)],
                          inbox_prefix=cfg["inbox_prefix"], stream=cfg["stream"],
                          consumer=cfg["consumer"], on_delivery=m.on_delivery).start()
        assert t.connected.wait(15), t.last_error
        m.transport = t
        transports.append(t)
        messengers[agent_id] = m
        return m

    def runner(agent_id, script):
        def run(ctx, sender, kind, text, inputs):
            tools = messengers[agent_id].tools(HopContext(
                trace_id=ctx.trace_id, chain=ctx.chain, depth=ctx.depth,
                session_id=f"s-{agent_id}"))
            return {"state": "completed", "output": script(tools, text),
                    "error": None, "session_id": f"s-{agent_id}"}
        return run

    def buyer_does(tools, text):
        out = tools["send_message"]("ar_agent", "please clear SINV-1")
        return f"checked stock; ar: {out.get('error')}"

    def coo_does(tools, text):
        h = tools["delegate"]("buyer_agent", text)["handle"]
        got = tools["check_delegation"](h, wait_s=30)
        return f"buyer: {got.get('output') or got.get('error')}"

    ar_ran = []
    join("ar_agent", run_task=lambda *a: ar_ran.append(a) or {"state": "completed"})
    join("buyer_agent", run_task=runner("buyer_agent", buyer_does))
    join("coo_agent", run_task=runner("coo_agent", coo_does))
    join("ceo_agent")
    yield messengers, links, ar_ran, url, passwords
    for t in transports:
        t.stop()


def test_ceo_to_coo_to_buyer_over_nats_is_one_trace(mesh):
    messengers, *_ = mesh
    tools = messengers["ceo_agent"].tools(HopContext(trace_id="trace-e2e",
                                                     chain=["ceo_agent"], session_id="s-ceo"))
    started = tools["delegate"]("coo_agent", "review stock of AYC-CH-001")
    assert started["ok"], started
    done = tools["check_delegation"](started["handle"], wait_s=60)
    assert done["state"] == "completed", done
    assert "buyer: checked stock" in done["output"]
    assert "may not message to ar_agent" in done["output"]
    for agent in ("ceo_agent", "coo_agent", "buyer_agent"):
        assert messengers[agent].trace("trace-e2e"), agent
    received = [e for e in messengers["buyer_agent"].trace("trace-e2e")
                if e["event"] == "bus_received"]
    assert received and received[0]["chain"] == ["ceo_agent", "coo_agent"]


def test_the_broker_refuses_the_buyer_publishing_to_ar(mesh):
    messengers, links, ar_ran, url, passwords = mesh
    cfg = links["buyer_agent"]
    raw = NatsTransport(url, user=cfg["user"],
                        password=passwords[L.bus_password_ref("buyer_agent")],
                        inbox_prefix=cfg["inbox_prefix"], publish_timeout=2).start(consume=False)
    try:
        assert raw.connected.wait(15)
        body = {"id": "x1", "kind": "message", "from": "buyer_agent", "to": "ar_agent",
                "text": "clear it", "trace_id": "trace-broker"}
        with pytest.raises(BusRefused) as refused:
            raw.publish("orgagents.local.agent.ar_agent.inbox.buyer_agent",
                        json.dumps(body).encode(), {"Nats-Msg-Id": "x1"})
        assert "ermissions" in str(refused.value)
    finally:
        raw.stop()
    time.sleep(0.5)
    assert not messengers["ar_agent"].trace("trace-broker")


def test_a_mis_scoped_publish_is_refused_by_the_receiver(mesh):
    messengers, links, ar_ran, url, passwords = mesh
    admin = NatsTransport(url, user=L.BUS_ADMIN_USER,
                          password=passwords[L.BUS_ADMIN_PASSWORD_REF],
                          inbox_prefix=f"_INBOX_{L.BUS_ADMIN_USER}").start(consume=False)
    try:
        assert admin.connected.wait(15)
        body = {"id": "x2", "kind": "message", "from": "buyer_agent", "to": "ar_agent",
                "text": "clear it", "trace_id": "trace-receiver"}
        admin.publish("orgagents.local.agent.ar_agent.inbox.buyer_agent",
                      json.dumps(body).encode(), {"Nats-Msg-Id": "x2"})
    finally:
        admin.stop()
    end = time.time() + 15
    while time.time() < end and not messengers["ar_agent"].trace("trace-receiver"):
        time.sleep(0.1)
    events = messengers["ar_agent"].trace("trace-receiver")
    assert events and events[0]["event"] == "bus_refused_inbound", events
    assert ar_ran == []
