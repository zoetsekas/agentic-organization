"""The two phases of the designer, and the gate between them (ADR-0019).

**Definition phase** — describe the organization in abstract terms: teams,
leaders, roles and responsibilities, data classes, capabilities, environment
classes, policies, triggers, human channels, lifecycle and budgets. Nothing
here names a vendor, and nothing here can be deployed. It is reviewed and
signed off on its own merits.

**Implementation phase** — choose how each abstract thing is realized for a
target: which framework runs the loop, which image backs an environment class,
which MCP server serves a capability, which workspace a channel lives in, which
scheduler fires a trigger, which cloud the whole thing lands in.

The gate between them is checkable, which is the point of this module: a
definition that is incomplete cannot be bound, and a binding that leaves an
abstract thing unrealized cannot be compiled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .spec.binding import Binding, TargetBinding
from .spec.model import (
    ChannelPurpose,
    EndpointTrust,
    HumanRole,
    LifecycleStage,
    SystemSpec,
    TriggerKind,
)
from .spec.validate import validate_spec

Phase = Literal["definition", "implementation"]
Status = Literal["pass", "warn", "fail"]


@dataclass
class Check:
    id: str
    phase: Phase
    status: Status
    title: str
    detail: str = ""
    fix: str = ""

    def __str__(self) -> str:
        mark = {"pass": "✓", "warn": "!", "fail": "✗"}[self.status]
        line = f"{mark} [{self.phase[:3]}] {self.title}"
        return f"{line}\n      {self.detail}" if self.detail else line


@dataclass
class PhaseReport:
    spec_name: str
    target: Optional[str] = None
    checks: list[Check] = field(default_factory=list)

    def of(self, phase: Phase) -> list[Check]:
        return [c for c in self.checks if c.phase == phase]

    def failures(self, phase: Optional[Phase] = None) -> list[Check]:
        return [
            c for c in self.checks
            if c.status == "fail" and (phase is None or c.phase == phase)
        ]

    @property
    def definition_complete(self) -> bool:
        return not self.failures("definition")

    @property
    def implementation_complete(self) -> bool:
        return not self.failures("implementation")

    @property
    def ready_to_compile(self) -> bool:
        return self.definition_complete and self.implementation_complete

    def summary(self) -> str:
        def count(phase: Phase, status: Status) -> int:
            return sum(1 for c in self.of(phase) if c.status == status)

        return (
            f"definition: {count('definition', 'pass')} pass, "
            f"{count('definition', 'warn')} warn, {count('definition', 'fail')} fail | "
            f"implementation: {count('implementation', 'pass')} pass, "
            f"{count('implementation', 'warn')} warn, "
            f"{count('implementation', 'fail')} fail"
        )


def _check(report: PhaseReport, phase: Phase, ok: bool, cid: str, title: str,
           detail: str = "", fix: str = "", soft: bool = False) -> None:
    status: Status = "pass" if ok else ("warn" if soft else "fail")
    report.checks.append(
        Check(cid, phase, status, title, "" if ok else detail, "" if ok else fix)
    )


# --------------------------------------------------------------------------
# Definition phase
# --------------------------------------------------------------------------


def review_definition(spec: SystemSpec, report: PhaseReport,
                      platform_policy: Optional[Any] = None) -> None:
    findings = validate_spec(spec, platform_policy=platform_policy)
    errors = [f for f in findings if f.severity == "error"]
    _check(
        report, "definition", not errors, "spec_valid",
        "The specification validates",
        f"{len(errors)} validation error(s): " + "; ".join(str(e) for e in errors[:3]),
        "run `orgagents spec validate` and fix the errors",
    )

    _check(
        report, "definition", bool(spec.metadata.owner), "system_owner",
        "The system has a named owner",
        "metadata.owner is empty",
        "name the team accountable for this system",
    )

    agents = spec.agents()
    _check(report, "definition", bool(agents), "has_agents",
           "The organization has at least one agent",
           "no agents are defined", "add teams and members under `organization`")

    roleless = [a.id for a in agents if not a.roles]
    _check(report, "definition", not roleless, "agents_have_roles",
           "Every agent holds a role",
           f"no role assigned: {roleless}",
           "assign a role; a role is what states the agent's accountability")

    # -- human pairing (ADR-0026) -----------------------------------------
    unpaired = [a.id for a in agents if not a.humans]
    _check(report, "definition", not unpaired, "agents_have_humans",
           "Every agent is paired with at least one human",
           f"paired with nobody: {unpaired}",
           "pair the people this agent answers to; agents do not own their outcomes")

    ownerless = [a.id for a in agents if a.humans and not a.owner]
    _check(report, "definition", not ownerless, "agents_have_one_owner",
           "Every agent has exactly one accountable owner",
           f"no owner among the paired humans: {ownerless}",
           "give exactly one paired human the `owner` role")

    gated_without_approver = [
        a.id for a in agents
        if a.approval_required_for and not a.humans_with(HumanRole.APPROVER)
    ]
    _check(report, "definition", not gated_without_approver,
           "gated_actions_have_approvers",
           "Gated actions have someone who can approve them",
           f"gates actions but pairs no approver: {gated_without_approver}",
           "pair an approver, or the agent stops at its own gate")

    lonely = [
        a.id for a in agents
        if len(a.humans) == 1 and not a.humans_with(HumanRole.ESCALATION)
    ]
    _check(report, "definition", not lonely, "agents_have_a_fallback_human",
           "Every agent has someone beyond its owner",
           f"only one paired human and no escalation contact: {lonely}",
           "pair an escalation contact; one person is a single point of failure",
           soft=True)

    empty_roles = [r.id for r in spec.roles if not r.responsibilities]
    _check(report, "definition", not empty_roles, "roles_have_responsibilities",
           "Every role states its responsibilities",
           f"no responsibilities: {empty_roles}",
           "state what the holder is accountable for, in prose")

    _check(report, "definition", bool(spec.data_classes), "data_classified",
           "Data is classified",
           "no data classes are declared",
           "declare the classes of data agents touch (ADR-0017)")

    _check(report, "definition", bool(spec.policies), "policies_declared",
           "Explicit policy rules exist",
           "no allow/deny policy rules are declared",
           "declare at least the denies that must never be overridden", soft=True)

    executing = [a for a in agents if a.environments]
    _check(report, "definition", bool(spec.environments), "environments_declared",
           "Execution environments are declared",
           "no environment classes are declared",
           "declare the isolation classes agents run in (ADR-0009)")
    _check(report, "definition", len(executing) == len(agents), "agents_placed",
           "Every agent is placed in an environment",
           f"unplaced: {[a.id for a in agents if not a.environments]}",
           "select an environment class per agent", soft=True)

    # Triggers (ADR-0020)
    bad_triggers = [
        t.id for t in spec.triggers
        if t.kind is TriggerKind.SCHEDULE and t.cadence is None
    ]
    _check(report, "definition", not bad_triggers, "schedules_have_cadence",
           "Every scheduled trigger states a cadence",
           f"missing cadence: {bad_triggers}", "add `cadence.expression`")
    unowned = [t.id for t in spec.triggers if not t.agent]
    _check(report, "definition", not unowned, "triggers_have_agents",
           "Every trigger names the agent that runs",
           f"no agent: {unowned}", "set `agent` on the trigger")
    unnotified = [
        t.id for t in spec.triggers
        if not t.deliver_to and not t.failure.notify_channel
    ]
    _check(report, "definition", not unnotified, "triggers_reach_humans",
           "Triggered runs report somewhere a human looks",
           f"no delivery or failure channel: {unnotified}",
           "set `deliver_to` or `failure.notify_channel`; unattended automation "
           "that fails silently is worse than none", soft=True)

    # Human channels (ADR-0021)
    human_channels = [c for c in spec.channels if c.human_facing]
    _check(report, "definition", bool(human_channels), "human_channels_declared",
           "At least one human-facing channel exists",
           "no human-facing channel is declared",
           "agents need a way to reach people (ADR-0021)")
    approval_channels = [
        c for c in human_channels if ChannelPurpose.APPROVE in (c.purposes or [])
    ]
    needs_approval = any(a.approval_required_for for a in agents) or any(
        t.requires_approval for t in spec.triggers
    )
    _check(report, "definition", not needs_approval or bool(approval_channels),
           "approval_route_exists", "Approvals have a channel to land on",
           "approvals are required but no channel serves `approve`",
           "add a human-facing channel with purpose `approve`")
    no_escalation = [
        c.id for c in approval_channels if not c.escalation
    ]
    _check(report, "definition", not no_escalation, "approvals_escalate",
           "Approval channels have an escalation chain",
           f"no escalation: {no_escalation}",
           "state who is tried next, and when, if nobody answers", soft=True)

    # Lifecycle, budgets, compliance (ADR-0022)
    _check(report, "definition", bool(spec.lifecycle.owner), "lifecycle_owner",
           "The lifecycle has an accountable owner",
           "lifecycle.owner is empty", "name who owns promotion and retirement")
    production_gate = any(
        g.to_stage is LifecycleStage.PRODUCTION for g in spec.lifecycle.gates
    )
    _check(report, "definition", production_gate, "production_gate",
           "A promotion gate guards production",
           "no gate defines what must hold before production",
           "add a `PromotionGate` for `production` (ADR-0022)")
    _check(report, "definition", bool(spec.lifecycle.evaluations), "evaluations_exist",
           "Evaluation cases exist",
           "no evaluation cases are defined",
           "define the checks an agent must pass before promotion", soft=True)
    _check(report, "definition", bool(spec.budgets), "budget_declared",
           "Spend is bounded",
           "no budget is declared",
           "declare a budget with an action on breach; no agent gets unbounded spend")

    # -- sub-agents, tools and endpoints (ADR-0027, 0029, 0030) -----------
    unclear_subagents = [
        f"{a.id}/{sub.id}" for a in agents for sub in a.subagents if not sub.returns
    ]
    _check(report, "definition", not unclear_subagents, "subagents_declare_returns",
           "Every sub-agent says what it returns",
           f"no declared return: {unclear_subagents}",
           "a sub-agent is a tool; say what the caller gets back", soft=True)

    # A critique or summarize sub-agent works on text it is handed and needs
    # nothing; one that goes looking for information does.
    from .spec.model import SubAgentKind

    needs_access = {SubAgentKind.RESEARCH, SubAgentKind.EXTRACT, SubAgentKind.VERIFY}
    wide_subagents = [
        f"{a.id}/{sub.id}" for a in agents for sub in a.subagents
        if sub.kind in needs_access
        and not (sub.capabilities or sub.tools or sub.knowledge)
    ]
    _check(report, "definition", not wide_subagents, "subagents_are_scoped",
           "Sub-agents that go looking for information name their sources",
           f"a {'/'.join(k.value for k in needs_access)} sub-agent with no "
           f"capabilities, tools or knowledge reaches nothing: {wide_subagents}",
           "name the capabilities, tools or knowledge the sub-agent needs",
           soft=True)

    ungated_external = [
        e.id for e in spec.endpoints
        if e.trust is EndpointTrust.EXTERNAL and not e.requires_approval
    ]
    _check(report, "definition", not ungated_external, "external_endpoints_gated",
           "External agent endpoints require approval",
           f"callable without approval: {ungated_external}",
           "gate calls out to agents you do not run")

    # -- missions and model policy (ADR-0039, ADR-0040) -------------------
    leaderless = [m.id for m in spec.missions if not m.leader]
    _check(report, "definition", not leaderless, "missions_have_leaders",
           "Every mission has a leader",
           f"no leader: {leaderless}",
           "every team has one accountable leader, however short-lived")

    endless = [m.id for m in spec.missions if not m.ends_on]
    _check(report, "definition", not endless, "missions_end",
           "Every mission has an end date",
           f"no end date: {endless}",
           "a mission that never ends is a reorganization; put it in the org chart")

    aimless = [m.id for m in spec.missions if not m.objective]
    _check(report, "definition", not aimless, "missions_have_objectives",
           "Every mission states its objective",
           f"no objective: {aimless}",
           "say what the mission is for; deliverables follow from it")

    policyless = [
        a.id for a in agents
        if not (a.model_policy or spec.model_policy).classes
        and not (a.model_policy or spec.model_policy).allow
    ]
    _check(report, "definition", not policyless, "agents_have_a_model_policy",
           "Every agent is limited to approved models",
           f"no permitted model class or allow list: {policyless}",
           "state which model classes this agent may run on (ADR-0040)")

    # -- memory (ADR-0028) ------------------------------------------------
    _check(report, "definition",
           not spec.memory.long_term.enabled or bool(spec.memory.namespaces),
           "memory_namespaces_declared",
           "Long-term memory has somewhere to live",
           "long-term memory is enabled but no namespace is declared",
           "declare namespaces; memory without a scope cannot be governed")

    unscoped = [n.id for n in spec.memory.namespaces if not n.data_classes]
    _check(report, "definition", not unscoped, "memory_namespaces_classified",
           "Every memory namespace states what it may hold",
           f"no data classes declared: {unscoped}",
           "classify what the namespace stores, as you would any other data",
           soft=True)

    _check(report, "definition",
           spec.memory.session.retention_days is None
           or spec.memory.session.retention_days <= 7,
           "session_memory_is_short_term",
           "Session memory is actually short term",
           f"session retention is {spec.memory.session.retention_days} days",
           "long retention makes it long-term memory; govern it as such")

    regulated = [d.id for d in spec.data_classes if not d.may_leave_region]
    _check(report, "definition",
           not regulated or bool(spec.compliance.data_residency),
           "residency_declared", "Residency is stated for restricted data",
           f"{regulated} may not leave a region, but no residency is declared",
           "set `compliance.data_residency`")


# --------------------------------------------------------------------------
# Implementation phase
# --------------------------------------------------------------------------


def review_platform_policy(
    spec: SystemSpec, policy: Optional[Any], report: PhaseReport
) -> None:
    """Say which house rules judged this design, and what they let through."""
    if policy is None:
        _check(report, "definition", True, "platform_policy",
               "Judged against the built-in rules only — no platform policy "
               "is in force")
        return
    # May it judge a build at all? A draft may be evaluated so its author can
    # see what it does; it may not decide whether something ships (ADR-0077).
    blocked = policy.refusal()
    _check(
        report, "definition", not blocked, "platform_policy_usable",
        f"Platform policy '{policy.stamp}' is approved and current"
        + (f" — approved by {policy.approved_by} on {policy.approved_on}"
           if policy.approved_by else ""),
        blocked,
        "approve it, or evaluate against it without building",
    )

    lowered = policy.lowered
    _check(
        report, "definition", not lowered, "platform_policy",
        f"Platform policy '{policy.stamp}' ({policy.fingerprint}) is in force"
        + (f", strictness {policy.treat_as}" if policy.treat_as else ""),
        "; ".join(f"{code} lowered — {why or 'no reason given'}"
                  for code, why in sorted(lowered.items())),
        "a weakened rule is reported every time the policy is; remove the "
        "override or accept that it travels with every verdict",
        soft=True,
    )


def review_control_ownership(spec: SystemSpec, report: PhaseReport) -> None:
    """Say plainly which controls this deployment does not enforce itself."""
    trusted = trusted_controls(spec)
    unnamed = [t for t in trusted if t[2] == "unnamed system"]
    _check(
        report, "definition", not unnamed, "controls_are_attributed",
        (
            "Every control names its enforcer"
            + (
                f" — {len(trusted)} left to an application: "
                + "; ".join(f"{w} ({k}) → {sys}" for w, k, sys in trusted)
                if trusted
                else " — this deployment enforces all of them itself"
            )
        ),
        "; ".join(f"{w} ({k})" for w, k, _ in unnamed)
        + " left to an application without saying which",
        "name the kind of system that enforces it, so a reader can point at it",
        soft=True,
    )


def trusted_controls(spec: SystemSpec) -> list[tuple[str, str, str]]:
    """Controls this deployment does not itself enforce (ADR-0073 rule 5).

    Returns `(where, what, system)` for every control left to an application or
    shared with one. An operator should be able to read which controls this
    platform checks and which it is relying on somebody else for, without
    inferring it from the absence of a check.
    """
    out: list[tuple[str, str, str]] = []

    def add(enforcement: Any, where: str, what: str) -> None:
        if enforcement.trusted_elsewhere:
            out.append((where, what, enforcement.enforced_in or "unnamed system"))

    for cap in spec.capabilities:
        add(cap.constraints.enforcement, cap.id, "capability constraints")
    for team in spec.teams():
        if team.mandate:
            add(team.mandate.enforcement, team.id, "team mandate conditions")
    for agent in spec.agents():
        if agent.mandate:
            add(agent.mandate.enforcement, agent.id, "agent mandate conditions")
    for rule in spec.separations:
        add(rule.enforcement, rule.id, "separation of duties")
    return sorted(out)


def review_implementation(
    spec: SystemSpec, binding: Optional[Binding], target: str, report: PhaseReport,
    catalog: Optional[object] = None,
) -> None:
    bound: Optional[TargetBinding] = binding.for_target(target) if binding else None
    _check(report, "implementation", bound is not None, "binding_exists",
           f"A binding exists for target '{target}'",
           "no binding document covers this target",
           f"add a `targets:` entry with `target: {target}`")
    if bound is None:
        return

    _check(report, "implementation", bool(bound.runtime.adapter), "runtime_chosen",
           "An agent framework is selected",
           "runtime.adapter is empty", "pick an adapter (ADR-0013)")
    _check(report, "implementation", bool(bound.model.model), "model_chosen",
           "A model is selected", "model.model is empty", "pick a model")

    unbound_caps = [
        c.id for c in spec.capabilities if bound.capability_binding(c.id) is None
    ]
    _check(report, "implementation", not unbound_caps, "capabilities_bound",
           "Every capability is bound to a server",
           f"unbound: {unbound_caps}",
           "add a `capabilities:` binding naming the MCP server (ADR-0010)")

    # Separation of duties has to survive the binding (ADR-0071). Two
    # decisions the spec keeps apart mean nothing if both their capabilities
    # resolve to one MCP server under one credential: the control is enforced
    # in the ERP and the banking portal, not in our mandate table, and a
    # single connection is a single place to defeat it.
    cap_decision = {c.id: c.decision for c in spec.capabilities if c.decision}
    for rule in spec.separations:
        by_server: dict[tuple[str, str], list[str]] = {}
        for cap_id, decision in cap_decision.items():
            if decision not in rule.decisions:
                continue
            cb = bound.capability_binding(cap_id)
            if cb is None:
                continue
            key = (cb.server_name, cb.dsn_secret_ref or "")
            by_server.setdefault(key, []).append(f"{decision} via {cap_id}")
        collisions = {k: v for k, v in by_server.items() if len(v) > 1}
        _check(
            report, "implementation", not collisions,
            f"separation_survives_binding:{rule.id}",
            f"Separation '{rule.id}' survives the binding",
            "; ".join(
                f"{' and '.join(sorted(v))} share server '{k[0]}'"
                + (f" and credential '{k[1]}'" if k[1] else "")
                for k, v in collisions.items()
            ),
            "bind the two sides to different servers, or to the same server "
            "under different credentials, so the downstream system can tell "
            "them apart",
        )

    used_envs = {o.environment for a in spec.agents() for o in a.environments}
    unbound_envs = [e for e in used_envs if bound.environment_binding(e) is None]
    _check(report, "implementation", not unbound_envs, "environments_bound",
           "Every environment class in use is bound",
           f"unbound: {unbound_envs}",
           "add an `environments:` binding with the image and resources")

    human_channels = [c.id for c in spec.channels if c.human_facing]
    bound_channels = {c.channel for c in bound.channels}
    unbound_channels = [c for c in human_channels if c not in bound_channels]
    _check(report, "implementation", not unbound_channels, "channels_bound",
           "Every human-facing channel is bound to a provider",
           f"unbound: {unbound_channels}",
           "bind each channel to a workspace or mailbox (ADR-0021)")

    _check(report, "implementation",
           not spec.triggers or bool(bound.scheduler), "scheduler_bound",
           "A scheduler backend is selected",
           "triggers are declared but `scheduler` is empty",
           "set `scheduler` on the target binding (ADR-0020)")

    unbound_knowledge = [
        k.id for k in spec.knowledge
        if k.id not in {b.knowledge for b in bound.knowledge}
    ]
    _check(report, "implementation", not unbound_knowledge, "knowledge_bound",
           "Every knowledge source is bound",
           f"unbound: {unbound_knowledge}",
           "bind each grounding source to a concrete system (ADR-0023)")

    refs = {c.secret_ref for c in spec.capabilities if c.secret_ref}
    refs |= {k.secret_ref for k in spec.knowledge if k.secret_ref}
    refs |= {r for e in spec.environments for r in e.secret_refs}
    _check(report, "implementation", bool(bound.secrets_backend) or not refs,
           "secrets_backend", "A secrets backend is selected",
           f"{len(refs)} secret reference(s) but no backend",
           "set `secrets_backend` (ADR-0015)")

    cloud = target.startswith("terraform")
    if cloud:
        infra = bound.infrastructure
        _check(report, "implementation", bool(infra.region), "region_set",
               "A deployment region is set", "infrastructure.region is empty",
               "set the region; residency depends on it")
        _check(report, "implementation", bool(infra.project), "project_set",
               "A target project or account is set",
               "infrastructure.project is empty", "set the project/account id")
        _check(report, "implementation", bool(infra.state_backend),
               "state_backend_set", "Remote state is configured",
               "infrastructure.state_backend is empty",
               "point at a state bucket before applying", soft=True)
        allowed = spec.compliance.data_residency
        _check(report, "implementation",
               not allowed or any(infra.region.startswith(r) for r in allowed),
               "region_matches_residency",
               "The region satisfies the declared residency",
               f"region '{infra.region}' is not in {allowed}",
               "choose a region inside the declared residency")

    unbound_endpoints = [
        e.id for e in spec.endpoints
        if e.secret_ref and e.secret_ref not in {
            c.dsn_secret_ref for c in bound.capabilities if c.dsn_secret_ref
        } and bound.secrets_backend == ""
    ]
    _check(report, "implementation", not unbound_endpoints, "endpoint_secrets_backed",
           "External endpoint credentials have a backend",
           f"no secrets backend for: {unbound_endpoints}",
           "set `secrets_backend` so endpoint credentials resolve")

    _check(report, "implementation",
           not spec.memory.long_term.enabled or bool(bound.memory),
           "memory_store_bound", "Long-term memory has a store",
           "long-term memory is enabled but no store is bound",
           "set `memory` on the target binding (ADR-0028)")

    if catalog is not None:
        from .compiler.ir import apply_model_approvals, build_ir

        # The same resolution the compiler performs, so the gate cannot pass
        # something the build would refuse — sub-agents included (ADR-0040).
        ir = apply_model_approvals(
            build_ir(spec, target=target, binding=bound), catalog)
        refused = []
        for agent in ir.agents:
            approval = agent.model_approval
            if approval is None:
                continue
            if not approval.approved:
                refused.append(f"{agent.id}: {approval.approval_reason}")
            if not approval.subagent_approved:
                refused.append(
                    f"{agent.id} (sub-agents): {approval.subagent_approval_reason}")
        _check(report, "implementation", not refused, "models_are_approved",
               "Every bound model is permitted and catalogued",
               "; ".join(refused[:3]),
               "bind a model the agent's policy permits, or have the catalog "
               "approve one")

    _check(report, "implementation", bool(bound.observability_sink),
           "observability_bound", "An observability sink is selected",
           "observability_sink is empty", "bind traces and metrics (ADR-0016)")


def review(
    spec: SystemSpec,
    *,
    binding: Optional[Binding] = None,
    target: Optional[str] = None,
    catalog: Optional[object] = None,
    platform_policy: Optional[Any] = None,
) -> PhaseReport:
    """Run both phase reviews; the implementation phase needs a target."""
    report = PhaseReport(spec_name=spec.metadata.name, target=target)
    review_platform_policy(spec, platform_policy, report)
    review_definition(spec, report, platform_policy)
    review_control_ownership(spec, report)
    if target:
        review_implementation(spec, binding, target, report, catalog)
    return report
