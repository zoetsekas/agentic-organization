"""Load a compiled system (IR) into the runtime.

This is what makes the runtime *a target consumer* rather than a second source
of truth (ADR-0003): teams become org units, `AgentIR` becomes the runtime's
`Agent`, resolved permissions become the harness's grants, and the composed
system prompt comes straight from the IR.
"""
from __future__ import annotations

from typing import Any, Optional

from ..compiler.ir import AgentIR, SystemIR
from ..models import (
    Agent,
    AgentKind,
    ChannelKind,
    DataGrant,
    Harness,
    HumanCounterpart,
    MCPServerRef,
    ModelSpec,
    OrgUnit,
    RelationalGrant,
    Runtime,
    SandboxSpec,
    SandboxTemplate,
    ToolBinding,
    Visibility,
    WorkflowRef,
)
from ..store import SANDBOX_TEMPLATES as TEMPLATE_COLLECTION
from ..store import WORKFLOWS

_ADAPTERS = {
    "langchain_deepagents": Runtime.DEEPAGENTS,
    "openai_agents_sdk": Runtime.OPENAI_AGENTS,
    "langgraph_native": Runtime.LANGGRAPH,
    "echo": Runtime.ECHO,
}
_TIER_RESOURCES = {
    "minimal": ("0.25", "256Mi", "1Gi"),
    "small": ("1", "2Gi", "10Gi"),
    "medium": ("2", "8Gi", "20Gi"),
    "large": ("4", "16Gi", "50Gi"),
    "accelerated": ("8", "64Gi", "200Gi"),
}
_NETWORK = {
    "none": "none",
    "allowlist": "egress_allowlist",
    "internal": "internal",
    "open": "full",
}
_PERSISTENCE = {
    "ephemeral": "ephemeral",
    "session": "session_persistent",
    "agent": "agent_persistent",
}


def _visibility(scope: str) -> Visibility:
    return {
        "private": Visibility.PRIVATE,
        "protected": Visibility.PROTECTED,
        "public": Visibility.PUBLIC,
    }[scope]


def _resolve_sandbox_provider(env: dict[str, Any], tenant_id: Optional[str],
                              requested: str = "container") -> dict[str, Any]:
    """Ask the provider seam what really isolates this environment.

    Resolution never raises: an unavailable provider degrades to the container
    floor and says so, and that degradation has to survive into the runtime
    record or an operator will read the requested provider as the one in force
    (ADR-0054).
    """
    from ..sandboxes import EnvironmentFacts, detect_context, resolve_provider

    facts = EnvironmentFacts.from_environment_class(env, tenant_id=tenant_id)
    resolution = resolve_provider(requested, facts, detect_context(target="local"))
    return {
        "provider": resolution.provider_name,
        "boundary_summary": resolution.boundary.summary,
        "boundary_verified": resolution.boundary.verified,
        "degraded_from": (resolution.degradation.requested
                          if resolution.degraded and resolution.degradation else None),
        "degradation_reason": (resolution.degradation.reason
                               if resolution.degraded and resolution.degradation else ""),
    }


def _sandbox_template(env: dict[str, Any], system: str, agent_id: str = "",
                      tenant_id: Optional[str] = None) -> SandboxTemplate:
    """Materialize one environment as a sandbox template.

    The IR narrows an environment class per agent (ADR-0009), so each agent gets
    its own template carrying the *resolved* limits rather than the class's.
    Class-level templates are also registered, for the catalog and the designer.
    """
    cpu, memory, disk = _TIER_RESOURCES.get(env.get("tier", "minimal"),
                                            _TIER_RESOURCES["minimal"])
    return SandboxTemplate(
        **_resolve_sandbox_provider(env, tenant_id),
        id=f"sbx_{system}_{env['id']}" + (f"_{agent_id}" if agent_id else ""),
        name=env["id"],
        description=env.get("description", ""),
        toolchain=[t for t in env.get("toolchains", []) if t != "none"],
        cpu=cpu,
        memory=memory,
        disk=disk,
        timeout_s=env.get("timeout_seconds", 300),
        network=_NETWORK.get(env.get("network", "none"), "none"),
        egress_allowlist=env.get("egress_allowlist", []),
        mounts=[_visibility(m) for m in env.get("mount_scopes", [])],
        filesystem=_PERSISTENCE.get(env.get("persistence", "ephemeral"), "ephemeral"),
        secret_refs=env.get("secret_refs", []),
    )


def _harness(agent: dict[str, Any], ir: dict[str, Any]) -> Harness:
    binding = ir.get("binding", {})
    cap_bindings = {c["capability"]: c for c in binding.get("capabilities", [])}

    mcp_servers: list[MCPServerRef] = []
    relational: list[RelationalGrant] = []
    tools: list[ToolBinding] = []

    for cap in agent.get("capabilities", []):
        bound = cap_bindings.get(cap["id"])
        constraints = cap.get("constraints", {})
        if bound and bound.get("engine"):
            relational.append(
                RelationalGrant(
                    connection_name=bound["server_name"],
                    engine=bound["engine"],
                    dsn_secret_ref=bound.get("dsn_secret_ref") or "",
                    allowed_statements=constraints.get("allowed_operations") or ["select"],
                    row_limit=constraints.get("max_rows") or 1000,
                    masked_columns=constraints.get("masked_fields") or [],
                    tables=constraints.get("resource_scope") or [],
                )
            )
        elif bound:
            mcp_servers.append(
                MCPServerRef(
                    name=bound["server_name"],
                    transport=bound.get("transport", "stdio"),
                    command=bound.get("command"),
                    args=bound.get("args", []),
                    url=bound.get("url"),
                    read_only=cap.get("action") in ("read", "query"),
                )
            )
        tools.append(
            ToolBinding(
                name=cap["id"],
                description=cap.get("description", ""),
                source="mcp",
                ref=bound["server_name"] if bound else cap["id"],
                requires_approval=bool(constraints.get("requires_approval")),
                decision=cap.get("decision"),
            )
        )

    grants = [DataGrant(visibility=Visibility.PRIVATE, can_read=True, can_write=True)]
    for access in agent.get("data_access", []):
        grants.append(
            DataGrant(
                visibility=_visibility(access["scope"]),
                groups=access.get("groups", []),
                can_read=access.get("read", False),
                can_write=access.get("write", False),
            )
        )

    model = agent.get("model", {})
    return Harness(
        runtime=_ADAPTERS.get(agent.get("runtime_adapter", "echo"), Runtime.ECHO),
        model=ModelSpec(
            provider=model.get("provider", "anthropic"),
            model=model.get("model", "claude-opus-5"),
            subagent_model=model.get("subagent_model"),
            temperature=model.get("temperature", 0.2),
            max_tokens=model.get("max_tokens", 8192),
        ),
        system_prompt=agent.get("system_prompt", ""),
        mcp_servers=mcp_servers,
        relational_grants=relational,
        tools=tools,
        data_grants=grants,
        max_subagent_depth=agent.get("max_delegation_depth", 3),
        interrupt_on=agent.get("requires_approval_for", []),
    )


_PROVIDER_CHANNELS = {
    "slack": ChannelKind.SLACK,
    "msteams": ChannelKind.TEAMS,
    "teams": ChannelKind.TEAMS,
    "smtp": ChannelKind.EMAIL,
    "mail": ChannelKind.EMAIL,
    "webhook": ChannelKind.WEBHOOK,
    "internal": ChannelKind.INTERNAL_BUS,
}


def load_channels(platform, data: dict[str, Any]) -> list[str]:
    """Bind each human-facing channel to a transport for its provider."""
    from ..messaging import channel_transport

    bound: list[str] = []
    for channel in data.get("channels", []):
        provider = channel.get("provider", "internal")
        kind = _PROVIDER_CHANNELS.get(provider, ChannelKind.INTERNAL_BUS)
        platform.bus.register_transport(kind, channel_transport(provider))
        bound.append(channel["id"])
    return bound


def load_system(platform, ir: SystemIR | dict[str, Any]) -> dict[str, Any]:
    """Materialize a compiled system into a running platform instance."""
    data = ir.model_dump(mode="json") if isinstance(ir, SystemIR) else dict(ir)
    system = data["name"].lower().replace(" ", "_")
    store = platform.store
    tenant_id = (data.get("tenant") or {}).get("id")

    # Environment classes → sandbox templates (the reviewable catalog), and the
    # per-agent narrowed instances the runtime actually uses.
    scopes = {d["id"]: d["scope"] for d in data.get("data_classes", [])}
    for env in data.get("environments", []):
        env = {**env, "mount_scopes": [scopes[m] for m in env.get("mounts", [])
                                       if m in scopes]}
        store.put(TEMPLATE_COLLECTION, _sandbox_template(env, system,
                                                        tenant_id=tenant_id))
    for agent in data.get("agents", []):
        # One narrowed template per sandbox the agent runs in (ADR-0082).
        for env in agent.get("environments", []):
            env = {**env, "mount_scopes": [scopes[m] for m in env.get("mounts", [])
                                           if m in scopes]}
            store.put(TEMPLATE_COLLECTION,
                      _sandbox_template(env, system, agent["id"],
                                        tenant_id=tenant_id))

    # Workflows.
    for wf in data.get("workflows", []):
        store.put(
            WORKFLOWS,
            WorkflowRef(
                id=wf["id"],
                name=wf.get("name", wf["id"]),
                description=wf.get("description", ""),
                graph=wf.get("graph"),
                interrupt_before=wf.get("interrupt_before", []),
            ),
        )

    # Separations of duties, which the runtime needs in order to know what a
    # standing-in may *not* confer (ADR-0094 rule 4). Loaded before the agents
    # so nothing can stand in before the rules are in place.
    platform.org.separations = [
        dict(rule) for rule in data.get("separations", [])
    ]

    # Teams → org units.
    for team in data.get("teams", []):
        platform.org.add_unit(
            OrgUnit(
                id=team["id"],
                name=team["name"],
                parent_id=team.get("parent_id"),
                kind="company" if team.get("parent_id") is None else "team",
                groups=team.get("groups", []),
                metadata={"mandate": team.get("mandate", [])},
            )
        )

    # Agents in reporting order, so a manager exists before its reports.
    by_id = {a["id"]: a for a in data.get("agents", [])}

    def depth(agent: dict[str, Any], seen: Optional[set[str]] = None) -> int:
        seen = seen or set()
        manager = agent.get("reports_to")
        if not manager or manager in seen or manager not in by_id:
            return 0
        return 1 + depth(by_id[manager], seen | {agent["id"]})

    agents = sorted(data.get("agents", []), key=lambda a: (depth(a), a["id"]))
    created: list[str] = []
    for agent in agents:
        prompt_source = AgentIR.model_validate(agent) if "id" in agent else None
        system_prompt = prompt_source.system_prompt() if prompt_source else ""
        enriched = {**agent, "system_prompt": system_prompt}
        humans = agent.get("humans") or []
        owner = next(
            (h for h in humans if "owner" in (h.get("roles") or [])),
            humans[0] if humans else None,
        )
        platform.org.add_agent(
            Agent(
                id=agent["id"],
                name=agent["name"],
                title=", ".join(agent.get("role_ids", [])) or agent["name"],
                kind=_kind(agent),
                description=agent.get("description", ""),
                org_unit_id=agent.get("team_id"),
                manager_agent_id=agent.get("reports_to"),
                # Standing reach only: mission peers arrive as grants below,
                # so a finished mission stops conferring reach on its own.
                peer_agent_ids=[
                    p for p in agent.get("standing_delegates_to",
                                         agent.get("delegates_to", []))
                    if p not in (agent.get("reports_to"),)
                ],
                mission_grants=list(agent.get("mission_grants", [])),
                # Empty means the manager, who already holds this mandate
                # (ADR-0094 rule 1), so it is left unset rather than filled in.
                successor_agent_id=agent.get("successor_agent_id") or None,
                mandate=list((agent.get("mandate") or {}).get("decisions", [])),
                mandate_conditions=list(
                    (agent.get("mandate") or {}).get("conditions", [])
                ),
                human=_counterpart(owner, agent) if owner else None,
                humans=[_counterpart(h, agent) for h in humans],
                subagents=agent.get("subagents", []),
                memory=agent.get("memory", {}),
                guardrails=agent.get("guardrails", []),
                artifact_store=agent.get("artifact_store") or {},
                context_policy=agent.get("context", {}),
                output_contract=agent.get("output_contract") or {},
                harness=_harness(enriched, data),
                workflow_ids=agent.get("workflows", []),
                sandboxes=[
                    SandboxSpec(
                        template_id=f"sbx_{system}_{env['id']}_{agent['id']}"
                    )
                    for env in agent.get("environments", [])
                ],
                channels=[ChannelKind(_channel(c)) for c in agent.get("channels", [])],
                groups=agent.get("groups", []),
                tags=[agent.get("team_id", "")],
            )
        )
        created.append(agent["id"])

    # An agent the design no longer contains has left (ADR-0098). Removal was
    # the one change that did not propagate: every other edit — a narrowed
    # mandate, a dropped permission, a changed reporting line — landed, while a
    # deleted agent kept its mandate and its delegation reach in the running
    # system. Silently ignoring it is the worst of the three possible
    # behaviours, because the reviewer who approved the removal cannot tell it
    # did not happen.
    present = set(created)
    departed = []
    for existing in platform.org.agents():
        if existing.id in present or not existing.is_active:
            continue
        if existing.kind is AgentKind.SUBAGENT:
            continue      # ephemeral, created by a run rather than the design
        departed.append(platform.org.decommission(
            existing.id,
            reason="the design no longer contains this agent",
        ))

    channels = load_channels(platform, data)
    return {
        "agents": created,
        "departed": [d["agent_id"] for d in departed],
        "departures": departed,
        "teams": [t["id"] for t in data.get("teams", [])],
        "channels": channels,
        "triggers": [t["id"] for t in data.get("triggers", [])],
    }


def _counterpart(human: dict[str, Any], agent: dict[str, Any]) -> HumanCounterpart:
    """One paired person, in the runtime's shape."""
    return HumanCounterpart(
        user_id=human["contact"],
        display_name=human["name"],
        email=human["contact"],
        role_title=human.get("role_title", "")
        or "/".join(human.get("roles", []) or []),
        approval_required_for=(
            agent.get("requires_approval_for", [])
            if "approver" in (human.get("roles") or [])
            or "owner" in (human.get("roles") or [])
            else []
        ),
        notify_channels=[
            ChannelKind(_channel(c)) for c in human.get("notify_on", ["mail"])
        ],
    )


def _kind(agent: dict[str, Any]) -> AgentKind:
    if agent.get("shared_service"):
        return AgentKind.SERVICE
    if agent.get("leader_of") and agent.get("reports_to") is None:
        return AgentKind.EXECUTIVE
    if agent.get("leader_of"):
        return AgentKind.MANAGER
    return AgentKind.INDIVIDUAL


def _channel(value: str) -> str:
    return {
        "direct": ChannelKind.DIRECT_TOOL.value,
        "async_bus": ChannelKind.INTERNAL_BUS.value,
        "team_chat": ChannelKind.SLACK.value,
        "mail": ChannelKind.EMAIL.value,
        "webhook": ChannelKind.WEBHOOK.value,
    }.get(value, ChannelKind.INTERNAL_BUS.value)
