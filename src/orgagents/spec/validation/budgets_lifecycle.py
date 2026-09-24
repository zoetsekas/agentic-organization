"""Budgets, lifecycle, compliance (ADR-0022)."""
from __future__ import annotations

from ..model import LifecycleStage
from .context import ValidationContext
from .registry import rule

SECTION = "budgets, lifecycle, compliance"


@rule(SECTION, {"budget_without_limit", "unknown_budget_scope",
                "unknown_budget_channel"})
def budgets_are_bounded(ctx: ValidationContext) -> None:
    err = ctx.err
    for budget in ctx.spec.budgets:
        if budget.limit_usd <= 0:
            err("budget_without_limit", f"budget '{budget.id}' has no positive limit",
                budget.id)
        if budget.scope_kind == "team" and budget.scope not in ctx.team_ids:
            err("unknown_budget_scope", f"budget '{budget.id}' scopes unknown team "
                f"'{budget.scope}'", budget.id)
        if budget.scope_kind == "agent" and budget.scope not in ctx.agent_ids:
            err("unknown_budget_scope", f"budget '{budget.id}' scopes unknown agent "
                f"'{budget.scope}'", budget.id)
        if budget.notify_channel and budget.notify_channel not in ctx.channel_ids:
            err("unknown_budget_channel", f"budget '{budget.id}' notifies unknown "
                f"channel '{budget.notify_channel}'", budget.id)


@rule(SECTION, {"agent_without_evaluation", "production_without_gate"})
def lifecycle_is_evidenced(ctx: ValidationContext) -> None:
    spec = ctx.spec
    covered = {e for case in spec.lifecycle.evaluations for e in case.applies_to}
    if spec.lifecycle.evaluations:
        for agent in ctx.agents:
            if agent.id not in covered and not any(
                not c.applies_to for c in spec.lifecycle.evaluations
            ):
                ctx.warn("agent_without_evaluation", f"agent '{agent.id}' has no "
                         "evaluation case", agent.id, strict=True)
    if spec.lifecycle.stage is LifecycleStage.PRODUCTION:
        if not any(g.to_stage is LifecycleStage.PRODUCTION for g in spec.lifecycle.gates):
            ctx.err("production_without_gate", "the system is in production with no "
                    "promotion gate defining how it got there")


@rule(SECTION, {"residency_undeclared", "trace_leak"})
def compliance_is_declared(ctx: ValidationContext) -> None:
    spec = ctx.spec
    residency = spec.compliance.data_residency
    for dc in spec.data_classes:
        if not dc.may_leave_region and not residency:
            ctx.warn("residency_undeclared", f"data class '{dc.id}' may not leave a "
                     "region but no residency is declared", dc.id, strict=True)
        if not dc.may_appear_in_traces and dc.id not in spec.observability.redact_data_classes:
            ctx.err("trace_leak", f"data class '{dc.id}' may not appear in traces but "
                    "is not redacted in the observability contract", dc.id)
