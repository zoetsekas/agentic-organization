"""References: everything an agent names exists, and is its to use.

The agent checks run agent by agent, each agent's findings together in the
order below, because that is the order a reader of one agent's issues has
always been shown them in.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..model import (
    EndpointTrust,
    HumanRole,
    NetworkPosture,
    SharingScope,
)
from .context import ValidationContext
from .helpers import _agent_capability_ids
from .registry import rule

SECTION = "references"


def _check_narrowing(agent_id: str, env: Any, override: Any,
                     err: Callable[..., None]) -> None:
    """An environment override may only make the class stricter (ADR-0009)."""
    order = [
        NetworkPosture.NONE,
        NetworkPosture.ALLOWLIST,
        NetworkPosture.INTERNAL,
        NetworkPosture.OPEN,
    ]
    if override.timeout_seconds is not None and override.timeout_seconds > env.timeout_seconds:
        err(
            "environment_widened",
            f"agent '{agent_id}' requests a longer timeout than environment "
            f"'{env.id}' permits",
            agent_id,
        )
    if override.network is not None and order.index(override.network) > order.index(
        env.network
    ):
        err(
            "environment_widened",
            f"agent '{agent_id}' requests network '{override.network.value}' but "
            f"environment '{env.id}' allows only '{env.network.value}'",
            agent_id,
        )
    if override.egress_allowlist:
        extra = set(override.egress_allowlist) - set(env.egress_allowlist)
        if extra or env.network is NetworkPosture.NONE:
            detail = sorted(extra) if extra else "on an isolated environment"
            err(
                "environment_widened",
                f"agent '{agent_id}' requests egress {detail} not permitted by "
                f"'{env.id}'",
                agent_id,
            )


# -- one agent, concern by concern -------------------------------------------


def _roles_and_names(ctx: ValidationContext, agent: Any) -> None:
    spec, err = ctx.spec, ctx.err
    for assignment in agent.roles:
        role = spec.role(assignment.role)
        if role is None:
            err("unknown_role", f"agent '{agent.id}' references unknown role "
                f"'{assignment.role}'", agent.id)
        elif role.kind != "agent":
            err("wrong_role_kind", f"agent '{agent.id}' is assigned team role "
                f"'{role.id}'", agent.id)
    for cap_id in agent.capabilities:
        if spec.capability(cap_id) is None:
            err("unknown_capability", f"agent '{agent.id}' references unknown "
                f"capability '{cap_id}'", agent.id)
    for wf_id in agent.workflows:
        if not any(w.id == wf_id for w in spec.workflows):
            err("unknown_workflow", f"agent '{agent.id}' references unknown "
                f"workflow '{wf_id}'", agent.id)
    for peer in agent.peers:
        if peer not in ctx.agent_ids:
            err("unknown_peer", f"agent '{agent.id}' references unknown peer "
                f"'{peer}'", agent.id)


def _sandboxes(ctx: ValidationContext, agent: Any) -> list[str]:
    """Sandboxes (ADR-0069, ADR-0082); returns the agent's environments.

    An agent may run work in more than one, and which one a call uses is
    derived rather than declared: a capability names its data classes, a data
    class names the environments it is allowed in, and the intersection is the
    answer. So the checks are about whether that intersection is ever empty,
    and whether a declared sandbox is ever the answer to anything.
    """
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    seen_environments: set[str] = set()
    agent_environments: list[str] = []
    for override in agent.environments:
        env = spec.environment(override.environment)
        if env is None:
            err("unknown_environment", f"agent '{agent.id}' references unknown "
                f"environment '{override.environment}'", agent.id)
            continue
        if override.environment in seen_environments:
            err("duplicate_environment", f"agent '{agent.id}' declares "
                f"environment '{override.environment}' twice; two narrowings "
                "of one class are two answers to one question", agent.id)
        seen_environments.add(override.environment)
        agent_environments.append(override.environment)
        _check_narrowing(agent.id, env, override, err)

    if agent_environments:
        reachable: dict[str, set[str]] = {}
        for capability_id in _agent_capability_ids(spec, agent):
            capability = spec.capability(capability_id)
            if capability is None:
                continue
            allowed: Optional[set[str]] = None
            for dc_id in capability.data_classes:
                data_class = spec.data_class(dc_id)
                if data_class is None:
                    continue
                # An empty `allowed_environments` means "anywhere": the
                # class carries no environment restriction of its own.
                permitted = set(data_class.allowed_environments)
                if not permitted:
                    continue
                allowed = permitted if allowed is None else allowed & permitted
            usable = (set(agent_environments) if allowed is None
                      else set(agent_environments) & allowed)
            if not usable:
                err("capability_without_a_sandbox",
                    f"agent '{agent.id}' holds capability '{capability_id}', "
                    f"whose data classes are allowed only in "
                    f"{sorted(allowed or [])}, and it runs in "
                    f"{sorted(agent_environments)}. There is nowhere for "
                    "that work to happen (ADR-0082)", agent.id)
            for environment_id in usable:
                reachable.setdefault(environment_id, set()).add(capability_id)
        for environment_id in agent_environments:
            if environment_id not in reachable:
                warn("sandbox_without_work",
                     f"agent '{agent.id}' declares environment "
                     f"'{environment_id}', which no capability it holds can "
                     "use. A sandbox nothing runs in is a boundary nobody "
                     "is inside", agent.id)
    return agent_environments


def _human_pairing(ctx: ValidationContext, agent: Any) -> None:
    """Human pairing (ADR-0026)."""
    err, warn = ctx.err, ctx.warn
    owners = agent.humans_with(HumanRole.OWNER)
    if not agent.humans:
        warn("agent_without_human", f"agent '{agent.id}' is paired with nobody",
             agent.id, strict=True)
    elif not owners:
        err("agent_without_owner", f"agent '{agent.id}' has {len(agent.humans)} "
            "paired human(s) but none is the accountable owner", agent.id)
    elif len(owners) > 1:
        err("multiple_owners", f"agent '{agent.id}' has {len(owners)} owners "
            f"({', '.join(h.principal() for h in owners)}); exactly one is "
            "accountable", agent.id)
    # Identity, not the contact field: a pairing that names a declared
    # `person` carries no contact of its own (ADR-0079).
    contacts = [h.principal() for h in agent.humans]
    if len(contacts) != len(set(contacts)):
        err("duplicate_pairing", f"agent '{agent.id}' pairs the same person "
            "twice", agent.id)
    for human in agent.humans:
        if human.channel and human.channel not in {c.id for c in ctx.spec.channels}:
            err("unknown_human_channel", f"agent '{agent.id}' routes "
                f"{human.contact} to unknown channel '{human.channel}'", agent.id)
    gated = agent.approval_required_for
    if gated and not agent.humans_with(HumanRole.APPROVER):
        err("approvals_without_approver", f"agent '{agent.id}' gates "
            f"{gated} but pairs no approver", agent.id)
    for action in gated:
        if not agent.approvers_for(action):
            err("action_without_approver", f"agent '{agent.id}' gates '{action}' "
                "but no paired approver covers it", agent.id)


def _skills_plugins_tools(ctx: ValidationContext, agent: Any,
                          held_caps: set[str]) -> None:
    """Skills, plugins, tools (ADR-0029)."""
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    for skill_id in agent.skills:
        skill = spec.skill(skill_id)
        if skill is None:
            err("unknown_skill", f"agent '{agent.id}' references unknown skill "
                f"'{skill_id}'", agent.id)
            continue
        missing = set(skill.requires_capabilities) - held_caps
        if missing:
            warn("skill_without_capability", f"agent '{agent.id}' holds skill "
                 f"'{skill_id}' which assumes capabilities it lacks: "
                 f"{sorted(missing)}", agent.id)
    for plugin_id in agent.plugins:
        plugin = spec.plugin(plugin_id)
        if plugin is None:
            err("unknown_plugin", f"agent '{agent.id}' references unknown plugin "
                f"'{plugin_id}'", agent.id)
            continue
        missing = set(plugin.requires_capabilities) - held_caps
        if missing:
            err("plugin_without_capability", f"agent '{agent.id}' installs plugin "
                f"'{plugin_id}' which requires capabilities it lacks: "
                f"{sorted(missing)}", agent.id)
    for tool_id in agent.tools:
        tool = spec.tool(tool_id)
        if tool is None:
            err("unknown_tool", f"agent '{agent.id}' references unknown tool "
                f"'{tool_id}'", agent.id)
            continue
        if tool.wraps_kind == "capability" and tool.wraps not in held_caps:
            err("tool_without_capability", f"agent '{agent.id}' holds tool "
                f"'{tool_id}' wrapping capability '{tool.wraps}' it does not "
                "hold", agent.id)
        if tool.wraps_kind == "endpoint" and tool.wraps not in agent.endpoints:
            err("tool_without_endpoint", f"agent '{agent.id}' holds tool "
                f"'{tool_id}' wrapping endpoint '{tool.wraps}' it may not "
                "reach", agent.id)
        if tool.wraps_kind == "workflow" and tool.wraps not in agent.workflows:
            err("tool_without_workflow", f"agent '{agent.id}' holds tool "
                f"'{tool_id}' wrapping workflow '{tool.wraps}' it may not "
                "invoke", agent.id)
        if tool.wraps_kind == "subagent" and tool.wraps not in {
            sa.id for sa in agent.subagents
        }:
            err("tool_without_subagent", f"agent '{agent.id}' holds tool "
                f"'{tool_id}' wrapping sub-agent '{tool.wraps}' it does not "
                "define", agent.id)


def _subagents(ctx: ValidationContext, agent: Any, held_caps: set[str],
               agent_environments: list[str]) -> None:
    """Sub-agents are tools, and narrow only (ADR-0027)."""
    err, warn = ctx.err, ctx.warn
    seen_sub: set[str] = set()
    for sub in agent.subagents:
        if sub.id in seen_sub:
            err("duplicate_subagent", f"agent '{agent.id}' defines sub-agent "
                f"'{sub.id}' twice", agent.id)
        seen_sub.add(sub.id)
        extra = set(sub.capabilities) - held_caps
        if extra:
            err("subagent_widens_access", f"sub-agent '{sub.id}' of "
                f"'{agent.id}' requests capabilities its parent lacks: "
                f"{sorted(extra)}", agent.id)
        extra_tools = set(sub.tools) - set(agent.tools)
        if extra_tools:
            err("subagent_widens_tools", f"sub-agent '{sub.id}' of '{agent.id}' "
                f"requests tools its parent lacks: {sorted(extra_tools)}",
                agent.id)
        extra_knowledge = set(sub.knowledge) - set(agent.knowledge)
        if extra_knowledge:
            err("subagent_widens_knowledge", f"sub-agent '{sub.id}' of "
                f"'{agent.id}' requests knowledge its parent lacks: "
                f"{sorted(extra_knowledge)}", agent.id)
        widened = set(sub.environments) - set(agent_environments)
        if widened and agent_environments:
            err("subagent_changes_environment", f"sub-agent '{sub.id}' of "
                f"'{agent.id}' requests sandbox(es) its parent does not run "
                f"in: {sorted(widened)}. A sub-agent that could pick its own "
                "would be a way to reach a boundary the caller was never "
                "given", agent.id)
        if sub.max_runtime_seconds > ctx.spec.resilience.max_run_seconds:
            err("subagent_exceeds_run_budget", f"sub-agent '{sub.id}' allows "
                f"{sub.max_runtime_seconds}s beyond the system budget", agent.id)
        if not sub.returns:
            warn("subagent_without_return", f"sub-agent '{sub.id}' of "
                 f"'{agent.id}' does not say what it returns; a tool with an "
                 "undefined result is hard to use well", agent.id)


def _endpoints(ctx: ValidationContext, agent: Any) -> None:
    """External endpoints (ADR-0030)."""
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    for endpoint_id in agent.endpoints:
        endpoint = spec.endpoint(endpoint_id)
        if endpoint is None:
            err("unknown_endpoint", f"agent '{agent.id}' references unknown "
                f"endpoint '{endpoint_id}'", agent.id)
            continue
        sendable = set(endpoint.send_data_classes)
        for dc_id in sendable:
            dc = spec.data_class(dc_id)
            if dc is None:
                err("unknown_data_class", f"endpoint '{endpoint_id}' may send "
                    f"unknown data class '{dc_id}'", endpoint_id)
            elif (endpoint.trust is not EndpointTrust.INTERNAL
                  and dc.scope is not SharingScope.PUBLIC):
                err("endpoint_exfiltration", f"endpoint '{endpoint_id}' is "
                    f"{endpoint.trust.value} but may send non-public data class "
                    f"'{dc_id}'", endpoint_id)
        if (endpoint.trust is not EndpointTrust.INTERNAL
                and not endpoint.treat_output_as_data):
            err("endpoint_trusts_output", f"endpoint '{endpoint_id}' is "
                f"{endpoint.trust.value}; its answers must be treated as data, "
                "never as instructions", endpoint_id)
        if endpoint.trust is EndpointTrust.EXTERNAL and not endpoint.requires_approval:
            warn("external_endpoint_ungated", f"agent '{agent.id}' may call "
                 f"external endpoint '{endpoint_id}' without approval",
                 agent.id, strict=True)


def _workspace_and_contracts(ctx: ValidationContext, agent: Any) -> None:
    """Guardrails, workspace, contracts per agent."""
    spec, err = ctx.spec, ctx.err
    for guardrail_id in agent.guardrails:
        if spec.guardrail(guardrail_id) is None:
            err("unknown_guardrail", f"agent '{agent.id}' references unknown "
                f"guardrail '{guardrail_id}'", agent.id)
    if agent.artifact_store and spec.artifact_store(agent.artifact_store) is None:
        err("unknown_artifact_store", f"agent '{agent.id}' uses unknown artifact "
            f"store '{agent.artifact_store}'", agent.id)
    if agent.output_contract and spec.output_contract(agent.output_contract) is None:
        err("unknown_output_contract", f"agent '{agent.id}' references unknown "
            f"output contract '{agent.output_contract}'", agent.id)
    for sub in agent.subagents:
        if sub.output_contract and spec.output_contract(sub.output_contract) is None:
            err("unknown_output_contract", f"sub-agent '{sub.id}' references "
                f"unknown output contract '{sub.output_contract}'", agent.id)
    if agent.context and agent.context.offload_to and spec.artifact_store(
        agent.context.offload_to
    ) is None:
        err("unknown_artifact_store", f"agent '{agent.id}' offloads to unknown "
            f"store '{agent.context.offload_to}'", agent.id)


def _memory(ctx: ValidationContext, agent: Any) -> None:
    """Memory (ADR-0028)."""
    if agent.memory:
        for namespace_id in agent.memory.namespaces:
            if ctx.spec.namespace(namespace_id) is None:
                ctx.err("unknown_memory_namespace", f"agent '{agent.id}' uses "
                        f"unknown memory namespace '{namespace_id}'", agent.id)


@rule(SECTION, {
    "unknown_role", "wrong_role_kind", "unknown_capability", "unknown_workflow",
    "unknown_peer", "unknown_environment", "duplicate_environment",
    "environment_widened", "capability_without_a_sandbox", "sandbox_without_work",
    "agent_without_human", "agent_without_owner", "multiple_owners",
    "duplicate_pairing", "unknown_human_channel", "approvals_without_approver",
    "action_without_approver", "unknown_skill", "skill_without_capability",
    "unknown_plugin", "plugin_without_capability", "unknown_tool",
    "tool_without_capability", "tool_without_endpoint", "tool_without_workflow",
    "tool_without_subagent", "duplicate_subagent", "subagent_widens_access",
    "subagent_widens_tools", "subagent_widens_knowledge",
    "subagent_changes_environment", "subagent_exceeds_run_budget",
    "subagent_without_return", "unknown_endpoint", "unknown_data_class",
    "endpoint_exfiltration", "endpoint_trusts_output",
    "external_endpoint_ungated", "unknown_guardrail", "unknown_artifact_store",
    "unknown_output_contract", "unknown_memory_namespace",
})
def agent_references(ctx: ValidationContext) -> None:
    for agent in ctx.agents:
        _roles_and_names(ctx, agent)
        agent_environments = _sandboxes(ctx, agent)
        _human_pairing(ctx, agent)
        # Capabilities reach an agent through its roles as well as directly,
        # so every containment check below uses the effective set.
        held_caps = ctx.spec.effective_capabilities(agent.id)
        _skills_plugins_tools(ctx, agent, held_caps)
        _subagents(ctx, agent, held_caps, agent_environments)
        _endpoints(ctx, agent)
        _workspace_and_contracts(ctx, agent)
        _memory(ctx, agent)


@rule(SECTION, {"unknown_role", "wrong_role_kind"})
def team_roles_resolve(ctx: ValidationContext) -> None:
    for team in ctx.teams:
        for assignment in team.roles:
            role = ctx.spec.role(assignment.role)
            if role is None:
                ctx.err("unknown_role", f"team '{team.id}' references unknown role "
                        f"'{assignment.role}'", team.id)
            elif role.kind != "team":
                ctx.err("wrong_role_kind", f"team '{team.id}' is assigned agent role "
                        f"'{role.id}'", team.id)


@rule(SECTION, {"unknown_data_class"})
def capability_data_classes_resolve(ctx: ValidationContext) -> None:
    for cap in ctx.spec.capabilities:
        for dc_id in cap.data_classes:
            if ctx.spec.data_class(dc_id) is None:
                ctx.err("unknown_data_class", f"capability '{cap.id}' references "
                        f"unknown data class '{dc_id}'", cap.id)


@rule(SECTION, {"unknown_data_class", "placement_violation", "egress_on_isolated_env"})
def environments_mount_what_they_may(ctx: ValidationContext) -> None:
    spec, err = ctx.spec, ctx.err
    for env in spec.environments:
        for dc_id in env.mounts:
            dc = spec.data_class(dc_id)
            if dc is None:
                err("unknown_data_class", f"environment '{env.id}' mounts unknown "
                    f"data class '{dc_id}'", env.id)
            elif dc.allowed_environments and env.id not in dc.allowed_environments:
                err(
                    "placement_violation",
                    f"data class '{dc_id}' may not be mounted in environment "
                    f"'{env.id}'",
                    env.id,
                )
        if env.network is NetworkPosture.NONE and env.egress_allowlist:
            err("egress_on_isolated_env", f"environment '{env.id}' has no network but "
                "declares an egress allowlist", env.id)


@rule(SECTION, {"protected_without_groups"})
def protected_classes_name_groups(ctx: ValidationContext) -> None:
    for dc in ctx.spec.data_classes:
        if dc.scope is SharingScope.PROTECTED and not dc.groups:
            ctx.err("protected_without_groups", f"data class '{dc.id}' is protected "
                    "but names no groups", dc.id)


# -- system-level skill/plugin/tool references ---------------------------------


@rule("system-level skill/plugin/tool references",
      {"unknown_skill", "unknown_tool", "tool_wraps_unknown"})
def system_level_references(ctx: ValidationContext) -> None:
    spec, err = ctx.spec, ctx.err
    for plugin in spec.plugins:
        for skill_id in plugin.provides_skills:
            if spec.skill(skill_id) is None:
                err("unknown_skill", f"plugin '{plugin.id}' provides unknown skill "
                    f"'{skill_id}'", plugin.id)
        for tool_id in plugin.provides_tools:
            if spec.tool(tool_id) is None:
                err("unknown_tool", f"plugin '{plugin.id}' provides unknown tool "
                    f"'{tool_id}'", plugin.id)
    for tool in spec.tools:
        if tool.wraps_kind == "capability" and spec.capability(tool.wraps) is None:
            err("tool_wraps_unknown", f"tool '{tool.id}' wraps unknown capability "
                f"'{tool.wraps}'", tool.id)
        if tool.wraps_kind == "endpoint" and spec.endpoint(tool.wraps) is None:
            err("tool_wraps_unknown", f"tool '{tool.id}' wraps unknown endpoint "
                f"'{tool.wraps}'", tool.id)
