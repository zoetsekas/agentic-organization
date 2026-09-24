"""Interaction flows (ADR-0024)."""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule


@rule("interaction flows", {"unknown_flow_endpoint", "self_flow"})
def flows_join_two_parties(ctx: ValidationContext) -> None:
    for flow in ctx.spec.interaction_flows:
        for end, label in ((flow.source, "source"), (flow.target, "target")):
            if end not in ctx.agent_ids and end not in ctx.team_ids:
                ctx.err("unknown_flow_endpoint", f"flow {flow.source}->{flow.target} "
                        f"has unknown {label} '{end}'")
        if flow.source == flow.target:
            ctx.err("self_flow", f"flow from '{flow.source}' to itself")
