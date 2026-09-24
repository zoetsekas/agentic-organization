"""Authority: mandates name declared decisions, and the top one is written
down (ADR-0065, ADR-0071)."""
from __future__ import annotations

from typing import Any

from .context import ValidationContext
from .registry import rule


def check_mandate(ctx: ValidationContext, mandate: Any, where: str,
                  what: str) -> None:
    if mandate is None:
        return
    for decision in mandate.decisions:
        if decision not in ctx.declared_decisions:
            ctx.err(
                "undeclared_decision",
                f"{what} '{where}' claims decision class '{decision}', "
                "which the spec does not declare",
                where,
            )


@rule("authority: mandates", {"undeclared_decision", "root_leader_without_mandate"})
def mandates_are_declared(ctx: ValidationContext) -> None:
    spec = ctx.spec
    # The root *team* may default to the whole declared vocabulary (ADR-0071):
    # a team is a scope and nobody exercises it, and requiring the root to
    # enumerate every decision is what made authority accumulate upward.
    #
    # The root's *leader* is a different matter. It is a principal, it inherits
    # its unit's mandate, and at the root that is everything. So the one place
    # authority must be written down is the agent at the top.
    root_leader = spec.organization.leader
    if root_leader:
        top = next(
            (m for m in spec.organization.members if m.id == root_leader), None
        )
        if top is not None and top.mandate is None:
            ctx.err(
                "root_leader_without_mandate",
                f"agent '{root_leader}' leads the organization and declares no "
                "mandate, so it inherits every decision this organization can "
                "take. A principal's authority is the one thing that is never "
                "silent: declare it, or declare `decisions: []` to say it "
                "decides nothing (ADR-0071)",
                root_leader,
            )

    for team in ctx.teams:
        check_mandate(ctx, team.mandate, team.id, "team")
    for agent in ctx.agents:
        check_mandate(ctx, agent.mandate, agent.id, "agent")
    for mission in spec.missions:
        check_mandate(ctx, mission.mandate, mission.id, "mission")
