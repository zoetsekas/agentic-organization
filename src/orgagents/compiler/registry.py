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
        human = agent.human.name if agent.human else "**unowned**"
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

    unowned = [a.id for a in ir.agents if a.human is None]
    unbudgeted = [a.id for a in ir.agents if not a.budget_usd]
    untriggered = [a.id for a in ir.agents if not a.triggers]

    gates = "\n".join(
        f"| {g.to_stage.value} | {', '.join(r.value for r in g.requires)} | "
        f"{g.min_pass_rate:.0%} | {', '.join(g.approvers) or '—'} |"
        for g in ir.lifecycle.gates
    ) or "| — | — | — | — |"

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

## What each agent may reach

| Agent | Capability | Action | Resource class | Data classes | Approval |
|---|---|---|---|---|---|
{chr(10).join(capability_rows) or "| — |"}

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
- **Compliance frameworks:** {", ".join(ir.compliance.frameworks) or "none declared"}
- **Data residency:** {", ".join(ir.compliance.data_residency) or "unrestricted"}
- **Redacted from traces:** {", ".join(ir.compliance.redact_data_classes) or "nothing"}
"""
