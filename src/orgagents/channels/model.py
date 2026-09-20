"""The things a channel bridge carries: principals, posts, approvals (ADR-0061).

These types are ours, not a vendor's. An adapter translates its product's
payloads into these at its own boundary, so nothing above the port has to know
what a Mattermost post or an OpenClaw event looks like.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..ids import new_id, now


class BotPrincipal(BaseModel):
    """The non-human identity an agent speaks as (ADR-0061 rule 2).

    `is_bot` is what the platform told us, not what we hoped: an adapter that
    can only present a person's account must say so here rather than dress a
    human account up as a bot.
    """

    id: str = ""
    username: str = ""
    display_name: str = ""
    is_bot: bool = False
    #: Which agent this principal speaks for, if it is dedicated to one.
    agent_id: str = ""


class PostedMessage(BaseModel):
    """One message that exists on the channel surface."""

    id: str = ""
    channel_id: str = ""
    #: The message this one hangs under, for threaded replies.
    thread_id: str = ""
    text: str = ""
    posted_by: str = ""
    posted_at: datetime = Field(default_factory=now)
    #: Vendor payload, kept for audit. Nothing reads it to make a decision.
    raw: dict[str, Any] = Field(default_factory=dict)


class InboundMessage(BaseModel):
    """Text a human typed. Untrusted (ADR-0061 rule 5, ADR-0035).

    Deliberately inert — it holds strings. Anything that reads `text` must send
    it across the input guardrail first; `ChannelService.receive` is that path.
    """

    channel_id: str = ""
    thread_id: str = ""
    author_id: str = ""
    author_contact: str = ""
    text: str = ""
    tenant_id: str = ""
    sent_at: datetime = Field(default_factory=now)
    raw: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    DENY = "deny"


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class ApprovalRequest(BaseModel):
    """A question put to named humans, with a deadline (ADR-0061 rule 4).

    Every field here exists so a callback can be *correlated*: which request,
    whose tenant, which agent and session asked, who was entitled to answer,
    and by when. A request without an expiry would make a click from last month
    a decision about today.
    """

    id: str = Field(default_factory=lambda: new_id("apr"))
    tenant_id: str = ""
    agent_id: str = ""
    session_id: str = ""
    channel_id: str = ""
    question: str = ""
    detail: str = ""
    #: Contacts entitled to answer. Empty is not "anyone" — the ledger refuses
    #: to open a request nobody is named on.
    expected_approvers: tuple[str, ...] = ()
    requested_at: datetime = Field(default_factory=now)
    expires_at: datetime = Field(
        default_factory=lambda: now() + timedelta(minutes=60)
    )
    state: ApprovalState = ApprovalState.PENDING
    decision: Optional[ApprovalDecision] = None
    decided_by: str = ""
    decided_at: Optional[datetime] = None
    #: The message the request landed on, once the bridge has posted it.
    post_id: str = ""

    def is_expired(self, moment: Optional[datetime] = None) -> bool:
        return (moment or now()) > self.expires_at

    def answered(self) -> bool:
        return self.state is not ApprovalState.PENDING


class ApprovalCallback(BaseModel):
    """What a bridge made of a human's click, after authenticating it.

    An adapter produces one of these only when it has verified the callback
    really came from the platform (rule 3/4). Producing one from an unverified
    payload would move a forgery past the only place that could catch it.
    """

    request_id: str = ""
    responder_id: str = ""
    responder_contact: str = ""
    tenant_id: str = ""
    decision: ApprovalDecision = ApprovalDecision.DENY
    received_at: datetime = Field(default_factory=now)
    raw: dict[str, Any] = Field(default_factory=dict)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ApprovalCallback",
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalState",
    "BotPrincipal",
    "InboundMessage",
    "PostedMessage",
    "utcnow",
]
