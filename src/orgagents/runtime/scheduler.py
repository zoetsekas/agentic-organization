"""The scheduler service: fires triggers, honours their policies (ADR-0020).

Deliberately small and injectable — the clock and the runner are parameters —
so overlap, catch-up, retry, escalation and halt behaviour are testable without
waiting for wall-clock time.

Two invariants:

* a triggered run uses **the owning agent's identity and permissions**, never a
  privileged scheduler account;
* a run that fails repeatedly stops and tells a human, rather than retrying
  into a budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from ..scheduling import (
    missed_runs,
    next_fire_time,
    retry_delays,
    should_escalate,
    should_halt,
)
from ..spec.model import Cadence, OverlapPolicy, TriggerSpec

Runner = Callable[[TriggerSpec, datetime], Any]
Notifier = Callable[[str, str, dict[str, Any]], None]


@dataclass
class TriggerState:
    trigger_id: str
    next_run: Optional[datetime] = None
    last_run: Optional[datetime] = None
    running: int = 0
    queued: int = 0
    consecutive_failures: int = 0
    total_runs: int = 0
    total_failures: int = 0
    halted: bool = False
    halted_reason: str = ""


@dataclass
class FireOutcome:
    trigger_id: str
    fired_at: datetime
    ok: bool
    skipped: bool = False
    reason: str = ""
    attempts: int = 1
    escalated_to: Optional[str] = None
    result: Any = None


@dataclass
class SchedulerService:
    """Holds triggers and decides, at any instant, what should run."""

    triggers: list[TriggerSpec]
    runner: Runner
    notifier: Optional[Notifier] = None
    max_concurrency: int = 4
    state: dict[str, TriggerState] = field(default_factory=dict)
    history: list[FireOutcome] = field(default_factory=list)

    def __post_init__(self) -> None:
        for trigger in self.triggers:
            self.state.setdefault(trigger.id, TriggerState(trigger.id))

    # -- planning ----------------------------------------------------------

    def prime(self, now: datetime) -> None:
        """Compute the first fire time for every scheduled trigger."""
        for trigger in self.triggers:
            if trigger.cadence and trigger.enabled:
                self.state[trigger.id].next_run = next_fire_time(trigger.cadence, now)

    def due(self, now: datetime) -> list[TriggerSpec]:
        out = []
        for trigger in self.triggers:
            state = self.state[trigger.id]
            if not trigger.enabled or state.halted or state.next_run is None:
                continue
            if state.next_run <= now:
                out.append(trigger)
        return out

    def in_flight(self) -> int:
        return sum(s.running for s in self.state.values())

    # -- firing ------------------------------------------------------------

    def fire(self, trigger: TriggerSpec, now: datetime) -> FireOutcome:
        """Run one trigger, applying overlap, retry, escalation and halt rules."""
        state = self.state[trigger.id]

        if state.halted:
            return self._skip(trigger, now, f"halted: {state.halted_reason}")
        if self.in_flight() >= self.max_concurrency:
            return self._skip(trigger, now, "scheduler at max concurrency")
        if state.running:
            if trigger.overlap is OverlapPolicy.SKIP:
                return self._skip(trigger, now, "previous run still in flight")
            if trigger.overlap is OverlapPolicy.QUEUE:
                state.queued += 1
                return self._skip(trigger, now, "queued behind the previous run")
            if trigger.overlap is OverlapPolicy.CANCEL_PREVIOUS:
                state.running = 0

        state.running += 1
        attempts = 0
        error: Optional[Exception] = None
        result: Any = None
        try:
            for attempt in range(len(retry_delays(trigger)) + 1):
                attempts = attempt + 1
                try:
                    result = self.runner(trigger, now)
                    error = None
                    break
                except Exception as e:      # retried, then escalated
                    error = e
        finally:
            state.running = max(state.running - 1, 0)

        state.total_runs += 1
        state.last_run = now
        if trigger.cadence:
            state.next_run = next_fire_time(trigger.cadence, now)

        if error is None:
            state.consecutive_failures = 0
            outcome = FireOutcome(trigger.id, now, True, attempts=attempts, result=result)
            self._deliver(trigger, outcome)
        else:
            state.consecutive_failures += 1
            state.total_failures += 1
            outcome = FireOutcome(
                trigger.id, now, False, reason=f"{type(error).__name__}: {error}",
                attempts=attempts,
            )
            if should_halt(trigger, state.consecutive_failures):
                state.halted = True
                state.halted_reason = (
                    f"{state.consecutive_failures} consecutive failures"
                )
                outcome.reason += " — halted"
            if should_escalate(trigger, state.consecutive_failures):
                outcome.escalated_to = trigger.failure.notify_channel
                self._notify(
                    trigger.failure.notify_channel,
                    f"trigger '{trigger.id}' failed "
                    f"{state.consecutive_failures}x: {outcome.reason}",
                    {"trigger": trigger.id, "agent": trigger.agent},
                )
        self.history.append(outcome)
        return outcome

    def _skip(self, trigger: TriggerSpec, now: datetime, reason: str) -> FireOutcome:
        outcome = FireOutcome(trigger.id, now, True, skipped=True, reason=reason)
        # A skipped run still advances the schedule; it is not retried later.
        state = self.state[trigger.id]
        if trigger.cadence:
            state.next_run = next_fire_time(trigger.cadence, now)
        self.history.append(outcome)
        return outcome

    def _deliver(self, trigger: TriggerSpec, outcome: FireOutcome) -> None:
        for channel in trigger.deliver_to:
            self._notify(channel, f"trigger '{trigger.id}' completed",
                         {"trigger": trigger.id, "result": outcome.result})

    def _notify(self, channel: Optional[str], message: str,
                payload: dict[str, Any]) -> None:
        if channel and self.notifier:
            self.notifier(channel, message, payload)

    # -- catch-up and ticking ---------------------------------------------

    def catch_up(self, now: datetime, downtime_since: datetime) -> list[FireOutcome]:
        """Run what was missed while the scheduler was down, per policy."""
        outcomes = []
        for trigger in self.triggers:
            if not trigger.enabled or trigger.cadence is None:
                continue
            for moment in missed_runs(trigger, downtime_since, now):
                outcomes.append(self.fire(trigger, moment))
        return outcomes

    def tick(self, now: datetime) -> list[FireOutcome]:
        """Fire everything due at `now`."""
        return [self.fire(trigger, now) for trigger in self.due(now)]

    def run_window(
        self, start: datetime, end: datetime, step: timedelta = timedelta(minutes=1)
    ) -> list[FireOutcome]:
        """Simulate a window — used by tests and by `orgagents schedule --simulate`."""
        self.prime(start)
        outcomes: list[FireOutcome] = []
        moment = start
        while moment <= end:
            outcomes.extend(self.tick(moment))
            moment += step
        return outcomes

    # -- reporting ---------------------------------------------------------

    def table(self, now: Optional[datetime] = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        rows = []
        for trigger in self.triggers:
            state = self.state[trigger.id]
            rows.append(
                {
                    "trigger": trigger.id,
                    "agent": trigger.agent,
                    "enabled": trigger.enabled,
                    "next_run": state.next_run.isoformat() if state.next_run else None,
                    "runs": state.total_runs,
                    "failures": state.total_failures,
                    "halted": state.halted,
                }
            )
        return rows


def from_manifest(
    manifest: list[dict[str, Any]], runner: Runner, **kwargs: Any
) -> SchedulerService:
    """Build a service from the compiled `triggers.json` a target emits."""
    triggers = []
    for row in manifest:
        cadence = None
        if row.get("cron"):
            cadence = Cadence(expression=row["cron"], timezone=row.get("timezone", "UTC"))
        elif row.get("interval_seconds"):
            minutes = max(int(row["interval_seconds"]) // 60, 1)
            cadence = Cadence(expression=f"every {minutes} minutes",
                              timezone=row.get("timezone", "UTC"))
        triggers.append(
            TriggerSpec(
                id=row["id"],
                description=row.get("description", ""),
                kind=row.get("kind", "schedule"),
                agent=row.get("agent_id", ""),
                workflow=row.get("workflow"),
                cadence=cadence,
                event_class=row.get("event_class", ""),
                channel=row.get("channel"),
                input=row.get("input", {}),
                enabled=row.get("enabled", True),
                overlap=row.get("overlap", "skip"),
                catch_up=row.get("catch_up", "skip_missed"),
                max_runtime_seconds=row.get("max_runtime_seconds", 900),
                requires_approval=row.get("requires_approval", False),
                deliver_to=row.get("deliver_to", []),
                failure={"retries": row.get("retries", 0),
                         "notify_channel": row.get("notify_on_failure")},
            )
        )
    return SchedulerService(triggers=triggers, runner=runner, **kwargs)
