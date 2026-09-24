"""The conformance suite every channel adapter must pass (WS-013 M4).

An adapter claims to implement `ChannelBridge` by subclassing
`ChannelBridgeConformance` in its own test module and answering three
questions: how to build the bridge, what a callback from it looks like, and —
if it authenticates callbacks at all — what a forged one looks like. Everything
else is inherited, so the suite is run *against the adapter* rather than
rewritten for it. OpenClaw is the next adapter through it.

It lives in `src/` rather than `tests/` on purpose: an adapter written outside
this repository must be able to import it.

What the suite requires:

* capabilities are declared and stable, and the declaration is honest about
  callbacks — a bridge that claims interactive callbacks must refuse a forged
  one, and one that does not claim them must fail to bind `approve`;
* the principal an agent posts as is a **bot**, and it has an id;
* a post comes back with an id, and a reply hangs under its thread;
* an approval reaches the surface and a click correlates back to it;
* stale, duplicate, cross-tenant and unexpected-approver callbacks are refused.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

import pytest

from ..ids import now
from ..spec.model import ChannelPurpose
from .approvals import (
    AlreadyAnswered,
    ApprovalLedger,
    CrossTenantCallback,
    StaleApproval,
    UnexpectedApprover,
    UnknownApproval,
)
from .binding import BridgeRefused, bind
from .model import ApprovalDecision, ApprovalState
from .port import (
    BridgeCapabilities,
    CallbackNotAuthenticated,
    ChannelBridge,
    PrincipalKind,
)

TENANT = "tnt_conformance"
AGENT = "agt_conformance"
CHANNEL = "chn_conformance"
APPROVER = "owner@example.com"
OUTSIDER = "passerby@example.com"


class ChannelBridgeConformance:
    """Subclass this in an adapter's tests. Implement the hooks."""

    # -- hooks an adapter must provide -------------------------------------

    def make_bridge(self) -> ChannelBridge:
        raise NotImplementedError("an adapter must build its own bridge")

    def callback_payload(
        self,
        bridge: ChannelBridge,
        request,
        *,
        responder: str,
        decision: ApprovalDecision = ApprovalDecision.APPROVE,
    ) -> Mapping[str, Any]:
        """A raw payload the platform would send when `responder` clicks."""
        raise NotImplementedError("an adapter must say what a callback looks like")

    def forged_callback_payload(
        self, bridge: ChannelBridge, request, *, responder: str
    ) -> Mapping[str, Any]:
        """A payload from somebody who is not the platform.

        Return `None` only if the adapter declares `interactive_callbacks`
        false — in which case it may not carry `approve` at all.
        """
        raise NotImplementedError("an adapter must say what a forgery looks like")

    # -- fixtures ----------------------------------------------------------

    @pytest.fixture()
    def bridge(self) -> ChannelBridge:
        return self.make_bridge()

    @pytest.fixture()
    def ledger(self, bridge) -> ApprovalLedger:
        return ApprovalLedger(bridge.capabilities().tenant_id or TENANT)

    @pytest.fixture()
    def request_(self, bridge, ledger):
        req = ledger.open(
            agent_id=AGENT,
            session_id="ses_conformance",
            channel_id=CHANNEL,
            question="Post the September close?",
            expected_approvers=[APPROVER],
         encoding="utf-8")
        bridge.open_approval(req)
        return req

    # -- the suite ---------------------------------------------------------

    def test_satisfies_the_port(self, bridge):
        assert isinstance(bridge, ChannelBridge)

    def test_declares_capabilities_and_they_are_stable(self, bridge):
        caps = bridge.capabilities()
        assert isinstance(caps, BridgeCapabilities)
        assert caps == bridge.capabilities(), "capabilities must be stable"

    def test_an_agent_posts_as_a_bot(self, bridge):
        principal = bridge.identify(agent_id=AGENT)
        assert principal.is_bot, "an agent never speaks through a human account"
        assert principal.id, "a principal with no id is not an identity"

    def test_identity_is_stable_for_one_agent(self, bridge):
        assert bridge.identify(agent_id=AGENT).id == bridge.identify(agent_id=AGENT).id

    def test_binding_matches_the_declaration(self, bridge):
        caps = bridge.capabilities()
        tenant = caps.tenant_id or TENANT
        if caps.interactive_callbacks:
            bound = bind(bridge, tenant_id=tenant, purposes=[ChannelPurpose.APPROVE])
            assert bound.carries(ChannelPurpose.APPROVE)
        else:
            with pytest.raises(BridgeRefused):
                bind(bridge, tenant_id=tenant, purposes=[ChannelPurpose.APPROVE])
            assert bind(
                bridge, tenant_id=tenant, purposes=[ChannelPurpose.NOTIFY]
            ).carries(ChannelPurpose.NOTIFY)

    def test_a_bridge_that_binds_declares_a_service_principal(self, bridge):
        assert bridge.capabilities().principal_kind is PrincipalKind.SERVICE

    def test_a_post_comes_back_with_an_id(self, bridge):
        posted = bridge.post(CHANNEL, "the ledger is closed", agent_id=AGENT)
        assert posted.id and posted.channel_id == CHANNEL

    def test_a_reply_hangs_under_its_thread(self, bridge):
        root = bridge.post(CHANNEL, "parent", agent_id=AGENT)
        reply = bridge.reply(CHANNEL, root.id, "child", agent_id=AGENT)
        assert reply.thread_id == root.id

    def test_an_approval_reaches_the_surface(self, bridge, ledger):
        req = ledger.open(
            agent_id=AGENT, session_id="ses", channel_id=CHANNEL,
            question="Release the payment run?", expected_approvers=[APPROVER],
         encoding="utf-8")
        assert bridge.open_approval(req).id

    def test_a_click_correlates_back_to_its_request(self, bridge, ledger, request_):
        callback = bridge.resolve_approval(
            self.callback_payload(bridge, request_, responder=APPROVER)
        )
        assert callback.request_id == request_.id
        resolved = ledger.resolve(callback)
        assert resolved.state is ApprovalState.APPROVED
        assert resolved.decided_by == APPROVER

    def test_a_forged_callback_is_refused(self, bridge, request_):
        if not bridge.capabilities().interactive_callbacks:
            pytest.skip("bridge declares no interactive callbacks")
        payload = self.forged_callback_payload(bridge, request_, responder=APPROVER)
        with pytest.raises(CallbackNotAuthenticated):
            bridge.resolve_approval(payload)

    def test_a_second_click_does_not_change_the_answer(self, bridge, ledger, request_):
        ledger.resolve(
            bridge.resolve_approval(
                self.callback_payload(bridge, request_, responder=APPROVER)
            )
        )
        with pytest.raises(AlreadyAnswered):
            ledger.resolve(
                bridge.resolve_approval(
                    self.callback_payload(
                        bridge, request_, responder=APPROVER,
                        decision=ApprovalDecision.DENY,
                    )
                )
            )
        assert ledger.get(request_.id).state is ApprovalState.APPROVED

    def test_a_stale_click_is_refused(self, bridge, ledger, request_):
        callback = bridge.resolve_approval(
            self.callback_payload(bridge, request_, responder=APPROVER)
        )
        with pytest.raises(StaleApproval):
            ledger.resolve(callback, moment=request_.expires_at + timedelta(minutes=1))
        assert ledger.get(request_.id).state is ApprovalState.EXPIRED

    def test_an_unexpected_human_is_refused(self, bridge, ledger, request_):
        callback = bridge.resolve_approval(
            self.callback_payload(bridge, request_, responder=OUTSIDER)
        )
        with pytest.raises(UnexpectedApprover):
            ledger.resolve(callback)
        assert ledger.get(request_.id).state is ApprovalState.PENDING

    def test_a_callback_for_an_unknown_request_is_refused(self, bridge, ledger, request_):
        callback = bridge.resolve_approval(
            self.callback_payload(bridge, request_, responder=APPROVER)
        )
        callback.request_id = "apr_never_opened"
        with pytest.raises(UnknownApproval):
            ledger.resolve(callback)

    def test_another_tenants_callback_is_refused(self, bridge, ledger, request_):
        callback = bridge.resolve_approval(
            self.callback_payload(bridge, request_, responder=APPROVER)
        )
        callback.tenant_id = "tnt_somebody_else"
        with pytest.raises(CrossTenantCallback):
            ledger.resolve(callback)
        assert ledger.get(request_.id).state is ApprovalState.PENDING

    def test_an_unanswered_request_expires_rather_than_lingering(self, ledger, request_):
        expired = ledger.expire_due(now() + timedelta(days=1))
        assert [r.id for r in expired] == [request_.id]
        assert ledger.pending(now() + timedelta(days=1)) == []


__all__ = [
    "AGENT",
    "APPROVER",
    "CHANNEL",
    "ChannelBridgeConformance",
    "OUTSIDER",
    "TENANT",
]
