"""Selecting a bus backend at startup, and receiving from it.

`orgagents.bus` can publish; nothing consumed. This is the other half: the
worker that subscribes on an agent's own subject, decides whether the message
should be handed over at all, and answers a request.

The rule that shapes it: **receiving on a subject is not proof the sender was
allowed to send** (ADR-0059 rule 4). A publisher that bypassed our client, or
an operator who mis-scoped a subject, produces bytes on the wire that look
identical to a legitimate message. So the inbound path re-runs the same
org-chart check the outbound path ran, and refuses on its own account.

No NATS client is imported here either. The client is injected, and a fake one
is how this is tested — no broker exists in the environment this was written
in, so nothing below has been run against a real one.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Protocol

from ..bus import ChannelDurability, NatsBusAdapter, SubjectNamespace, configure_bus
from ..messaging import DeliveryError, MessageBus
from ..models import ChannelKind, Message

# Env the generated stack sets on every agent service (see the local target).
BACKEND_ENV = "ORGAGENTS_BUS"
URL_ENV = "ORGAGENTS_BUS_URL"
TENANT_ENV = "ORGAGENTS_TENANT"
DEFAULT_BACKEND = "in_process"


class Subscriber(Protocol):
    """The inbound half of a client: hand us messages for a subject."""

    def subscribe(self, subject: str, handler: Callable[[bytes], Optional[bytes]]) -> Any:
        ...


@dataclass
class InboundResult:
    """What the worker did with one delivery, and why."""

    accepted: bool
    reason: str = ""
    message: Optional[Message] = None
    reply: Optional[dict[str, Any]] = None


@dataclass
class BusWorker:
    """Receives bus messages for one agent and decides whether to act on them.

    `handler` is what actually runs the work — in the runtime that is a call
    into `AgentRuntime.run`. It is injected so this module does not depend on
    the agent loop, and so a test can assert the handler was *not* reached.
    """

    agent_id: str
    subjects: SubjectNamespace
    can_deliver: Callable[[str, str], bool]
    handler: Optional[Callable[[Message], Any]] = None
    received: list[InboundResult] = field(default_factory=list)

    @property
    def subject(self) -> str:
        return self.subjects.agent(self.agent_id)

    def handle(self, raw: bytes | str | Mapping[str, Any]) -> InboundResult:
        """Decide on one inbound delivery. Never raises on bad input.

        A malformed payload is refused rather than thrown: the bus is reachable
        by anything that can publish, so a decode error is an expected event on
        a boundary, not a bug in us.
        """
        try:
            data = raw if isinstance(raw, Mapping) else json.loads(
                raw.decode() if isinstance(raw, bytes) else raw
            )
            message = Message.model_validate(dict(data))
        except Exception as exc:                            # noqa: BLE001
            return self._record(InboundResult(False, f"undecodable: {exc}"))

        # Addressed to somebody else entirely: a subject mis-scope, not a
        # message for us.
        if message.to_agent_id and message.to_agent_id != self.agent_id:
            return self._record(InboundResult(
                False, f"addressed to '{message.to_agent_id}', not '{self.agent_id}'",
                message,
            ))

        # The check that matters. The sender claims an identity in the payload;
        # the org chart decides whether that identity may reach this agent.
        if message.to_agent_id and not self.can_deliver(
            message.from_agent_id, self.agent_id
        ):
            return self._record(InboundResult(
                False,
                f"'{message.from_agent_id}' may not reach '{self.agent_id}'",
                message,
            ))

        if self.handler is None:
            return self._record(InboundResult(True, "accepted; no handler bound",
                                              message))
        outcome = self.handler(message)
        reply = None
        if message.requires_response:
            reply = {
                "in_reply_to": message.id,
                "from_agent_id": self.agent_id,
                "body": getattr(outcome, "text", None) or str(outcome or ""),
            }
        return self._record(InboundResult(True, "handled", message, reply))

    def _record(self, result: InboundResult) -> InboundResult:
        self.received.append(result)
        return result

    def subscribe(self, client: Subscriber) -> Any:
        """Attach to the agent's own subject. The reply is JSON or nothing."""

        def on_delivery(raw: bytes) -> Optional[bytes]:
            result = self.handle(raw)
            if result.reply is None:
                return None
            return json.dumps(result.reply).encode()

        return client.subscribe(self.subject, on_delivery)


def configure_from_env(
    bus: MessageBus,
    *,
    client: Any = None,
    env: Optional[Mapping[str, str]] = None,
    durability: Optional[Mapping[ChannelKind, ChannelDurability]] = None,
    channels: tuple[ChannelKind, ...] = (ChannelKind.INTERNAL_BUS,),
) -> Optional[NatsBusAdapter]:
    """Select the bus backend the generated stack asked for.

    Must run *after* `Platform.__init__`, which registers its own transport for
    `INTERNAL_BUS`; configuring earlier would be overwritten by it and the bus
    would silently stay in-process.
    """
    env = env if env is not None else os.environ
    backend = env.get(BACKEND_ENV, DEFAULT_BACKEND)
    if backend == DEFAULT_BACKEND:
        return None
    if client is None:
        raise DeliveryError(
            f"{BACKEND_ENV}={backend} but no client was supplied; "
            f"the bus client is injected, never constructed here"
        )
    return configure_bus(
        bus,
        backend=backend,
        tenant=env.get(TENANT_ENV, "") or "untenanted",
        client=client,
        durability=durability,
        channels=channels,
    )
