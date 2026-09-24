"""Triggers (ADR-0020): what wakes an agent, and who hears about it."""
from __future__ import annotations

from ..model import TriggerKind
from .context import ValidationContext
from .registry import rule


@rule("triggers", {
    "unknown_trigger_agent", "unknown_trigger_workflow",
    "schedule_without_cadence", "invalid_cadence", "event_without_class",
    "unknown_trigger_channel", "unknown_delivery_channel",
    "unknown_failure_channel", "trigger_exceeds_run_budget",
    "trigger_workflow_not_granted", "silent_trigger",
})
def triggers_are_wired(ctx: ValidationContext) -> None:
    # Imported here: `scheduling` reads the spec model, so a module-level
    # import would close a cycle through this package's __init__.
    from ...scheduling import CadenceError, parse_cadence

    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    channel_ids = ctx.channel_ids
    for trigger in spec.triggers:
        if trigger.agent not in ctx.agent_ids:
            err("unknown_trigger_agent", f"trigger '{trigger.id}' runs unknown agent "
                f"'{trigger.agent}'", trigger.id)
        if trigger.workflow and not any(w.id == trigger.workflow for w in spec.workflows):
            err("unknown_trigger_workflow", f"trigger '{trigger.id}' references unknown "
                f"workflow '{trigger.workflow}'", trigger.id)
        if trigger.kind is TriggerKind.SCHEDULE:
            if trigger.cadence is None:
                err("schedule_without_cadence", f"trigger '{trigger.id}' is scheduled "
                    "but states no cadence", trigger.id)
            else:
                try:
                    parse_cadence(trigger.cadence)
                except CadenceError as e:
                    err("invalid_cadence", f"trigger '{trigger.id}': {e}", trigger.id)
        if trigger.kind is TriggerKind.EVENT and not trigger.event_class:
            err("event_without_class", f"trigger '{trigger.id}' is event-driven but "
                "names no event class", trigger.id)
        if trigger.kind is TriggerKind.MESSAGE and trigger.channel not in channel_ids:
            err("unknown_trigger_channel", f"trigger '{trigger.id}' listens on unknown "
                f"channel '{trigger.channel}'", trigger.id)
        for cid in trigger.deliver_to:
            if cid not in channel_ids:
                err("unknown_delivery_channel", f"trigger '{trigger.id}' delivers to "
                    f"unknown channel '{cid}'", trigger.id)
        if trigger.failure.notify_channel and trigger.failure.notify_channel not in channel_ids:
            err("unknown_failure_channel", f"trigger '{trigger.id}' notifies unknown "
                f"channel '{trigger.failure.notify_channel}'", trigger.id)
        if trigger.max_runtime_seconds > spec.resilience.max_run_seconds:
            err("trigger_exceeds_run_budget", f"trigger '{trigger.id}' allows "
                f"{trigger.max_runtime_seconds}s but resilience caps runs at "
                f"{spec.resilience.max_run_seconds}s", trigger.id)
        agent = spec.agent(trigger.agent)
        if agent and trigger.workflow and trigger.workflow not in agent.workflows:
            err("trigger_workflow_not_granted", f"trigger '{trigger.id}' runs workflow "
                f"'{trigger.workflow}' that agent '{trigger.agent}' may not invoke",
                trigger.id)
        if not trigger.deliver_to and not trigger.failure.notify_channel:
            warn("silent_trigger", f"trigger '{trigger.id}' reports to nobody",
                 trigger.id, strict=True)
