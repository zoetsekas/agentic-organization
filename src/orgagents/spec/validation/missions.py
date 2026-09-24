"""Missions (ADR-0039): short-lived teams drawn from the standing organization."""
from __future__ import annotations

from datetime import date

from .context import ValidationContext
from .registry import rule


@rule("missions", {
    "duplicate_mission", "mission_without_leader", "mission_leader_not_member",
    "mission_member_not_in_org", "mission_duplicate_member",
    "mission_without_objective", "mission_without_deliverables",
    "mission_without_end", "mission_ends_before_it_starts", "mission_too_long",
    "mission_bad_dates", "mission_past_its_end_date", "unknown_mission_channel",
    "unknown_mission_workflow", "unknown_role", "mission_would_widen_access",
})
def missions_are_bounded(ctx: ValidationContext) -> None:
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    seen_missions: set[str] = set()
    for mission in spec.missions:
        if mission.id in seen_missions:
            err("duplicate_mission", f"mission '{mission.id}' is defined twice",
                mission.id)
        seen_missions.add(mission.id)

        if not mission.leader:
            err("mission_without_leader", f"mission '{mission.id}' has no leader; "
                "every team has one", mission.id)
        elif mission.leader not in mission.members:
            err("mission_leader_not_member", f"leader '{mission.leader}' is not a "
                f"member of mission '{mission.id}'", mission.id)
        unknown = [m for m in mission.members if m not in ctx.agent_ids]
        if unknown:
            err("mission_member_not_in_org", f"mission '{mission.id}' includes "
                f"{unknown}, who are not agents in the organization; a mission "
                "draws from the standing organization", mission.id)
        if len(set(mission.members)) != len(mission.members):
            err("mission_duplicate_member", f"mission '{mission.id}' lists the same "
                "member twice", mission.id)
        if not mission.objective:
            err("mission_without_objective", f"mission '{mission.id}' states no "
                "objective; a short-lived team exists to achieve something",
                mission.id)
        if not mission.deliverables:
            warn("mission_without_deliverables", f"mission '{mission.id}' names no "
                 "deliverables, so nothing says when it is done", mission.id,
                 strict=True)
        if not mission.ends_on:
            err("mission_without_end", f"mission '{mission.id}' has no end date; a "
                "mission that never ends is a reorganization, and belongs in the "
                "org chart", mission.id)
        elif mission.starts_on:
            try:
                start = date.fromisoformat(mission.starts_on)
                end = date.fromisoformat(mission.ends_on)
                if end <= start:
                    err("mission_ends_before_it_starts", f"mission '{mission.id}' "
                        f"ends on {mission.ends_on}, on or before its start",
                        mission.id)
                elif (end - start).days > 180:
                    warn("mission_too_long", f"mission '{mission.id}' runs for "
                         f"{(end - start).days} days; past about two quarters this "
                         "is a standing team and should be in the org chart",
                         mission.id)
            except ValueError:
                err("mission_bad_dates", f"mission '{mission.id}' has unparseable "
                    "dates; use ISO format", mission.id)
        if mission.ends_on and mission.status.value in ("proposed", "active"):
            try:
                if date.fromisoformat(mission.ends_on) < date.today():
                    warn("mission_past_its_end_date",
                         f"mission '{mission.id}' ended on {mission.ends_on} but is "
                         f"still marked '{mission.status.value}'; the runtime stops "
                         "honouring it either way, so close the record "
                         "(`orgagents missions sweep`)", mission.id)
            except ValueError:
                pass
        if mission.channel and mission.channel not in {c.id for c in spec.channels}:
            err("unknown_mission_channel", f"mission '{mission.id}' uses unknown "
                f"channel '{mission.channel}'", mission.id)
        for workflow_id in mission.workflows:
            if not any(w.id == workflow_id for w in spec.workflows):
                err("unknown_mission_workflow", f"mission '{mission.id}' references "
                    f"unknown workflow '{workflow_id}'", mission.id)

        # A mission may narrow what members hold; it may never widen it.
        for assignment in mission.roles:
            role = spec.role(assignment.role)
            if role is None:
                err("unknown_role", f"mission '{mission.id}' references unknown role "
                    f"'{assignment.role}'", mission.id)
                continue
            wanted = {p.key() for p in role.permissions}
            for member_id in mission.members:
                member = spec.agent(member_id)
                if member is None:
                    continue
                held: set[str] = set()
                for member_assignment in member.roles:
                    member_role = spec.role(member_assignment.role)
                    if member_role:
                        held |= {p.key() for p in member_role.permissions}
                home = spec.team_of(member_id)
                if home:
                    for team_assignment in home.roles:
                        team_role = spec.role(team_assignment.role)
                        if team_role:
                            held |= {p.key() for p in team_role.permissions}
                extra = wanted - held
                if extra:
                    warn("mission_would_widen_access", f"mission '{mission.id}' "
                         f"assigns role '{role.id}', which asks for permissions "
                         f"'{member_id}' does not hold ({sorted(extra)[:3]}); they "
                         "are dropped, not granted", mission.id)
