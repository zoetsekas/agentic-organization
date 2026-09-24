"""Knowledge (ADR-0023)."""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule


@rule("knowledge", {"unknown_data_class", "unknown_knowledge"})
def knowledge_resolves(ctx: ValidationContext) -> None:
    spec = ctx.spec
    for source in spec.knowledge:
        for dc_id in source.data_classes:
            if spec.data_class(dc_id) is None:
                ctx.err("unknown_data_class", f"knowledge source '{source.id}' "
                        f"references unknown data class '{dc_id}'", source.id)
    for agent in ctx.agents:
        for source_id in agent.knowledge:
            if spec.knowledge_source(source_id) is None:
                ctx.err("unknown_knowledge", f"agent '{agent.id}' references unknown "
                        f"knowledge source '{source_id}'", agent.id)
