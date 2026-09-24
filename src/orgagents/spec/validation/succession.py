"""Succession (ADR-0094).

A declared successor stands in laterally, so it does not already hold the
failed leader's mandate the way a manager does. Checking the union here means
a succession that could never be safe is refused before deployment, rather
than discovered during the outage it was meant to survive. A control found at
3am is a control that failed.
"""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule


@rule("succession", {"successor_is_self", "unknown_successor",
                     "successor_breaks_separation"})
def successors_are_safe(ctx: ValidationContext) -> None:
    err = ctx.err
    agents_by_id = ctx.agents_by_id
    resolved = ctx.separation_mandates
    for agent in agents_by_id.values():
        successor_id = getattr(agent, "successor", None)
        if not successor_id:
            continue          # the manager stands in, and is granted nothing
        if successor_id == agent.id:
            err("successor_is_self",
                f"agent '{agent.id}' names itself as its own successor, which "
                "covers nothing: the case this exists for is the agent not "
                "running", agent.id)
            continue
        if successor_id not in agents_by_id:
            err("unknown_successor",
                f"agent '{agent.id}' names successor '{successor_id}', which "
                "the spec does not declare", agent.id)
            continue
        if resolved is None:
            continue          # no separations declared: nothing to collapse
        leader = resolved.agents.get(agent.id)
        stand_in = resolved.agents.get(successor_id)
        if leader is None or stand_in is None:
            continue
        for rule_ in ctx.spec.separations:
            both = set(rule_.decisions) & (leader.decisions | stand_in.decisions)
            if len(both) > 1 and (set(rule_.decisions) & leader.decisions) \
                    and (set(rule_.decisions) & stand_in.decisions):
                err(
                    "successor_breaks_separation",
                    f"agent '{successor_id}' is declared successor to "
                    f"'{agent.id}', and between them they hold "
                    f"{sorted(both)}, which separation '{rule_.id}' forbids"
                    + (f": {rule_.reason}" if rule_.reason else "")
                    + ". Standing in would put both sides of this control in "
                    "one place at exactly the moment nobody is watching. Name "
                    "a successor that holds neither side, or leave it unset so "
                    "the manager stands in",
                    agent.id,
                )
