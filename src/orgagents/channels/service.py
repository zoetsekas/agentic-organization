"""Driving a bound bridge on behalf of one tenant's agents (ADR-0061).

Three things happen here and nothing else: an agent says something, an agent
asks for a decision, and a human's text or click comes back.

Inbound text is **not** screened here (rule 5). It is handed to the runtime as
a prompt, and the runtime's existing input guardrail is the boundary it
crosses (ADR-0035) — the same stance `tasks/service.py` takes for a task
description. A second, channel-shaped guardrail path would be a second place
for the boundary to be wrong, and the two would drift.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Protocol

from ..humans import RoutingPlan
from ..humans import plan as route
from ..spec.model import ChannelPurpose, ChannelSpec
from .approvals import DEFAULT_EXPIRY_MINUTES, ApprovalLedger
from .binding import BoundChannelBridge
from .model import (
    ApprovalCallback,
    ApprovalRequest,
    ApprovalState,
    InboundMessage,
    PostedMessage,
)
from .port import ChannelBridgeError


class PurposeNotBound(ChannelBridgeError):
    """The binding does not carry this purpose on this bridge (rule 3)."""

    def __init__(self, purpose: ChannelPurpose, bound: tuple[ChannelPurpose, ...]) -> None:
        super().__init__(
            f"this bridge does not carry '{purpose.value}'; it carries "
            f"{[p.value for p in bound]}"
        )
        self.purpose = purpose
        self.bound = bound


class TenantMismatch(ChannelBridgeError):
    """Something arrived from outside the tenant this service serves."""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(
            f"message belongs to tenant '{actual}', not '{expected}'; a bridge "
            "instance serves exactly one tenant (ADR-0050)"
        )
        self.expected = expected
        self.actual = actual


class ChannelRunner(Protocol):
    """The slice of `AgentRuntime` this needs, and no more."""

    sessions: Any

    def run(self, agent_id: str, prompt: str, *, created_by: str = "") -> Any: ...


class ChannelService:
    """The runtime's side of a human channel."""

    def __init__(
        self,
        bound: BoundChannelBridge,
        runtime: Optional[ChannelRunner] = None,
        *,
        ledger: Optional[ApprovalLedger] = None,
    ) -> None:
        self.bound = bound
        self.bridge = bound.bridge
        self.runtime = runtime
        self.ledger = ledger or ApprovalLedger(bound.tenant_id)
        if self.ledger.tenant_id != bound.tenant_id:
            raise TenantMismatch(bound.tenant_id, self.ledger.tenant_id)

    # -- outbound ----------------------------------------------------------

    def announce(
        self,
        channel_id: str,
        text: str,
        *,
        agent_id: str = "",
        purpose: ChannelPurpose = ChannelPurpose.NOTIFY,
    ) -> PostedMessage:
        if not self.bound.carries(purpose):
            raise PurposeNotBound(purpose, self.bound.purposes)
        return self.bridge.post(channel_id, text, agent_id=agent_id)

    def respond(
        self, message: PostedMessage | InboundMessage, text: str, *, agent_id: str = ""
    ) -> PostedMessage:
        """Answer in the thread the human is already reading."""
        thread = getattr(message, "thread_id", "") or getattr(message, "id", "")
        return self.bridge.reply(message.channel_id, thread, text, agent_id=agent_id)

    # -- approvals (rules 3 and 4) ----------------------------------------

    def request_approval(
        self,
        *,
        agent_id: str,
        session_id: str,
        channel_id: str,
        question: str,
        expected_approvers,
        detail: str = "",
        expires_in_minutes: int = DEFAULT_EXPIRY_MINUTES,
        moment: Optional[datetime] = None,
    ) -> ApprovalRequest:
        """Open a decision and put it on the surface as something clickable."""
        if not self.bound.carries(ChannelPurpose.APPROVE):
            raise PurposeNotBound(ChannelPurpose.APPROVE, self.bound.purposes)
        request = self.ledger.open(
            agent_id=agent_id,
            session_id=session_id,
            channel_id=channel_id,
            question=question,
            detail=detail,
            expected_approvers=expected_approvers,
            expires_in_minutes=expires_in_minutes,
            moment=moment,
         encoding="utf-8")
        posted = self.bridge.open_approval(request)
        request.post_id = posted.id
        return request

    def handle_callback(
        self, payload: Mapping[str, Any], *, moment: Optional[datetime] = None
    ) -> ApprovalRequest:
        """Authenticate, correlate and record one click. Refusals propagate.

        Nothing here catches `ApprovalRefused`: a caller that wants to report a
        refused click to an operator needs to be told one happened, and a
        silent drop would leave the agent waiting on a decision the human
        believes they gave.
        """
        callback: ApprovalCallback = self.bridge.resolve_approval(payload)
        return self.ledger.resolve(callback, moment=moment)

    def expire_due(self, moment: Optional[datetime] = None) -> list[ApprovalRequest]:
        return self.ledger.expire_due(moment)

    def decision_of(self, request_id: str) -> ApprovalState:
        request = self.ledger.get(request_id)
        return request.state if request else ApprovalState.EXPIRED

    # -- inbound (rule 5) --------------------------------------------------

    def receive(self, message: InboundMessage, *, agent_id: str) -> Any:
        """Hand a human's message to an agent as untrusted input.

        The text goes in as a prompt; the runtime screens it on the input
        boundary exactly as it screens anything else that came from outside.
        """
        if message.tenant_id and message.tenant_id != self.bound.tenant_id:
            raise TenantMismatch(self.bound.tenant_id, message.tenant_id)
        if self.runtime is None:
            raise ChannelBridgeError(
                "no runtime is attached to this channel service, so there is "
                "nothing for a human's message to reach"
            )
        return self.runtime.run(
            agent_id,
            message.text,
            created_by=message.author_contact or message.author_id,
        )

    # -- routing -----------------------------------------------------------

    def routing_plan(
        self,
        channel: ChannelSpec,
        purpose: ChannelPurpose,
        *,
        now: Optional[datetime] = None,
        data_classes: Optional[list[str]] = None,
    ) -> RoutingPlan:
        """The existing routing decision (ADR-0021), unchanged by this port.

        The bridge answers *how* a human is reached; `humans.py` still answers
        whether, when, how long and then who.
        """
        return route(channel, purpose, now=now, data_classes=data_classes)


__all__ = ["ChannelRunner", "ChannelService", "PurposeNotBound", "TenantMismatch"]
