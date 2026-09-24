"""Memory contract (ADR-0028)."""
from __future__ import annotations

from ..model import SharingScope
from .context import ValidationContext
from .registry import rule


@rule("memory contract", {
    "memory_namespace_without_groups", "unknown_data_class",
    "memory_retains_sensitive_class", "memory_residency_undeclared",
    "long_term_without_namespace", "session_memory_is_long_term",
})
def memory_is_governed(ctx: ValidationContext) -> None:
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    for namespace in spec.memory.namespaces:
        if namespace.scope is SharingScope.PROTECTED and not namespace.groups:
            err("memory_namespace_without_groups", f"memory namespace "
                f"'{namespace.id}' is protected but names no groups", namespace.id)
        for dc_id in namespace.data_classes:
            dc = spec.data_class(dc_id)
            if dc is None:
                err("unknown_data_class", f"memory namespace '{namespace.id}' holds "
                    f"unknown data class '{dc_id}'", namespace.id)
                continue
            # Anything excluded from traces is excluded from durable memory too,
            # unless the tier redacts it.
            if (not dc.may_appear_in_traces
                    and dc_id not in spec.memory.long_term.redact_data_classes):
                err("memory_retains_sensitive_class", f"memory namespace "
                    f"'{namespace.id}' holds '{dc_id}', which may not be retained "
                    "unredacted", namespace.id)
            if not dc.may_leave_region and not spec.compliance.data_residency:
                warn("memory_residency_undeclared", f"memory namespace "
                     f"'{namespace.id}' holds region-restricted '{dc_id}' but no "
                     "residency is declared", namespace.id, strict=True)
    if spec.memory.long_term.enabled and not spec.memory.namespaces:
        warn("long_term_without_namespace", "long-term memory is enabled but no "
             "namespace is declared, so nothing can be promoted into it")
    if spec.memory.session.retention_days and spec.memory.session.retention_days > 7:
        warn("session_memory_is_long_term", "session memory retained for "
             f"{spec.memory.session.retention_days} days is long-term memory that "
             "has not been governed as such")
