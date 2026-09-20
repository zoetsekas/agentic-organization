"""Spec validation: structure, references and least privilege.

Findings are severity-tagged. `error` fails the build; `warning` is reported
and, for the rules ADR-0008 marks as mandatory in production, promoted to an
error when `metadata.environment == "production"`.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Literal, Optional

from .model import (
    ChannelPurpose,
    LifecycleStage,
    NetworkPosture,
    Permission,
    RoleAssignment,
    SharingScope,
    SystemSpec,
    Team,
    TriggerKind,
)

Severity = Literal["error", "warning"]


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str
    where: str = ""

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.severity}: {self.code}{loc}: {self.message}"


def _perm_keys(perms: list[Permission]) -> set[str]:
    return {p.key() for p in perms}


def _assignment_permissions(spec: SystemSpec, assignment: RoleAssignment) -> set[str]:
    role = spec.role(assignment.role)
    if role is None:
        return set()
    return _perm_keys(role.permissions) - set(assignment.withhold)


def team_permissions(spec: SystemSpec, team: Team) -> set[str]:
    """Permissions a team grants its members, including inherited ones."""
    granted: set[str] = set()
    for assignment in team.roles:
        granted |= _assignment_permissions(spec, assignment)
    parent = _parent_of(spec.organization, team.id)
    if parent is not None:
        granted |= team_permissions(spec, parent)
    return granted


def _parent_of(root: Team, team_id: str) -> Optional[Team]:
    for team in root.walk():
        if any(child.id == team_id for child in team.teams):
            return team
    return None


def validate_spec(spec: SystemSpec) -> list[Finding]:
    """Return every finding; an empty list means the spec is sound."""
    # Imported here: `scheduling` reads the spec model, so a module-level import
    # would close a cycle through this package's __init__.
    from ..scheduling import CadenceError, parse_cadence

    out: list[Finding] = []
    production = spec.metadata.environment == "production"

    def err(code: str, message: str, where: str = "") -> None:
        out.append(Finding("error", code, message, where))

    def warn(code: str, message: str, where: str = "", strict: bool = False) -> None:
        severity: Severity = "error" if (strict and production) else "warning"
        out.append(Finding(severity, code, message, where))

    teams = spec.teams()
    agents = spec.agents()
    agent_ids = [a.id for a in agents]
    team_ids = [t.id for t in teams]

    # -- uniqueness --------------------------------------------------------
    for label, ids in (
        ("agent", agent_ids),
        ("team", team_ids),
        ("role", [r.id for r in spec.roles]),
        ("capability", [c.id for c in spec.capabilities]),
        ("environment", [e.id for e in spec.environments]),
        ("data class", [d.id for d in spec.data_classes]),
        ("workflow", [w.id for w in spec.workflows]),
    ):
        seen: set[str] = set()
        for i in ids:
            if i in seen:
                err("duplicate_id", f"duplicate {label} id '{i}'")
            seen.add(i)

    # -- organization structure (ADR-0006) ---------------------------------
    for team in teams:
        member_ids = {m.id for m in team.members}
        if not team.leader:
            err("team_without_leader", f"team '{team.id}' has no leader", team.id)
        elif team.leader not in member_ids:
            err(
                "leader_not_member",
                f"leader '{team.leader}' is not a member of team '{team.id}'",
                team.id,
            )
        # A child team's leader participates in the parent *implicitly* through
        # leadership (ADR-0006 v1.1.0); listing it in the parent's members too
        # would give the agent two home teams.
        for child in team.teams:
            if child.leader and child.leader in member_ids:
                err(
                    "child_leader_double_listed",
                    f"leader '{child.leader}' of child team '{child.id}' is also "
                    f"listed as a member of parent team '{team.id}'; parent "
                    "membership is implicit",
                    child.id,
                )
        if not team.mandate:
            warn("team_without_mandate", f"team '{team.id}' declares no mandate", team.id)

    # An agent must have exactly one home team.
    homes: dict[str, list[str]] = {}
    for team in teams:
        for member in team.members:
            homes.setdefault(member.id, []).append(team.id)
    for agent_id, owners in homes.items():
        # A leader appears as leader of its own team and member of the parent;
        # membership is still recorded once, so >1 is a genuine error.
        if len(owners) > 1:
            err(
                "multiple_home_teams",
                f"agent '{agent_id}' is a member of {owners}; exactly one is allowed",
                agent_id,
            )

    # -- references --------------------------------------------------------
    for agent in agents:
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
            if peer not in agent_ids:
                err("unknown_peer", f"agent '{agent.id}' references unknown peer "
                    f"'{peer}'", agent.id)
        if agent.environment:
            env = spec.environment(agent.environment.environment)
            if env is None:
                err("unknown_environment", f"agent '{agent.id}' references unknown "
                    f"environment '{agent.environment.environment}'", agent.id)
            else:
                _check_narrowing(agent.id, env, agent.environment, err)
        if agent.human is None:
            warn("agent_without_human", f"agent '{agent.id}' has no human counterpart",
                 agent.id, strict=True)

    for team in teams:
        for assignment in team.roles:
            role = spec.role(assignment.role)
            if role is None:
                err("unknown_role", f"team '{team.id}' references unknown role "
                    f"'{assignment.role}'", team.id)
            elif role.kind != "team":
                err("wrong_role_kind", f"team '{team.id}' is assigned agent role "
                    f"'{role.id}'", team.id)

    for cap in spec.capabilities:
        for dc_id in cap.data_classes:
            if spec.data_class(dc_id) is None:
                err("unknown_data_class", f"capability '{cap.id}' references unknown "
                    f"data class '{dc_id}'", cap.id)

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

    for dc in spec.data_classes:
        if dc.scope is SharingScope.PROTECTED and not dc.groups:
            err("protected_without_groups", f"data class '{dc.id}' is protected but "
                "names no groups", dc.id)

    # -- least privilege (ADR-0008) ---------------------------------------
    for role in spec.roles:
        for perm in role.permissions:
            if perm.resource == "*":
                warn(
                    "wildcard_resource",
                    f"role '{role.id}' grants {perm.action.value} on every "
                    f"{perm.resource_kind.value}",
                    role.id,
                    strict=True,
                )
        text = " ".join(role.responsibilities).lower()
        for cap_id in role.capabilities:
            cap = spec.capability(cap_id)
            if not _responsibility_mentions(text, cap_id, cap):
                warn(
                    "capability_without_responsibility",
                    f"role '{role.id}' grants capability '{cap_id}' that no "
                    "responsibility mentions",
                    role.id,
                )

    # An agent's effective permissions may not exceed its team's grant plus its
    # own roles — inheritance narrows, never widens (ADR-0008).
    for team in teams:
        inherited = team_permissions(spec, team)
        for member in team.members:
            own: set[str] = set()
            for assignment in member.roles:
                own |= _assignment_permissions(spec, assignment)
            for assignment in member.roles:
                role = spec.role(assignment.role)
                if role and set(assignment.withhold) - _perm_keys(role.permissions):
                    warn(
                        "withhold_unknown_permission",
                        f"agent '{member.id}' withholds a permission role "
                        f"'{role.id}' does not grant",
                        member.id,
                    )
            escalated = {
                key
                for key in own
                if key.endswith(":administer") or key.startswith("administer:")
            }
            if escalated and not (escalated <= inherited):
                warn(
                    "possible_escalation",
                    f"agent '{member.id}' holds administrative permissions its team "
                    "does not",
                    member.id,
                    strict=True,
                )

    # -- triggers (ADR-0020) ----------------------------------------------
    channel_ids = {c.id for c in spec.channels}
    for trigger in spec.triggers:
        if trigger.agent not in agent_ids:
            err("unknown_trigger_agent", f"trigger '{trigger.id}' runs unknown agent "
                f"'{trigger.agent}'", trigger.id)
        if trigger.workflow and not any(w.id == trigger.workflow for w in spec.workflows):
            err("unknown_trigger_workflow", f"trigger '{trigger.id}' references unknown "
                f"workflow '{trigger.workflow}'", trigger.id)
        if trigger.kind is TriggerKind.SCHEDULE:
            if trigger.cadence is None:
                err("schedule_without_cadence", f"trigger '{trigger.id}' is scheduled "
                    "but states no cadence", trigger.id)
            else:
                try:
                    parse_cadence(trigger.cadence)
                except CadenceError as e:
                    err("invalid_cadence", f"trigger '{trigger.id}': {e}", trigger.id)
        if trigger.kind is TriggerKind.EVENT and not trigger.event_class:
            err("event_without_class", f"trigger '{trigger.id}' is event-driven but "
                "names no event class", trigger.id)
        if trigger.kind is TriggerKind.MESSAGE and trigger.channel not in channel_ids:
            err("unknown_trigger_channel", f"trigger '{trigger.id}' listens on unknown "
                f"channel '{trigger.channel}'", trigger.id)
        for cid in trigger.deliver_to:
            if cid not in channel_ids:
                err("unknown_delivery_channel", f"trigger '{trigger.id}' delivers to "
                    f"unknown channel '{cid}'", trigger.id)
        if trigger.failure.notify_channel and trigger.failure.notify_channel not in channel_ids:
            err("unknown_failure_channel", f"trigger '{trigger.id}' notifies unknown "
                f"channel '{trigger.failure.notify_channel}'", trigger.id)
        if trigger.max_runtime_seconds > spec.resilience.max_run_seconds:
            err("trigger_exceeds_run_budget", f"trigger '{trigger.id}' allows "
                f"{trigger.max_runtime_seconds}s but resilience caps runs at "
                f"{spec.resilience.max_run_seconds}s", trigger.id)
        agent = spec.agent(trigger.agent)
        if agent and trigger.workflow and trigger.workflow not in agent.workflows:
            err("trigger_workflow_not_granted", f"trigger '{trigger.id}' runs workflow "
                f"'{trigger.workflow}' that agent '{trigger.agent}' may not invoke",
                trigger.id)
        if not trigger.deliver_to and not trigger.failure.notify_channel:
            warn("silent_trigger", f"trigger '{trigger.id}' reports to nobody",
                 trigger.id, strict=True)

    # -- channels (ADR-0021) ----------------------------------------------
    for channel in spec.channels:
        for dc_id in channel.forbid_data_classes:
            if spec.data_class(dc_id) is None:
                err("unknown_data_class", f"channel '{channel.id}' forbids unknown data "
                    f"class '{dc_id}'", channel.id)
        for member in channel.members:
            if member not in agent_ids and member not in team_ids:
                err("unknown_channel_member", f"channel '{channel.id}' lists unknown "
                    f"member '{member}'", channel.id)
        for step in channel.escalation:
            if step.channel and step.channel not in channel_ids:
                err("unknown_escalation_channel", f"channel '{channel.id}' escalates to "
                    f"unknown channel '{step.channel}'", channel.id)
        if channel.escalation:
            offsets = [s.after_minutes for s in channel.escalation]
            if offsets != sorted(offsets):
                err("escalation_out_of_order", f"channel '{channel.id}' escalation steps "
                    "are not in increasing time order", channel.id)
        if channel.human_facing and not channel.purposes:
            warn("channel_without_purpose", f"human-facing channel '{channel.id}' states "
                 "no purpose", channel.id)
        if (channel.human_facing and channel.response_sla_minutes
                and not channel.escalation):
            warn("sla_without_escalation", f"channel '{channel.id}' promises a reply in "
                 f"{channel.response_sla_minutes} minutes but nobody is escalated to",
                 channel.id, strict=True)
        # A channel that people only watch in office hours cannot carry an
        # incident SLA shorter than the time until they are back.
        if (channel.working_hours and channel.out_of_hours == "queue"
                and (channel.response_sla_minutes or 0) and channel.response_sla_minutes < 60
                and ChannelPurpose.NOTIFY in (channel.purposes or [])):
            warn("sla_unreachable_out_of_hours", f"channel '{channel.id}' queues out of "
                 "hours but promises a sub-hour reply", channel.id)

    # -- interaction flows (ADR-0024) -------------------------------------
    for flow in spec.interaction_flows:
        for end, label in ((flow.source, "source"), (flow.target, "target")):
            if end not in agent_ids and end not in team_ids:
                err("unknown_flow_endpoint", f"flow {flow.source}->{flow.target} has "
                    f"unknown {label} '{end}'")
        if flow.source == flow.target:
            err("self_flow", f"flow from '{flow.source}' to itself")

    # -- knowledge (ADR-0023) ---------------------------------------------
    for source in spec.knowledge:
        for dc_id in source.data_classes:
            if spec.data_class(dc_id) is None:
                err("unknown_data_class", f"knowledge source '{source.id}' references "
                    f"unknown data class '{dc_id}'", source.id)
    for agent in agents:
        for source_id in agent.knowledge:
            source = spec.knowledge_source(source_id)
            if source is None:
                err("unknown_knowledge", f"agent '{agent.id}' references unknown "
                    f"knowledge source '{source_id}'", agent.id)

    # -- budgets, lifecycle, compliance (ADR-0022) ------------------------
    for budget in spec.budgets:
        if budget.limit_usd <= 0:
            err("budget_without_limit", f"budget '{budget.id}' has no positive limit",
                budget.id)
        if budget.scope_kind == "team" and budget.scope not in team_ids:
            err("unknown_budget_scope", f"budget '{budget.id}' scopes unknown team "
                f"'{budget.scope}'", budget.id)
        if budget.scope_kind == "agent" and budget.scope not in agent_ids:
            err("unknown_budget_scope", f"budget '{budget.id}' scopes unknown agent "
                f"'{budget.scope}'", budget.id)
        if budget.notify_channel and budget.notify_channel not in channel_ids:
            err("unknown_budget_channel", f"budget '{budget.id}' notifies unknown "
                f"channel '{budget.notify_channel}'", budget.id)

    covered = {e for case in spec.lifecycle.evaluations for e in case.applies_to}
    if spec.lifecycle.evaluations:
        for agent in agents:
            if agent.id not in covered and not any(
                not c.applies_to for c in spec.lifecycle.evaluations
            ):
                warn("agent_without_evaluation", f"agent '{agent.id}' has no evaluation "
                     "case", agent.id, strict=True)
    if spec.lifecycle.stage is LifecycleStage.PRODUCTION:
        if not any(g.to_stage is LifecycleStage.PRODUCTION for g in spec.lifecycle.gates):
            err("production_without_gate", "the system is in production with no "
                "promotion gate defining how it got there")

    residency = spec.compliance.data_residency
    for dc in spec.data_classes:
        if not dc.may_leave_region and not residency:
            warn("residency_undeclared", f"data class '{dc.id}' may not leave a region "
                 "but no residency is declared", dc.id, strict=True)
        if not dc.may_appear_in_traces and dc.id not in spec.observability.redact_data_classes:
            err("trace_leak", f"data class '{dc.id}' may not appear in traces but is not "
                "redacted in the observability contract", dc.id)

    # -- capability coverage ----------------------------------------------
    used = {c for a in agents for c in a.capabilities}
    used |= {c for r in spec.roles for c in r.capabilities}
    for cap in spec.capabilities:
        if cap.id not in used:
            warn("unused_capability", f"capability '{cap.id}' is declared but unused",
                 cap.id)

    return out


def _responsibility_mentions(text: str, cap_id: str, cap) -> bool:
    """True when the prose responsibilities plausibly describe this capability.

    Deliberately loose: it matches word stems from the capability id and its
    resource class, because the point is to catch a permission nobody explained,
    not to police wording.
    """
    tokens = set(cap_id.lower().split("_"))
    if cap is not None:
        tokens |= set((cap.resource_class or "").lower().split("_"))
        tokens |= {cap.action.value.lower()}
    words = set(text.replace(",", " ").replace(".", " ").split())
    for token in tokens:
        if len(token) < 4:
            continue
        stem = token[:6]
        if any(w.startswith(stem) for w in words):
            return True
    return False


def _check_narrowing(agent_id: str, env, override, err) -> None:
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


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


def matches(pattern: str, value: str) -> bool:
    return fnmatch.fnmatch(value, pattern)
