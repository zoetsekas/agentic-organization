"""Effective authority: what a unit may decide without asking (ADR-0065).

A mandate is declared on a team, an agent or a mission and is **never read as
declared**. What binds is the *effective* mandate: the intersection of a unit's
own with every unit above it, so authority narrows downward and a unit can
never grant what it does not hold — the rule permissions already follow
(ADR-0008).

Resolution happens once, at the phase gate, and is written into the IR. The
runtime reads it and never re-derives it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class EffectiveMandate:
    """What a unit may actually decide, after narrowing.

    `conditions` is a chain, not a merge. Every condition from every unit in
    the line applies, which makes narrowing correct by construction: a child
    adding a condition tightens the bound, and a child cannot drop one its
    parent set because dropping is not an operation this structure has.
    """

    decisions: frozenset[str] = frozenset()
    conditions: tuple[dict[str, Any], ...] = ()
    #: Units whose declarations produced this, root first. For explaining a
    #: refusal to somebody who did not write the spec.
    line: tuple[str, ...] = ()

    def covers(self, decision: str) -> bool:
        return decision in self.decisions

    def narrowed_by(
        self, unit_id: str, declared: Optional["MandateLike"]
    ) -> "EffectiveMandate":
        """Apply a child's declaration to this mandate.

        Declaring nothing inherits. Declaring more than the parent holds
        narrows to the parent rather than being honoured — the declaration is
        not an error, it simply cannot reach past its line.
        """
        line = self.line + (unit_id,)
        if declared is None:
            return EffectiveMandate(self.decisions, self.conditions, line)
        decisions = self.decisions & frozenset(declared.decisions or ())
        conditions = self.conditions
        if declared.conditions:
            conditions = conditions + (dict(declared.conditions),)
        return EffectiveMandate(decisions, conditions, line)

    def overreach(self, declared: Optional["MandateLike"]) -> list[str]:
        """Decisions a unit claimed that its line does not hold."""
        if declared is None:
            return []
        return sorted(frozenset(declared.decisions or ()) - self.decisions)


class MandateLike:
    """Structural type: anything with `decisions` and `conditions`."""

    decisions: list[str]
    conditions: dict[str, Any]


def root_mandate(declared: Optional[MandateLike], unit_id: str = "root") -> EffectiveMandate:
    """The organization root is where authority enters the system.

    Nothing above it narrows it, so it is taken as declared. A root that
    declares nothing holds nothing — silence never means authority, which is
    why the spec validator refuses a root without a mandate rather than
    letting it default to unlimited.
    """
    if declared is None:
        return EffectiveMandate(frozenset(), (), (unit_id,))
    return EffectiveMandate(
        frozenset(declared.decisions or ()),
        ((dict(declared.conditions),) if declared.conditions else ()),
        (unit_id,),
    )


@dataclass
class MandateMap:
    """Effective mandates for every unit in one organization."""

    teams: dict[str, EffectiveMandate] = field(default_factory=dict)
    agents: dict[str, EffectiveMandate] = field(default_factory=dict)
    #: agent id -> its home team id, so a holder search can walk upward.
    home: dict[str, str] = field(default_factory=dict)
    #: team id -> parent team id.
    parent: dict[str, str] = field(default_factory=dict)
    #: team id -> its leader's agent id. A unit's authority is exercised by a
    #: person or an agent, never by the unit, so the walk goes leader to leader.
    leader: dict[str, str] = field(default_factory=dict)
    #: (unit id, decision) claimed but not held, for reporting.
    overreach: dict[str, list[str]] = field(default_factory=dict)

    def for_agent(self, agent_id: str) -> EffectiveMandate:
        return self.agents.get(agent_id, EffectiveMandate())

    def holder(self, agent_id: str, decision: str) -> Optional[str]:
        """The nearest **agent** up the line that may take `decision` itself.

        A team is a scope, not a principal (ADR-0070): its mandate bounds what
        its members may hold, and nobody exercises it. This used to return a
        team id, which both named something that cannot act and disagreed with
        `OrgChart.mandate_holder`, which walks agents. The walk is therefore
        leader to leader, and what is tested at each step is that agent's own
        effective mandate — a leader narrowed for separation of duties does not
        hold what its unit bounds.

        `None` means no principal in this line may take the decision. That is a
        refusal. It is not a promotion to the root, and it is not a reason to
        look sideways: an agent elsewhere holding the decision is reached
        through the process that owns it, not by escalating past a control.
        """
        if self.for_agent(agent_id).covers(decision):
            return agent_id
        seen: set[str] = set()
        team = self.home.get(agent_id)
        while team and team not in seen:
            seen.add(team)
            lead = self.leader.get(team)
            if lead and lead != agent_id and self.for_agent(lead).covers(decision):
                return lead
            team = self.parent.get(team)
        return None

    def holders(self, decision: str) -> list[str]:
        """Every agent whose effective mandate covers `decision`.

        For explaining a refusal — "this belongs to Treasury" is a better
        answer than "nobody" — without routing the decision there. Naming a
        holder is not reaching one.
        """
        return sorted(a for a, eff in self.agents.items() if eff.covers(decision))


def resolve(
    organization: Any, vocabulary: Optional[Iterable[str]] = None
) -> MandateMap:
    """Walk a spec's organization tree and resolve every unit's authority.

    A root that declares no mandate holds the whole declared `vocabulary`
    (ADR-0071). That is safe because a team is a scope and nobody exercises it
    (ADR-0070), and it removes the enumeration burden that made authority
    accumulate: without it, giving a leaf agent one new decision meant editing
    every unit between it and the root, and forgetting to was a warning and an
    agent that silently decided nothing.

    What is *not* safe is a principal inheriting it, so the root's leader still
    declares its own — checked by the spec validator, not here.
    """
    out = MandateMap()

    def visit(team: Any, inherited: Optional[EffectiveMandate]) -> None:
        declared = getattr(team, "mandate", None)
        if inherited is None:
            if declared is None or not getattr(declared, "decisions", None):
                effective = EffectiveMandate(
                    frozenset(vocabulary or ()), (), (team.id,)
                )
            else:
                effective = root_mandate(declared, team.id)
        else:
            over = inherited.overreach(declared)
            if over:
                out.overreach[team.id] = over
            effective = inherited.narrowed_by(team.id, declared)
        out.teams[team.id] = effective
        if getattr(team, "leader", ""):
            out.leader[team.id] = team.leader

        for member in getattr(team, "members", []) or []:
            member_declared = getattr(member, "mandate", None)
            over = effective.overreach(member_declared)
            if over:
                out.overreach[member.id] = over
            out.agents[member.id] = effective.narrowed_by(member.id, member_declared)
            out.home[member.id] = team.id

        for child in getattr(team, "teams", []) or []:
            out.parent[child.id] = team.id
            visit(child, effective)

    visit(organization, None)
    return out


def mission_mandate(
    mission: Any, leader_mandate: EffectiveMandate
) -> EffectiveMandate:
    """Authority lent to a mission, bounded by the unit accountable for it.

    ADR-0065 rule 8 bounds this by the *sponsor's* mandate. People do not
    carry mandates yet — that is the hybrid-organization gap ADR-0064 holds
    open — so the bound is the mission leader's effective mandate, which is
    already narrowed by the standing tree and is the stricter reading.

    Expiry is not handled here: a mission's window is checked per call by
    `missions.grant_is_open`, so an unswept mission confers nothing.
    """
    return leader_mandate.narrowed_by(mission.id, getattr(mission, "mandate", None))
