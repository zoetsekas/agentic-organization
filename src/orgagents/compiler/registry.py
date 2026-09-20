"""The agent registry — a generated inventory of the whole fleet (ADR-0022).

Inspired by the control-plane pattern: a single source of truth naming every
agent, who owns it, what identity it runs as, what it may reach, what wakes it
up and where it talks to people. The difference is that ours is a generated
artifact the customer owns, not a hosted console.

It is the document an auditor asks for, so it is generated for every target.
"""
from __future__ import annotations

from .ir import SystemIR


def registry_report(ir: SystemIR) -> str:
    rows = []
    for agent in sorted(ir.agents, key=lambda a: a.id):
        owner = agent.owner.name if agent.owner else "**unowned**"
        others = len(agent.humans) - (1 if agent.owner else 0)
        human = owner + (f" +{others}" if others > 0 else "")
        triggers = ", ".join(agent.triggers) or "interactive only"
        channels = ", ".join(agent.human_channels) or "—"
        budget = (
            f"${agent.budget_usd:,.0f}/{agent.budget_period} ({agent.on_budget_breach})"
            if agent.budget_usd
            else "**unbounded**"
        )
        rows.append(
            f"| `{agent.id}` | {' / '.join(agent.team_path)} | {human} | "
            f"`{agent.identity.id if agent.identity else '—'}` | "
            f"{len(agent.permissions)} | "
            f"{agent.environment.id if agent.environment else '—'} | "
            f"{triggers} | {channels} | {budget} |"
        )

    capability_rows = []
    for agent in sorted(ir.agents, key=lambda a: a.id):
        for cap in agent.capabilities:
            approval = "yes" if cap.constraints.requires_approval else "no"
            capability_rows.append(
                f"| `{agent.id}` | `{cap.id}` | {cap.action.value} | "
                f"{cap.resource_class or '—'} | "
                f"{', '.join(cap.data_classes) or '—'} | {approval} |"
            )

    trigger_rows = [
        f"| `{t.id}` | {t.kind.value} | {t.schedule} | `{t.agent_id}` | "
        f"{t.overlap} / {t.catch_up} | {t.max_runtime_seconds}s | "
        f"{', '.join(t.deliver_to) or '—'} | {t.notify_on_failure or '—'} |"
        for t in ir.triggers
    ]

    channel_rows = [
        f"| `{c.id}` | {c.provider} | {c.address or '—'} | "
        f"{', '.join(c.purposes) or '—'} | "
        f"{str(c.response_sla_minutes) + ' min' if c.response_sla_minutes else '—'} | "
        f"{len(c.escalation)} step(s) | {', '.join(c.forbid_data_classes) or '—'} |"
        for c in ir.channels if c.human_facing
    ]

    flow_rows = [
        f"| `{f.source}` | `{f.target}` | {f.kind.value} | {f.description} |"
        for f in ir.flows
    ]

    knowledge_rows = [
        f"| `{k.id}` | {k.kind} | {k.provider} | "
        f"{', '.join(k.data_classes) or '—'} | {'yes' if k.require_citation else 'no'} |"
        for k in ir.knowledge
    ]

    unowned = [a.id for a in ir.agents if a.owner is None]
    unbudgeted = [a.id for a in ir.agents if not a.budget_usd]
    untriggered = [a.id for a in ir.agents if not a.triggers]

    gates = "\n".join(
        f"| {g.to_stage.value} | {', '.join(r.value for r in g.requires)} | "
        f"{g.min_pass_rate:.0%} | {', '.join(g.approvers) or '—'} |"
        for g in ir.lifecycle.gates
    ) or "| — | — | — | — |"

    pairing_rows = [
        f"| `{agent.id}` | {human.name} | {human.role_title or '—'} | "
        f"{', '.join(r.value for r in human.roles)} | "
        f"{', '.join(human.approves) or '—'} | {human.channel or '—'} |"
        for agent in sorted(ir.agents, key=lambda a: a.id)
        for human in agent.humans
    ]

    people: dict[str, list[str]] = {}
    for agent in ir.agents:
        for human in agent.humans:
            people.setdefault(f"{human.name} ({human.contact})", []).append(
                f"{agent.id}:{'/'.join(r.value for r in human.roles)}"
            )
    person_rows = [
        f"| {person} | {len(pairs)} | {', '.join(sorted(pairs))} |"
        for person, pairs in sorted(people.items())
    ]

    subagent_rows = [
        f"| `{agent.id}` | `{sub.tool_name}` | {sub.kind} | {sub.purpose} | "
        f"{', '.join(sub.capabilities) or 'none'} | {sub.returns or '—'} | "
        f"{sub.max_runtime_seconds}s |"
        for agent in sorted(ir.agents, key=lambda a: a.id)
        for sub in agent.subagents
    ]

    tool_rows = [
        f"| `{agent.id}` | `{tool.id}` | {tool.wraps_kind} `{tool.wraps}` | "
        f"{tool.source} | {'yes' if tool.requires_approval else 'no'} |"
        for agent in sorted(ir.agents, key=lambda a: a.id)
        for tool in agent.tools
    ]

    skill_rows = [
        f"| `{agent.id}` | {', '.join(s.id for s in agent.skills) or '—'} | "
        f"{', '.join(agent.plugins) or '—'} |"
        for agent in sorted(ir.agents, key=lambda a: a.id)
        if agent.skills or agent.plugins
    ]

    endpoint_rows = [
        f"| `{e.id}` | {e.trust.value} | {', '.join(e.provides) or '—'} | "
        f"{', '.join(e.send_data_classes) or 'nothing'} | "
        f"{'yes' if e.requires_approval else '**no**'} |"
        for e in {e.id: e for a in ir.agents for e in a.endpoints}.values()
    ]

    memory_rows = [
        f"| `{agent.id}` | {'yes' if agent.memory.session_enabled else 'no'} | "
        f"{'yes' if agent.memory.long_term_enabled else 'no'} | "
        f"{', '.join(n.id for n in agent.memory.namespaces) or '—'} | "
        f"{agent.memory.recall} | "
        f"{'yes' if agent.memory.may_promote else 'no'} |"
        for agent in sorted(ir.agents, key=lambda a: a.id)
    ]

    namespace_rows = [
        f"| `{n.id}` | {n.scope.value} | {', '.join(n.groups) or '—'} | "
        f"{', '.join(n.data_classes) or '—'} | {n.retention_days or 'forever'} |"
        for n in ir.memory.namespaces
    ]

    mission_rows = [
        f"| `{m.id}` | {m.objective.strip().splitlines()[0][:70] if m.objective else '—'} | "
        f"`{m.leader}` | {', '.join(m.members)} | {m.status} | "
        f"{m.starts_on or '—'} → {m.ends_on or '**no end**'} | "
        f"{len(m.deliverables)} |"
        for m in ir.missions
    ]

    model_rows = [
        f"| `{a.id}` | {a.model_approval.model if a.model_approval else a.model.get('model', '—')} | "
        f"{', '.join(c.value for c in a.model_policy.classes) or '—'} | "
        f"{(a.model_policy.max_cost_per_million_tokens or '—')} | "
        f"{', '.join(a.model_policy.require_regions) or 'any'} | "
        f"{'yes' if (a.model_approval and a.model_approval.approved) else ('**no**' if a.model_approval else 'not checked')} | "
        f"{(a.model_approval.subagent_model or '—') if a.model_approval else '—'} | "
        f"{a.model_approval.fallback_reason or a.model_approval.subagent_fallback_reason or '—' if a.model_approval else '—'} |"
        for a in sorted(ir.agents, key=lambda x: x.id)
    ]

    # A fallback is a model change nobody asked for, so it is flagged, not
    # buried in a cell (ADR-0040 v1.1.0).
    fell_back = [
        a.id for a in ir.agents
        if a.model_approval and (a.model_approval.fallback_applied
                                 or a.model_approval.subagent_fallback_applied)
    ]

    unapproved_models = [
        a.id for a in ir.agents if a.model_approval
        and (not a.model_approval.approved or not a.model_approval.subagent_approved)
    ]
    open_missions = [
        m.id for m in ir.missions if m.status in ("proposed", "active") and not m.ends_on
    ]

    single_human = [
        a.id for a in ir.agents if len(a.humans) == 1
    ]
    no_memory = [a.id for a in ir.agents if not a.memory.long_term_enabled]

    return f"""# Agent registry — {ir.name}

Generated from the system spec (spec_version {ir.spec_version}) for target
`{ir.target}`. This is the fleet inventory: every agent, its owner, its identity,
what it may reach, what wakes it and where it talks to people.

**Lifecycle stage:** {ir.lifecycle.stage.value} ·
**Owner:** {ir.lifecycle.owner or "**unassigned**"} ·
**Permission review:** every {ir.compliance.permission_review_days} days ·
**Retire after idle:** {ir.lifecycle.retire_after_idle_days or "never"} days

## Agents

| Agent | Team | Human owner | Identity | Perms | Environment | Triggers | Channels | Budget |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows) or "| — |"}

## Who each agent answers to

| Agent | Person | Title | Roles | Approves | Channel |
|---|---|---|---|---|---|
{chr(10).join(pairing_rows) or "| — |"}

## People, and what they are on the hook for

| Person | Agents | Pairings |
|---|---|---|
{chr(10).join(person_rows) or "| — |"}

## What each agent may reach

| Agent | Capability | Action | Resource class | Data classes | Approval |
|---|---|---|---|---|---|
{chr(10).join(capability_rows) or "| — |"}

## Sub-agents (called as tools)

| Parent | Tool | Kind | Purpose | Capabilities | Returns | Budget |
|---|---|---|---|---|---|---|
{chr(10).join(subagent_rows) or "| — |"}

## Tools and what they wrap

| Agent | Tool | Wraps | Source | Approval |
|---|---|---|---|---|
{chr(10).join(tool_rows) or "| — |"}

## Skills and plugins

| Agent | Skills | Plugins |
|---|---|---|
{chr(10).join(skill_rows) or "| — |"}

## External agent endpoints

| Endpoint | Trust | Provides | May be sent | Approval |
|---|---|---|---|---|
{chr(10).join(endpoint_rows) or "| — |"}

## Memory

| Agent | Session | Long term | Namespaces | Recall | May promote |
|---|---|---|---|---|---|
{chr(10).join(memory_rows) or "| — |"}

| Namespace | Scope | Groups | Data classes | Retention (days) |
|---|---|---|---|---|
{chr(10).join(namespace_rows) or "| — |"}

## Missions (short-lived teams)

| Mission | Objective | Leader | Members | Status | Window | Deliverables |
|---|---|---|---|---|---|---|
{chr(10).join(mission_rows) or "| — |"}

## Models and model policy

| Agent | Bound model | Permitted classes | Cost ceiling | Regions | Approved | Sub-agent model | Fallback |
|---|---|---|---|---|---|---|---|
{chr(10).join(model_rows) or "| — |"}

## What wakes them

| Trigger | Kind | When | Agent | Overlap / catch-up | Max runtime | Delivers to | On failure |
|---|---|---|---|---|---|---|---|
{chr(10).join(trigger_rows) or "| — |"}

## Human contact surfaces

| Channel | Provider | Address | Purposes | SLA | Escalation | Forbidden data |
|---|---|---|---|---|---|---|
{chr(10).join(channel_rows) or "| — |"}

## Declared interaction flows

Beyond the hierarchy: who may consult, notify or escalate to whom.

| From | To | Kind | Why |
|---|---|---|---|
{chr(10).join(flow_rows) or "| — |"}

## Grounding sources

| Source | Kind | Provider | Data classes | Citation required |
|---|---|---|---|---|
{chr(10).join(knowledge_rows) or "| — |"}

## Promotion gates

| To stage | Requires | Min pass rate | Approvers |
|---|---|---|---|
{gates}

## Review flags

- **Agents without a human owner:** {", ".join(f"`{a}`" for a in unowned) or "none"}
- **Agents without a budget:** {", ".join(f"`{a}`" for a in unbudgeted) or "none"}
- **Agents that never run unattended:** {", ".join(f"`{a}`" for a in untriggered) or "none"}
- **Agents with a single paired human:** {", ".join(f"`{a}`" for a in single_human) or "none"}
- **Agents with no long-term memory:** {", ".join(f"`{a}`" for a in no_memory) or "none"}
- **Agents on an unapproved model:** {", ".join(f"`{a}`" for a in unapproved_models) or "none"}
- **Agents moved to a fallback model:** {", ".join(f"`{a}`" for a in fell_back) or "none"}
- **Missions with no end date:** {", ".join(f"`{m}`" for m in open_missions) or "none"}
- **Compliance frameworks:** {", ".join(ir.compliance.frameworks) or "none declared"}
- **Data residency:** {", ".join(ir.compliance.data_residency) or "unrestricted"}
- **Redacted from traces:** {", ".join(ir.compliance.redact_data_classes) or "nothing"}
"""
