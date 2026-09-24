"""The asynchronous message transport: NATS with JetStream (ADR-0059).

`messaging.MessageBus` keeps its in-process delivery as the default, because
tests and single-process mode have one process and need no broker. This module
is the alternative backend, selected by configuration, for the generated Docker
stack where each agent is its own container and nothing shares memory.

Three things it deliberately is not:

* **Not a dependency.** `nats-py` is not installed and must not become
  required, so the client is injected — anything with `publish` and `request`
  (or two callables) satisfies it. That is the same seam `runtime/endpoints.py`
  uses for HTTP, and it is what makes this testable against a fake.
* **Not an authorization boundary** (ADR-0059 rule 4, ADR-0058 restated).
  Delivery runs *after* `MessageBus.send` has applied its checks, and an
  addressed message is refused here too when the org chart refuses it.
  Subscribing to a subject is not permission to be reached.
* **Not verified.** No daemon exists in this repository: the NATS service is
  generated into Compose and parsed, never started. Nothing below has spoken to
  a real broker.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Protocol

from .messaging import DeliveryError, MessageBus, Transport
from .models import ChannelKind, Message

# The one place the subject namespace is spelled. Every subject is
# tenant-prefixed (ADR-0059 rule 1) so that two tenants whose brokers were
# wrongly merged would still not share a subject — the prefix is a second line
# behind the per-tenant instance, not the isolation itself.
SUBJECT_ROOT = "orgagents"
UNTENANTED = "local"

# The in-process bus stays the default; NATS is opt-in (`ORGAGENTS_BUS=nats`).
DEFAULT_BACKEND = "in_process"
BACKENDS = ("in_process", "nats")


def _token(value: str) -> str:
    """NATS subject tokens cannot contain `.`, ` `, `*` or `>`."""
    out = "".join(c if c.isalnum() or c in "-_" else "-" for c in (value or ""))
    return out or "unknown"


@dataclass(frozen=True)
class SubjectNamespace:
    """Tenant-prefixed subjects. Nothing else builds a subject string."""

    tenant: str = UNTENANTED
    root: str = SUBJECT_ROOT

    @property
    def prefix(self) -> str:
        return f"{self.root}.{_token(self.tenant)}"

    def agent(self, agent_id: str) -> str:
        return f"{self.prefix}.agent.{_token(agent_id)}"

    def channel(self, kind: ChannelKind, address: str = "") -> str:
        tail = f".{_token(address)}" if address else ""
        return f"{self.prefix}.channel.{_token(kind.value)}{tail}"

    def for_message(self, msg: Message) -> str:
        """An addressed message goes to the recipient; the rest to the channel."""
        if msg.to_agent_id:
            return self.agent(msg.to_agent_id)
        return self.channel(msg.channel, msg.channel_address)

    @property
    def wildcard(self) -> str:
        return f"{self.prefix}.>"

    # -- agent-to-agent over JetStream (ADR-0118) ---------------------------
    # The sender is the *last* token, so a broker permission that lets an
    # agent publish only to `<to>.inbox.<itself>` is what vouches for who
    # sent it: the receiver reads the sender from the subject, never from
    # the payload.

    def inbox(self, to_agent_id: str, from_agent_id: str) -> str:
        return f"{self.agent(to_agent_id)}.inbox.{_token(from_agent_id)}"

    def reply(self, to_agent_id: str, from_agent_id: str) -> str:
        return f"{self.agent(to_agent_id)}.reply.{_token(from_agent_id)}"

    def mailbox(self, agent_id: str) -> str:
        """Everything addressed to one agent: its inbox and its replies."""
        return f"{self.agent(agent_id)}.>"

    @property
    def agents_wildcard(self) -> str:
        return f"{self.prefix}.agent.>"

    @property
    def stream(self) -> str:
        """The tenant's one JetStream stream for agent mail."""
        return "ORGAGENTS_" + _token(self.tenant).upper().replace("-", "_")

    def consumer(self, agent_id: str) -> str:
        return "agent_" + _token(agent_id)


@dataclass(frozen=True)
class ChannelDurability:
    """Durability declared *per channel* (ADR-0059 rule 3).

    Plain core NATS is at-most-once. A channel whose messages must outlive a
    restart says so and gets a JetStream stream; everything else does not pay
    for persistence it never needed.
    """

    channel: ChannelKind
    durable: bool = True
    stream: str = ""
    max_age_seconds: Optional[int] = None
    replicas: int = 1

    def stream_name(self, subjects: SubjectNamespace) -> str:
        return self.stream or f"{_token(subjects.tenant)}-{_token(self.channel.value)}"


class NatsClient(Protocol):
    """The shape of the injected client — `nats-py`'s or a fake's."""

    def publish(self, subject: str, payload: bytes) -> Any: ...

    def request(self, subject: str, payload: bytes, timeout: float) -> Any: ...


@dataclass
class NatsBusAdapter:
    """Delivers `Message`s over NATS, behind the `messaging.Transport` seam."""

    subjects: SubjectNamespace = field(default_factory=SubjectNamespace)
    client: Optional[NatsClient] = None
    durability: Mapping[ChannelKind, ChannelDurability] = field(default_factory=dict)
    # How an addressed delivery is authorized. Injected rather than assumed so
    # the bus cannot be wired up without one; `attach` passes the org chart's.
    can_deliver: Optional[Callable[[str, str], bool]] = None
    request_timeout: float = 5.0
    declared_streams: list[dict[str, Any]] = field(default_factory=list)

    # -- wiring ------------------------------------------------------------

    def declare_streams(self) -> list[dict[str, Any]]:
        """Create the JetStream stream for each channel that declared one.

        Idempotent by intent: `add_stream` on an existing stream is an update
        upstream, and a client that cannot do it (a core-NATS-only build) is
        not an error — the channel simply has no durability, which the returned
        records show.
        """
        declared: list[dict[str, Any]] = []
        add = getattr(self.client, "add_stream", None)
        for rule in self.durability.values():
            if not rule.durable:
                continue
            spec = {
                "name": rule.stream_name(self.subjects),
                "subjects": [self.subjects.channel(rule.channel, "") + ".>",
                             self.subjects.channel(rule.channel, "")],
                "max_age": rule.max_age_seconds,
                "num_replicas": rule.replicas,
                "channel": rule.channel.value,
            }
            if callable(add):
                add(**{k: v for k, v in spec.items() if k != "channel"})
            declared.append(spec)
        self.declared_streams = declared
        return declared

    def transport(self, channel: ChannelKind) -> Transport:
        """A `messaging.Transport` for one channel kind."""

        def send(msg: Message) -> dict[str, Any]:
            return self.deliver(msg, channel)

        return send

    def attach(
        self,
        bus: MessageBus,
        channels: tuple[ChannelKind, ...] = (ChannelKind.INTERNAL_BUS,),
    ) -> "NatsBusAdapter":
        """Register this adapter on a `MessageBus` for the given channels."""
        if self.can_deliver is None:
            self.can_deliver = bus.org.can_delegate
        self.declare_streams()
        for channel in channels:
            bus.register_transport(channel, self.transport(channel))
        return self

    # -- delivery ----------------------------------------------------------

    def deliver(self, msg: Message, channel: ChannelKind) -> dict[str, Any]:
        if self.client is None:
            raise DeliveryError("no NATS client is bound to the bus adapter")
        # The wire is not the boundary (ADR-0058, ADR-0059 rule 4). Reaching a
        # named agent is reaching them, whichever transport carries it, so the
        # same org-chart check the direct path makes is made here — before the
        # bytes exist, because a refusal after publishing is not a refusal.
        # Unaddressed traffic (a message left on a channel subject) is not
        # addressed at anyone and is not a delegation.
        if msg.to_agent_id and self.can_deliver is not None:
            if not self.can_deliver(msg.from_agent_id, msg.to_agent_id):
                raise DeliveryError(
                    f"{msg.from_agent_id} may not reach {msg.to_agent_id} over the "
                    "bus; subscribing to a subject is not permission to be reached"
                )
        subject = self.subjects.for_message(msg)
        rule = self.durability.get(channel)
        durable = bool(rule and rule.durable)
        payload = json.dumps(msg.model_dump(mode="json"), sort_keys=True).encode()
        result: dict[str, Any] = {
            "transport": "nats",
            "subject": subject,
            "tenant": self.subjects.tenant,
            "durable": durable,
            "stream": rule.stream_name(self.subjects) if durable and rule else "",
            "message_id": msg.id,
        }
        # `requires_response` already means "I expect an answer", so it maps
        # onto NATS request/reply rather than becoming a second concept
        # (ADR-0059 rule 2).
        if msg.requires_response:
            reply = self.client.request(subject, payload, self.request_timeout)
            result["mode"] = "request"
            result["response"] = _decode(reply)
            result["status"] = "answered"
        else:
            self.client.publish(subject, payload)
            result["mode"] = "publish"
            result["status"] = "published"
        return result


def _decode(reply: Any) -> Any:
    """A reply is whatever the client returns; JSON when it is JSON."""
    data = getattr(reply, "data", reply)
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", "replace")
    if isinstance(data, str):
        try:
            return json.loads(data)
        except ValueError:
            return data
    return data


def configure_bus(
    bus: MessageBus,
    *,
    backend: str = DEFAULT_BACKEND,
    tenant: str = UNTENANTED,
    client: Optional[NatsClient] = None,
    durability: Optional[Mapping[ChannelKind, ChannelDurability]] = None,
    channels: tuple[ChannelKind, ...] = (ChannelKind.INTERNAL_BUS,),
) -> Optional[NatsBusAdapter]:
    """Select the backend for a bus. Returns None for the in-process default."""
    if backend == DEFAULT_BACKEND:
        return None
    if backend not in BACKENDS:
        raise DeliveryError(f"unknown message bus backend '{backend}'")
    adapter = NatsBusAdapter(
        subjects=SubjectNamespace(tenant=tenant),
        client=client,
        durability=dict(durability or {}),
    )
    return adapter.attach(bus, channels=channels)
