"""The fabric's own rules about designs (ADR-0076).

Not to be confused with `spec.policies`, which are a *design's* RBAC allow and
deny rules. This is the layer above: what the platform requires of any design
before it may be built, owned by the fabric and never by the design.

Three properties make it worth having as data rather than more Python in the
validator:

* **The fabric sets severity, not the design.** Fourteen built-in rules used to
  promote from warning to error on `metadata.environment == "production"` — a
  field the design declares about itself. A design that called itself
  development simply was not judged by the strict rules.
* **It is versioned, and the version is stamped into the IR.** "This passed
  platform policy `house/2.1.0`" is a fact somebody can re-check. Without it,
  a design that passed six months ago cannot be re-judged and nobody can say
  what passing meant at the time.
* **A lowered rule is loud.** Raising a severity needs no justification.
  Lowering one needs a reason and is always reported, because a control quietly
  switched off is the failure this whole layer exists to prevent.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

SEVERITIES = ("error", "warning", "ignore")

#: Blocks a policy may require a design to have declared, and how to find them.
REQUIRABLE = {
    "separations": lambda s: s.separations,
    "guardrails": lambda s: s.guardrails,
    "decisions": lambda s: s.decisions,
    "evaluations": lambda s: s.lifecycle.evaluations,
    "output_contracts": lambda s: s.output_contracts,
    "observability_alerts": lambda s: s.observability.alerts,
}


class PlatformPolicy(BaseModel):
    """House rules a fabric applies to every design it builds."""

    id: str
    #: Semver. Stamped into the IR so a verdict can be re-checked later.
    version: str = "0.1.0"
    description: str = ""

    #: Judge the design at this strictness whatever it declares about itself.
    #: `None` leaves `metadata.environment` deciding, which is the old
    #: behaviour and the reason this field exists.
    treat_as: Optional[str] = None

    #: Built-in finding code -> error | warning | ignore.
    severity: dict[str, str] = Field(default_factory=dict)
    #: Why a code was lowered. A lowering without one is refused.
    reasons: dict[str, str] = Field(default_factory=dict)

    #: Spec blocks that must be non-empty. See `REQUIRABLE`.
    require_declared: list[str] = Field(default_factory=list)
    #: Decision classes no agent may hold an autonomous posture over.
    forbid_autonomy_over: list[str] = Field(default_factory=list)
    #: A ceiling on autonomy across the whole design.
    max_autonomy: Optional[str] = None

    @model_validator(mode="after")
    def _check(self) -> "PlatformPolicy":
        for code, level in self.severity.items():
            if level not in SEVERITIES:
                raise ValueError(
                    f"platform policy '{self.id}': severity for '{code}' is "
                    f"'{level}', which is not one of {SEVERITIES}"
                )
            if level in ("warning", "ignore") and code not in self.reasons:
                raise ValueError(
                    f"platform policy '{self.id}' lowers '{code}' to '{level}' "
                    "and gives no reason. Raising a rule needs no justification; "
                    "lowering one does, and the reason is reported wherever the "
                    "policy is (ADR-0076)"
                )
        unknown = [b for b in self.require_declared if b not in REQUIRABLE]
        if unknown:
            raise ValueError(
                f"platform policy '{self.id}' requires {unknown}, which this "
                f"platform cannot check. Known blocks: {sorted(REQUIRABLE)}"
            )
        return self

    @property
    def stamp(self) -> str:
        return f"{self.id}/{self.version}"

    @property
    def lowered(self) -> dict[str, str]:
        """Rules this policy weakened, with the reason given for each."""
        return {
            code: self.reasons.get(code, "")
            for code, level in self.severity.items()
            if level in ("warning", "ignore")
        }


def load(path: str) -> PlatformPolicy:
    import yaml
    from pathlib import Path

    return PlatformPolicy.model_validate(
        yaml.safe_load(Path(path).read_text()) or {}
    )


def apply_severity(policy: Optional[PlatformPolicy], findings: list) -> list:
    """Re-rank findings, dropping the ones the policy ignores.

    An `ignore` really does remove the finding — a rule a fabric has decided
    not to run should not sit in the report as noise. What keeps that honest is
    `lowered`, which the gate reports separately, so the removal is visible in
    the one place a reader looks for what this policy changed.
    """
    if policy is None or not policy.severity:
        return findings
    out = []
    for f in findings:
        level = policy.severity.get(f.code)
        if level == "ignore":
            continue
        if level in ("error", "warning") and level != f.severity:
            f = type(f)(level, f.code, f.message, f.where)
        out.append(f)
    return out


def policy_findings(policy: Optional[PlatformPolicy], spec: Any) -> list:
    """What the fabric's own rules say about this design."""
    from .spec.model import AutonomyPosture, autonomy_rank
    from .spec.validate import Finding

    if policy is None:
        return []
    out: list[Finding] = []

    for block in policy.require_declared:
        if not REQUIRABLE[block](spec):
            out.append(Finding(
                "error", "platform_policy_requires",
                f"platform policy '{policy.stamp}' requires this design to "
                f"declare {block}, and it declares none",
                block,
            ))

    forbidden = set(policy.forbid_autonomy_over)
    ceiling = AutonomyPosture(policy.max_autonomy) if policy.max_autonomy else None
    if not forbidden and ceiling is None:
        return out

    caps = {c.id: c for c in spec.capabilities}
    roles = {r.id: r for r in spec.roles}

    def walk(team: Any, inherited: list) -> None:
        team_roles = inherited + [r.role for r in team.roles]
        for agent in team.members:
            held = set(agent.capabilities)
            for assignment in list(agent.roles) + list(team_roles):
                role = roles.get(getattr(assignment, "role", assignment))
                if role:
                    held.update(role.capabilities)
            for cap_id in sorted(c for c in held if c in caps):
                cap = caps[cap_id]
                posture = agent.autonomy.get(cap_id) or cap.autonomy
                where = f"{agent.id}/{cap_id}"
                if (posture is AutonomyPosture.AUTONOMOUS
                        and cap.decision in forbidden):
                    out.append(Finding(
                        "error", "platform_policy_forbids_autonomy",
                        f"platform policy '{policy.stamp}' does not allow an "
                        f"agent to run unattended over '{cap.decision}'",
                        where,
                    ))
                if ceiling is not None and autonomy_rank(posture) < autonomy_rank(ceiling):
                    out.append(Finding(
                        "error", "platform_policy_autonomy_ceiling",
                        f"{where} is '{posture.value}' and platform policy "
                        f"'{policy.stamp}' allows at most "
                        f"'{ceiling.value}' anywhere in this design",
                        where,
                    ))
        for child in team.teams:
            walk(child, team_roles)

    walk(spec.organization, [])
    return out
