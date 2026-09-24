"""The doors on a running stack (ADR-0114).

* an approval comes from a signed release by an authenticated person, never
  from a name in a request field — forged, unsigned, altered, replayed,
  another agent's, and a person the design does not name are all refused;
* grants and their use are written to the worker's audit log;
* a worker answers nothing but `/healthz` without its own service token;
* the designer refuses to start trusted-proxy mode with no way to tell its
  proxy apart, and in that mode believes X-User only from the proxy;
* the local target publishes on 127.0.0.1 only, keeps `control` internal, does
  not publish telemetry, hardens agents and sandboxes, and gives each worker
  its own token and only the issuer's *public* keys -- no private key
  material in any worker's environment;
* approvals are Ed25519 (ADR-0114 v1.1): a token signed with a key the worker
  does not trust is refused, rotation keeps the previous key trusted, and a
  replay is refused even after the worker restarts;
* the chat's copy of the token format and local_stack's key format agree with
  the platform's.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from orgagents.harness.builder import ApprovalGrants
from orgagents.security.service_auth import (
    ApprovalTokenError,
    NonceStore,
    bearer_matches,
    generate_signing_key,
    issue_approval,
    parse_public_keys,
    public_key_of,
    verify_approval,
)

ROOT = Path(__file__).resolve().parents[1]
AYC = ROOT / "examples" / "ayc"
COMMITTED = AYC / "generated" / "local"
#: The issuer's private key (only the chat holds one) and the public key a
#: worker verifies with.
SIGNING, PUBLIC = generate_signing_key("k-test")
KEY = PUBLIC
ARGS = {"invoice_id": "SINV-1", "amount": 10}


# -- approval tokens -----------------------------------------------------------

def test_a_signed_release_verifies_for_exactly_that_call():
    tok = issue_approval(SIGNING, "ap_agent", "pay", ARGS, "p_coo")
    claims = verify_approval(KEY, tok, agent_id="ap_agent", tool="pay",
                             arguments={"amount": 10, "invoice_id": "SINV-1"})
    assert claims["approver"] == "p_coo"
    assert claims["aud"] == "ap_agent" and claims["kid"] == "k-test"


@pytest.mark.parametrize("mutate,why", [
    (lambda t: "e30.AAAA", "not signed"),
    (lambda t: t.split(".")[0] + ".", "not signed"),
    (lambda t: "garbage", "not signed"),
])
def test_a_forged_or_unsigned_release_is_refused(mutate, why):
    tok = mutate(issue_approval(SIGNING, "ap_agent", "pay", ARGS, "p_coo"))
    with pytest.raises(ApprovalTokenError, match=why):
        verify_approval(KEY, tok, agent_id="ap_agent", tool="pay", arguments=ARGS)


def test_a_release_is_bound_to_agent_tool_arguments_and_time():
    tok = issue_approval(SIGNING, "ap_agent", "pay", ARGS, "p_coo", now=1000, ttl=60)
    with pytest.raises(ApprovalTokenError, match="other arguments"):
        verify_approval(KEY, tok, agent_id="ap_agent", tool="pay",
                        arguments={**ARGS, "amount": 100}, now=1000)
    with pytest.raises(ApprovalTokenError, match="another tool"):
        verify_approval(KEY, tok, agent_id="ap_agent", tool="refund", arguments=ARGS,
                        now=1000)
    with pytest.raises(ApprovalTokenError, match="expired"):
        verify_approval(KEY, tok, agent_id="ap_agent", tool="pay", arguments=ARGS,
                        now=2000)


def test_a_release_for_another_agent_is_refused():
    tok = issue_approval(SIGNING, "buyer_agent", "pay", ARGS, "p_coo")
    with pytest.raises(ApprovalTokenError, match="another agent"):
        verify_approval(KEY, tok, agent_id="ap_agent", tool="pay", arguments=ARGS)


def test_a_release_signed_with_a_wrong_key_is_refused():
    other, _ = generate_signing_key("k-test")          # same kid, other key
    stranger, _ = generate_signing_key("k-stranger")   # a kid nobody trusts
    for key in (other, stranger):
        tok = issue_approval(key, "ap_agent", "pay", ARGS, "p_coo")
        with pytest.raises(ApprovalTokenError, match="not signed"):
            verify_approval(KEY, tok, agent_id="ap_agent", tool="pay", arguments=ARGS)
    # A public key is not a signing key: holding it signs nothing.
    with pytest.raises(Exception):
        tok = issue_approval(PUBLIC, "ap_agent", "pay", ARGS, "p_coo")
        verify_approval(KEY, tok, agent_id="ap_agent", tool="pay", arguments=ARGS)


def test_rotation_trusts_listed_keys_by_kid():
    new_signing, new_public = generate_signing_key("k-new")
    trusted = f"{new_public},{PUBLIC}"
    for key in (new_signing, SIGNING):
        tok = issue_approval(key, "ap_agent", "pay", ARGS, "p_coo")
        assert verify_approval(trusted, tok, agent_id="ap_agent", tool="pay",
                               arguments=ARGS)["approver"] == "p_coo"
    tok = issue_approval(SIGNING, "ap_agent", "pay", ARGS, "p_coo")
    with pytest.raises(ApprovalTokenError, match="not signed"):   # dropped
        verify_approval(new_public, tok, agent_id="ap_agent", tool="pay", arguments=ARGS)
    assert public_key_of(new_signing) == new_public
    assert set(parse_public_keys(trusted)) == {"k-new", "k-test"}


def test_used_nonces_survive_a_restart(tmp_path):
    import time

    path = tmp_path / "nonces.jsonl"
    first = NonceStore(path)
    assert first.use("n1", time.time() + 60)
    assert not first.use("n1", time.time() + 60)
    first.use("old", time.time() - 1)                  # already lapsed
    again = NonceStore(path)                            # the worker restarted
    assert "n1" in again and not again.use("n1", time.time() + 60)
    assert "old" not in again


def test_bearer_matching():
    assert bearer_matches("Bearer abc", "abc")
    assert not bearer_matches("Bearer abd", "abc")
    assert not bearer_matches("", "abc")
    assert not bearer_matches("Bearer ", "")


def test_grants_and_their_use_are_audited():
    seen = []
    grants = ApprovalGrants(audit=lambda e, entry: seen.append((e, entry)))
    grants.grant("ap", "pay", ARGS, "p_coo", nonce="n1")
    assert grants.consume("ap", "pay", ARGS)
    assert [e for e, _ in seen] == ["approval_granted", "approval_consumed"]
    assert all(entry["approver"] == "p_coo" and entry["nonce"] == "n1"
               for _, entry in seen)
    grants.ttl_seconds = -1
    grants.grant("ap", "pay", ARGS, "p_coo")
    assert grants.consume("ap", "pay", ARGS) is None
    assert grants.events[-1]["event"] == "approval_expired"


# -- the worker over HTTP ------------------------------------------------------

class _Runtime:
    def run(self, agent_id, prompt, created_by=""):
        return SimpleNamespace(session_id="s1", state=SimpleNamespace(value="done"),
                               output="ok", error=None, tool_calls=[])


@pytest.fixture
def worker_client(tmp_path):
    from fastapi.testclient import TestClient

    from orgagents.runtime import worker

    platform = SimpleNamespace(
        harness=SimpleNamespace(approvals=ApprovalGrants()),
        runtime=_Runtime(),
        compiled_agent={"humans": [
            {"person": "p_coo", "name": "Clark", "roles": ["approver"]},
            {"person": "p_ap", "name": "Beverly", "roles": ["owner"]}]},
    )
    platform.harness.approvals.audit = worker.audit_sink(
        "ap_agent", str(tmp_path / "audit.jsonl"))
    app = worker.create_app(platform, "ap_agent", service_token="svc",
                            approval_public_keys=KEY,
                            nonces=worker.nonce_store("ap_agent",
                                                      str(tmp_path / "nonces.jsonl")))
    return TestClient(app), platform, tmp_path / "audit.jsonl"


AUTH = {"Authorization": "Bearer svc"}


def test_run_without_a_token_is_401(worker_client):
    client, _, _ = worker_client
    assert client.post("/run", json={"prompt": "hi"}).status_code == 401
    assert client.post("/run", json={"prompt": "hi"},
                       headers={"Authorization": "Bearer other"}).status_code == 401
    assert client.post("/workflow", json={"workflow": "x"}).status_code == 401
    assert client.post("/approve", json={"tool": "t", "token": "x"}).status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.post("/run", json={"prompt": "hi"}, headers=AUTH).status_code == 200


def test_a_worker_with_no_token_refuses_to_start():
    from orgagents.runtime import worker

    with pytest.raises(SystemExit):
        worker.create_app(SimpleNamespace(), "ap_agent", service_token="")


def test_an_approver_named_in_a_field_is_not_an_approval(worker_client):
    client, platform, _ = worker_client
    # The old request shape: a name, no signature.
    r = client.post("/approve", headers=AUTH,
                    json={"tool": "pay", "arguments": ARGS, "approver": "p_coo"})
    assert r.status_code == 422
    r = client.post("/approve", headers=AUTH,
                    json={"tool": "pay", "arguments": ARGS, "token": "e30.forged"})
    assert r.status_code == 403
    assert platform.harness.approvals.events == []


def test_a_signed_release_grants_once_and_is_audited(worker_client):
    client, platform, audit = worker_client
    tok = issue_approval(SIGNING, "ap_agent", "pay", ARGS, "p_coo")
    body = {"tool": "pay", "arguments": ARGS, "token": tok}
    r = client.post("/approve", headers=AUTH, json=body)
    assert r.status_code == 200 and r.json()["approval"]["approver"] == "p_coo"
    assert client.post("/approve", headers=AUTH, json=body).status_code == 403  # replay
    assert platform.harness.approvals.consume("ap_agent", "pay", ARGS)
    logged = [json.loads(line)["event"] for line in audit.read_text(encoding="utf-8").splitlines()]
    assert logged == ["approval_granted", "approval_consumed"]
    assert client.get("/audit", headers=AUTH).json()["events"][0]["approver"] == "p_coo"


def test_a_signed_release_by_someone_the_design_does_not_name_is_refused(worker_client):
    client, _, _ = worker_client
    tok = issue_approval(SIGNING, "ap_agent", "pay", ARGS, "p_ap")   # the owner
    r = client.post("/approve", headers=AUTH,
                    json={"tool": "pay", "arguments": ARGS, "token": tok})
    assert r.status_code == 403 and "not an approver" in r.json()["detail"]


def test_a_replay_after_the_worker_restarts_is_refused(worker_client):
    from fastapi.testclient import TestClient

    from orgagents.runtime import worker

    client, platform, audit = worker_client
    body = {"tool": "pay", "arguments": ARGS,
            "token": issue_approval(SIGNING, "ap_agent", "pay", ARGS, "p_coo")}
    assert client.post("/approve", headers=AUTH, json=body).status_code == 200
    # A new process over the same state path: nothing carried in memory.
    restarted = TestClient(worker.create_app(
        platform, "ap_agent", service_token="svc", approval_public_keys=KEY,
        nonces=worker.nonce_store("ap_agent", str(audit.parent / "nonces.jsonl"))))
    r = restarted.post("/approve", headers=AUTH, json=body)
    assert r.status_code == 403 and "already used" in r.json()["detail"]


def test_a_worker_with_a_wrong_key_refuses_over_http(worker_client):
    client, _, _ = worker_client
    wrong, _ = generate_signing_key("k-test")
    r = client.post("/approve", headers=AUTH, json={
        "tool": "pay", "arguments": ARGS,
        "token": issue_approval(wrong, "ap_agent", "pay", ARGS, "p_coo")})
    assert r.status_code == 403 and "not signed" in r.json()["detail"]


# -- the designer's auth mode --------------------------------------------------

def test_trusted_proxy_without_a_secret_refuses_to_start(tmp_path, monkeypatch):
    from orgagents.api import create_app
    from orgagents.designer.auth import AuthConfigurationError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORGAGENTS_DESIGNER_AUTH", "trusted_proxy")
    monkeypatch.delenv("ORGAGENTS_PROXY_SECRET", raising=False)
    monkeypatch.delenv("ORGAGENTS_PROXY_SOURCES", raising=False)
    with pytest.raises(AuthConfigurationError):
        create_app(str(tmp_path / "d.db"))


def test_trusted_proxy_believes_x_user_only_from_the_proxy(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from orgagents.api import create_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORGAGENTS_DESIGNER_AUTH", "trusted_proxy")
    monkeypatch.setenv("ORGAGENTS_PROXY_SECRET", "p-secret")
    client = TestClient(create_app(str(tmp_path / "d.db")))
    assert client.get("/api/designer/whoami",
                      headers={"X-User": "alice"}).status_code == 401
    assert client.get("/api/designer/whoami", headers={
        "X-User": "alice", "X-Orgagents-Proxy-Secret": "wrong"}).status_code == 401
    who = client.get("/api/designer/whoami", headers={
        "X-User": "alice", "X-Orgagents-Proxy-Secret": "p-secret"})
    assert who.status_code == 200 and who.json()["user_id"] == "alice"


def test_trusted_proxy_by_source_address():
    from orgagents.designer.auth import Authenticator, MissingCredentials
    from orgagents.designer.models import DesignerSettings

    auth = Authenticator(DesignerSettings(persistence="memory", auth_mode="trusted_proxy"),
                         proxy_sources=["10.0.0.0/24"])
    auth.require_proxy_guard()
    assert auth.authenticate(user_header="a", client_host="10.0.0.7").user_id == "a"
    with pytest.raises(MissingCredentials):
        auth.authenticate(user_header="a", client_host="192.168.1.2")


def test_the_designer_defaults_to_single_user_local_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from orgagents.api import create_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORGAGENTS_DESIGNER_AUTH", raising=False)
    client = TestClient(create_app(str(tmp_path / "d.db")))
    assert client.app.state.designer_auth.settings.auth_mode == "none"


def test_the_designer_compose_is_local_only_and_hardened():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    designer = compose["services"]["designer"]
    assert all(str(p).startswith("127.0.0.1:") for p in designer["ports"])
    assert designer["environment"]["ORGAGENTS_DESIGNER_AUTH"] == "none"
    _hardened(designer)


# -- the generated stack -------------------------------------------------------

def _hardened(service):
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    pids = service.get("pids_limit") or \
        service.get("deploy", {}).get("resources", {}).get("limits", {}).get("pids")
    assert pids and pids > 0
    assert "/tmp" in [t.split(":")[0] for t in service["tmpfs"]]


@pytest.fixture(scope="module")
def stack():
    import tempfile

    from orgagents.compiler import compile_system
    from orgagents.spec import load_binding, load_spec

    out = Path(tempfile.mkdtemp())
    compile_system(load_spec(str(AYC / "ayc.system.yaml")), targets=["local"],
                   out_dir=out, binding=load_binding(str(AYC / "ayc.local.binding.yaml")))
    return (yaml.safe_load((out / "local/docker-compose.yaml").read_text(encoding="utf-8")),
            (out / "local/.env.example").read_text(encoding="utf-8"))


def test_published_ports_are_loopback_only(stack):
    compose, _ = stack
    published = {n: s["ports"] for n, s in compose["services"].items() if s.get("ports")}
    assert published, "the designer at least is published"
    for name, ports in published.items():
        assert all(str(p).startswith("127.0.0.1:") for p in ports), name
    assert "ports" not in compose["services"]["telemetry"]


def test_control_is_internal_and_ingress_cannot_reach_out(stack):
    compose, _ = stack
    assert compose["networks"]["control"] == {"internal": True}
    assert compose["networks"]["ingress"]["driver_opts"][
        "com.docker.network.bridge.enable_ip_masquerade"] == "false"
    on_ingress = [n for n, s in compose["services"].items()
                  if "ingress" in (s.get("networks") or [])]
    assert on_ingress == ["designer"]


def test_agents_and_sandboxes_are_hardened(stack):
    compose, _ = stack
    for name, service in compose["services"].items():
        if name.startswith(("agent-", "sandbox-")):
            _hardened(service)
    assert any(t.startswith("/workspace") for t in
               compose["services"]["sandbox-finance--finance_ops"]["tmpfs"])


def test_the_stacks_own_services_are_hardened_too(stack):
    """ADR-0114 v1.2: state, bus, artifacts, telemetry and the in-stack
    designer -- read-only, no capabilities (none added back), no privilege
    escalation, a process bound, writable paths on tmpfs or volumes."""
    compose, _ = stack
    services = compose["services"]
    for name in ("state", "nats", "artifacts", "telemetry", "designer", "bus-init"):
        _hardened(services[name])
        assert "cap_add" not in services[name], name
    assert services["state"]["user"] == "70:70"
    assert "/var/run/postgresql" in services["state"]["tmpfs"]
    assert any(v.endswith(":/var/lib/postgresql/data") for v in services["state"]["volumes"])
    assert any(v.endswith(":/data") for v in services["nats"]["volumes"])
    assert any(v.endswith(":/data") for v in services["artifacts"]["volumes"])
    designer = services["designer"]
    assert any(v.endswith(":/var/lib/orgagents") for v in designer["volumes"])
    assert "/var/lib/orgagents/designer.db" in designer["command"]


def test_the_broker_holds_only_public_nkeys_and_each_worker_only_its_seed(stack):
    compose, env = stack
    services = compose["services"]
    broker = json.dumps(services["nats"]["environment"])
    assert "SEED" not in broker and "PASSWORD" not in broker
    assert "ORGAGENTS_BUS_NKEY_BUYER_AGENT" in broker
    assert "ORGAGENTS_BUS_ADMIN_SEED" in json.dumps(services["bus-init"]["environment"])
    for name, service in services.items():
        if not name.startswith("agent-"):
            continue
        suffix = name[len("agent-"):].upper()
        e = service["environment"]
        assert e["ORGAGENTS_BUS_NKEY_SEED"] == f"${{ORGAGENTS_BUS_SEED_{suffix}}}"
        assert e["ORGAGENTS_BUS_PUBLIC_KEYS"] == "${ORGAGENTS_BUS_PUBLIC_KEYS}"
        seeds = [v for v in e.values() if "ORGAGENTS_BUS_SEED_" in str(v)
                 or "ADMIN_SEED" in str(v)]
        assert seeds == [f"${{ORGAGENTS_BUS_SEED_{suffix}}}"], name
        assert "BUS_PASSWORD" not in json.dumps(e)
    assert "ORGAGENTS_BUS_SEED_BUYER_AGENT=" in env and "BUS_PASSWORD" not in env


def test_local_stack_generates_nkeys_and_retires_bus_passwords(tmp_path):
    from orgagents.security import nkey

    stack_mod = _load("ayc_local_stack", AYC / "local_stack.py")
    ir = {"agents": [{"id": "buyer_agent"}, {"id": "ap_agent"}]}
    have = {"ORGAGENTS_BUS_PASSWORD_BUYER_AGENT": "old", "ORGAGENTS_BUS_ADMIN_PASSWORD": "x"}
    added = stack_mod.ensure_bus_nkeys(have, ir)
    seed = have["ORGAGENTS_BUS_SEED_BUYER_AGENT"]
    assert nkey.public_of(seed) == have["ORGAGENTS_BUS_NKEY_BUYER_AGENT"]
    assert have["ORGAGENTS_BUS_PUBLIC_KEYS"].startswith("buyer_agent:U")
    assert "ORGAGENTS_BUS_ADMIN_SEED" in added
    assert stack_mod.ensure_bus_nkeys(have, ir) == []          # stable once made
    assert all(k.startswith(stack_mod.RETIRED) for k in
               ("ORGAGENTS_BUS_PASSWORD_BUYER_AGENT", "ORGAGENTS_BUS_ADMIN_PASSWORD"))


def test_each_worker_gets_its_own_token_and_only_public_keys(stack):
    compose, env = stack
    for name, service in compose["services"].items():
        if not name.startswith("agent-"):
            continue
        suffix = name[len("agent-"):].upper()
        e = service["environment"]
        assert e["ORGAGENTS_WORKER_TOKEN"] == f"${{ORGAGENTS_WORKER_TOKEN_{suffix}}}"
        assert e["ORGAGENTS_APPROVAL_PUBLIC_KEYS"] == "${ORGAGENTS_APPROVAL_PUBLIC_KEYS}"
        # No private key material, and nothing it could be derived from.
        flat = json.dumps(e)
        for secret in ("SIGNING_KEY", "APPROVAL_SECRET", "ORGAGENTS_APPROVAL_KEY"):
            assert secret not in flat, (name, secret)
        assert not [k for k in e if k.startswith("ORGAGENTS_WORKER_TOKEN_")]
        assert f"ORGAGENTS_WORKER_TOKEN_{suffix}=" in env
        # Used nonces are on a volume, so a restart does not forget them.
        assert e["ORGAGENTS_STATE_DIR"] == "/var/lib/orgagents"
        assert any(v.endswith(":/var/lib/orgagents") for v in service["volumes"])


def test_the_workstation_overlay_publishes_on_loopback_only():
    overlay = (COMMITTED / "overlays" / "20-ayc-workstation.yaml").read_text(encoding="utf-8")
    import re

    for port in re.findall(r'"([0-9.:]+:\d+)"', overlay):
        if port.count(":") >= 1 and not port.startswith("127.0.0.1:"):
            pytest.fail(f"published on every interface: {port}")
    assert "14317" not in overlay


# -- the copies agree ----------------------------------------------------------

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_chat_signs_releases_the_worker_accepts(monkeypatch):
    chat = _load("ayc_chat_server", AYC / "chat" / "server.py")
    monkeypatch.setattr(chat, "APPROVAL_SIGNING_KEY", SIGNING)
    tok = chat.issue_approval("ap_agent", "pay", ARGS, "p_coo")
    assert verify_approval(KEY, tok, agent_id="ap_agent", tool="pay",
                           arguments=ARGS)["approver"] == "p_coo"


def test_only_the_chat_is_given_the_signing_key():
    import re

    compose = yaml.safe_load((COMMITTED / "docker-compose.yaml").read_text(encoding="utf-8"))
    assert "ORGAGENTS_APPROVAL_SIGNING_KEY" not in json.dumps(compose)
    # The overlay uses Compose-only tags (!override), so it is read as text:
    # split into top-level service blocks and find who names the key.
    overlay = (COMMITTED / "overlays" / "20-ayc-workstation.yaml").read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^  (?=[A-Za-z0-9_.-]+:\s*$)", overlay.split("\nservices:", 1)[1])
    holders = {b.split(":", 1)[0] for b in blocks if "ORGAGENTS_APPROVAL_SIGNING_KEY" in b}
    assert holders == {"chat"}


def test_the_chat_checks_passcodes(monkeypatch):
    chat = _load("ayc_chat_server", AYC / "chat" / "server.py")
    monkeypatch.setenv("CHAT_PASSCODE_P_COO", "right")
    assert chat.check_passcode("p_coo", "right")
    assert not chat.check_passcode("p_coo", "wrong")
    assert not chat.check_passcode("p_ceo", "")


def test_local_stack_generates_keys_the_platform_reads():
    stack_mod = _load("ayc_local_stack", AYC / "local_stack.py")
    signing, public = stack_mod.new_keypair()
    assert public_key_of(signing) == public
    tok = issue_approval(signing, "ap_agent", "pay", ARGS, "p_coo")
    assert verify_approval(public, tok, agent_id="ap_agent", tool="pay",
                           arguments=ARGS)["approver"] == "p_coo"
