"""Correlating a click with the question it answers (ADR-0061 rule 4).

The ledger is the only place a decision is recorded, and it refuses four
things outright rather than interpreting them:

* a callback for a request it has never heard of — including one for another
  tenant's request, which is the same refusal with a worse cause;
* a callback for a request that has **expired**: a stale approval is a
  decision nobody made today, and honouring one lets an old click authorize
  something it was never shown;
* a callback for a request that has **already been answered** — replaying a
  click must not flip a denial into an approval;
* a callback from somebody **not on the expected approver list**: being able
  to see the message is not authority to answer it.

Every refusal is raised, never swallowed. A dropped callback looks to the
human like a button that did nothing, and looks to the agent like a human who
never answered — two different wrong stories about the same event.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from ..ids import now
from .model import (
    ApprovalCallback,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
)
from .port import ChannelBridgeError

#: Long enough for a human on another continent to read it; short enough that
#: an unanswered decision expires rather than lingering as a live permission.
DEFAULT_EXPIRY_MINUTES = 60


class ApprovalRefused(ChannelBridgeError):
    """Base class for a callback the ledger will not honour."""

    def __init__(self, request_id: str, reason: str) -> None:
        super().__init__(f"approval callback for '{request_id}' refused: {reason}")
        self.request_id = request_id
        self.reason = reason


class UnknownApproval(ApprovalRefused):
    """No such request here — it never existed, or it is another tenant's."""


class StaleApproval(ApprovalRefused):
    """The request expired before the click arrived."""


class AlreadyAnswered(ApprovalRefused):
    """The request has a decision; a second click does not replace it."""


class UnexpectedApprover(ApprovalRefused):
    """The human who clicked was not on the expected approver list."""


class CrossTenantCallback(ApprovalRefused):
    """The callback claims a different tenant from the ledger's (ADR-0050)."""


class ApprovalLedger:
    """Open approvals for one tenant, and decide what a callback proves.

    In-process and deliberately dumb. Durable storage is a later concern; the
    correlation rules are not, because they are what makes a click a decision.
    """

    def __init__(self, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("an approval ledger belongs to exactly one tenant")
        self.tenant_id = tenant_id
        self._requests: dict[str, ApprovalRequest] = {}

    # -- opening -----------------------------------------------------------

    def open(
        self,
        *,
        agent_id: str,
        session_id: str,
        channel_id: str,
        question: str,
        expected_approvers: Iterable[str],
        detail: str = "",
        expires_in_minutes: int = DEFAULT_EXPIRY_MINUTES,
        moment: Optional[datetime] = None,
    ) -> ApprovalRequest:
        approvers = tuple(dict.fromkeys(a for a in expected_approvers if a))
        if not approvers:
            # "Anyone in the channel" is not an approver set; it is the absence
            # of one, and rule 4 needs a set to check a responder against.
            raise ValueError(
                "an approval request must name the humans entitled to answer it"
            )
        if expires_in_minutes <= 0:
            raise ValueError("an approval must expire; a permission that never "
                             "goes stale is a standing grant, not a decision")
        started = moment or now()
        request = ApprovalRequest(
            tenant_id=self.tenant_id,
            agent_id=agent_id,
            session_id=session_id,
            channel_id=channel_id,
            question=question,
            detail=detail,
            expected_approvers=approvers,
            requested_at=started,
            expires_at=started + timedelta(minutes=expires_in_minutes),
        )
        self._requests[request.id] = request
        return request

    # -- reading -----------------------------------------------------------

    def get(self, request_id: str) -> Optional[ApprovalRequest]:
        return self._requests.get(request_id)

    def pending(self, moment: Optional[datetime] = None) -> list[ApprovalRequest]:
        at = moment or now()
        return [
            r for r in self._requests.values()
            if r.state is ApprovalState.PENDING and not r.is_expired(at)
        ]

    def expire_due(self, moment: Optional[datetime] = None) -> list[ApprovalRequest]:
        """Mark unanswered, out-of-time requests as expired, and report them."""
        at = moment or now()
        expired = []
        for request in self._requests.values():
            if request.state is ApprovalState.PENDING and request.is_expired(at):
                request.state = ApprovalState.EXPIRED
                expired.append(request)
        return expired

    # -- resolving ---------------------------------------------------------

    def resolve(
        self, callback: ApprovalCallback, *, moment: Optional[datetime] = None
    ) -> ApprovalRequest:
        """Apply an authenticated callback, or refuse it with the reason."""
        at = moment or callback.received_at or now()
        request = self._requests.get(callback.request_id)

        if callback.tenant_id and callback.tenant_id != self.tenant_id:
            raise CrossTenantCallback(
                callback.request_id,
                f"it claims tenant '{callback.tenant_id}' but this ledger "
                f"serves '{self.tenant_id}'",
            )
        if request is None:
            raise UnknownApproval(
                callback.request_id,
                "no such request in this tenant's ledger; it was never opened "
                "here, or it belongs to somebody else",
            )
        if request.state is not ApprovalState.PENDING:
            raise AlreadyAnswered(
                request.id,
                f"it was already {request.state.value} "
                f"by '{request.decided_by or 'nobody'}'",
            )
        if request.is_expired(at):
            request.state = ApprovalState.EXPIRED
            raise StaleApproval(
                request.id,
                f"it expired at {request.expires_at.isoformat()} and the click "
                f"arrived at {at.isoformat()}; a stale approval is a decision "
                "nobody made today",
            )
        responder = callback.responder_contact or callback.responder_id
        if responder not in request.expected_approvers:
            raise UnexpectedApprover(
                request.id,
                f"'{responder or '<anonymous>'}' is not one of "
                f"{list(request.expected_approvers)}",
            )

        request.state = (
            ApprovalState.APPROVED
            if callback.decision is ApprovalDecision.APPROVE
            else ApprovalState.DENIED
        )
        request.decision = callback.decision
        request.decided_by = responder
        request.decided_at = at
        return request


__all__ = [
    "AlreadyAnswered",
    "ApprovalLedger",
    "ApprovalRefused",
    "CrossTenantCallback",
    "DEFAULT_EXPIRY_MINUTES",
    "StaleApproval",
    "UnexpectedApprover",
    "UnknownApproval",
]
