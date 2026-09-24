"""Control ownership (ADR-0073).

A bound that reads as enforced and is not is worse than no bound, so a
control claimed for this platform must be evaluable here, and a control left
to an application may not be claimed.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from ..model import (
    PLATFORM_EVALUATED_CONSTRAINTS,
    ControlEnforcer,
    _wider,
    condition_is_evaluable,
)
from .context import ValidationContext
from .registry import rule


def check_enforcement(
    ctx: ValidationContext,
    enforcement: Any, declared: Iterable[str], where: str, what: str,
    evaluable: Callable[[str], bool],
) -> None:
    err, warn = ctx.err, ctx.warn
    names = sorted(set(declared))
    if enforcement.enforced_by is ControlEnforcer.BOTH:
        if enforcement.authoritative is None:
            err(
                "both_without_authority",
                f"{what} '{where}' is enforced by this platform and by an "
                "application, and does not say which wins. Two rule sets "
                "that can disagree need an answer, not a debate",
                where,
            )
        elif enforcement.authoritative is ControlEnforcer.BOTH:
            err(
                "both_without_authority",
                f"{what} '{where}' names 'both' as authoritative, which "
                "answers nothing",
                where,
            )
    if enforcement.enforced_by is ControlEnforcer.APPLICATION:
        if not enforcement.enforced_in:
            warn(
                "application_control_unnamed",
                f"{what} '{where}' is left to an application and does not "
                "say which kind. A control nobody can point at is a control "
                "nobody will check",
                where,
            )
        return  # described here, not evaluated here

    # PLATFORM or BOTH: whatever we claim, we must be able to check.
    for name in names:
        if not evaluable(name):
            err(
                "unenforceable_platform_control",
                f"{what} '{where}' claims '{name}' as a control this "
                "platform enforces, and nothing here evaluates it. Either "
                "something must, or declare it `enforced_by: application` "
                "and name the system that does (ADR-0073)",
                where,
            )

    # Rule 7: our bound may never be wider than the application's claim.
    for name in names:
        theirs = enforcement.application_bounds.get(name)
        ours = None
        if hasattr(enforcement, "_ours"):
            ours = enforcement._ours.get(name)
        if theirs is None or ours is None:
            continue
        if _wider(name, ours, theirs):
            err(
                "platform_bound_wider_than_application",
                f"{what} '{where}': this platform allows {name}={ours} "
                f"where the application is recorded as allowing {theirs}. "
                "A bound may narrow what the application permits and never "
                "widen it",
                where,
            )


def _check_mandate_enforcement(ctx: ValidationContext, unit: Any, kind: str) -> None:
    mandate = getattr(unit, "mandate", None)
    if mandate is None or not mandate.conditions:
        return
    # The bounds this platform claims, kept on the enforcement for the width
    # check above, as they always have been.
    enforcement: Any = mandate.enforcement
    enforcement._ours = dict(mandate.conditions)
    check_enforcement(
        ctx, enforcement, mandate.conditions, unit.id, kind,
        condition_is_evaluable,
    )


@rule("control ownership", {"both_without_authority", "application_control_unnamed",
                            "unenforceable_platform_control",
                            "platform_bound_wider_than_application"})
def controls_are_owned(ctx: ValidationContext) -> None:
    for cap in ctx.spec.capabilities:
        claimed = [
            f for f in ("requires_approval", "max_rows", "masked_fields",
                        "allowed_operations", "resource_scope", "rate_per_minute")
            if getattr(cap.constraints, f, None) not in (None, False, [], {})
        ]
        enforcement: Any = cap.constraints.enforcement
        enforcement._ours = {f: getattr(cap.constraints, f) for f in claimed}
        check_enforcement(
            ctx, enforcement, claimed, cap.id, "capability",
            lambda n: n in PLATFORM_EVALUATED_CONSTRAINTS,
        )

    for team in ctx.teams:
        _check_mandate_enforcement(ctx, team, "team mandate")
    for agent in ctx.agents:
        _check_mandate_enforcement(ctx, agent, "agent mandate")

    for rule_ in ctx.spec.separations:
        check_enforcement(
            ctx, rule_.enforcement, [], rule_.id, "separation", lambda n: True,
        )
