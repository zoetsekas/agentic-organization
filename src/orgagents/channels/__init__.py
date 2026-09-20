"""Humans reaching agents through a channel bridge (ADR-0061, WS-013 M4).

The port is the commitment; Mattermost is the first adapter and OpenClaw is
the second. Nothing in here has been verified against a running chat server:
there is no daemon in this environment and every vendor documentation site is
blocked by its egress proxy.
"""
from .approvals import (
    AlreadyAnswered,
    ApprovalLedger,
    ApprovalRefused,
    CrossTenantCallback,
    DEFAULT_EXPIRY_MINUTES,
    StaleApproval,
    UnexpectedApprover,
    UnknownApproval,
)
from .binding import BoundChannelBridge, BridgeRefused, bind
from .mattermost import MattermostBridge, MattermostWire
from .model import (
    ApprovalCallback,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
    BotPrincipal,
    InboundMessage,
    PostedMessage,
)
from .port import (
    POST_ONLY_PURPOSES,
    BridgeCapabilities,
    CallbackNotAuthenticated,
    ChannelBridge,
    ChannelBridgeError,
    PostFailed,
    PrincipalKind,
)
from .service import ChannelRunner, ChannelService, PurposeNotBound, TenantMismatch

__all__ = [
    "AlreadyAnswered",
    "ApprovalCallback",
    "ApprovalDecision",
    "ApprovalLedger",
    "ApprovalRefused",
    "ApprovalRequest",
    "ApprovalState",
    "BotPrincipal",
    "BoundChannelBridge",
    "BridgeCapabilities",
    "BridgeRefused",
    "CallbackNotAuthenticated",
    "ChannelBridge",
    "ChannelBridgeError",
    "ChannelRunner",
    "ChannelService",
    "CrossTenantCallback",
    "DEFAULT_EXPIRY_MINUTES",
    "InboundMessage",
    "MattermostBridge",
    "MattermostWire",
    "POST_ONLY_PURPOSES",
    "PostFailed",
    "PostedMessage",
    "PrincipalKind",
    "PurposeNotBound",
    "StaleApproval",
    "TenantMismatch",
    "UnexpectedApprover",
    "UnknownApproval",
]
