"""Phase 1.5: diff two `SystemIR`s so a change can actually be reviewed.

A reviewer looking at a spec change sees the spec, not its consequences. One
line moved in a role widens resolved permissions three levels down the team
tree; one environment override adds an egress destination; a binding edit moves
an agent onto a model the catalog never approved. None of that is visible in a
source diff, and all of it is visible in the IR — which is the point of
resolving everything exactly once (ADR-0003, ADR-0005).

Two properties make this useful rather than noisy:

*Keyed by identity, not position.* Agents, permissions, endpoints and
guardrails are compared by their stable ids, like the designer's structural
merge, so reordering a list is not a change and an id that moved is reported as
a move rather than as one deletion plus one addition.

*Direction, not just difference.* Losing a permission and gaining one are
different events. Only widening is a security finding; narrowing is reported
too — it can break a system — but it is never ranked as a risk. Every finding
carries the rule that assigned its severity, so a reviewer can disagree with
the ranking rather than merely with the verdict.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from .ir import AgentIR, SystemIR

DIFF_VERSION = "1.0.0"


class Severity(str, Enum):
    """How much a reviewer should care, worst first."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


_SEVERITY_ORDER = {s: i for i, s in enumerate(Severity)}


class Direction(str, Enum):
    """Which way a boundary moved.

    The distinction the whole report rests on: `WIDENED` means the system can
    now reach, send or trust something it could not before; `NARROWED` means
    the opposite and is an availability question, not a security one.
    """

    WIDENED = "widened"
    NARROWED = "narrowed"
    NEUTRAL = "neutral"


class ChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    MOVED = "moved"
    REUSED = "reused"      # the id survived but names something else now


class Change(BaseModel):
    """One reviewable consequence of the change under review."""

    subject_kind: str            # agent | team | identity | endpoint | system | ...
    subject: str                 # the stable id the change hangs off
    field: str                   # what about the subject moved
    kind: ChangeKind
    direction: Direction = Direction.NEUTRAL
    severity: Severity = Severity.INFO
    summary: str = ""
    rationale: str = ""          # why this severity, so it can be argued with
    before: Any = None
    after: Any = None

    @property
    def security_relevant(self) -> bool:
        """A widening that a reviewer must sign off on.

        Narrowing is never a security finding, and cosmetic widening (a longer
        description) is not one either — hence the severity floor.
        """
        return (
            self.direction is Direction.WIDENED
            and _SEVERITY_ORDER[self.severity] <= _SEVERITY_ORDER[Severity.MEDIUM]
        )

    @property
    def path(self) -> str:
        return f"{self.subject_kind}:{self.subject}.{self.field}"


class IncomparableIRError(Exception):
    """Refusal: these two IRs do not describe the same thing.

    A diff of two different tenants' or two different targets' IRs would render
    as a long list of changes that nobody made, which is worse than no diff at
    all — a reviewer would read consequence into an artefact of the comparison.
    """


class IRDiff(BaseModel):
    diff_version: str = DIFF_VERSION
    system: str = ""
    target: str = ""
    left_spec_version: str = ""
    right_spec_version: str = ""
    changes: list[Change] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.changes

    @property
    def security_findings(self) -> list[Change]:
        return [c for c in self.changes if c.security_relevant]

    @property
    def other_changes(self) -> list[Change]:
        return [c for c in self.changes if not c.security_relevant]

    def worst(self) -> Optional[Severity]:
        if not self.changes:
            return None
        return min((c.severity for c in self.changes), key=_SEVERITY_ORDER.__getitem__)

    def to_dict(self) -> dict[str, Any]:
        """The machine-readable form, for a gate or a bot."""
        payload = self.model_dump(mode="json")
        for raw, change in zip(payload["changes"], self.changes):
            raw["security_relevant"] = change.security_relevant
            raw["path"] = change.path
        payload["summary"] = {
            "total": len(self.changes),
            "security_findings": len(self.security_findings),
            "worst_severity": self.worst().value if self.worst() else None,
        }
        return payload

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def to_text(self) -> str:
        """A report for a pull request comment or a terminal.

        Security findings lead, because that is what the reader is for; when
        there are none it says so plainly rather than leaving the reader to
        infer it from an absence.
        """
        head = f"IR diff — {self.system} (target: {self.target})"
        if self.left_spec_version != self.right_spec_version:
            head += f"  {self.left_spec_version} → {self.right_spec_version}"
        lines = [head, "=" * len(head), ""]

        if self.empty:
            lines.append("No differences: the two IRs are identical.")
            return "\n".join(lines)

        findings = self.security_findings
        if findings:
            lines.append(f"SECURITY-RELEVANT ({len(findings)}) — review these first")
            lines.append("")
            lines.extend(_render(findings))
        else:
            lines.append("SECURITY-RELEVANT (0) — no widening of access, egress, "
                         "trust, secrets or guardrails.")
        lines.append("")

        rest = self.other_changes
        if rest:
            lines.append(f"OTHER CHANGES ({len(rest)})")
            lines.append("")
            lines.extend(_render(rest))
        lines.append("")
        lines.append(
            f"{len(self.changes)} change(s); "
            f"{len(findings)} security-relevant; "
            f"worst severity: {self.worst().value}."
        )
        return "\n".join(lines)


def _render(changes: Iterable[Change]) -> list[str]:
    out: list[str] = []
    for change in changes:
        arrow = {
            Direction.WIDENED: "▲ widened",
            Direction.NARROWED: "▼ narrowed",
            Direction.NEUTRAL: "·",
        }[change.direction]
        out.append(
            f"  [{change.severity.value.upper():8}] {change.path}  ({arrow})"
        )
        out.append(f"      {change.summary}")
        if change.rationale:
            out.append(f"      why: {change.rationale}")
    return out


# --------------------------------------------------------------------------
# Ordered scales. Comparing positions on these is how direction is decided.
# --------------------------------------------------------------------------

_NETWORK_ORDER = ["none", "allowlist", "internal", "open"]
_SCOPE_ORDER = ["private", "protected", "public"]
# Trust runs the other way round: `internal` is the *most* trusted, because its
# answers may be acted on rather than merely read as data.
_TRUST_ORDER = ["external", "partner", "internal"]
# A guardrail that only flags is weaker than one that blocks.
_GUARDRAIL_STRENGTH = ["flag", "redact", "escalate", "block"]
# Actions that change state or reach further are ranked above read-only ones.
_POWERFUL_ACTIONS = {"write", "publish", "approve", "administer", "delegate"}


def _rank(scale: list[str], value: Any) -> int:
    text = value.value if isinstance(value, Enum) else str(value or "")
    return scale.index(text) if text in scale else -1


def _keyed(items: Iterable[Any], key: str = "id") -> dict[str, Any]:
    return {getattr(i, key): i for i in items}


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_enum_value(v) for v in value]
    return value


# --------------------------------------------------------------------------
# Comparability
# --------------------------------------------------------------------------


def _tenant_id(ir: SystemIR) -> str:
    return ir.tenant.id if ir.tenant else ""


def assert_comparable(left: SystemIR, right: SystemIR) -> None:
    """Refuse pairs whose differences would not mean what they look like."""
    reasons: list[str] = []
    if left.target != right.target:
        reasons.append(
            f"different targets ({left.target} vs {right.target}): the same spec "
            "compiles to different resources and bindings per target, so every "
            "difference would be the target's, not the change's"
        )
    if _tenant_id(left) != _tenant_id(right):
        reasons.append(
            f"different tenants ({_tenant_id(left) or 'untenanted'} vs "
            f"{_tenant_id(right) or 'untenanted'}): names are tenant-qualified "
            "and isolation is absolute, so these are two systems, not two "
            "versions of one (ADR-0050)"
        )
    if left.ir_version.split(".")[0] != right.ir_version.split(".")[0]:
        reasons.append(
            f"incompatible IR versions ({left.ir_version} vs {right.ir_version}): "
            "fields may have changed meaning between majors"
        )
    if reasons:
        raise IncomparableIRError(
            "refusing to diff these IRs: " + "; ".join(reasons)
        )


# --------------------------------------------------------------------------
# The diff
# --------------------------------------------------------------------------


class _Collector:
    def __init__(self) -> None:
        self.changes: list[Change] = []

    def add(self, **kwargs: Any) -> None:
        kwargs["before"] = _enum_value(kwargs.get("before"))
        kwargs["after"] = _enum_value(kwargs.get("after"))
        self.changes.append(Change(**kwargs))

    def set_change(
        self,
        *,
        subject_kind: str,
        subject: str,
        field: str,
        before: Iterable[Any],
        after: Iterable[Any],
        widened_severity: Severity,
        narrowed_severity: Severity,
        widened_why: str,
        narrowed_why: str,
        noun: str,
        protective: bool = False,
    ) -> None:
        """Report set membership changes, gains and losses separately.

        `protective` is for sets of restrictions — guardrail checks, approval
        requirements, redactions, forbidden data classes — where losing an
        entry is the widening and gaining one is the narrowing.
        """
        old = {str(_enum_value(v)) for v in before}
        new = {str(_enum_value(v)) for v in after}
        gained, lost = sorted(new - old), sorted(old - new)
        gain_direction = Direction.NARROWED if protective else Direction.WIDENED
        loss_direction = Direction.WIDENED if protective else Direction.NARROWED
        if gained:
            self.add(
                subject_kind=subject_kind, subject=subject, field=field,
                kind=ChangeKind.ADDED, direction=gain_direction,
                severity=widened_severity,
                summary=f"{noun} added: {', '.join(gained)}",
                rationale=widened_why, before=sorted(old), after=sorted(new),
            )
        if lost:
            self.add(
                subject_kind=subject_kind, subject=subject, field=field,
                kind=ChangeKind.REMOVED, direction=loss_direction,
                severity=narrowed_severity,
                summary=f"{noun} removed: {', '.join(lost)}",
                rationale=narrowed_why, before=sorted(old), after=sorted(new),
            )


def _permission_severity(key: str) -> Severity:
    """A grant's blast radius: what it lets an agent do, and to how much."""
    action, _kind, resource = key.split(":", 2)
    # A state-changing action over every resource of a kind is the worst shape
    # a grant can take; anything else newly granted is still a finding.
    if action in _POWERFUL_ACTIONS and resource == "*":
        return Severity.CRITICAL
    return Severity.HIGH


def _diff_permissions(
    out: _Collector, subject_kind: str, subject: str, left: Any, right: Any
) -> None:
    old = {p.key(): p for p in left}
    new = {p.key(): p for p in right}
    for key in sorted(new.keys() - old.keys()):
        out.add(
            subject_kind=subject_kind, subject=subject, field="permissions",
            kind=ChangeKind.ADDED, direction=Direction.WIDENED,
            severity=_permission_severity(key),
            summary=f"permission granted: {key}",
            rationale="resolved effective access grew; inheritance is supposed "
                      "to narrow only, so a new grant is the reviewable event "
                      "(ADR-0008)",
            after=key,
        )
    for key in sorted(old.keys() - new.keys()):
        out.add(
            subject_kind=subject_kind, subject=subject, field="permissions",
            kind=ChangeKind.REMOVED, direction=Direction.NARROWED,
            severity=Severity.MEDIUM,
            summary=f"permission revoked: {key}",
            rationale="less access than before — a correctness and availability "
                      "risk, not a security one",
            before=key,
        )
    for key in sorted(old.keys() & new.keys()):
        before, after = old[key].conditions, new[key].conditions
        if before == after:
            continue
        # Conditions are the only thing narrowing a grant, so dropping one
        # widens it even though the permission key is unchanged.
        dropped = set(before) - set(after)
        direction = Direction.WIDENED if dropped else Direction.NARROWED
        out.add(
            subject_kind=subject_kind, subject=subject, field="permissions",
            kind=ChangeKind.MODIFIED, direction=direction,
            severity=Severity.HIGH if dropped else Severity.LOW,
            summary=f"conditions on {key} changed",
            rationale="a condition is what keeps a grant narrow; dropping one "
                      "makes the same permission apply in more situations",
            before=before, after=after,
        )


def _diff_environment(out: _Collector, agent_id: str, left: Any, right: Any) -> None:
    if left is None and right is None:
        return
    if left is None or right is None:
        out.add(
            subject_kind="agent", subject=agent_id, field="environment",
            kind=ChangeKind.ADDED if left is None else ChangeKind.REMOVED,
            direction=Direction.WIDENED if left is None else Direction.NARROWED,
            severity=Severity.HIGH,
            summary=("agent gained a sandbox environment"
                     if left is None else "agent lost its sandbox environment"),
            rationale="the sandbox decides egress and mounts; its presence or "
                      "absence changes what the agent can reach",
            before=left.id if left else None, after=right.id if right else None,
        )
        return
    if left.id != right.id:
        out.add(
            subject_kind="agent", subject=agent_id, field="environment",
            kind=ChangeKind.MODIFIED, direction=Direction.NEUTRAL,
            severity=Severity.MEDIUM,
            summary=f"environment class changed: {left.id} → {right.id}",
            rationale="a different execution profile; its posture and mounts "
                      "are reported separately",
            before=left.id, after=right.id,
        )
    before, after = _rank(_NETWORK_ORDER, left.network), _rank(_NETWORK_ORDER, right.network)
    if before != after:
        widened = after > before
        out.add(
            subject_kind="agent", subject=agent_id, field="environment.network",
            kind=ChangeKind.MODIFIED,
            direction=Direction.WIDENED if widened else Direction.NARROWED,
            severity=(
                Severity.CRITICAL
                if widened and _enum_value(right.network) == "open"
                else Severity.HIGH if widened else Severity.LOW
            ),
            summary=f"network posture {_enum_value(left.network)} → "
                    f"{_enum_value(right.network)}",
            rationale="the sandbox network posture decides whether data can "
                      "leave at all; 'open' removes the boundary entirely",
            before=left.network, after=right.network,
        )
    out.set_change(
        subject_kind="agent", subject=agent_id, field="environment.egress_allowlist",
        before=left.egress_allowlist, after=right.egress_allowlist,
        widened_severity=Severity.HIGH, narrowed_severity=Severity.LOW,
        widened_why="a new destination this agent's data may be sent to",
        narrowed_why="one fewer destination reachable; may break a workflow",
        noun="egress destination",
    )
    out.set_change(
        subject_kind="agent", subject=agent_id, field="environment.secret_refs",
        before=left.secret_refs, after=right.secret_refs,
        widened_severity=Severity.CRITICAL, narrowed_severity=Severity.LOW,
        widened_why="a credential is now mounted into this sandbox — check who "
                    "holds it and what it opens",
        narrowed_why="a credential is no longer mounted here",
        noun="secret",
    )
    out.set_change(
        subject_kind="agent", subject=agent_id, field="environment.mounts",
        before=left.mounts, after=right.mounts,
        widened_severity=Severity.HIGH, narrowed_severity=Severity.LOW,
        widened_why="a classified data class is now mounted into the sandbox",
        narrowed_why="a data class is no longer mounted",
        noun="mount",
    )


def _diff_guardrails(
    out: _Collector, subject_kind: str, subject: str, left: Any, right: Any
) -> None:
    old, new = _keyed(left), _keyed(right)
    for gid in sorted(new.keys() - old.keys()):
        out.add(
            subject_kind=subject_kind, subject=subject, field="guardrails",
            kind=ChangeKind.ADDED, direction=Direction.NARROWED,
            severity=Severity.LOW,
            summary=f"guardrail added: {gid}",
            rationale="one more check on what may pass",
            after=gid,
        )
    for gid in sorted(old.keys() - new.keys()):
        out.add(
            subject_kind=subject_kind, subject=subject, field="guardrails",
            kind=ChangeKind.REMOVED, direction=Direction.WIDENED,
            severity=Severity.CRITICAL,
            summary=f"guardrail removed: {gid}",
            rationale="permissions decide what an agent may reach, guardrails "
                      "what may pass; losing one lets correctly-permissioned "
                      "content out unchecked (ADR-0035)",
            before=gid,
        )
    for gid in sorted(old.keys() & new.keys()):
        before, after = old[gid], new[gid]
        strength = (_rank(_GUARDRAIL_STRENGTH, before.on_violation),
                    _rank(_GUARDRAIL_STRENGTH, after.on_violation))
        if strength[0] != strength[1]:
            weakened = strength[1] < strength[0]
            out.add(
                subject_kind=subject_kind, subject=subject,
                field=f"guardrails[{gid}].on_violation",
                kind=ChangeKind.MODIFIED,
                direction=Direction.WIDENED if weakened else Direction.NARROWED,
                severity=Severity.HIGH if weakened else Severity.LOW,
                summary=f"{gid} on_violation "
                        f"{_enum_value(before.on_violation)} → "
                        f"{_enum_value(after.on_violation)}",
                rationale="a guardrail that flags still lets the content "
                          "through; one that blocks does not",
                before=before.on_violation, after=after.on_violation,
            )
        out.set_change(
            subject_kind=subject_kind, subject=subject,
            field=f"guardrails[{gid}].checks",
            before=before.checks, after=after.checks,
            widened_severity=Severity.LOW, narrowed_severity=Severity.HIGH,
            widened_why="one more thing checked for",
            narrowed_why="the guardrail no longer looks for this, so content "
                         "it used to stop now passes",
            noun="check", protective=True,
        )


def _diff_endpoints(out: _Collector, agent_id: str, left: Any, right: Any) -> None:
    old, new = _keyed(left), _keyed(right)
    for eid in sorted(new.keys() - old.keys()):
        endpoint = new[eid]
        out.add(
            subject_kind="agent", subject=agent_id, field="endpoints",
            kind=ChangeKind.ADDED, direction=Direction.WIDENED,
            severity=Severity.HIGH,
            summary=f"external agent endpoint added: {eid} "
                    f"({_enum_value(endpoint.trust)})",
            rationale="the system now talks to an agent outside it (ADR-0030)",
            after=eid,
        )
    for eid in sorted(old.keys() - new.keys()):
        out.add(
            subject_kind="agent", subject=agent_id, field="endpoints",
            kind=ChangeKind.REMOVED, direction=Direction.NARROWED,
            severity=Severity.MEDIUM,
            summary=f"external agent endpoint removed: {eid}",
            rationale="one fewer outside party in the loop",
            before=eid,
        )
    for eid in sorted(old.keys() & new.keys()):
        before, after = old[eid], new[eid]
        b, a = _rank(_TRUST_ORDER, before.trust), _rank(_TRUST_ORDER, after.trust)
        if b != a:
            raised = a > b
            out.add(
                subject_kind="agent", subject=agent_id,
                field=f"endpoints[{eid}].trust",
                kind=ChangeKind.MODIFIED,
                direction=Direction.WIDENED if raised else Direction.NARROWED,
                severity=Severity.CRITICAL if raised else Severity.LOW,
                summary=f"{eid} trust {_enum_value(before.trust)} → "
                        f"{_enum_value(after.trust)}",
                rationale="raising trust means this party's output may be acted "
                          "on rather than read as untrusted data",
                before=before.trust, after=after.trust,
            )
        if before.treat_output_as_data and not after.treat_output_as_data:
            out.add(
                subject_kind="agent", subject=agent_id,
                field=f"endpoints[{eid}].treat_output_as_data",
                kind=ChangeKind.MODIFIED, direction=Direction.WIDENED,
                severity=Severity.CRITICAL,
                summary=f"{eid} output is no longer treated as data",
                rationale="an outside party's text can now reach the agent as "
                          "instructions — a prompt-injection path",
                before=True, after=False,
            )
        out.set_change(
            subject_kind="agent", subject=agent_id,
            field=f"endpoints[{eid}].send_data_classes",
            before=before.send_data_classes, after=after.send_data_classes,
            widened_severity=Severity.HIGH, narrowed_severity=Severity.LOW,
            widened_why="classified data may now be sent outside the system",
            narrowed_why="one fewer data class leaves the system",
            noun="outbound data class",
        )
        if before.requires_approval and not after.requires_approval:
            out.add(
                subject_kind="agent", subject=agent_id,
                field=f"endpoints[{eid}].requires_approval",
                kind=ChangeKind.MODIFIED, direction=Direction.WIDENED,
                severity=Severity.HIGH,
                summary=f"{eid} no longer requires approval",
                rationale="a human was in this loop and is not any more",
                before=True, after=False,
            )


def _diff_model(out: _Collector, agent_id: str, left: AgentIR, right: AgentIR) -> None:
    before, after = left.model_approval, right.model_approval
    if before is None or after is None:
        return
    if (before.provider, before.model) != (after.provider, after.model):
        out.add(
            subject_kind="agent", subject=agent_id, field="model",
            kind=ChangeKind.MODIFIED, direction=Direction.NEUTRAL,
            severity=Severity.MEDIUM,
            summary=f"model {before.provider}/{before.model} → "
                    f"{after.provider}/{after.model}",
            rationale="a different model is a different data processor and a "
                      "different behaviour profile (ADR-0040)",
            before=f"{before.provider}/{before.model}",
            after=f"{after.provider}/{after.model}",
        )
    if before.approved and not after.approved:
        out.add(
            subject_kind="agent", subject=agent_id, field="model.approved",
            kind=ChangeKind.MODIFIED, direction=Direction.WIDENED,
            severity=Severity.CRITICAL,
            summary=f"model {after.model} is not approved: {after.approval_reason}",
            rationale="the agent would run on a model the catalog does not "
                      "permit, which is governance bypassed rather than applied",
            before=True, after=False,
        )
    elif not before.approved and after.approved:
        out.add(
            subject_kind="agent", subject=agent_id, field="model.approved",
            kind=ChangeKind.MODIFIED, direction=Direction.NARROWED,
            severity=Severity.LOW,
            summary=f"model {after.model} is now approved",
            rationale="back inside the catalog",
            before=False, after=True,
        )
    if before.subagent_approved and not after.subagent_approved:
        out.add(
            subject_kind="agent", subject=agent_id, field="model.subagent_approved",
            kind=ChangeKind.MODIFIED, direction=Direction.WIDENED,
            severity=Severity.HIGH,
            summary=f"sub-agent model is not approved: "
                    f"{after.subagent_approval_reason}",
            rationale="sub-agents inherit the agent's reach; their model is "
                      "governed too",
            before=True, after=False,
        )
    if not before.fallback_applied and after.fallback_applied:
        out.add(
            subject_kind="agent", subject=agent_id, field="model.fallback_applied",
            kind=ChangeKind.MODIFIED, direction=Direction.NEUTRAL,
            severity=Severity.MEDIUM,
            summary=f"model fallback applied: {after.requested_model} → "
                    f"{after.model} ({after.fallback_reason})",
            rationale="the binding asked for one model and got another",
            before=after.requested_model, after=after.model,
        )


def _diff_data_access(out: _Collector, agent_id: str, left: AgentIR, right: AgentIR) -> None:
    old = {d.data_class: d for d in left.data_access}
    new = {d.data_class: d for d in right.data_access}
    for dc in sorted(new.keys() - old.keys()):
        entry = new[dc]
        out.add(
            subject_kind="agent", subject=agent_id, field="data_access",
            kind=ChangeKind.ADDED, direction=Direction.WIDENED,
            severity=Severity.HIGH,
            summary=f"data access gained: {dc} "
                    f"({'read' if entry.read else ''}"
                    f"{'/write' if entry.write else ''})",
            rationale="the agent can now see or change a class of data it "
                      "could not before",
            after=dc,
        )
    for dc in sorted(old.keys() - new.keys()):
        out.add(
            subject_kind="agent", subject=agent_id, field="data_access",
            kind=ChangeKind.REMOVED, direction=Direction.NARROWED,
            severity=Severity.MEDIUM,
            summary=f"data access lost: {dc}",
            rationale="the agent can no longer reach this data",
            before=dc,
        )
    for dc in sorted(old.keys() & new.keys()):
        before, after = old[dc], new[dc]
        if not before.write and after.write:
            out.add(
                subject_kind="agent", subject=agent_id,
                field=f"data_access[{dc}].write",
                kind=ChangeKind.MODIFIED, direction=Direction.WIDENED,
                severity=Severity.HIGH,
                summary=f"{dc} access is now writable",
                rationale="read access became write access",
                before=False, after=True,
            )
        b, a = _rank(_SCOPE_ORDER, before.scope), _rank(_SCOPE_ORDER, after.scope)
        if b != a:
            out.add(
                subject_kind="agent", subject=agent_id,
                field=f"data_access[{dc}].scope",
                kind=ChangeKind.MODIFIED,
                direction=Direction.WIDENED if a > b else Direction.NARROWED,
                severity=Severity.HIGH if a > b else Severity.LOW,
                summary=f"{dc} sharing scope {_enum_value(before.scope)} → "
                        f"{_enum_value(after.scope)}",
                rationale="how widely this data may travel",
                before=before.scope, after=after.scope,
            )


def _diff_agent(
    out: _Collector, left: AgentIR, right: AgentIR,
    *, skip_guardrails: frozenset[str] = frozenset(),
) -> None:
    agent_id = left.id
    # An id reused for a different agent: everything else in the comparison
    # would read as an edit when it is really a replacement.
    if left.name != right.name and left.team_id != right.team_id:
        out.add(
            subject_kind="agent", subject=agent_id, field="identity",
            kind=ChangeKind.REUSED, direction=Direction.NEUTRAL,
            severity=Severity.HIGH,
            summary=f"id '{agent_id}' now names a different agent: "
                    f"'{left.name}' in {left.team_id} → '{right.name}' in "
                    f"{right.team_id}",
            rationale="the id survived but its subject did not; the changes "
                      "below are a replacement, not an edit",
            before=left.name, after=right.name,
        )
    elif left.team_id != right.team_id:
        out.add(
            subject_kind="agent", subject=agent_id, field="team",
            kind=ChangeKind.MOVED, direction=Direction.NEUTRAL,
            severity=Severity.HIGH,
            summary=f"moved team: {'/'.join(left.team_path)} → "
                    f"{'/'.join(right.team_path)}",
            rationale="team permissions inherit downwards, so a move changes "
                      "what this agent holds and who it reports to",
            before=left.team_id, after=right.team_id,
        )
    if left.name != right.name and left.team_id == right.team_id:
        out.add(
            subject_kind="agent", subject=agent_id, field="name",
            kind=ChangeKind.MODIFIED, severity=Severity.INFO,
            summary=f"renamed: {left.name} → {right.name}", before=left.name,
            after=right.name,
        )
    if left.description != right.description:
        out.add(
            subject_kind="agent", subject=agent_id, field="description",
            kind=ChangeKind.MODIFIED, severity=Severity.INFO,
            summary="description changed", rationale="cosmetic; no resolved "
                                                     "access moved",
            before=left.description, after=right.description,
        )

    _diff_permissions(out, "agent", agent_id, left.permissions, right.permissions)
    # An agent may run in several sandboxes (ADR-0082). Each is diffed
    # against the one with the same id on the other side, because a sandbox
    # gained or lost is a different change from one whose posture moved.
    left_envs = {e.id: e for e in left.environments}
    right_envs = {e.id: e for e in right.environments}
    for env_id in sorted(set(left_envs) | set(right_envs)):
        _diff_environment(out, agent_id, left_envs.get(env_id),
                          right_envs.get(env_id))
    # A guardrail added or removed system-wide lands on every agent; it is
    # reported once against the system instead of once per agent.
    _diff_guardrails(
        out, "agent", agent_id,
        [g for g in left.guardrails if g.id not in skip_guardrails],
        [g for g in right.guardrails if g.id not in skip_guardrails],
    )
    _diff_endpoints(out, agent_id, left.endpoints, right.endpoints)
    _diff_model(out, agent_id, left, right)
    _diff_data_access(out, agent_id, left, right)

    out.set_change(
        subject_kind="agent", subject=agent_id, field="capabilities",
        before=[c.id for c in left.capabilities],
        after=[c.id for c in right.capabilities],
        widened_severity=Severity.MEDIUM, narrowed_severity=Severity.MEDIUM,
        widened_why="one more thing this agent may do",
        narrowed_why="a capability this agent relied on is gone",
        noun="capability",
    )
    out.set_change(
        subject_kind="agent", subject=agent_id, field="delegates_to",
        before=left.delegates_to, after=right.delegates_to,
        widened_severity=Severity.MEDIUM, narrowed_severity=Severity.LOW,
        widened_why="work — and the context that comes with it — can now reach "
                    "another agent",
        narrowed_why="one fewer delegation path",
        noun="delegation target",
    )
    out.set_change(
        subject_kind="agent", subject=agent_id, field="requires_approval_for",
        before=left.requires_approval_for, after=right.requires_approval_for,
        widened_severity=Severity.LOW, narrowed_severity=Severity.HIGH,
        widened_why="one more action a human must sign off",
        narrowed_why="an action that needed a human no longer does",
        noun="approval requirement", protective=True,
    )
    out.set_change(
        subject_kind="agent", subject=agent_id, field="missions",
        before=left.missions, after=right.missions,
        widened_severity=Severity.MEDIUM, narrowed_severity=Severity.LOW,
        widened_why="a mission lends lateral reach for its window (ADR-0039)",
        narrowed_why="one fewer mission",
        noun="mission",
    )
    out.set_change(
        subject_kind="agent", subject=agent_id, field="memory.redact_data_classes",
        before=left.memory.redact_data_classes,
        after=right.memory.redact_data_classes,
        widened_severity=Severity.LOW, narrowed_severity=Severity.HIGH,
        widened_why="one more class redacted out of memory",
        narrowed_why="a class that was redacted out of memory is now retained",
        noun="memory redaction", protective=True,
    )
    # A data contract that moves is a governance change like any other, and
    # was previously invisible: the diff could say which agents an authority
    # change affected and nothing could say it for a change to what an agent
    # relies on (ADR-0099).
    def _dep_key(dependency: Any) -> str:
        fields = ",".join(sorted(dependency.fields)) or "*"
        return (f"{dependency.data_class}[{fields}]"
                f"@{dependency.max_age_seconds or '—'}"
                f"/{dependency.on_stale.value}")

    before_deps = {d.data_class: d for d in left.data_dependencies}
    after_deps = {d.data_class: d for d in right.data_dependencies}
    for class_id in sorted(set(before_deps) | set(after_deps)):
        was, now = before_deps.get(class_id), after_deps.get(class_id)
        if was is not None and now is not None and _dep_key(was) == _dep_key(now):
            continue
        if was is None:
            out.add(
                subject_kind="agent", subject=agent_id,
                field=f"data_dependencies.{class_id}", kind=ChangeKind.ADDED,
                direction=Direction.WIDENED, severity=Severity.MEDIUM,
                summary=f"now relies on '{class_id}' ({_dep_key(now)})",
                rationale="a new reliance: a change to this data is now a "
                          "change to this agent",
                before=None, after=_dep_key(now),
            )
            continue
        if now is None:
            out.add(
                subject_kind="agent", subject=agent_id,
                field=f"data_dependencies.{class_id}", kind=ChangeKind.REMOVED,
                direction=Direction.NARROWED, severity=Severity.LOW,
                summary=f"no longer relies on '{class_id}'",
                rationale="one fewer thing that can break this agent",
                before=_dep_key(was), after=None,
            )
            continue
        # A shorter freshness window is a stricter promise to keep, so it is
        # the direction that can newly refuse work at run time.
        stricter = (
            (now.max_age_seconds or 0) and
            (not was.max_age_seconds or now.max_age_seconds < was.max_age_seconds)
        )
        out.add(
            subject_kind="agent", subject=agent_id,
            field=f"data_dependencies.{class_id}", kind=ChangeKind.MODIFIED,
            direction=Direction.NARROWED if stricter else Direction.WIDENED,
            severity=Severity.MEDIUM,
            summary=f"reliance on '{class_id}': {_dep_key(was)} → {_dep_key(now)}",
            rationale="what this agent counts on about this data has moved",
            before=_dep_key(was), after=_dep_key(now),
        )

    if left.max_delegation_depth != right.max_delegation_depth:
        deeper = right.max_delegation_depth > left.max_delegation_depth
        out.add(
            subject_kind="agent", subject=agent_id, field="max_delegation_depth",
            kind=ChangeKind.MODIFIED,
            direction=Direction.WIDENED if deeper else Direction.NARROWED,
            severity=Severity.MEDIUM if deeper else Severity.LOW,
            summary=f"delegation depth {left.max_delegation_depth} → "
                    f"{right.max_delegation_depth}",
            rationale="how far a task may travel from the agent that accepted it",
            before=left.max_delegation_depth, after=right.max_delegation_depth,
        )
    out.set_change(
        subject_kind="agent", subject=agent_id, field="humans",
        before=[h.contact for h in left.humans],
        after=[h.contact for h in right.humans],
        widened_severity=Severity.LOW, narrowed_severity=Severity.MEDIUM,
        widened_why="one more person accountable for this agent",
        narrowed_why="a person accountable for this agent is gone (ADR-0026)",
        noun="human counterpart",
    )


def _diff_identities(out: _Collector, left: SystemIR, right: SystemIR) -> None:
    old, new = _keyed(left.identities), _keyed(right.identities)
    for iid in sorted(new.keys() - old.keys()):
        out.add(
            subject_kind="identity", subject=iid, field="identity",
            kind=ChangeKind.ADDED, direction=Direction.WIDENED,
            severity=Severity.MEDIUM,
            summary=f"workload identity added for {new[iid].agent_id}",
            rationale="a new principal exists in the deployment (ADR-0015)",
            after=iid,
        )
    for iid in sorted(old.keys() - new.keys()):
        out.add(
            subject_kind="identity", subject=iid, field="identity",
            kind=ChangeKind.REMOVED, direction=Direction.NARROWED,
            severity=Severity.MEDIUM,
            summary=f"workload identity removed ({old[iid].agent_id})",
            rationale="one fewer principal", before=iid,
        )
    for iid in sorted(old.keys() & new.keys()):
        out.set_change(
            subject_kind="identity", subject=iid, field="secret_refs",
            before=old[iid].secret_refs, after=new[iid].secret_refs,
            widened_severity=Severity.CRITICAL, narrowed_severity=Severity.LOW,
            widened_why="this identity now holds a credential it did not before "
                        "— the blast radius of the agent is whatever that secret "
                        "opens",
            narrowed_why="one fewer credential held here",
            noun="secret reference",
        )
        if old[iid].agent_id != new[iid].agent_id:
            out.add(
                subject_kind="identity", subject=iid, field="agent_id",
                kind=ChangeKind.REUSED, direction=Direction.WIDENED,
                severity=Severity.CRITICAL,
                summary=f"identity now belongs to a different agent: "
                        f"{old[iid].agent_id} → {new[iid].agent_id}",
                rationale="whatever this identity holds, including its secrets, "
                          "has changed hands",
                before=old[iid].agent_id, after=new[iid].agent_id,
            )


def _diff_system_level(
    out: _Collector, left: SystemIR, right: SystemIR
) -> frozenset[str]:
    if left.name != right.name:
        out.add(
            subject_kind="system", subject=left.name, field="name",
            kind=ChangeKind.MODIFIED, severity=Severity.LOW,
            summary=f"system renamed: {left.name} → {right.name}",
            rationale="same target and tenant, so this is a rename rather than "
                      "a different system",
            before=left.name, after=right.name,
        )
    if left.environment != right.environment:
        out.add(
            subject_kind="system", subject=left.name, field="environment",
            kind=ChangeKind.MODIFIED, direction=Direction.NEUTRAL,
            severity=Severity.MEDIUM,
            summary=f"deployment environment {left.environment} → "
                    f"{right.environment}",
            before=left.environment, after=right.environment,
        )
    _diff_guardrails(out, "system", left.name, left.guardrails, right.guardrails)
    system_guardrails = {g.id for g in left.guardrails} ^ {g.id for g in right.guardrails}

    old_policies = _keyed(left.policies)
    new_policies = _keyed(right.policies)
    for pid in sorted(old_policies.keys() - new_policies.keys()):
        deny = _enum_value(old_policies[pid].effect) == "deny"
        out.add(
            subject_kind="policy", subject=pid, field="policies",
            kind=ChangeKind.REMOVED,
            direction=Direction.WIDENED if deny else Direction.NARROWED,
            severity=Severity.CRITICAL if deny else Severity.MEDIUM,
            summary=f"{'deny' if deny else 'allow'} policy removed: {pid}",
            rationale="a deny always wins and cannot be overridden; removing "
                      "one lets through everything it was stopping (ADR-0008)",
            before=pid,
        )
    for pid in sorted(new_policies.keys() - old_policies.keys()):
        deny = _enum_value(new_policies[pid].effect) == "deny"
        out.add(
            subject_kind="policy", subject=pid, field="policies",
            kind=ChangeKind.ADDED,
            direction=Direction.NARROWED if deny else Direction.WIDENED,
            severity=Severity.LOW if deny else Severity.MEDIUM,
            summary=f"{'deny' if deny else 'allow'} policy added: {pid}",
            rationale="a new explicit rule in the decision path",
            after=pid,
        )

    old_k, new_k = _keyed(left.knowledge), _keyed(right.knowledge)
    for kid in sorted(old_k.keys() & new_k.keys()):
        before, after = old_k[kid], new_k[kid]
        if before.secret_ref != after.secret_ref and after.secret_ref:
            out.add(
                subject_kind="knowledge", subject=kid, field="secret_ref",
                kind=ChangeKind.MODIFIED, direction=Direction.WIDENED,
                severity=Severity.CRITICAL,
                summary=f"knowledge source {kid} now reads with secret "
                        f"{after.secret_ref}",
                rationale="a credential moved into a grounding source",
                before=before.secret_ref, after=after.secret_ref,
            )
        out.set_change(
            subject_kind="knowledge", subject=kid, field="data_classes",
            before=before.data_classes, after=after.data_classes,
            widened_severity=Severity.HIGH, narrowed_severity=Severity.LOW,
            widened_why="this source now carries classified data into context",
            narrowed_why="one fewer class in this source",
            noun="data class",
        )

    old_c, new_c = _keyed(left.channels), _keyed(right.channels)
    for cid in sorted(old_c.keys() & new_c.keys()):
        out.set_change(
            subject_kind="channel", subject=cid, field="forbid_data_classes",
            before=old_c[cid].forbid_data_classes,
            after=new_c[cid].forbid_data_classes,
            widened_severity=Severity.LOW, narrowed_severity=Severity.HIGH,
            widened_why="one more class barred from this channel",
            narrowed_why="a class that was barred from this channel may now "
                         "be posted to it",
            noun="forbidden data class", protective=True,
        )
    return frozenset(system_guardrails)


def diff_ir(left: SystemIR, right: SystemIR, *, force: bool = False) -> IRDiff:
    """Compare two IRs of the same system and rank what moved by consequence.

    `force` overrides the comparability refusal for a caller who knows the two
    IRs really are two versions of one thing (a deliberate re-target, say).
    """
    if not force:
        assert_comparable(left, right)

    out = _Collector()
    system_guardrails = _diff_system_level(out, left, right)
    _diff_identities(out, left, right)

    old_teams, new_teams = _keyed(left.teams), _keyed(right.teams)
    for tid in sorted(new_teams.keys() - old_teams.keys()):
        out.add(
            subject_kind="team", subject=tid, field="team",
            kind=ChangeKind.ADDED, severity=Severity.MEDIUM,
            summary=f"team added: {new_teams[tid].name}", after=tid,
        )
    for tid in sorted(old_teams.keys() - new_teams.keys()):
        out.add(
            subject_kind="team", subject=tid, field="team",
            kind=ChangeKind.REMOVED, severity=Severity.MEDIUM,
            summary=f"team removed: {old_teams[tid].name}", before=tid,
        )
    for tid in sorted(old_teams.keys() & new_teams.keys()):
        before, after = old_teams[tid], new_teams[tid]
        if before.parent_id != after.parent_id:
            out.add(
                subject_kind="team", subject=tid, field="parent",
                kind=ChangeKind.MOVED, direction=Direction.NEUTRAL,
                severity=Severity.HIGH,
                summary=f"team moved: parent {before.parent_id} → "
                        f"{after.parent_id}",
                rationale="a team inherits its parent's permissions, so moving "
                          "one re-resolves everything beneath it",
                before=before.parent_id, after=after.parent_id,
            )
        if before.leader_agent_id != after.leader_agent_id:
            out.add(
                subject_kind="team", subject=tid, field="leader",
                kind=ChangeKind.MODIFIED, direction=Direction.NEUTRAL,
                severity=Severity.MEDIUM,
                summary=f"team leader {before.leader_agent_id} → "
                        f"{after.leader_agent_id}",
                rationale="the leader is who the team delegates through and "
                          "escalates to",
                before=before.leader_agent_id, after=after.leader_agent_id,
            )
        _diff_permissions(out, "team", tid, before.permissions, after.permissions)

    old_agents, new_agents = _keyed(left.agents), _keyed(right.agents)
    for aid in sorted(new_agents.keys() - old_agents.keys()):
        agent = new_agents[aid]
        out.add(
            subject_kind="agent", subject=aid, field="agent",
            kind=ChangeKind.ADDED, direction=Direction.WIDENED,
            severity=Severity.MEDIUM,
            summary=f"agent added: {agent.name} in "
                    f"{'/'.join(agent.team_path)} with "
                    f"{len(agent.permissions)} permission(s)",
            rationale="a new principal doing work; its resolved grants are its "
                      "own finding only if they exceed what the team held",
            after=aid,
        )
    for aid in sorted(old_agents.keys() - new_agents.keys()):
        agent = old_agents[aid]
        out.add(
            subject_kind="agent", subject=aid, field="agent",
            kind=ChangeKind.REMOVED, direction=Direction.NARROWED,
            severity=Severity.MEDIUM,
            summary=f"agent removed: {agent.name} (was in "
                    f"{'/'.join(agent.team_path)})",
            rationale="whatever depended on it — delegation, triggers, "
                      "escalation — now has nowhere to land",
            before=aid,
        )
    for aid in sorted(old_agents.keys() & new_agents.keys()):
        _diff_agent(out, old_agents[aid], new_agents[aid],
                    skip_guardrails=system_guardrails)

    changes = sorted(
        out.changes,
        key=lambda c: (
            not c.security_relevant,
            _SEVERITY_ORDER[c.severity],
            c.subject_kind,
            c.subject,
            c.field,
            c.summary,
        ),
    )
    return IRDiff(
        system=right.name,
        target=right.target,
        left_spec_version=left.spec_version,
        right_spec_version=right.spec_version,
        changes=changes,
    )
