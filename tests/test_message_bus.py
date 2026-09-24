"""The NATS/JetStream message bus adapter (ADR-0059).

Nothing here talks to a broker. `nats-py` is not installed and must not become
required, so the client is injected and these tests drive a fake — the same
seam `runtime/endpoints.py` uses for HTTP. The generated NATS service is
likewise parsed, never started: no daemon exists in this environment.
"""
from pathlib import Path

import pytest
import yaml

from orgagents.bus import (
    BACKENDS,
    DEFAULT_BACKEND,
    ChannelDurability,
    NatsBusAdapter,
    SubjectNamespace,
    configure_bus,
)
from orgagents.compiler import compile_system
from orgagents.compiler.base import register_builtin_targets
from orgagents.fabric.tenants import TenantRegistry
from orgagents.messaging import DeliveryError
from orgagents.models import Agent, ChannelKind, Message
from orgagents.spec import load_spec
from orgagents.store import AGENTS, Store

ROOT = Path(__file__).resolve().parents[1]


class FakeNats:
    """Records what would have gone on the wire, and answers requests."""

    def __init__(self, reply: bytes = b'{"ok": true}') -> None:
        self.published: list[tuple[str, bytes]] = []
        self.requested: list[tuple[str, bytes, float]] = []
        self.streams: list[dict] = []
        self._reply = reply

    def publish(self, subject, payload):
        self.published.append((subject, payload))

    def request(self, subject, payload, timeout):
        self.requested.append((subject, payload, timeout))
        return self._reply

    def add_stream(self, **spec):
        self.streams.append(spec)

    @property
    def subjects(self):
        return [s for s, _ in self.published] + [s for s, _, _ in self.requested]


DURABILITY = {
    ChannelKind.INTERNAL_BUS: ChannelDurability(ChannelKind.INTERNAL_BUS,
                                                durable=True, max_age_seconds=86400),
    ChannelKind.SLACK: ChannelDurability(ChannelKind.SLACK, durable=False),
}


@pytest.fixture()
def wired(platform):
    client = FakeNats()
    adapter = configure_bus(
        platform.bus, backend="nats", tenant="northwind", client=client,
        durability=DURABILITY,
        channels=(ChannelKind.INTERNAL_BUS, ChannelKind.SLACK),
    )
    return platform, adapter, client


def _agent(platform, agent_id: str) -> Agent:
    return platform.store.get(AGENTS, agent_id, Agent)


# -- delivery --------------------------------------------------------------


def test_the_adapter_publishes_a_message_it_is_given(wired):
    platform, _, client = wired
    ceo = _agent(platform, "agt_ceo")
    msg = platform.bus.send(ceo, to_agent_id="agt_cfo", channel=ChannelKind.INTERNAL_BUS,
                            subject="quarter close", body="numbers when you can")
    assert len(client.published) == 1
    subject, payload = client.published[0]
    assert msg.payload["delivery"]["mode"] == "publish"
    assert msg.payload["delivery"]["subject"] == subject
    assert b"quarter close" in payload


def test_requires_response_maps_onto_request_reply_not_a_new_concept(wired):
    platform, _, client = wired
    ceo = _agent(platform, "agt_ceo")
    msg = platform.bus.send(ceo, to_agent_id="agt_cfo", channel=ChannelKind.INTERNAL_BUS,
                            subject="ping", requires_response=True)
    assert client.published == []
    assert len(client.requested) == 1
    assert msg.payload["delivery"]["mode"] == "request"
    assert msg.payload["delivery"]["response"] == {"ok": True}


def test_delivery_without_a_client_is_an_error_not_a_silent_drop():
    adapter = NatsBusAdapter(subjects=SubjectNamespace(tenant="t"),
                             can_deliver=lambda a, b: True)
    with pytest.raises(DeliveryError):
        adapter.deliver(Message(from_agent_id="a", to_agent_id="b"),
                        ChannelKind.INTERNAL_BUS)


# -- subjects (rule 1) -----------------------------------------------------


def test_every_subject_is_tenant_prefixed(wired):
    platform, adapter, client = wired
    ceo = _agent(platform, "agt_ceo")
    platform.bus.send(ceo, to_agent_id="agt_cfo", channel=ChannelKind.INTERNAL_BUS)
    platform.bus.send(ceo, channel=ChannelKind.INTERNAL_BUS,
                      channel_address="#finance-ops", subject="fyi")
    assert client.subjects
    for subject in client.subjects:
        assert subject.startswith("orgagents.northwind.")
    assert adapter.subjects.wildcard == "orgagents.northwind.>"


def test_two_tenants_do_not_share_a_subject_even_on_one_broker():
    a = SubjectNamespace(tenant="northwind")
    b = SubjectNamespace(tenant="contoso")
    assert a.agent("agt_cfo") != b.agent("agt_cfo")
    assert not a.agent("agt_cfo").startswith(b.prefix)


def test_a_tenant_id_that_is_not_a_subject_token_is_made_into_one():
    # A dot in a tenant id would silently create a subject level of its own.
    assert SubjectNamespace(tenant="north.wind a/b").prefix == "orgagents.north-wind-a-b"


# -- durability (rule 3) ---------------------------------------------------


def test_durability_is_declared_per_channel_not_applied_to_everything(wired):
    platform, adapter, client = wired
    streams = {s["channel"]: s for s in adapter.declared_streams}
    assert set(streams) == {"internal_bus"}, "slack declared no durability"
    assert [s["name"] for s in client.streams] == ["northwind-internal_bus"]

    ceo = _agent(platform, "agt_ceo")
    bus_msg = platform.bus.send(ceo, to_agent_id="agt_cfo",
                                channel=ChannelKind.INTERNAL_BUS)
    slack_msg = platform.bus.send(ceo, channel=ChannelKind.SLACK,
                                  channel_address="#ops")
    assert bus_msg.payload["delivery"]["durable"] is True
    assert bus_msg.payload["delivery"]["stream"] == "northwind-internal_bus"
    assert slack_msg.payload["delivery"]["durable"] is False


def test_a_client_without_jetstream_still_carries_the_undurable_channels():
    class CoreOnly(FakeNats):
        add_stream = None

    client = CoreOnly()
    adapter = NatsBusAdapter(subjects=SubjectNamespace(tenant="t"), client=client,
                             durability=DURABILITY, can_deliver=lambda a, b: True)
    assert [s["channel"] for s in adapter.declare_streams()] == ["internal_bus"]


# -- the bus is a transport, not an authorization boundary (rule 4) --------


def test_an_agent_the_org_chart_refuses_cannot_reach_the_target_over_the_bus(wired):
    platform, _, client = wired
    analyst = _agent(platform, "agt_fin_analyst")
    assert not platform.org.can_delegate("agt_fin_analyst", "agt_platform_eng")
    with pytest.raises(DeliveryError):
        platform.bus.send(analyst, to_agent_id="agt_platform_eng",
                          channel=ChannelKind.INTERNAL_BUS, subject="do this")
    # And nothing reached the wire: a refusal after publishing is not a refusal.
    assert client.published == [] and client.requested == []


def test_the_direct_path_is_still_refused_when_the_bus_is_the_transport(wired):
    platform, _, client = wired
    analyst = _agent(platform, "agt_fin_analyst")
    with pytest.raises(DeliveryError):
        platform.bus.send(analyst, to_agent_id="agt_platform_eng",
                          channel=ChannelKind.DIRECT_TOOL)
    assert client.subjects == []


def test_an_unaddressed_message_on_a_channel_subject_is_not_a_delegation(wired):
    # Leaving a message on a shared subject is the lower-privilege path the
    # delegation refusal points at; it reaches nobody in particular.
    platform, _, client = wired
    analyst = _agent(platform, "agt_fin_analyst")
    platform.bus.send(analyst, channel=ChannelKind.INTERNAL_BUS,
                      channel_address="#finance-ops", body="context for whoever")
    assert len(client.published) == 1


# -- the in-process bus remains the default --------------------------------


def test_the_in_process_bus_is_the_default_backend(platform):
    assert DEFAULT_BACKEND == "in_process" and DEFAULT_BACKEND in BACKENDS
    client = FakeNats()
    assert configure_bus(platform.bus, backend=DEFAULT_BACKEND, client=client) is None
    ceo = _agent(platform, "agt_ceo")
    msg = platform.bus.send(ceo, to_agent_id="agt_cfo",
                            channel=ChannelKind.INTERNAL_BUS, subject="hello")
    assert client.subjects == []
    assert msg.payload["delivery"]["status"] == "recorded"
    assert platform.bus.inbox("agt_cfo")


def test_an_unknown_backend_is_refused_rather_than_ignored(platform):
    with pytest.raises(DeliveryError):
        configure_bus(platform.bus, backend="kafka")


def test_importing_the_adapter_does_not_require_nats_py():
    # nats-py is the optional `bus` extra (ADR-0117): importing the adapter,
    # the compiler's edges or the worker's messenger never imports it.
    import subprocess
    import sys

    code = ("import sys; import orgagents.bus, orgagents.compiler.links, "
            "orgagents.runtime.agent_bus; print('nats' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**__import__("os").environ,
                              "PYTHONPATH": str(ROOT / "src")})
    assert out.stdout.strip() == "False", out.stderr


# -- the generated per-tenant stack ----------------------------------------


@pytest.fixture(scope="module")
def tenant_compose(tmp_path_factory):
    register_builtin_targets()
    tmp = tmp_path_factory.mktemp("bus-tenant")
    spec = load_spec(ROOT / "examples" / "acme" / "acme.system.yaml")
    tenant = TenantRegistry(Store(tmp / "fabric.db")).register(
        id="northwind", name="Northwind", cloud_boundary="proj-northwind")
    result = compile_system(spec, targets=["local"], out_dir=tmp,
                            tenant=tenant.to_ir())[0]
    files = {f.path: f.content for f in result.files}
    return yaml.safe_load(files["docker-compose.yaml"])


def test_a_tenant_gets_its_own_nats_on_its_own_network_and_volume(tenant_compose):
    nats = tenant_compose["services"]["nats"]
    assert nats["image"] == "nats:2.15.0-alpine"
    source, _, mount = nats["volumes"][0].partition(":")
    assert source.startswith("northwind-") and source in tenant_compose["volumes"]
    assert nats["networks"] == [n for n in tenant_compose["networks"]
                                if n.endswith("control")]


def test_the_generated_bus_has_jetstream_enabled_on_that_volume(tenant_compose):
    # JetStream and its store are in the generated nats.conf now, beside the
    # per-agent users (ADR-0117); the store is the tenant's own volume.
    nats = tenant_compose["services"]["nats"]
    assert nats["command"] == ["-c", "/etc/nats/nats.conf"]
    assert nats["volumes"][0].endswith(":/data")
    assert "./nats/nats.conf:/etc/nats/nats.conf:ro" in nats["volumes"]


def test_the_generated_bus_carries_the_tenant_subject_prefix(tenant_compose):
    nats = tenant_compose["services"]["nats"]
    assert nats["labels"]["org.agentic.subject_prefix"] == "orgagents.northwind"
    for name, service in tenant_compose["services"].items():
        if not name.startswith("agent-"):
            continue
        env = service["environment"]
        assert env["ORGAGENTS_BUS_SUBJECT_PREFIX"] == "orgagents.northwind"
        # Every agent container is on the tenant's NATS (ADR-0117).
        assert env["ORGAGENTS_BUS"] == "${ORGAGENTS_BUS:-nats}"


def test_the_fabric_bus_is_not_the_tenants_bus():
    fabric = yaml.safe_load((ROOT / "docker" / "compose" / "fabric.yml").read_text())
    nats = fabric["services"]["nats"]
    assert nats["image"] == "nats:2.15.0-alpine"
    assert nats["networks"] == ["fabric"]
    assert "fabric-nats" in fabric["volumes"]
    assert "carries no tenant payloads" in (
        ROOT / "docker" / "compose" / "fabric.yml").read_text()
