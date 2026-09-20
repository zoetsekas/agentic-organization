"""The inbound bus path refuses on its own account.

Receiving on a subject is not proof the sender was allowed to send: a publisher
that bypassed our client, or an operator who mis-scoped a subject, puts bytes
on the wire that look exactly like a legitimate message. So the inbound worker
re-runs the org-chart check rather than trusting that the outbound one ran.

Nothing here has met a broker — there is none in this environment.
"""
import json

import pytest

from orgagents.bus import SubjectNamespace
from orgagents.messaging import DeliveryError
from orgagents.models import ChannelKind, Message
from orgagents.runtime.bus_worker import BusWorker, configure_from_env

SUBJECTS = SubjectNamespace(tenant="acme")


def _worker(allow=True, handler=None):
    return BusWorker(
        agent_id="analyst", subjects=SUBJECTS,
        can_deliver=lambda src, dst: allow, handler=handler,
    )


def _msg(**kw):
    base = dict(from_agent_id="cfo", to_agent_id="analyst", body="look at this",
                channel=ChannelKind.INTERNAL_BUS)
    base.update(kw)
    return Message(**base)


def test_the_worker_listens_on_its_own_agent_subject():
    assert _worker().subject == "orgagents.acme.agent.analyst"


def test_an_allowed_message_reaches_the_handler():
    seen = []
    result = _worker(handler=seen.append).handle(_msg().model_dump_json())
    assert result.accepted
    assert seen and seen[0].from_agent_id == "cfo"


def test_a_sender_the_org_chart_refuses_never_reaches_the_handler():
    seen = []
    worker = _worker(allow=False, handler=seen.append)
    result = worker.handle(_msg(from_agent_id="platform_engineer").model_dump_json())
    assert not result.accepted
    assert "may not reach" in result.reason
    # The point of the test: the work never started.
    assert seen == []


def test_a_message_addressed_to_somebody_else_is_refused():
    worker = _worker()
    result = worker.handle(_msg(to_agent_id="cro").model_dump_json())
    assert not result.accepted
    assert "addressed to" in result.reason


def test_an_undecodable_payload_is_refused_not_raised():
    # The bus is reachable by anything that can publish, so a decode failure is
    # an expected event on a boundary rather than a bug in us.
    result = _worker().handle(b"{not json")
    assert not result.accepted
    assert "undecodable" in result.reason


def test_an_unaddressed_post_is_not_treated_as_delegation():
    # Nobody is being reached, so the delegation check does not apply — this is
    # the lower-privilege path the delegation refusal points at.
    seen = []
    worker = _worker(allow=False, handler=seen.append)
    result = worker.handle(_msg(to_agent_id=None, channel_address="#finance-ops")
                           .model_dump_json())
    assert result.accepted
    assert len(seen) == 1


def test_a_request_gets_a_reply_and_a_notification_does_not():
    worker = _worker(handler=lambda m: "done")
    assert worker.handle(_msg(requires_response=True).model_dump_json()).reply
    assert worker.handle(_msg(requires_response=False).model_dump_json()).reply is None


def test_subscribing_wires_the_subject_and_returns_json_replies():
    captured = {}

    class FakeClient:
        def subscribe(self, subject, handler):
            captured["subject"] = subject
            captured["handler"] = handler
            return "sub-1"

    worker = _worker(handler=lambda m: "ok")
    assert worker.subscribe(FakeClient()) == "sub-1"
    assert captured["subject"] == "orgagents.acme.agent.analyst"
    raw = captured["handler"](_msg(requires_response=True).model_dump_json().encode())
    assert json.loads(raw)["from_agent_id"] == "analyst"
    assert captured["handler"](_msg().model_dump_json().encode()) is None


# -- backend selection ----------------------------------------------------


class _Bus:
    def __init__(self):
        self.transports = {}

    def register_transport(self, channel, transport):
        self.transports[channel] = transport


def test_the_default_backend_wires_nothing():
    assert configure_from_env(_Bus(), env={}) is None


def test_a_nats_backend_without_a_client_is_refused_loudly():
    # The client is injected, never constructed here, so a misconfiguration
    # fails at startup rather than at the first message.
    with pytest.raises(DeliveryError) as err:
        configure_from_env(_Bus(), env={"ORGAGENTS_BUS": "nats"})
    assert "no client was supplied" in str(err.value)


def test_an_unknown_backend_is_refused():
    with pytest.raises(DeliveryError):
        configure_from_env(_Bus(), env={"ORGAGENTS_BUS": "kafka"}, client=object())
