"""Capability coverage: a capability nothing holds is a declaration nobody uses."""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule


@rule("capability coverage", {"unused_capability"})
def capabilities_are_used(ctx: ValidationContext) -> None:
    spec, agents = ctx.spec, ctx.agents
    used = {c for a in agents for c in a.capabilities}
    used |= {c for r in spec.roles for c in r.capabilities}
    used |= {c for a in agents for sa in a.subagents for c in sa.capabilities}
    used |= {t.wraps for t in spec.tools if t.wraps_kind == "capability"}
    used |= {c for e in spec.endpoints for c in e.provides}
    for cap in spec.capabilities:
        if cap.id not in used:
            ctx.warn("unused_capability", f"capability '{cap.id}' is declared but "
                     "unused", cap.id)
