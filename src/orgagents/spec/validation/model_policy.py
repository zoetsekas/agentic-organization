"""Model policy (ADR-0040): which models may be used, system-wide and per agent."""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule


@rule("model policy", {"model_policy_empty", "model_policy_redundant",
                       "model_policy_contradiction",
                       "model_regions_outside_residency"})
def model_policies_are_coherent(ctx: ValidationContext) -> None:
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    for label, policy in [("system", spec.model_policy)] + [
        (a.id, a.model_policy) for a in ctx.agents if a.model_policy
    ]:
        if not policy.classes and not policy.allow:
            err("model_policy_empty", f"model policy for '{label}' permits nothing: "
                "name at least one class or an allowed model", label)
        if policy.allow and policy.classes:
            warn("model_policy_redundant", f"model policy for '{label}' names both "
                 "explicit models and classes; the explicit list wins", label)
        overlap = set(policy.allow) & set(policy.deny)
        if overlap:
            err("model_policy_contradiction", f"model policy for '{label}' both "
                f"allows and denies {sorted(overlap)}", label)
        if policy.require_regions and spec.compliance.data_residency:
            if not set(policy.require_regions) & set(spec.compliance.data_residency):
                warn("model_regions_outside_residency", f"model policy for '{label}' "
                     f"requires {policy.require_regions}, outside the declared "
                     f"residency {spec.compliance.data_residency}", label,
                     strict=True)
