"""Agent-to-agent communication.

Two modes, one API:

* **Direct tool call** — synchronous, in-process, used when one agent hands
  work to a report or peer. Delegation legality is checked against the org
  chart before the call is made.
* **Enterprise channels** — asynchronous fan-out to Slack/Teams/email/webhooks
  via pluggable transports, plus an always-available internal bus that stores
  messages for later retrieval.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .models import Agent, ChannelKind, Message
from .org import OrgChart
from .store import MESSAGES, Store

Transport = Callable[[Message], dict[str, Any]]


class DeliveryError(RuntimeError):
    pass


class MessageBus:
    """Routes messages between agents and to enterprise channels."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.org = OrgChart(store)
        self._transports: dict[ChannelKind, Transport] = {}
        self._handlers: dict[str, Callable[[Message], Any]] = {}

    # -- wiring ------------------------------------------------------------

    def register_transport(self, channel: ChannelKind, transport: Transport) -> None:
        """Attach a real Slack/Teams/email client for a channel kind."""
        self._transports[channel] = transport

    def register_handler(self, agent_id: str, handler: Callable[[Message], Any]) -> None:
        """Register the in-process receiver for an agent (direct tool calls)."""
        self._handlers[agent_id] = handler

    # -- sending -----------------------------------------------------------

    def send(
        self,
        sender: Agent,
        *,
        to_agent_id: Optional[str] = None,
        channel: ChannelKind = ChannelKind.DIRECT_TOOL,
        channel_address: str = "",
        subject: str = "",
        body: str = "",
        session_id: Optional[str] = None,
        requires_response: bool = False,
        payload: Optional[dict[str, Any]] = None,
    ) -> Message:
        if channel is ChannelKind.DIRECT_TOOL:
            if not to_agent_id:
                raise DeliveryError("direct tool calls need a recipient agent")
            if not self.org.can_delegate(sender.id, to_agent_id):
                raise DeliveryError(
                    f"{sender.name} may not call {to_agent_id} directly; "
                    "route through a shared manager or an enterprise channel"
                )
        if channel not in sender.channels and channel is not ChannelKind.INTERNAL_BUS:
            raise DeliveryError(f"{sender.name} is not enabled for channel {channel.value}")

        msg = Message(
            from_agent_id=sender.id,
            to_agent_id=to_agent_id,
            channel=channel,
            channel_address=channel_address,
            subject=subject,
            body=body,
            session_id=session_id,
            requires_response=requires_response,
            payload=payload or {},
        )
        self.store.put(MESSAGES, msg, parent=to_agent_id or channel_address, name=subject)

        transport = self._transports.get(channel)
        if transport is not None:
            msg.payload["delivery"] = transport(msg)
            self.store.put(MESSAGES, msg, parent=to_agent_id or channel_address)
        elif channel is ChannelKind.DIRECT_TOOL:
            handler = self._handlers.get(to_agent_id or "")
            if handler is None:
                raise DeliveryError(f"agent {to_agent_id} has no active receiver")
            msg.payload["response"] = handler(msg)
            self.store.put(MESSAGES, msg, parent=to_agent_id)
        return msg

    def reply(self, sender: Agent, original: Message, body: str, **kw: Any) -> Message:
        return self.send(
            sender,
            to_agent_id=original.from_agent_id,
            channel=original.channel,
            channel_address=original.channel_address,
            subject=f"Re: {original.subject}",
            body=body,
            session_id=original.session_id,
            **kw,
        )

    # -- reading -----------------------------------------------------------

    def inbox(self, agent_id: str, limit: int = 50) -> list[Message]:
        return self.store.list(MESSAGES, Message, parent=agent_id, limit=limit)

    def channel_history(self, channel_address: str, limit: int = 50) -> list[Message]:
        return self.store.list(MESSAGES, Message, parent=channel_address, limit=limit)

    def thread(self, message_id: str) -> list[Message]:
        all_msgs = self.store.list(MESSAGES, Message, limit=2000)
        return [m for m in all_msgs if m.id == message_id or m.reply_to_id == message_id]


# --------------------------------------------------------------------------
# Reference transports
# --------------------------------------------------------------------------


def logging_transport(channel: ChannelKind) -> Transport:
    """A transport that records delivery without contacting an external system."""

    def send(msg: Message) -> dict[str, Any]:
        return {
            "channel": channel.value,
            "address": msg.channel_address,
            "status": "recorded",
            "message_id": msg.id,
        }

    return send


def webhook_transport(post: Callable[[str, dict], Any]) -> Transport:
    """Deliver messages by POSTing them to the channel address."""

    def send(msg: Message) -> dict[str, Any]:
        return {"status": "posted", "response": post(msg.channel_address, msg.model_dump())}

    return send
