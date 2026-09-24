"""Uniqueness: one id, one thing."""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule


def _kinds(ctx: ValidationContext) -> tuple[tuple[str, list[str]], ...]:
    # Every kind with an id belongs here. The list used to name eight of them,
    # so a spec could declare two tools called the same thing and the second
    # simply won by being second — a silent overwrite of a declaration.
    spec = ctx.spec
    return (
        ("agent", ctx.agent_ids),
        ("team", ctx.team_ids),
        ("role", [r.id for r in spec.roles]),
        ("capability", [c.id for c in spec.capabilities]),
        ("decision class", [d.id for d in spec.decisions]),
        ("environment", [e.id for e in spec.environments]),
        ("data class", [d.id for d in spec.data_classes]),
        ("workflow", [w.id for w in spec.workflows]),
        ("person", [p.id for p in spec.people]),
        ("separation", [x.id for x in spec.separations]),
        ("policy", [x.id for x in spec.policies]),
        ("skill", [x.id for x in spec.skills]),
        ("plugin", [x.id for x in spec.plugins]),
        ("tool", [x.id for x in spec.tools]),
        ("endpoint", [x.id for x in spec.endpoints]),
        ("knowledge source", [x.id for x in spec.knowledge]),
        ("channel", [x.id for x in spec.channels]),
        ("trigger", [x.id for x in spec.triggers]),
        ("guardrail", [x.id for x in spec.guardrails]),
    )


@rule("uniqueness", {"duplicate_id", "id_used_by_two_kinds"})
def unique_ids(ctx: ValidationContext) -> None:
    kinds = _kinds(ctx)
    for label, ids in kinds:
        seen: set[str] = set()
        for i in ids:
            if i in seen:
                ctx.err("duplicate_id", f"duplicate {label} id '{i}'")
            seen.add(i)

    # The same id on two *different* kinds resolves — references are looked up
    # per kind — but nothing that draws a spec can show both, because a
    # picture has one box per id. A warning rather than an error: it is a
    # legibility problem, not a correctness one.
    by_id: dict[str, set[str]] = {}
    for label, ids in kinds:
        for i in ids:
            by_id.setdefault(i, set()).add(label)
    for i, labels in sorted(by_id.items()):
        if len(labels) > 1:
            ctx.warn(
                "id_used_by_two_kinds",
                f"'{i}' names both a {' and a '.join(sorted(labels))}. It "
                "resolves, because references are looked up per kind, but "
                "nothing can draw or diff the two apart",
                where=i,
            )
