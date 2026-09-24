"""Autonomy postures (ADR-0072), and authority a line does not hold (ADR-0071).

The posture used to be emergent — action, decision, mandate and approval in
four places and no stated intent. Declaring it is only worth anything if the
mechanics are checked against the declaration, which is what this does. Drift
is then a failed check rather than a silent change.
"""
from __future__ import annotations

from typing import Any

from ..model import AutonomyPosture, HumanRole, autonomy_rank
from .context import ValidationContext
from .registry import rule

SECTION = "autonomy postures"

READING = {"read", "query"}


@rule(SECTION, {"autonomy_widened", "advisory_mutates",
                "autonomous_without_decision", "autonomous_without_mandate",
                "autonomy_without_evidence", "supervised_without_approval",
                "supervised_by_its_own_owner", "human_decides_but_agent_holds",
                "human_decides_with_no_holder"})
def postures_match_mechanics(ctx: ValidationContext) -> None:
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    if not spec.capabilities:
        return
    _mandates = ctx.mandates
    cap_by_id, role_by_id = ctx.cap_by_id, ctx.role_by_id
    evaluated = {
        a for case in spec.lifecycle.evaluations for a in (case.applies_to or [])
    }
    evaluates_everyone = any(
        not case.applies_to for case in spec.lifecycle.evaluations
    )

    def posture_for(agent: Any, cap_id: str) -> AutonomyPosture:
        cap = cap_by_id[cap_id]
        declared = agent.autonomy.get(cap_id)
        posture: AutonomyPosture = declared or cap.autonomy
        return posture

    def capabilities_for(agent: Any, team_roles: list[Any]) -> set[str]:
        out: set[str] = set()
        for assignment in list(agent.roles) + list(team_roles):
            role = role_by_id.get(getattr(assignment, "role", assignment))
            if role:
                out.update(role.capabilities)
        out.update(agent.capabilities)
        return {c for c in out if c in cap_by_id}

    def check_autonomy(team: Any, inherited: list[Any]) -> None:
        team_roles = inherited + [r.role for r in team.roles]
        for agent in team.members:
            for cap_id in sorted(capabilities_for(agent, team_roles)):
                cap = cap_by_id[cap_id]
                posture = posture_for(agent, cap_id)
                declared = agent.autonomy.get(cap_id)
                mutates = cap.action.value not in READING
                holds = bool(
                    cap.decision
                    and cap.decision in _mandates.for_agent(agent.id).decisions
                )
                where = f"{agent.id}/{cap_id}"

                # 6. An assignment may tighten and never loosen.
                if declared and autonomy_rank(declared) < autonomy_rank(cap.autonomy):
                    err(
                        "autonomy_widened",
                        f"{where}: agent declares '{declared.value}' where the "
                        f"capability declares '{cap.autonomy.value}'; an "
                        "assignment may only ever tighten",
                        where,
                    )

                # 1. Advisory may not mutate a system of record.
                if posture is AutonomyPosture.ADVISORY and mutates:
                    err(
                        "advisory_mutates",
                        f"{where}: declared advisory but the action is "
                        f"'{cap.action.value}', so it changes a system of "
                        "record. An advisory activity reads and models; give it "
                        "a decision class and a posture, or make it read-only",
                        where,
                    )

                # 2. Autonomy needs a decision class the agent actually holds.
                if posture is AutonomyPosture.AUTONOMOUS:
                    if not cap.decision:
                        err(
                            "autonomous_without_decision",
                            f"{where}: declared autonomous but names no decision "
                            "class, so no mandate governs it, no condition "
                            "bounds it and nothing can escalate",
                            where,
                        )
                    elif not holds:
                        err(
                            "autonomous_without_mandate",
                            f"{where}: declared autonomous but the agent does "
                            f"not hold '{cap.decision}', so every call would "
                            "escalate",
                            where,
                        )
                    # 5. Autonomy needs evidence, and "never run" is not it.
                    if not (evaluates_everyone or agent.id in evaluated):
                        warn(
                            "autonomy_without_evidence",
                            f"{where}: runs unattended and no evaluation case "
                            "applies to this agent; never-evaluated is not "
                            "safe-unattended (ADR-0060)",
                            where,
                            strict=True,
                        )

                # 3. Supervised means a person confirms, and not the requester's
                #    own owner.
                if posture is AutonomyPosture.SUPERVISED:
                    if not cap.constraints.requires_approval:
                        err(
                            "supervised_without_approval",
                            f"{where}: declared supervised but the capability "
                            "does not require approval, so nothing stops for a "
                            "person",
                            where,
                        )
                    approvers = [
                        h for h in agent.humans
                        if HumanRole.APPROVER in h.roles and not h.is_owner
                    ]
                    if not approvers:
                        warn(
                            "supervised_by_its_own_owner",
                            f"{where}: the only person who can confirm this is "
                            "the agent's own owner, which is not a second pair "
                            "of eyes; name an approver who does not own it",
                            where,
                        )

                # 4. Human-decides means the agent must not hold it, and
                #    somebody must.
                if posture is AutonomyPosture.HUMAN_DECIDES:
                    if holds:
                        err(
                            "human_decides_but_agent_holds",
                            f"{where}: declared as the human's decision and the "
                            f"agent holds '{cap.decision}', so it would never "
                            "reach one",
                            where,
                        )
                    elif cap.decision and not _mandates.holders(cap.decision):
                        warn(
                            "human_decides_with_no_holder",
                            f"{where}: declared as somebody else's decision and "
                            f"no agent holds '{cap.decision}', so the work "
                            "cannot complete inside this organization",
                            where,
                        )
        for child in team.teams:
            check_autonomy(child, team_roles)

    check_autonomy(spec.organization, [])


@rule(SECTION, {"mandate_overreach"})
def no_mandate_overreach(ctx: ValidationContext) -> None:
    # Claiming authority your line does not hold is narrowed, not honoured —
    # so it is reported rather than refused. A spec that reads as if a team may
    # decide something it cannot is a spec somebody will act on.
    for unit_id, over in ctx.mandates.overreach.items():
        # An error, not a warning (ADR-0071). The claim has no effect, so the
        # unit decides less than its author believes — and with the root now
        # defaulting to the whole vocabulary, reaching this at all means some
        # ancestor deliberately narrowed. Silently deciding nothing is the
        # worst of the three possible outcomes.
        ctx.err(
            "mandate_overreach",
            f"'{unit_id}' claims {sorted(over)}, which its line does not hold, "
            "so it would decide nothing of the kind. Authority narrows "
            "downward: either widen the ancestor that excludes it, or drop "
            "the claim",
            unit_id,
        )
