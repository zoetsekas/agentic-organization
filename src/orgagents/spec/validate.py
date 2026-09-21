"""Spec validation: structure, references and least privilege.

Findings are severity-tagged. `error` fails the build; `warning` is reported
and, for the rules ADR-0008 marks as mandatory in production, promoted to an
error when `metadata.environment == "production"`.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, Optional

from .model import (
    ChannelPurpose,
    EndpointTrust,
    HumanRole,
    MemoryTier,
    LifecycleStage,
    NetworkPosture,
    Permission,
    RoleAssignment,
    SharingScope,
    SystemSpec,
    Team,
    TriggerKind,
)

if TYPE_CHECKING:  # a runtime import would close a cycle: directory reads this package
    from ..directory import Directory

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


def validate_spec(
    spec: SystemSpec,
    directory: Optional["Directory"] = None,
    platform_policy: Optional[Any] = None,
) -> list[Finding]:
    """Return every finding; an empty list means the spec is sound.

    `directory`, when given, is additionally consulted about the people the
    spec pairs with agents (ADR-0047). It is optional because most callers have
    none, and a directory that knows nothing contributes nothing.
    """
    # Imported here: `scheduling` reads the spec model, so a module-level import
    # would close a cycle through this package's __init__.
    from ..scheduling import CadenceError, parse_cadence

    out: list[Finding] = []
    # Strictness is the fabric's call where a policy is in force (ADR-0076).
    # `metadata.environment` is a field the design declares about itself, so a
    # design that called itself development was simply not judged by the
    # strict rules.
    production = (
        platform_policy.treat_as == "production"
        if platform_policy and platform_policy.treat_as
        else spec.metadata.environment == "production"
    )

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
        ("decision class", [d.id for d in spec.decisions]),
        ("environment", [e.id for e in spec.environments]),
        ("data class", [d.id for d in spec.data_classes]),
        ("workflow", [w.id for w in spec.workflows]),
    ):
        seen: set[str] = set()
        for i in ids:
            if i in seen:
                err("duplicate_id", f"duplicate {label} id '{i}'")
            seen.add(i)

    # -- authority: mandates (ADR-0065) ------------------------------------
    declared_decisions = {d.id for d in spec.decisions}

    def check_mandate(mandate, where: str, what: str) -> None:
        if mandate is None:
            return
        for decision in mandate.decisions:
            if decision not in declared_decisions:
                err(
                    "undeclared_decision",
                    f"{what} '{where}' claims decision class '{decision}', "
                    "which the spec does not declare",
                    where,
                )

    # The root *team* may default to the whole declared vocabulary (ADR-0071):
    # a team is a scope and nobody exercises it, and requiring the root to
    # enumerate every decision is what made authority accumulate upward.
    #
    # The root's *leader* is a different matter. It is a principal, it inherits
    # its unit's mandate, and at the root that is everything. So the one place
    # authority must be written down is the agent at the top.
    root_leader = spec.organization.leader
    if root_leader:
        top = next(
            (m for m in spec.organization.members if m.id == root_leader), None
        )
        if top is not None and top.mandate is None:
            err(
                "root_leader_without_mandate",
                f"agent '{root_leader}' leads the organization and declares no "
                "mandate, so it inherits every decision this organization can "
                "take. A principal's authority is the one thing that is never "
                "silent: declare it, or declare `decisions: []` to say it "
                "decides nothing (ADR-0071)",
                root_leader,
            )

    for team in teams:
        check_mandate(team.mandate, team.id, "team")
    for agent in agents:
        check_mandate(agent.mandate, agent.id, "agent")
    for mission in spec.missions:
        check_mandate(mission.mandate, mission.id, "mission")

    # -- people as principals (ADR-0079) -----------------------------------
    # A person is a principal for authority and never for access. Declared
    # once, so that one human is one principal — without which no separation
    # check over people can work.
    seen_people: set[str] = set()
    for person in spec.people:
        if person.id in seen_people:
            err(
                "duplicate_person",
                f"person '{person.id}' is declared more than once. One human is "
                "one principal, or the checks below are checking copies",
                person.id,
            )
        seen_people.add(person.id)
        claims = person.claims_access
        if claims:
            err(
                "person_holds_access",
                f"person '{person.id}' declares {', '.join(claims)}. A person's "
                "access is not mediated here — they sign in under their "
                "employer's IAM — so a permission this platform cannot enforce "
                "is worse than none. Declare what they may *decide* as a "
                "mandate instead (ADR-0079)",
                person.id,
            )
        if person.unit and not any(t.id == person.unit for t in teams):
            err(
                "unknown_reference",
                f"person '{person.id}' is attached to unit '{person.unit}', "
                "which the organization does not contain",
                person.id,
            )
        check_mandate(person.mandate, person.id, "person")

    by_contact: dict[str, list[str]] = {}
    for person in spec.people:
        if person.contact:
            by_contact.setdefault(person.contact.lower(), []).append(person.id)
    for contact, ids in by_contact.items():
        if len(ids) > 1:
            err(
                "duplicate_person",
                f"{sorted(ids)} share the contact '{contact}', so one human is "
                "declared as several principals. Separation of duties cannot "
                "see past that (ADR-0079)",
                sorted(ids)[0],
            )

    for agent in agents:
        for human in agent.humans:
            if human.person and not any(p.id == human.person for p in spec.people):
                err(
                    "unknown_reference",
                    f"agent '{agent.id}' is paired with person "
                    f"'{human.person}', which the spec does not declare",
                    agent.id,
                )

    # Four-eyes, expressed where it can be checked (ADR-0079 rule 5). The
    # person accountable for an agent cannot also be the one who approves what
    # it raises; ADR-0072 rule 3 could only approximate this because a person
    # was not an identity.
    for agent in agents:
        owners = {
            h.principal() for h in agent.humans if HumanRole.OWNER in h.roles
        }
        for human in agent.humans:
            if HumanRole.APPROVER not in human.roles:
                continue
            who = human.principal()
            if who and who in owners:
                err(
                    "owner_approves_own_agent",
                    f"agent '{agent.id}' is owned and approved by the same "
                    f"person ('{who}'), so the approval is the raiser's own "
                    "signature. Name an approver who does not own it "
                    "(ADR-0079)",
                    agent.id,
                )

    # Separation of duties (ADR-0070). Checked over *effective agent*
    # mandates, because an agent is what acts: a team's mandate bounds its
    # members and is exercised by nobody.
    resolved = None
    if spec.separations:
        from ..mandates import resolve as _resolve_for_separation

        resolved = _resolve_for_separation(
            spec.organization, declared_decisions, spec.people
        )
        for rule in spec.separations:
            unknown = [d for d in rule.decisions if d not in declared_decisions]
            for d in unknown:
                err(
                    "undeclared_decision",
                    f"separation '{rule.id}' names decision class '{d}', which "
                    "the spec does not declare",
                    rule.id,
                )
            if len(rule.decisions) < 2:
                warn(
                    "separation_without_conflict",
                    f"separation '{rule.id}' names fewer than two decisions, so "
                    "nothing can violate it",
                    rule.id,
                )
        for person_id, effective in resolved.people.items():
            for rule in spec.separations:
                held = sorted(set(rule.decisions) & effective.decisions)
                if len(held) > 1:
                    err(
                        "separation_violated",
                        f"person '{person_id}' holds {held}, which separation "
                        f"'{rule.id}' forbids"
                        + (f": {rule.reason}" if rule.reason else "")
                        + ". People are the principal in most real frauds, so "
                        "a rule that does not cover them does not cover the "
                        "case it was written for (ADR-0079)",
                        person_id,
                    )
        for agent_id, effective in resolved.agents.items():
            for rule in spec.separations:
                held = sorted(set(rule.decisions) & effective.decisions)
                if len(held) > 1:
                    err(
                        "separation_violated",
                        f"agent '{agent_id}' holds {held}, which separation "
                        f"'{rule.id}' forbids"
                        + (f": {rule.reason}" if rule.reason else "")
                        + ". An agent inheriting its unit's mandate holds "
                        "everything beneath it, so a leader over both sides of "
                        "a control must declare a narrower mandate of its own",
                        agent_id,
                    )

    # -- control ownership (ADR-0073) --------------------------------------
    # A bound that reads as enforced and is not is worse than no bound, so a
    # control claimed for this platform must be evaluable here, and a control
    # left to an application may not be claimed.
    from .model import (
        PLATFORM_EVALUATED_CONSTRAINTS,
        ControlEnforcer,
        _wider,
        condition_is_evaluable,
    )

    def check_enforcement(
        enforcement: Any, declared: Iterable[str], where: str, what: str,
        evaluable: Callable[[str], bool],
    ) -> None:
        names = sorted(set(declared))
        if enforcement.enforced_by is ControlEnforcer.BOTH:
            if enforcement.authoritative is None:
                err(
                    "both_without_authority",
                    f"{what} '{where}' is enforced by this platform and by an "
                    "application, and does not say which wins. Two rule sets "
                    "that can disagree need an answer, not a debate",
                    where,
                )
            elif enforcement.authoritative is ControlEnforcer.BOTH:
                err(
                    "both_without_authority",
                    f"{what} '{where}' names 'both' as authoritative, which "
                    "answers nothing",
                    where,
                )
        if enforcement.enforced_by is ControlEnforcer.APPLICATION:
            if not enforcement.enforced_in:
                warn(
                    "application_control_unnamed",
                    f"{what} '{where}' is left to an application and does not "
                    "say which kind. A control nobody can point at is a control "
                    "nobody will check",
                    where,
                )
            return  # described here, not evaluated here

        # PLATFORM or BOTH: whatever we claim, we must be able to check.
        for name in names:
            if not evaluable(name):
                err(
                    "unenforceable_platform_control",
                    f"{what} '{where}' claims '{name}' as a control this "
                    "platform enforces, and nothing here evaluates it. Either "
                    "something must, or declare it `enforced_by: application` "
                    "and name the system that does (ADR-0073)",
                    where,
                )

        # Rule 7: our bound may never be wider than the application's claim.
        for name in names:
            theirs = enforcement.application_bounds.get(name)
            ours = None
            if hasattr(enforcement, "_ours"):
                ours = enforcement._ours.get(name)
            if theirs is None or ours is None:
                continue
            if _wider(name, ours, theirs):
                err(
                    "platform_bound_wider_than_application",
                    f"{what} '{where}': this platform allows {name}={ours} "
                    f"where the application is recorded as allowing {theirs}. "
                    "A bound may narrow what the application permits and never "
                    "widen it",
                    where,
                )

    for cap in spec.capabilities:
        claimed = [
            f for f in ("requires_approval", "max_rows", "masked_fields",
                        "allowed_operations", "resource_scope", "rate_per_minute")
            if getattr(cap.constraints, f, None) not in (None, False, [], {})
        ]
        cap.constraints.enforcement._ours = {  # type: ignore[attr-defined]
            f: getattr(cap.constraints, f) for f in claimed
        }
        check_enforcement(
            cap.constraints.enforcement, claimed, cap.id, "capability",
            lambda n: n in PLATFORM_EVALUATED_CONSTRAINTS,
        )

    def check_mandate_enforcement(unit: Any, kind: str) -> None:
        mandate = getattr(unit, "mandate", None)
        if mandate is None or not mandate.conditions:
            return
        mandate.enforcement._ours = dict(mandate.conditions)  # type: ignore[attr-defined]
        check_enforcement(
            mandate.enforcement, mandate.conditions, unit.id, kind,
            condition_is_evaluable,
        )

    for team in teams:
        check_mandate_enforcement(team, "team mandate")
    for agent in agents:
        check_mandate_enforcement(agent, "agent mandate")

    for rule in spec.separations:
        check_enforcement(
            rule.enforcement, [], rule.id, "separation", lambda n: True,
        )

    # -- autonomy postures (ADR-0072) --------------------------------------
    # The posture used to be emergent — action, decision, mandate and approval
    # in four places and no stated intent. Declaring it is only worth anything
    # if the mechanics are checked against the declaration, which is what this
    # does. Drift is then a failed check rather than a silent change.
    from .model import AutonomyPosture, autonomy_rank

    _mandates = resolved or None
    if _mandates is None:
        from ..mandates import resolve as _rm

        _mandates = _rm(spec.organization, declared_decisions, spec.people)

    cap_by_id = {c.id: c for c in spec.capabilities}
    role_by_id = {r.id: r for r in spec.roles}
    evaluated = {
        a for case in spec.lifecycle.evaluations for a in (case.applies_to or [])
    }
    evaluates_everyone = any(
        not case.applies_to for case in spec.lifecycle.evaluations
    )
    READING = {"read", "query"}

    def posture_for(agent: Any, cap_id: str) -> AutonomyPosture:
        cap = cap_by_id[cap_id]
        declared = agent.autonomy.get(cap_id)
        return declared or cap.autonomy

    def capabilities_for(agent: Any, team_roles: list[str]) -> set[str]:
        out: set[str] = set()
        for assignment in list(agent.roles) + list(team_roles):
            role = role_by_id.get(getattr(assignment, "role", assignment))
            if role:
                out.update(role.capabilities)
        out.update(agent.capabilities)
        return {c for c in out if c in cap_by_id}

    def check_autonomy(team: Any, inherited: list[str]) -> None:
        team_roles = inherited + [r.role for r in team.roles]
        for agent in team.members:
            for cap_id in sorted(capabilities_for(agent, team_roles)):
                cap = cap_by_id[cap_id]
                posture = posture_for(agent, cap_id)
                declared = agent.autonomy.get(cap_id)
                mutates = cap.action.value not in READING
                holds = bool(
                    cap.decision
                    and cap.decision in _mandates.for_agent(agent.id).decisions
                )
                where = f"{agent.id}/{cap_id}"

                # 6. An assignment may tighten and never loosen.
                if declared and autonomy_rank(declared) < autonomy_rank(cap.autonomy):
                    err(
                        "autonomy_widened",
                        f"{where}: agent declares '{declared.value}' where the "
                        f"capability declares '{cap.autonomy.value}'; an "
                        "assignment may only ever tighten",
                        where,
                    )

                # 1. Advisory may not mutate a system of record.
                if posture is AutonomyPosture.ADVISORY and mutates:
                    err(
                        "advisory_mutates",
                        f"{where}: declared advisory but the action is "
                        f"'{cap.action.value}', so it changes a system of "
                        "record. An advisory activity reads and models; give it "
                        "a decision class and a posture, or make it read-only",
                        where,
                    )

                # 2. Autonomy needs a decision class the agent actually holds.
                if posture is AutonomyPosture.AUTONOMOUS:
                    if not cap.decision:
                        err(
                            "autonomous_without_decision",
                            f"{where}: declared autonomous but names no decision "
                            "class, so no mandate governs it, no condition "
                            "bounds it and nothing can escalate",
                            where,
                        )
                    elif not holds:
                        err(
                            "autonomous_without_mandate",
                            f"{where}: declared autonomous but the agent does "
                            f"not hold '{cap.decision}', so every call would "
                            "escalate",
                            where,
                        )
                    # 5. Autonomy needs evidence, and "never run" is not it.
                    if not (evaluates_everyone or agent.id in evaluated):
                        warn(
                            "autonomy_without_evidence",
                            f"{where}: runs unattended and no evaluation case "
                            "applies to this agent; never-evaluated is not "
                            "safe-unattended (ADR-0060)",
                            where,
                            strict=True,
                        )

                # 3. Supervised means a person confirms, and not the requester's
                #    own owner.
                if posture is AutonomyPosture.SUPERVISED:
                    if not cap.constraints.requires_approval:
                        err(
                            "supervised_without_approval",
                            f"{where}: declared supervised but the capability "
                            "does not require approval, so nothing stops for a "
                            "person",
                            where,
                        )
                    approvers = [
                        h for h in agent.humans
                        if HumanRole.APPROVER in h.roles and not h.is_owner
                    ]
                    if not approvers:
                        warn(
                            "supervised_by_its_own_owner",
                            f"{where}: the only person who can confirm this is "
                            "the agent's own owner, which is not a second pair "
                            "of eyes; name an approver who does not own it",
                            where,
                        )

                # 4. Human-decides means the agent must not hold it, and
                #    somebody must.
                if posture is AutonomyPosture.HUMAN_DECIDES:
                    if holds:
                        err(
                            "human_decides_but_agent_holds",
                            f"{where}: declared as the human's decision and the "
                            f"agent holds '{cap.decision}', so it would never "
                            "reach one",
                            where,
                        )
                    elif cap.decision and not _mandates.holders(cap.decision):
                        warn(
                            "human_decides_with_no_holder",
                            f"{where}: declared as somebody else's decision and "
                            f"no agent holds '{cap.decision}', so the work "
                            "cannot complete inside this organization",
                            where,
                        )
        for child in team.teams:
            check_autonomy(child, team_roles)

    if spec.capabilities:
        check_autonomy(spec.organization, [])

    # Claiming authority your line does not hold is narrowed, not honoured —
    # so it is reported rather than refused. A spec that reads as if a team may
    # decide something it cannot is a spec somebody will act on.
    from ..mandates import resolve as _resolve_mandates

    _map = resolved or _resolve_mandates(
        spec.organization, declared_decisions, spec.people
    )
    for unit_id, over in _map.overreach.items():
        # An error, not a warning (ADR-0071). The claim has no effect, so the
        # unit decides less than its author believes — and with the root now
        # defaulting to the whole vocabulary, reaching this at all means some
        # ancestor deliberately narrowed. Silently deciding nothing is the
        # worst of the three possible outcomes.
        err(
            "mandate_overreach",
            f"'{unit_id}' claims {sorted(over)}, which its line does not hold, "
            "so it would decide nothing of the kind. Authority narrows "
            "downward: either widen the ancestor that excludes it, or drop "
            "the claim",
            unit_id,
        )

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
        # A team declaring nothing inherits its parent's authority, which is
        # a legitimate and common choice (ADR-0065 rule 5), so silence here is
        # no longer worth a warning. What matters is the root, checked below.

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
        # -- human pairing (ADR-0026) --------------------------------------
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
            if human.channel and human.channel not in {c.id for c in spec.channels}:
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

        # Capabilities reach an agent through its roles as well as directly,
        # so every containment check below uses the effective set.
        held_caps = set(agent.capabilities)
        for assignment in agent.roles:
            role = spec.role(assignment.role)
            if role:
                held_caps |= set(role.capabilities)
        team_of = spec.team_of(agent.id)
        if team_of:
            for assignment in team_of.roles:
                role = spec.role(assignment.role)
                if role:
                    held_caps |= set(role.capabilities)

        # -- skills, plugins, tools (ADR-0029) -----------------------------
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

        # -- sub-agents are tools, and narrow only (ADR-0027) --------------
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
            if sub.environment and agent.environment and (
                sub.environment != agent.environment.environment
            ):
                err("subagent_changes_environment", f"sub-agent '{sub.id}' of "
                    f"'{agent.id}' requests a different environment; a sub-agent "
                    "may not change the isolation boundary", agent.id)
            if sub.max_runtime_seconds > spec.resilience.max_run_seconds:
                err("subagent_exceeds_run_budget", f"sub-agent '{sub.id}' allows "
                    f"{sub.max_runtime_seconds}s beyond the system budget", agent.id)
            if not sub.returns:
                warn("subagent_without_return", f"sub-agent '{sub.id}' of "
                     f"'{agent.id}' does not say what it returns; a tool with an "
                     "undefined result is hard to use well", agent.id)

        # -- external endpoints (ADR-0030) ---------------------------------
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

        # -- guardrails, workspace, contracts per agent --------------------
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

        # -- memory (ADR-0028) ---------------------------------------------
        if agent.memory:
            for namespace_id in agent.memory.namespaces:
                if spec.namespace(namespace_id) is None:
                    err("unknown_memory_namespace", f"agent '{agent.id}' uses "
                        f"unknown memory namespace '{namespace_id}'", agent.id)

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

    # -- missions (ADR-0039) ----------------------------------------------
    from datetime import date

    seen_missions: set[str] = set()
    for mission in spec.missions:
        if mission.id in seen_missions:
            err("duplicate_mission", f"mission '{mission.id}' is defined twice",
                mission.id)
        seen_missions.add(mission.id)

        if not mission.leader:
            err("mission_without_leader", f"mission '{mission.id}' has no leader; "
                "every team has one", mission.id)
        elif mission.leader not in mission.members:
            err("mission_leader_not_member", f"leader '{mission.leader}' is not a "
                f"member of mission '{mission.id}'", mission.id)
        unknown = [m for m in mission.members if m not in agent_ids]
        if unknown:
            err("mission_member_not_in_org", f"mission '{mission.id}' includes "
                f"{unknown}, who are not agents in the organization; a mission "
                "draws from the standing organization", mission.id)
        if len(set(mission.members)) != len(mission.members):
            err("mission_duplicate_member", f"mission '{mission.id}' lists the same "
                "member twice", mission.id)
        if not mission.objective:
            err("mission_without_objective", f"mission '{mission.id}' states no "
                "objective; a short-lived team exists to achieve something",
                mission.id)
        if not mission.deliverables:
            warn("mission_without_deliverables", f"mission '{mission.id}' names no "
                 "deliverables, so nothing says when it is done", mission.id,
                 strict=True)
        if not mission.ends_on:
            err("mission_without_end", f"mission '{mission.id}' has no end date; a "
                "mission that never ends is a reorganization, and belongs in the "
                "org chart", mission.id)
        elif mission.starts_on:
            try:
                start = date.fromisoformat(mission.starts_on)
                end = date.fromisoformat(mission.ends_on)
                if end <= start:
                    err("mission_ends_before_it_starts", f"mission '{mission.id}' "
                        f"ends on {mission.ends_on}, on or before its start",
                        mission.id)
                elif (end - start).days > 180:
                    warn("mission_too_long", f"mission '{mission.id}' runs for "
                         f"{(end - start).days} days; past about two quarters this "
                         "is a standing team and should be in the org chart",
                         mission.id)
            except ValueError:
                err("mission_bad_dates", f"mission '{mission.id}' has unparseable "
                    "dates; use ISO format", mission.id)
        if mission.ends_on and mission.status.value in ("proposed", "active"):
            try:
                if date.fromisoformat(mission.ends_on) < date.today():
                    warn("mission_past_its_end_date",
                         f"mission '{mission.id}' ended on {mission.ends_on} but is "
                         f"still marked '{mission.status.value}'; the runtime stops "
                         "honouring it either way, so close the record "
                         "(`orgagents missions sweep`)", mission.id)
            except ValueError:
                pass
        if mission.channel and mission.channel not in {c.id for c in spec.channels}:
            err("unknown_mission_channel", f"mission '{mission.id}' uses unknown "
                f"channel '{mission.channel}'", mission.id)
        for workflow_id in mission.workflows:
            if not any(w.id == workflow_id for w in spec.workflows):
                err("unknown_mission_workflow", f"mission '{mission.id}' references "
                    f"unknown workflow '{workflow_id}'", mission.id)

        # A mission may narrow what members hold; it may never widen it.
        for assignment in mission.roles:
            role = spec.role(assignment.role)
            if role is None:
                err("unknown_role", f"mission '{mission.id}' references unknown role "
                    f"'{assignment.role}'", mission.id)
                continue
            wanted = {p.key() for p in role.permissions}
            for member_id in mission.members:
                member = spec.agent(member_id)
                if member is None:
                    continue
                held: set[str] = set()
                for member_assignment in member.roles:
                    member_role = spec.role(member_assignment.role)
                    if member_role:
                        held |= {p.key() for p in member_role.permissions}
                home = spec.team_of(member_id)
                if home:
                    for team_assignment in home.roles:
                        team_role = spec.role(team_assignment.role)
                        if team_role:
                            held |= {p.key() for p in team_role.permissions}
                extra = wanted - held
                if extra:
                    warn("mission_would_widen_access", f"mission '{mission.id}' "
                         f"assigns role '{role.id}', which asks for permissions "
                         f"'{member_id}' does not hold ({sorted(extra)[:3]}); they "
                         "are dropped, not granted", mission.id)

    # -- model policy (ADR-0040) -------------------------------------------
    for label, policy in [("system", spec.model_policy)] + [
        (a.id, a.model_policy) for a in agents if a.model_policy
    ]:
        if not policy.classes and not policy.allow:
            err("model_policy_empty", f"model policy for '{label}' permits nothing: "
                "name at least one class or an allowed model", label)
        if policy.allow and policy.classes:
            warn("model_policy_redundant", f"model policy for '{label}' names both "
                 "explicit models and classes; the explicit list wins", label)
        overlap = set(policy.allow) & set(policy.deny)
        if overlap:
            err("model_policy_contradiction", f"model policy for '{label}' both "
                f"allows and denies {sorted(overlap)}", label)
        if policy.require_regions and spec.compliance.data_residency:
            if not set(policy.require_regions) & set(spec.compliance.data_residency):
                warn("model_regions_outside_residency", f"model policy for '{label}' "
                     f"requires {policy.require_regions}, outside the declared "
                     f"residency {spec.compliance.data_residency}", label,
                     strict=True)

    # -- guardrails, workspaces, contracts (ADR-0035/0036/0037) -----------
    for guardrail in spec.guardrails:
        if not guardrail.checks:
            err("guardrail_without_checks", f"guardrail '{guardrail.id}' declares "
                "no checks", guardrail.id)
        for dc_id in guardrail.data_classes:
            if spec.data_class(dc_id) is None:
                err("unknown_data_class", f"guardrail '{guardrail.id}' watches "
                    f"unknown data class '{dc_id}'", guardrail.id)
        if guardrail.escalate_channel and guardrail.escalate_channel not in {
            c.id for c in spec.channels
        }:
            err("unknown_guardrail_channel", f"guardrail '{guardrail.id}' escalates "
                f"to unknown channel '{guardrail.escalate_channel}'", guardrail.id)
        if (guardrail.on_violation.value == "escalate"
                and not guardrail.escalate_channel):
            err("escalation_without_channel", f"guardrail '{guardrail.id}' escalates "
                "but names no channel", guardrail.id)
    if not spec.guardrails:
        warn("no_guardrails", "no guardrails are declared; permissions decide what "
             "agents may reach, but nothing checks what may pass", strict=True)
    else:
        kinds = {k for g in spec.guardrails for k in g.applies_to}
        for needed in ("input", "output"):
            if needed not in {k.value for k in kinds}:
                warn("guardrail_gap", f"no guardrail applies to '{needed}'",
                     strict=True)

    for store in spec.artifact_stores:
        if store.scope is SharingScope.PROTECTED and not store.groups:
            err("artifact_store_without_groups", f"artifact store '{store.id}' is "
                "protected but names no groups", store.id)
        for dc_id in store.data_classes:
            if spec.data_class(dc_id) is None:
                err("unknown_data_class", f"artifact store '{store.id}' holds "
                    f"unknown data class '{dc_id}'", store.id)
    if spec.context.offload_to and spec.artifact_store(spec.context.offload_to) is None:
        err("unknown_artifact_store", f"context offloads to unknown store "
            f"'{spec.context.offload_to}'")
    if spec.context.summarize_after_tokens >= spec.context.max_context_tokens:
        err("summarize_never_fires", "summarize_after_tokens is not below "
            "max_context_tokens, so compaction would never run")

    for contract in spec.output_contracts:
        if not contract.schema_:
            err("contract_without_schema", f"output contract '{contract.id}' has no "
                "schema, so it checks nothing", contract.id)

    # -- memory contract (ADR-0028) ---------------------------------------
    for namespace in spec.memory.namespaces:
        if namespace.scope is SharingScope.PROTECTED and not namespace.groups:
            err("memory_namespace_without_groups", f"memory namespace "
                f"'{namespace.id}' is protected but names no groups", namespace.id)
        for dc_id in namespace.data_classes:
            dc = spec.data_class(dc_id)
            if dc is None:
                err("unknown_data_class", f"memory namespace '{namespace.id}' holds "
                    f"unknown data class '{dc_id}'", namespace.id)
                continue
            # Anything excluded from traces is excluded from durable memory too,
            # unless the tier redacts it.
            if (not dc.may_appear_in_traces
                    and dc_id not in spec.memory.long_term.redact_data_classes):
                err("memory_retains_sensitive_class", f"memory namespace "
                    f"'{namespace.id}' holds '{dc_id}', which may not be retained "
                    "unredacted", namespace.id)
            if not dc.may_leave_region and not spec.compliance.data_residency:
                warn("memory_residency_undeclared", f"memory namespace "
                     f"'{namespace.id}' holds region-restricted '{dc_id}' but no "
                     "residency is declared", namespace.id, strict=True)
    if spec.memory.long_term.enabled and not spec.memory.namespaces:
        warn("long_term_without_namespace", "long-term memory is enabled but no "
             "namespace is declared, so nothing can be promoted into it")
    if spec.memory.session.retention_days and spec.memory.session.retention_days > 7:
        warn("session_memory_is_long_term", "session memory retained for "
             f"{spec.memory.session.retention_days} days is long-term memory that "
             "has not been governed as such")

    # -- system-level skill/plugin/tool references ------------------------
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

    # -- capability coverage ----------------------------------------------
    used = {c for a in agents for c in a.capabilities}
    used |= {c for r in spec.roles for c in r.capabilities}
    used |= {c for a in agents for sa in a.subagents for c in sa.capabilities}
    used |= {t.wraps for t in spec.tools if t.wraps_kind == "capability"}
    used |= {c for e in spec.endpoints for c in e.provides}
    for cap in spec.capabilities:
        if cap.id not in used:
            warn("unused_capability", f"capability '{cap.id}' is declared but unused",
                 cap.id)

    # -- people who have left (ADR-0047) -----------------------------------
    out.extend(directory_findings(spec, directory))

    # The fabric's own rules, and its ranking of ours, come last: a policy
    # judges the whole finding set rather than being one more rule inside it.
    return _with_platform_policy(out, platform_policy, spec)


def directory_findings(
    spec: SystemSpec, directory: Optional["Directory"] = None
) -> list[Finding]:
    """Findings for pairings the directory disputes (ADR-0047).

    Severity is the governance call, and it is made here rather than in the
    directory module:

    * a **confirmed departed owner** (or mission sponsor) leaves the agent with
      nobody accountable, which ADR-0026 does not allow — it is promoted to an
      error in production, like the other mandatory pairing rules;
    * any other **confirmed departed** pairing is a warning: an absent reviewer
      or stakeholder is stale, not unsafe;
    * a contact **unknown to the directory** is only ever a warning, even for an
      owner and even in production, because the likeliest explanation is a
      contractor or an alias the directory does not hold, not a departure;
    * a directory that knows nothing — `NullDirectory`, an empty one, or one
      that could not be reached — produces nothing at all.
    """
    from ..directory import reconcile   # deferred: `directory` imports this package

    if directory is None:
        return []
    report = reconcile(spec, directory)
    if not report.consulted:
        return []

    out: list[Finding] = []
    production = spec.metadata.environment == "production"
    for issue in report.issues:
        who = f"{issue.name} <{issue.contact}>" if issue.name else issue.contact
        roles = ", ".join(issue.roles) or "sponsor"
        if issue.departed:
            severity: Severity = (
                "error" if (issue.is_owner and production) else "warning"
            )
            code = "departed_owner" if issue.is_owner else "departed_human"
            out.append(Finding(
                severity, code,
                f"{issue.kind} '{issue.where}' is paired with {who} as {roles}, "
                f"but directory '{report.directory}' reports them as departed",
                issue.where,
            ))
        else:
            out.append(Finding(
                "warning", "human_unknown_to_directory",
                f"{issue.kind} '{issue.where}' is paired with {who} as {roles}, "
                f"but directory '{report.directory}' has no record of them",
                issue.where,
            ))
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


def _with_platform_policy(
    findings: list[Finding], policy: Optional[Any], spec: SystemSpec
) -> list[Finding]:
    from ..platform_policy import apply_severity, policy_findings

    return apply_severity(policy, findings) + policy_findings(policy, spec)


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


def matches(pattern: str, value: str) -> bool:
    return fnmatch.fnmatch(value, pattern)
