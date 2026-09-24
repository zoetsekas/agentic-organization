"""The fabric's platform policy (ADR-0076), applied last.

A policy judges the whole finding set rather than being one more rule inside
it: it may re-rank or drop what the rules found, and adds its own findings
after them.
"""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule

CODES = {"platform_policy_requires", "platform_policy_forbids_autonomy",
         "platform_policy_autonomy_ceiling"}


@rule("platform policy", CODES, stage="finalize")
def platform_policy_judges(ctx: ValidationContext) -> None:
    from ...platform_policy import apply_severity, policy_findings

    policy = ctx.platform_policy
    ctx.out = apply_severity(policy, ctx.out) + policy_findings(policy, ctx.spec)
