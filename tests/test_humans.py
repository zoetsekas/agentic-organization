"""Human contact: availability, routing, SLA and escalation (ADR-0021)."""
from datetime import datetime, timezone

import pytest

from orgagents.humans import (
    RoutingError,
    choose_channel,
    is_available,
    next_available,
    plan,
)
from orgagents.spec.model import (
    ChannelPurpose,
    ChannelSpec,
    EscalationStep,
    WorkingHours,
)

LONDON = WorkingHours(timezone="Europe/London", days=[1, 2, 3, 4, 5],
                      start_hour=9, end_hour=17)
MONDAY_3AM = datetime(2026, 9, 21, 3, 0, tzinfo=timezone.utc)
MONDAY_11AM = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)   # 11:00 London
SATURDAY = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def approvals(**kw) -> ChannelSpec:
    defaults = dict(
        id="finance_approvals", human_facing=True,
        purposes=[ChannelPurpose.APPROVE], working_hours=LONDON,
        response_sla_minutes=120,
        escalation=[
            EscalationStep(after_minutes=60, notify="priya@acme.example"),
            EscalationStep(after_minutes=240, notify="dana@acme.example",
                           channel="exec_briefing"),
        ],
    )
    defaults.update(kw)
    return ChannelSpec(**defaults)


def test_availability_respects_hours_days_and_holidays():
    assert is_available(LONDON, MONDAY_11AM)
    assert not is_available(LONDON, MONDAY_3AM)
    assert not is_available(LONDON, SATURDAY)
    holiday = LONDON.model_copy(update={"holidays": ["2026-09-21"]})
    assert not is_available(holiday, MONDAY_11AM)
    assert is_available(None, SATURDAY)          # no declared hours = always


def test_next_available_skips_to_the_working_window():
    assert next_available(LONDON, MONDAY_3AM).isoformat() == "2026-09-21T08:00:00+00:00"
    # Saturday noon rolls forward to Monday morning.
    assert next_available(LONDON, SATURDAY).isoformat() == "2026-09-21T08:00:00+00:00"


def test_in_hours_delivery_is_immediate_with_an_sla():
    routing = plan(approvals(), ChannelPurpose.APPROVE, now=MONDAY_11AM)
    assert not routing.deferred
    assert routing.deliver_at == MONDAY_11AM
    assert routing.sla_expires_at.isoformat() == "2026-09-21T12:00:00+00:00"


def test_out_of_hours_queueing_defers_and_shifts_escalation():
    routing = plan(approvals(), ChannelPurpose.APPROVE, now=MONDAY_3AM)
    assert routing.deferred
    assert routing.deliver_at.isoformat() == "2026-09-21T08:00:00+00:00"
    assert [h.due_at.isoformat() for h in routing.escalations] == [
        "2026-09-21T09:00:00+00:00",
        "2026-09-21T12:00:00+00:00",
    ]
    assert routing.escalations[1].channel == "exec_briefing"


def test_out_of_hours_escalation_pages_immediately():
    channel = approvals(out_of_hours="escalate")
    routing = plan(channel, ChannelPurpose.APPROVE, now=MONDAY_3AM)
    assert not routing.deferred
    assert routing.escalations[0].due_at == MONDAY_3AM
    assert routing.escalations[0].notify == "priya@acme.example"


def test_incident_channel_ignores_hours():
    incidents = ChannelSpec(id="incidents", human_facing=True,
                            purposes=[ChannelPurpose.NOTIFY],
                            out_of_hours="notify_anyway", working_hours=LONDON)
    routing = plan(incidents, ChannelPurpose.NOTIFY, now=MONDAY_3AM)
    assert not routing.deferred and routing.deliver_at == MONDAY_3AM


def test_channel_refuses_a_purpose_it_does_not_serve():
    with pytest.raises(RoutingError, match="does not serve"):
        plan(approvals(), ChannelPurpose.HANDOFF, now=MONDAY_11AM)


def test_channel_refuses_forbidden_data():
    channel = approvals(forbid_data_classes=["customer_pii"])
    with pytest.raises(RoutingError, match="forbids data class"):
        plan(channel, ChannelPurpose.APPROVE, now=MONDAY_11AM,
             data_classes=["finance_internal", "customer_pii"])


def test_sla_breach_detection():
    routing = plan(approvals(), ChannelPurpose.APPROVE, now=MONDAY_11AM)
    assert not routing.breached(MONDAY_11AM)
    later = datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)
    assert routing.breached(later)
    assert len(routing.due_hops(later)) == 1


def test_choose_channel_prefers_named_then_available():
    slow = ChannelSpec(id="mail", human_facing=True,
                       purposes=[ChannelPurpose.APPROVE], response_sla_minutes=1440)
    fast = approvals()
    assert choose_channel([slow, fast], ChannelPurpose.APPROVE,
                          now=MONDAY_11AM).id == "finance_approvals"
    assert choose_channel([slow, fast], ChannelPurpose.APPROVE,
                          preferred=["mail"], now=MONDAY_11AM).id == "mail"
    assert choose_channel([], ChannelPurpose.APPROVE) is None
