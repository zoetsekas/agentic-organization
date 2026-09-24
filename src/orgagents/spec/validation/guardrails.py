"""Guardrails, workspaces, contracts (ADR-0035, ADR-0036, ADR-0037)."""
from __future__ import annotations

from ..model import SharingScope
from .context import ValidationContext
from .registry import rule

SECTION = "guardrails, workspaces, contracts"


@rule(SECTION, {"guardrail_without_checks", "unknown_data_class",
                "unknown_guardrail_channel", "escalation_without_channel",
                "no_guardrails", "guardrail_gap"})
def guardrails_check_something(ctx: ValidationContext) -> None:
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    for guardrail in spec.guardrails:
        if not guardrail.checks:
            err("guardrail_without_checks", f"guardrail '{guardrail.id}' declares "
                "no checks", guardrail.id)
        for dc_id in guardrail.data_classes:
            if spec.data_class(dc_id) is None:
                err("unknown_data_class", f"guardrail '{guardrail.id}' watches "
                    f"unknown data class '{dc_id}'", guardrail.id)
        if guardrail.escalate_channel and guardrail.escalate_channel not in {
            c.id for c in spec.channels
        }:
            err("unknown_guardrail_channel", f"guardrail '{guardrail.id}' escalates "
                f"to unknown channel '{guardrail.escalate_channel}'", guardrail.id)
        if (guardrail.on_violation.value == "escalate"
                and not guardrail.escalate_channel):
            err("escalation_without_channel", f"guardrail '{guardrail.id}' escalates "
                "but names no channel", guardrail.id)
    if not spec.guardrails:
        warn("no_guardrails", "no guardrails are declared; permissions decide what "
             "agents may reach, but nothing checks what may pass", strict=True)
    else:
        kinds = {k for g in spec.guardrails for k in g.applies_to}
        for needed in ("input", "output"):
            if needed not in {k.value for k in kinds}:
                warn("guardrail_gap", f"no guardrail applies to '{needed}'",
                     strict=True)


@rule(SECTION, {"artifact_store_without_groups", "unknown_data_class",
                "unknown_artifact_store", "summarize_never_fires"})
def workspaces_are_scoped(ctx: ValidationContext) -> None:
    spec, err = ctx.spec, ctx.err
    for store in spec.artifact_stores:
        if store.scope is SharingScope.PROTECTED and not store.groups:
            err("artifact_store_without_groups", f"artifact store '{store.id}' is "
                "protected but names no groups", store.id)
        for dc_id in store.data_classes:
            if spec.data_class(dc_id) is None:
                err("unknown_data_class", f"artifact store '{store.id}' holds "
                    f"unknown data class '{dc_id}'", store.id)
    if spec.context.offload_to and spec.artifact_store(spec.context.offload_to) is None:
        err("unknown_artifact_store", f"context offloads to unknown store "
            f"'{spec.context.offload_to}'")
    if spec.context.summarize_after_tokens >= spec.context.max_context_tokens:
        err("summarize_never_fires", "summarize_after_tokens is not below "
            "max_context_tokens, so compaction would never run")


@rule(SECTION, {"contract_without_schema"})
def contracts_have_schemas(ctx: ValidationContext) -> None:
    for contract in ctx.spec.output_contracts:
        if not contract.schema_:
            ctx.err("contract_without_schema", f"output contract '{contract.id}' has "
                    "no schema, so it checks nothing", contract.id)
