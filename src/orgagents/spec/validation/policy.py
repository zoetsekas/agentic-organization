"""Policy conditions (ADR-0008).

A condition key nothing evaluates is not inert. On `conditions` it under- or
over-applies the rule depending on its effect; on `unless` it disapplies the
rule outright, so one transposed letter turned a deny on PII into an allow.
The runtime now refuses such a rule; this refuses the spec, which is where it
should be caught.
"""
from __future__ import annotations

from ..model import POLICY_CONDITION_KEYS
from .context import ValidationContext
from .registry import rule


@rule("policy conditions", {"unknown_policy_condition"})
def policy_conditions_are_evaluable(ctx: ValidationContext) -> None:
    for rule_ in ctx.spec.policies:
        for where, conditions in (("conditions", rule_.conditions),
                                  ("unless", rule_.unless)):
            for key in sorted(set(conditions or {}) - POLICY_CONDITION_KEYS):
                ctx.err(
                    "unknown_policy_condition",
                    f"policy '{rule_.id}' guards on `{where}.{key}`, which "
                    "nothing evaluates. This platform understands "
                    f"{sorted(POLICY_CONDITION_KEYS)}"
                    + (". An unevaluated key in `unless` disapplies the whole "
                       "rule, so a deny written here would not deny"
                       if where == "unless" else ""),
                    rule_.id,
                )
