"""Cadence semantics and trigger policies (ADR-0020)."""
from datetime import datetime, timedelta, timezone

import pytest

from orgagents.scheduling import (
    CadenceError,
    CronSchedule,
    admits_new_run,
    describe,
    iter_fire_times,
    missed_runs,
    next_fire_time,
    next_fire_times,
    parse_cadence,
    retry_delays,
    should_escalate,
    should_halt,
)
from orgagents.spec.model import (
    Cadence,
    CatchUpPolicy,
    FailurePolicy,
    OverlapPolicy,
    TriggerSpec,
)

SUNDAY = datetime(2026, 9, 20, 8, 30, tzinfo=timezone.utc)   # a Sunday


def _cron(expression: str, tz: str = "UTC") -> Cadence:
    return Cadence(expression=expression, timezone=tz)


def test_cron_fields():
    schedule = CronSchedule.parse("*/15 9-17 * * 1-5")
    assert schedule.minutes == {0, 15, 30, 45}
    assert schedule.hours == set(range(9, 18))
    assert schedule.weekdays == {1, 2, 3, 4, 5}


def test_sunday_is_zero_and_seven():
    assert CronSchedule.parse("0 0 * * 7").weekdays == {0}
    assert CronSchedule.parse("0 0 * * 0").weekdays == {0}


@pytest.mark.parametrize("expression", ["", "0 9 * *", "61 9 * * *", "0 9 * * 9",
                                        "0 9 * * */0", "abc"])
def test_invalid_cron_is_rejected(expression):
    with pytest.raises(CadenceError):
        parse_cadence(_cron(expression))


def test_weekday_cron_skips_the_weekend():
    times = next_fire_times(_cron("0 9 * * 1-5"), SUNDAY, 3)
    assert [t.isoformat() for t in times] == [
        "2026-09-21T09:00:00+00:00",
        "2026-09-22T09:00:00+00:00",
        "2026-09-23T09:00:00+00:00",
    ]


def test_intervals_are_anchored_to_the_epoch():
    """Stable across restarts: a restart must not shift the cadence."""
    first = next_fire_times(_cron("every 15 minutes"), SUNDAY, 2)
    later = next_fire_times(_cron("every 15 minutes"),
                            SUNDAY + timedelta(minutes=3), 2)
    assert first[0].minute == 45 and later[0].minute == 45


def test_timezone_and_daylight_saving():
    """A 6am London job is 05:00 UTC in summer and 06:00 UTC in winter."""
    summer = next_fire_time(_cron("0 6 1 * *", "Europe/London"),
                            datetime(2026, 9, 5, tzinfo=timezone.utc))
    winter = next_fire_time(_cron("0 6 1 * *", "Europe/London"),
                            datetime(2026, 11, 5, tzinfo=timezone.utc))
    assert summer.hour == 5 and winter.hour == 6


def test_aliases_and_descriptions():
    assert parse_cadence(_cron("daily")).describe() == "every 1 day"
    assert parse_cadence(_cron("@hourly")).kind == "cron"
    assert "cron" in parse_cadence(_cron("0 9 * * *")).describe()


def test_unknown_timezone_is_rejected():
    with pytest.raises(CadenceError):
        parse_cadence(_cron("0 9 * * *", "Mars/Olympus"))


def test_iter_fire_times_is_bounded():
    window = list(iter_fire_times(_cron("every 1 hour"), SUNDAY,
                                  SUNDAY + timedelta(hours=5)))
    assert len(window) == 5


def _trigger(**kw) -> TriggerSpec:
    defaults = dict(id="t", agent="a", cadence=_cron("every 1 hour"))
    defaults.update(kw)
    return TriggerSpec(**defaults)


def test_catch_up_policies():
    downtime = SUNDAY - timedelta(hours=5)
    assert missed_runs(_trigger(catch_up=CatchUpPolicy.SKIP_MISSED),
                       downtime, SUNDAY) == []
    assert len(missed_runs(_trigger(catch_up=CatchUpPolicy.RUN_ONCE),
                           downtime, SUNDAY)) == 1
    assert len(missed_runs(_trigger(catch_up=CatchUpPolicy.RUN_ALL),
                           downtime, SUNDAY)) == 5


def test_overlap_policies():
    assert admits_new_run(_trigger(overlap=OverlapPolicy.SKIP), running=0)
    assert not admits_new_run(_trigger(overlap=OverlapPolicy.SKIP), running=1)
    assert admits_new_run(_trigger(overlap=OverlapPolicy.ALLOW), running=3)


def test_failure_thresholds_and_backoff():
    trigger = _trigger(failure=FailurePolicy(retries=3, backoff_seconds=30,
                                             escalate_after_failures=2,
                                             halt_after_consecutive_failures=4))
    assert retry_delays(trigger) == [30, 60, 120]
    assert not should_escalate(trigger, 1) and should_escalate(trigger, 2)
    assert not should_halt(trigger, 3) and should_halt(trigger, 4)


def test_describe_covers_every_trigger_kind():
    assert "every 1 hour" in describe(_trigger())
    assert "event" in describe(_trigger(kind="event", cadence=None,
                                        event_class="alert"))
    assert "message" in describe(_trigger(kind="message", cadence=None,
                                          channel="ops"))
    assert "request" in describe(_trigger(kind="manual", cadence=None))
