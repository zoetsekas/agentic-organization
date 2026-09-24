"""Unit links: association, not containment (ADR-0081).

Containment is `team.teams` and is checked everywhere else. These are the
relationships a tree cannot hold, and each kind is checked against what it
claims — an association with nothing to fail is decoration.
"""
from __future__ import annotations

from ..model import Team, UnitLinkKind
from .context import ValidationContext
from .registry import rule


def _subtree_ids(team: Team) -> set[str]:
    out = {team.id}
    for child in team.teams:
        out |= _subtree_ids(child)
    return out


@rule("unit links: association, not containment", {
    "unknown_unit_link_endpoint", "self_unit_link", "duplicate_unit_link",
    "oversight_without_independence", "oversight_shares_a_leader",
    "escalation_runs_downward", "unit_link_without_a_reason",
})
def unit_links_hold(ctx: ValidationContext) -> None:
    err, warn = ctx.err, ctx.warn
    teams_by_id = ctx.teams_by_id
    seen_links: set[tuple[str, str, str]] = set()
    for link in ctx.spec.unit_links:
        where = f"{link.source}->{link.target}"
        missing = [
            (end, label)
            for end, label in ((link.source, "source"), (link.target, "target"))
            if end not in teams_by_id
        ]
        for end, label in missing:
            err("unknown_unit_link_endpoint",
                f"unit link {where} has unknown {label} '{end}'", where)
        if link.source == link.target:
            err("self_unit_link",
                f"unit '{link.source}' is linked to itself; containment and "
                "association are both relationships between two units", where)
            continue
        key = (link.source, link.target, link.kind.value)
        if key in seen_links:
            err("duplicate_unit_link",
                f"unit link {where} '{link.kind.value}' is declared twice. "
                "Saying it twice does not make it two facts", where)
        seen_links.add(key)
        if missing:
            continue

        source_subtree = _subtree_ids(teams_by_id[link.source])
        target_subtree = _subtree_ids(teams_by_id[link.target])

        if link.kind is UnitLinkKind.OVERSEES:
            # The rule that earns this feature. Independence used to be
            # expressed by absence, and an absence cannot be checked.
            if link.source in target_subtree:
                err("oversight_without_independence",
                    f"'{link.source}' claims to oversee '{link.target}', and "
                    f"sits inside it. An overseer contained by what it "
                    "oversees is not independent, and the independence is the "
                    "whole control (ADR-0081)", where)
            source_leader = teams_by_id[link.source].leader
            target_leader = teams_by_id[link.target].leader
            if source_leader and source_leader == target_leader:
                err("oversight_shares_a_leader",
                    f"'{link.source}' claims to oversee '{link.target}' and "
                    f"both are led by '{source_leader}', so the oversight "
                    "reports to what it is overseeing", where)
        elif link.kind is UnitLinkKind.ESCALATES_TO:
            if link.target in source_subtree:
                err("escalation_runs_downward",
                    f"'{link.source}' escalates to '{link.target}', which is "
                    "inside it. An escalation that lands in your own subtree "
                    "has not left the problem", where)
        elif link.kind is UnitLinkKind.PARTNERS_WITH:
            # Symmetric, so the reverse is the same fact written twice.
            if (link.target, link.source, link.kind.value) in seen_links:
                err("duplicate_unit_link",
                    f"'{link.source}' and '{link.target}' are declared to "
                    "partner with each other in both directions. It is a "
                    "symmetric relationship: that is one fact", where)

        if not link.reason:
            warn("unit_link_without_a_reason",
                 f"unit link {where} '{link.kind.value}' gives no reason. An "
                 "association nobody can explain is the decoration this model "
                 "is meant to refuse", where)
