"""Channels (ADR-0021): who is on them, and whether a promise can be kept."""
from __future__ import annotations

from ..model import ChannelPurpose
from .context import ValidationContext
from .registry import rule


@rule("channels", {
    "unknown_data_class", "unknown_channel_member", "unknown_escalation_channel",
    "escalation_out_of_order", "channel_without_purpose",
    "sla_without_escalation", "sla_unreachable_out_of_hours",
})
def channels_are_answerable(ctx: ValidationContext) -> None:
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    for channel in spec.channels:
        for dc_id in channel.forbid_data_classes:
            if spec.data_class(dc_id) is None:
                err("unknown_data_class", f"channel '{channel.id}' forbids unknown data "
                    f"class '{dc_id}'", channel.id)
        for member in channel.members:
            if member not in ctx.agent_ids and member not in ctx.team_ids:
                err("unknown_channel_member", f"channel '{channel.id}' lists unknown "
                    f"member '{member}'", channel.id)
        for step in channel.escalation:
            if step.channel and step.channel not in ctx.channel_ids:
                err("unknown_escalation_channel", f"channel '{channel.id}' escalates to "
                    f"unknown channel '{step.channel}'", channel.id)
        if channel.escalation:
            offsets = [s.after_minutes for s in channel.escalation]
            if offsets != sorted(offsets):
                err("escalation_out_of_order", f"channel '{channel.id}' escalation steps "
                    "are not in increasing time order", channel.id)
        if channel.human_facing and not channel.purposes:
            warn("channel_without_purpose", f"human-facing channel '{channel.id}' states "
                 "no purpose", channel.id)
        if (channel.human_facing and channel.response_sla_minutes
                and not channel.escalation):
            warn("sla_without_escalation", f"channel '{channel.id}' promises a reply in "
                 f"{channel.response_sla_minutes} minutes but nobody is escalated to",
                 channel.id, strict=True)
        # A channel that people only watch in office hours cannot carry an
        # incident SLA shorter than the time until they are back.
        if (channel.working_hours and channel.out_of_hours == "queue"
                and (channel.response_sla_minutes or 0) and channel.response_sla_minutes < 60
                and ChannelPurpose.NOTIFY in (channel.purposes or [])):
            warn("sla_unreachable_out_of_hours", f"channel '{channel.id}' queues out of "
                 "hours but promises a sub-hour reply", channel.id)
