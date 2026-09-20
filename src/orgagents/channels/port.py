"""The channel bridge port: how an agent reaches a human (ADR-0061).

Five verbs and a declaration: say who the bot is, post, reply in a thread,
open an approval, resolve the click that answers one. It is as small as the
task port (ADR-0057) and for the same reason — the product is the part that
will age, so the seam it sits behind must not encode one product's habits.

What is deliberately **not** here: reading history, listing channels, managing
membership, reactions, uploads. Every one of those is a thing a chat product
does; none of them is a thing the platform needs in order to ask a human a
question and be told the answer.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from pydantic import BaseModel

from ..spec.model import ChannelPurpose
from .model import ApprovalCallback, ApprovalRequest, BotPrincipal, PostedMessage


class PrincipalKind(str, Enum):
    """What identity a bridge can act as. Same vocabulary as the task port."""

    NONE = "none"        # anonymous or a shared webhook; nobody is accountable
    HUMAN = "human"      # a person's account, borrowed by software
    SERVICE = "service"  # a non-human principal of its own


#: Purposes that need nothing but the ability to post.
POST_ONLY_PURPOSES = (
    ChannelPurpose.NOTIFY,
    ChannelPurpose.REPORT,
    ChannelPurpose.ASK,
    ChannelPurpose.HANDOFF,
)


class BridgeCapabilities(BaseModel):
    """What a bridge declares about itself, checked once at bind time.

    Declared rather than probed, exactly as the task port does: rules 2 and 3
    have to be answerable *before* the binding is live. A bridge that finds out
    at call time that it cannot deliver an authenticated callback has already
    posted an approval request somebody is waiting on.
    """

    #: Binding-layer name (`mattermost`, `openclaw`). Never in the spec.
    name: str = ""
    principal_kind: PrincipalKind = PrincipalKind.NONE
    #: The non-human principal this bridge posts as, if it has one.
    bot_principal_id: str = ""
    #: Whether the bridge can *create* a bot principal, not only present one.
    #: Both halves of rule 2 matter: a deployment that must hand-make an
    #: account per agent will end up sharing one.
    can_mint_bot_principal: bool = False
    #: Whether a human's click arrives back as an authenticated callback.
    #: False means this bridge may not carry `ChannelPurpose.APPROVE` (rule 3).
    interactive_callbacks: bool = False
    #: Whether replies can hang under the message they answer.
    threaded_replies: bool = False
    #: The single tenant this bridge instance serves (ADR-0050, rule 6).
    tenant_id: str = ""
    notes: str = ""

    def supported_purposes(self) -> tuple[ChannelPurpose, ...]:
        """The purposes this bridge is allowed to carry, on its own claims."""
        purposes = list(POST_ONLY_PURPOSES)
        if self.interactive_callbacks:
            purposes.append(ChannelPurpose.APPROVE)
        return tuple(purposes)


class ChannelBridgeError(RuntimeError):
    """Base class for refusals raised by a bridge or by the port's rules."""


class CallbackNotAuthenticated(ChannelBridgeError):
    """The bridge could not prove a callback came from the platform.

    Raised *instead of* returning an `ApprovalCallback`: an unauthenticated
    payload must not reach correlation, because correlation would then be
    deciding on evidence it was told to trust.
    """


class PostFailed(ChannelBridgeError):
    """The surface refused or failed to accept a message."""


@runtime_checkable
class ChannelBridge(Protocol):
    """What every channel adapter must provide. Six methods, no more."""

    def capabilities(self) -> BridgeCapabilities:
        """What this bridge is, and what identity it can act as."""
        ...

    def identify(self, *, agent_id: str = "") -> BotPrincipal:
        """The non-human principal the agent speaks as, minting it if needed."""
        ...

    def post(self, channel_id: str, text: str, *, agent_id: str = "") -> PostedMessage:
        ...

    def reply(
        self, channel_id: str, thread_id: str, text: str, *, agent_id: str = ""
    ) -> PostedMessage:
        """Answer in the thread of an existing message."""
        ...

    def open_approval(self, request: ApprovalRequest) -> PostedMessage:
        """Put a decision in front of humans as something they can click."""
        ...

    def resolve_approval(self, payload: Mapping[str, Any]) -> ApprovalCallback:
        """Authenticate a raw callback and say what it claims. No decision."""
        ...


__all__ = [
    "POST_ONLY_PURPOSES",
    "BridgeCapabilities",
    "CallbackNotAuthenticated",
    "ChannelBridge",
    "ChannelBridgeError",
    "PostFailed",
    "PrincipalKind",
]
