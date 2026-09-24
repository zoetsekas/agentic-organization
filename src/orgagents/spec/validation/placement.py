"""Placement (ADR-0069).

A placement is an org unit crossed with an environment class. It is not a
security boundary — the tenant is — but it decides who shares a volume, a
process namespace and a network, so the things it makes easy are worth saying
out loud.
"""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule


@rule("placement", {"single_placement", "placement_denies_delegation",
                    "separated_agents_co_resident"})
def placements_say_what_they_share(ctx: ValidationContext) -> None:
    # Imported here: `placements` reads the spec model, so a module-level
    # import would close a cycle through this package's __init__.
    from ...placements import resolve as _resolve_placements
    from ...placements import standing_reach as _standing_reach

    spec, warn = ctx.spec, ctx.warn
    resolved = ctx.separation_mandates
    placed = _resolve_placements(spec)

    if len(placed.declared) == 1 and len(placed.placements) > 0:
        total = sum(len(p.agents) for p in placed.placements.values())
        if total > 1:
            warn(
                "single_placement",
                f"no team declares itself a placement boundary, so all {total} "
                "placed agents share their unit's sandbox environment, its "
                "volume and its process namespace. That is the widest "
                "arrangement this model has and the one a design gets by not "
                "deciding; declare `placement: true` on the units that should "
                "be kept apart (ADR-0069)",
                spec.organization.id,
            )

    # Generated policy and the org chart drift, and the drift is silent
    # (ADR-0069, Disadvantages). Making it a finding is what stops it being.
    for source, target, why in _standing_reach(spec):
        if source in placed.home and target in placed.home:
            if not placed.permits(source, target):
                warn(
                    "placement_denies_delegation",
                    f"'{source}' may hand work to '{target}' by {why}, and the "
                    f"network policy generated for placement "
                    f"{sorted(placed.home[source])} does not reach "
                    f"{sorted(placed.home[target])}. The org chart and the deployed "
                    "rules disagree, and nothing at run time will say so",
                    source,
                )

    # Separation of duties keeps two agents apart, and co-residency puts them
    # on one volume in one process namespace (ADR-0068 rule 7, unfixed). The
    # separation still holds — neither holds both sides — but the control is
    # weaker than the rule reads.
    for rule_ in spec.separations:
        for placement in placed.placements.values():
            holders_here: dict[str, list[str]] = {}
            for agent in placement.agents:
                effective = resolved.agents.get(agent) if resolved else None
                holders_here[agent] = sorted(
                    set(rule_.decisions)
                    & (effective.decisions if effective is not None else set())
                )
            sides = {a: held for a, held in holders_here.items() if held}
            distinct = {tuple(held) for held in sides.values()}
            if len(sides) > 1 and len(distinct) > 1:
                warn(
                    "separated_agents_co_resident",
                    f"separation '{rule_.id}' keeps {sorted(sides)} apart, and "
                    f"placement '{placement.id}' puts them in one sandbox "
                    "environment sharing a volume and a process namespace. The "
                    "rule still holds — no one agent holds both sides — but "
                    "the control is weaker than it reads; declare "
                    "`placement: true` on the unit that should be separate "
                    "(ADR-0069)",
                    placement.id,
                )
