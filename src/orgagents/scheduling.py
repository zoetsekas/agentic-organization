"""Cadence parsing and fire-time computation for triggers (ADR-0020).

The spec expresses *when* something runs in a form a person can read and any
target can honour: a five-field cron expression, or a plain interval such as
"every 15 minutes". This module is the single interpreter of that vocabulary,
so the local scheduler, the cloud schedulers and the designer's preview all
agree about when a trigger fires.

No external dependency: cron matching is implemented here so the semantics are
ours to test rather than a library's to surprise us with.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .spec.model import Cadence, CatchUpPolicy, OverlapPolicy, TriggerSpec

_INTERVAL_RE = re.compile(
    r"^every\s+(?:(\d+)\s+)?(minute|minutes|hour|hours|day|days|week|weeks)$", re.I
)
_ALIASES = {
    "hourly": "every 1 hour",
    "daily": "every 1 day",
    "nightly": "0 2 * * *",
    "weekly": "every 1 week",
    "weekdays": "0 9 * * 1-5",
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
}
_UNIT_SECONDS = {"minute": 60, "hour": 3600, "day": 86400, "week": 604800}


class CadenceError(ValueError):
    """Raised when a cadence expression cannot be interpreted."""


def resolve_timezone(name: str) -> timezone | ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        if name.upper() == "UTC":
            return timezone.utc
        raise CadenceError(f"unknown timezone '{name}'") from None


# --------------------------------------------------------------------------
# Cron
# --------------------------------------------------------------------------


def _parse_field(field: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise CadenceError(f"invalid step '{step_text}'")
            step = int(step_text)
        if part in ("*", "?"):
            start, end = low, high
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        if not (low <= start <= high and low <= end <= high and start <= end):
            raise CadenceError(f"field '{field}' out of range {low}-{high}")
        values |= set(range(start, end + 1, step))
    if not values:
        raise CadenceError(f"field '{field}' matches nothing")
    return values


@dataclass(frozen=True)
class CronSchedule:
    minutes: set[int]
    hours: set[int]
    days: set[int]
    months: set[int]
    weekdays: set[int]     # 0 and 7 both mean Sunday

    @classmethod
    def parse(cls, expression: str) -> "CronSchedule":
        fields = expression.split()
        if len(fields) != 5:
            raise CadenceError(
                f"cron expression needs 5 fields, got {len(fields)}: '{expression}'"
            )
        try:
            weekdays = _parse_field(fields[4], 0, 7)
        except ValueError as e:
            raise CadenceError(str(e)) from None
        if 7 in weekdays:
            weekdays = (weekdays - {7}) | {0}
        return cls(
            minutes=_parse_field(fields[0], 0, 59),
            hours=_parse_field(fields[1], 0, 23),
            days=_parse_field(fields[2], 1, 31),
            months=_parse_field(fields[3], 1, 12),
            weekdays=weekdays,
        )

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes or moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        # cron weekday: Monday=1..Sunday=0; python weekday(): Monday=0
        weekday = (moment.weekday() + 1) % 7
        return moment.day in self.days and weekday in self.weekdays


# --------------------------------------------------------------------------
# Cadence
# --------------------------------------------------------------------------


@dataclass
class ParsedCadence:
    kind: str                      # "cron" | "interval"
    cron: Optional[CronSchedule] = None
    interval_seconds: int = 0
    tz: object = timezone.utc
    source: str = ""

    def describe(self) -> str:
        if self.kind == "interval":
            seconds = self.interval_seconds
            for unit, size in (("week", 604800), ("day", 86400), ("hour", 3600),
                               ("minute", 60)):
                if seconds % size == 0:
                    count = seconds // size
                    plural = "" if count == 1 else "s"
                    return f"every {count} {unit}{plural}"
        return f"cron `{self.source}`"


def parse_cadence(cadence: Cadence) -> ParsedCadence:
    expression = _ALIASES.get(cadence.expression.strip().lower(),
                              cadence.expression.strip())
    tz = resolve_timezone(cadence.timezone)
    interval = _INTERVAL_RE.match(expression)
    if interval:
        count = int(interval.group(1) or 1)
        unit = interval.group(2).rstrip("s").lower()
        if count < 1:
            raise CadenceError("interval must be at least 1")
        return ParsedCadence("interval", interval_seconds=count * _UNIT_SECONDS[unit],
                             tz=tz, source=expression)
    return ParsedCadence("cron", cron=CronSchedule.parse(expression), tz=tz,
                         source=expression)


def next_fire_times(
    cadence: Cadence, after: datetime, count: int = 5, horizon_days: int = 400
) -> list[datetime]:
    """The next `count` fire times strictly after `after`, in UTC."""
    parsed = parse_cadence(cadence)
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    if parsed.kind == "interval":
        step = timedelta(seconds=parsed.interval_seconds)
        # Anchor to the epoch so an interval is stable across restarts.
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        elapsed = (after - epoch) // step
        first = epoch + (elapsed + 1) * step
        return [first + i * step for i in range(count)]

    local = after.astimezone(parsed.tz).replace(second=0, microsecond=0)
    moment = local + timedelta(minutes=1)
    deadline = local + timedelta(days=horizon_days)
    found: list[datetime] = []
    assert parsed.cron is not None
    while moment <= deadline and len(found) < count:
        if parsed.cron.matches(moment):
            found.append(moment.astimezone(timezone.utc))
            moment += timedelta(minutes=1)
            continue
        # Skip cheaply when the hour or day cannot match.
        if moment.hour not in parsed.cron.hours:
            moment = (moment + timedelta(hours=1)).replace(minute=0)
        else:
            moment += timedelta(minutes=1)
    return found


def next_fire_time(cadence: Cadence, after: datetime) -> Optional[datetime]:
    times = next_fire_times(cadence, after, count=1)
    return times[0] if times else None


def iter_fire_times(cadence: Cadence, start: datetime, end: datetime) -> Iterator[datetime]:
    """Every fire time in `[start, end)` — used for catch-up decisions."""
    cursor = start
    while True:
        upcoming = next_fire_time(cadence, cursor)
        if upcoming is None or upcoming >= end:
            return
        yield upcoming
        cursor = upcoming


# --------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------


def missed_runs(
    trigger: TriggerSpec, last_run: datetime, now: datetime
) -> list[datetime]:
    """Which missed fire times should actually run, per the catch-up policy."""
    if trigger.cadence is None:
        return []
    missed = list(iter_fire_times(trigger.cadence, last_run, now))
    if not missed or trigger.catch_up is CatchUpPolicy.SKIP_MISSED:
        return []
    if trigger.catch_up is CatchUpPolicy.RUN_ONCE:
        return missed[-1:]
    return missed


def admits_new_run(trigger: TriggerSpec, running: int) -> bool:
    """Whether a due run may start given how many are already in flight."""
    if running == 0:
        return True
    return trigger.overlap in (OverlapPolicy.ALLOW, OverlapPolicy.QUEUE,
                               OverlapPolicy.CANCEL_PREVIOUS)


def should_halt(trigger: TriggerSpec, consecutive_failures: int) -> bool:
    return consecutive_failures >= trigger.failure.halt_after_consecutive_failures


def should_escalate(trigger: TriggerSpec, consecutive_failures: int) -> bool:
    return consecutive_failures >= trigger.failure.escalate_after_failures


def retry_delays(trigger: TriggerSpec) -> list[int]:
    """Exponential backoff delays for the configured retry count."""
    base = trigger.failure.backoff_seconds
    return [base * (2**attempt) for attempt in range(trigger.failure.retries)]


def describe(trigger: TriggerSpec) -> str:
    """One line for the registry, the UI and generated documentation."""
    if trigger.kind.value == "schedule" and trigger.cadence:
        when = f"{parse_cadence(trigger.cadence).describe()} ({trigger.cadence.timezone})"
    elif trigger.kind.value == "event":
        when = f"on event '{trigger.event_class}'"
    elif trigger.kind.value == "message":
        when = f"on a message in '{trigger.channel}'"
    elif trigger.kind.value == "webhook":
        when = "on an inbound call"
    else:
        when = "on request"
    target = trigger.workflow or trigger.agent
    return f"{when} → {target}"
