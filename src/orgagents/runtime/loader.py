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


def _sandbox_template(env: dict[str, Any], system: str, agent_id: str = "") -> SandboxTemplate:
    """Materialize one environment as a sandbox template.

    The IR narrows an environment class per agent (ADR-0009), so each agent gets
    its own template carrying the *resolved* limits rather than the class's.
    Class-level templates are also registered, for the catalog and the designer.
    """
    cpu, memory, disk = _TIER_RESOURCES.get(env.get("tier", "minimal"),
                                            _TIER_RESOURCES["minimal"])
    return SandboxTemplate(
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

    # Environment classes → sandbox templates (the reviewable catalog), and the
    # per-agent narrowed instances the runtime actually uses.
    scopes = {d["id"]: d["scope"] for d in data.get("data_classes", [])}
    for env in data.get("environments", []):
        env = {**env, "mount_scopes": [scopes[m] for m in env.get("mounts", [])
                                       if m in scopes]}
        store.put(TEMPLATE_COLLECTION, _sandbox_template(env, system))
    for agent in data.get("agents", []):
        env = agent.get("environment")
        if env:
            env = {**env, "mount_scopes": [scopes[m] for m in env.get("mounts", [])
                                           if m in scopes]}
            store.put(TEMPLATE_COLLECTION, _sandbox_template(env, system, agent["id"]))

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
        human = agent.get("human")
        platform.org.add_agent(
            Agent(
                id=agent["id"],
                name=agent["name"],
                title=", ".join(agent.get("role_ids", [])) or agent["name"],
                kind=_kind(agent),
                description=agent.get("description", ""),
                org_unit_id=agent.get("team_id"),
                manager_agent_id=agent.get("reports_to"),
                peer_agent_ids=[
                    p for p in agent.get("delegates_to", [])
                    if p not in (agent.get("reports_to"),)
                ],
                human=HumanCounterpart(
                    user_id=human["contact"],
                    display_name=human["name"],
                    email=human["contact"],
                    role_title=human.get("role_title", ""),
                    approval_required_for=agent.get("requires_approval_for", []),
                    notify_channels=[
                        ChannelKind(_channel(c)) for c in human.get("notify_on", ["mail"])
                    ],
                )
                if human
                else None,
                harness=_harness(enriched, data),
                workflow_ids=agent.get("workflows", []),
                sandbox=SandboxSpec(
                    template_id=f"sbx_{system}_{agent['environment']['id']}_{agent['id']}"
                )
                if agent.get("environment")
                else None,
                channels=[ChannelKind(_channel(c)) for c in agent.get("channels", [])],
                groups=agent.get("groups", []),
                tags=[agent.get("team_id", "")],
            )
        )
        created.append(agent["id"])

    channels = load_channels(platform, data)
    return {
        "agents": created,
        "teams": [t["id"] for t in data.get("teams", [])],
        "channels": channels,
        "triggers": [t["id"] for t in data.get("triggers", [])],
    }


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
