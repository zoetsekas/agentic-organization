"""Human contact: routing, availability, SLA and escalation (ADR-0021).

Approval is a routing problem, not a boolean. This module answers the four
questions a declared channel contract raises:

* **May we?**       — does this channel serve this purpose, and is the content
                      allowed on it (a channel can forbid data classes);
* **When?**         — are the people on it available now, and if not, what does
                      the out-of-hours policy say;
* **How long?**     — when does the response SLA expire;
* **Then who?**     — the escalation chain, with the time each step is due.

Delivery itself belongs to `messaging.py`; this module decides what delivery
should happen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from .scheduling import resolve_timezone
from .spec.model import ChannelPurpose, ChannelSpec, WorkingHours


class RoutingError(RuntimeError):
    """Raised when a request may not be delivered on the chosen channel."""


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------


def is_available(hours: Optional[WorkingHours], moment: datetime) -> bool:
    """Whether the people on a channel are contactable at `moment`."""
    if hours is None:
        return True
    local = moment.astimezone(resolve_timezone(hours.timezone))
    if local.date().isoformat() in hours.holidays:
        return False
    # WorkingHours.days uses 1=Monday..7=Sunday.
    if (local.weekday() + 1) not in hours.days:
        return False
    return hours.start_hour <= local.hour < hours.end_hour


def next_available(hours: Optional[WorkingHours], moment: datetime) -> datetime:
    """The next instant the channel's people are contactable."""
    if hours is None or is_available(hours, moment):
        return moment
    tz = resolve_timezone(hours.timezone)
    local = moment.astimezone(tz)
    for _ in range(400):   # a generous bound; holidays can span a long break
        if is_available(hours, local):
            return local.astimezone(timezone.utc)
        if (local.weekday() + 1) in hours.days and local.hour < hours.start_hour:
            local = local.replace(hour=hours.start_hour, minute=0, second=0,
                                  microsecond=0)
            continue
        local = (local + timedelta(days=1)).replace(
            hour=hours.start_hour, minute=0, second=0, microsecond=0
        )
    raise RoutingError(
        f"no working hours found within a year for channel timezone {hours.timezone}"
    )


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


@dataclass
class EscalationHop:
    due_at: datetime
    notify: str
    channel: str
    note: str = ""


@dataclass
class RoutingPlan:
    """What should happen to one request for human attention."""

    channel: str
    purpose: ChannelPurpose
    deliver_at: datetime
    deferred: bool = False
    sla_expires_at: Optional[datetime] = None
    escalations: list[EscalationHop] = field(default_factory=list)
    reason: str = ""

    def due_hops(self, now: datetime) -> list[EscalationHop]:
        return [hop for hop in self.escalations if hop.due_at <= now]

    def breached(self, now: datetime) -> bool:
        return self.sla_expires_at is not None and now > self.sla_expires_at


def check_content(channel: ChannelSpec, data_classes: list[str]) -> None:
    """Refuse to route content a channel forbids (ADR-0017 placement rules)."""
    forbidden = set(channel.forbid_data_classes) & set(data_classes)
    if forbidden:
        raise RoutingError(
            f"channel '{channel.id}' forbids data class(es) {sorted(forbidden)}"
        )


def plan(
    channel: ChannelSpec,
    purpose: ChannelPurpose,
    *,
    now: Optional[datetime] = None,
    data_classes: Optional[list[str]] = None,
) -> RoutingPlan:
    """Decide when and how a human should be contacted."""
    now = now or datetime.now(timezone.utc)
    if channel.purposes and purpose not in channel.purposes:
        raise RoutingError(
            f"channel '{channel.id}' does not serve '{purpose.value}'; it serves "
            f"{[p.value for p in channel.purposes]}"
        )
    check_content(channel, data_classes or [])

    available = is_available(channel.working_hours, now)
    deliver_at, deferred, reason = now, False, "within working hours"
    if not available:
        if channel.out_of_hours == "queue":
            deliver_at = next_available(channel.working_hours, now)
            deferred = True
            reason = "queued until the channel's next working hours"
        elif channel.out_of_hours == "escalate":
            reason = "out of hours; escalating immediately"
        else:
            reason = "out of hours; delivering anyway"

    sla_expires = (
        deliver_at + timedelta(minutes=channel.response_sla_minutes)
        if channel.response_sla_minutes
        else None
    )

    hops: list[EscalationHop] = []
    base = deliver_at
    if not available and channel.out_of_hours == "escalate" and channel.escalation:
        # Skip the wait: the first hop is due at once.
        first = channel.escalation[0]
        hops.append(
            EscalationHop(now, first.notify, first.channel or channel.id,
                          first.note or "out-of-hours escalation")
        )
        remaining = channel.escalation[1:]
    else:
        remaining = list(channel.escalation)
    for step in remaining:
        hops.append(
            EscalationHop(
                base + timedelta(minutes=step.after_minutes),
                step.notify,
                step.channel or channel.id,
                step.note,
            )
        )

    return RoutingPlan(
        channel=channel.id,
        purpose=purpose,
        deliver_at=deliver_at,
        deferred=deferred,
        sla_expires_at=sla_expires,
        escalations=sorted(hops, key=lambda h: h.due_at),
        reason=reason,
    )


def approval_channels(channels: list[ChannelSpec]) -> list[ChannelSpec]:
    return [
        c for c in channels
        if c.human_facing and ChannelPurpose.APPROVE in (c.purposes or [])
    ]


def choose_channel(
    channels: list[ChannelSpec],
    purpose: ChannelPurpose,
    *,
    preferred: Optional[list[str]] = None,
    now: Optional[datetime] = None,
) -> Optional[ChannelSpec]:
    """Pick the best channel for a purpose: preferred first, then available."""
    now = now or datetime.now(timezone.utc)
    candidates = [
        c for c in channels
        if c.human_facing and (not c.purposes or purpose in c.purposes)
    ]
    if not candidates:
        return None
    order = {cid: i for i, cid in enumerate(preferred or [])}
    candidates.sort(
        key=lambda c: (
            order.get(c.id, len(order)),
            not is_available(c.working_hours, now),
            c.response_sla_minutes or 10**6,
        )
    )
    return candidates[0]
