"""Scale (ADR-0095): bounds that cannot hold.

Caught here rather than by a cloud rejecting the apply, because a design that
says something impossible about its own capacity should not reach a
deployment to find out.
"""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule


@rule("scale", {"scale_ceiling_below_one", "scale_floor_negative",
                "scale_floor_above_ceiling", "scale_concurrency_below_one"})
def scaling_bounds_hold(ctx: ValidationContext) -> None:
    err = ctx.err
    for agent in ctx.agents:
        policy = getattr(agent, "scaling", None)
        if policy is None:
            continue
        if policy.max_instances < 1:
            err("scale_ceiling_below_one",
                f"agent '{agent.id}' has max_instances "
                f"{policy.max_instances}, so it can never run. To stop an "
                "agent, do not deploy it", agent.id)
        if policy.min_instances < 0:
            err("scale_floor_negative",
                f"agent '{agent.id}' has min_instances "
                f"{policy.min_instances}", agent.id)
        elif policy.min_instances > policy.max_instances:
            err("scale_floor_above_ceiling",
                f"agent '{agent.id}' keeps {policy.min_instances} instances "
                f"warm and allows at most {policy.max_instances}. The floor "
                "cannot be above the ceiling", agent.id)
        if policy.concurrent_sessions_per_instance < 1:
            err("scale_concurrency_below_one",
                f"agent '{agent.id}' has "
                f"concurrent_sessions_per_instance "
                f"{policy.concurrent_sessions_per_instance}, so an instance "
                "would accept no work", agent.id)
