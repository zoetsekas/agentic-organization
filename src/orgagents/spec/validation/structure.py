"""Organization structure (ADR-0006): leaders, members, one home each."""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule

SECTION = "organization structure"


@rule(SECTION, {"team_without_leader", "leader_not_member",
                "child_leader_double_listed"})
def teams_are_led(ctx: ValidationContext) -> None:
    err = ctx.err
    for team in ctx.teams:
        member_ids = {m.id for m in team.members}
        if not team.leader:
            err("team_without_leader", f"team '{team.id}' has no leader", team.id)
        elif team.leader not in member_ids:
            err(
                "leader_not_member",
                f"leader '{team.leader}' is not a member of team '{team.id}'",
                team.id,
            )
        # A child team's leader participates in the parent *implicitly* through
        # leadership (ADR-0006 v1.1.0); listing it in the parent's members too
        # would give the agent two home teams.
        for child in team.teams:
            if child.leader and child.leader in member_ids:
                err(
                    "child_leader_double_listed",
                    f"leader '{child.leader}' of child team '{child.id}' is also "
                    f"listed as a member of parent team '{team.id}'; parent "
                    "membership is implicit",
                    child.id,
                )
        # A team declaring nothing inherits its parent's authority, which is
        # a legitimate and common choice (ADR-0065 rule 5), so silence here is
        # no longer worth a warning. What matters is the root, checked below.


@rule(SECTION, {"multiple_home_teams"})
def one_home_team(ctx: ValidationContext) -> None:
    # An agent must have exactly one home team.
    homes: dict[str, list[str]] = {}
    for team in ctx.teams:
        for member in team.members:
            homes.setdefault(member.id, []).append(team.id)
    for agent_id, owners in homes.items():
        # A leader appears as leader of its own team and member of the parent;
        # membership is still recorded once, so >1 is a genuine error.
        if len(owners) > 1:
            ctx.err(
                "multiple_home_teams",
                f"agent '{agent_id}' is a member of {owners}; exactly one is allowed",
                agent_id,
            )
