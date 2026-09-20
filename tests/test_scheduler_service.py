"""The scheduler service: overlap, retries, escalation, halting (ADR-0020)."""
from datetime import datetime, timedelta, timezone

from orgagents.runtime.scheduler import SchedulerService, from_manifest
from orgagents.spec.model import Cadence, FailurePolicy, OverlapPolicy, TriggerSpec

START = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)   # a Monday


def hourly(**kw) -> TriggerSpec:
    defaults = dict(id="hourly", agent="analyst",
                    cadence=Cadence(expression="every 1 hour"))
    defaults.update(kw)
    return TriggerSpec(**defaults)


def test_fires_on_cadence_and_advances():
    fired = []
    service = SchedulerService([hourly()], runner=lambda t, now: fired.append(now))
    service.run_window(START, START + timedelta(hours=5), timedelta(minutes=30))
    assert len(fired) == 5
    assert service.state["hourly"].next_run > START + timedelta(hours=5)


def test_disabled_triggers_never_fire():
    fired = []
    service = SchedulerService([hourly(enabled=False)],
                               runner=lambda t, now: fired.append(now))
    service.run_window(START, START + timedelta(hours=3))
    assert fired == []


def test_retries_then_succeeds():
    attempts = []

    def flaky(trigger, now):
        attempts.append(now)
        if len(attempts) < 3:
            raise RuntimeError("upstream timeout")
        return "recovered"

    trigger = hourly(failure=FailurePolicy(retries=3, backoff_seconds=1))
    service = SchedulerService([trigger], runner=flaky)
    service.prime(START)
    outcome = service.fire(trigger, START + timedelta(hours=1))
    assert outcome.ok and outcome.attempts == 3
    assert service.state["hourly"].consecutive_failures == 0


def test_escalates_and_then_halts():
    notices = []
    trigger = hourly(
        failure=FailurePolicy(retries=0, escalate_after_failures=2,
                              halt_after_consecutive_failures=3,
                              notify_channel="incidents")
    )

    def always_fails(t, now):
        raise RuntimeError("database unreachable")

    service = SchedulerService(
        [trigger], runner=always_fails,
        notifier=lambda ch, msg, payload: notices.append((ch, msg)),
    )
    service.prime(START)
    outcomes = [service.fire(trigger, START + timedelta(hours=i)) for i in range(1, 5)]

    assert outcomes[0].escalated_to is None          # first failure: retry quietly
    assert outcomes[1].escalated_to == "incidents"   # second: tell a human
    assert service.state["hourly"].halted            # third: stop trying
    assert outcomes[3].skipped and "halted" in outcomes[3].reason
    assert notices and notices[0][0] == "incidents"


def test_overlap_skip_versus_allow():
    trigger = hourly(overlap=OverlapPolicy.SKIP)
    service = SchedulerService([trigger], runner=lambda t, now: "ok")
    service.state["hourly"].running = 1
    assert service.fire(trigger, START).skipped

    permissive = hourly(id="parallel", overlap=OverlapPolicy.ALLOW)
    service2 = SchedulerService([permissive], runner=lambda t, now: "ok")
    service2.state["parallel"].running = 1
    assert not service2.fire(permissive, START).skipped


def test_concurrency_ceiling_is_respected():
    trigger = hourly(overlap=OverlapPolicy.ALLOW)
    service = SchedulerService([trigger], runner=lambda t, now: "ok", max_concurrency=1)
    service.state["hourly"].running = 1
    assert "max concurrency" in service.fire(trigger, START).reason


def test_catch_up_after_downtime():
    fired = []
    trigger = hourly(catch_up="run_once")
    service = SchedulerService([trigger], runner=lambda t, now: fired.append(now))
    outcomes = service.catch_up(START + timedelta(hours=6), START)
    assert len(outcomes) == 1 and len(fired) == 1


def test_results_are_delivered_to_channels():
    delivered = []
    trigger = hourly(deliver_to=["exec_briefing"])
    service = SchedulerService(
        [trigger], runner=lambda t, now: "the report",
        notifier=lambda ch, msg, payload: delivered.append((ch, payload["result"])),
    )
    service.prime(START)
    service.fire(trigger, START + timedelta(hours=1))
    assert delivered == [("exec_briefing", "the report")]


def test_from_manifest_round_trips_a_compiled_trigger(tmp_path):
    from orgagents.compiler import build_ir
    from orgagents.spec import load_binding, load_spec
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ir = build_ir(
        load_spec(root / "examples" / "acme.system.yaml"),
        binding=load_binding(root / "examples" / "acme.binding.yaml").for_target("local"),
    )
    manifest = [t.model_dump(mode="json") for t in ir.triggers]
    fired = []
    service = from_manifest(manifest, runner=lambda t, now: fired.append(t.id))
    service.prime(START)
    scheduled = [t for t in service.triggers if t.cadence]
    assert {t.id for t in scheduled} >= {"weekday_flash_report", "month_end_close_pack"}
    service.run_window(START, START + timedelta(days=1), timedelta(minutes=10))
    assert "weekday_flash_report" in fired
