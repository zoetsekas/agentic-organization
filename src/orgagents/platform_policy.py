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

import hashlib
import json
from datetime import date, timedelta
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

SEVERITIES = ("error", "warning", "ignore")
ACTIONS = ("drafted", "reviewed", "amended", "approved", "retired")

#: Fields that change what a design is judged by. Editing one of these without
#: a new version would make a verdict mean something different from what it
#: meant last week, which is why the fingerprint covers exactly this set
#: (ADR-0077, following ADR-0062's editorial/substantive split).
SUBSTANTIVE = (
    "treat_as", "severity", "reasons", "require_declared",
    "forbid_autonomy_over", "max_autonomy",
)


class PolicyStatus(str, Enum):
    """Where a platform policy is in its life (ADR-0077)."""

    #: Being written. May be evaluated so its author can see what it does.
    DRAFT = "draft"
    #: Submitted, not yet approved. Same: may be tried, may not judge a build.
    IN_REVIEW = "in_review"
    #: Somebody accountable accepted it. The only state that may judge a build.
    APPROVED = "approved"
    #: Withdrawn. Judges nothing, and stays readable because the verdicts it
    #: produced are still on record.
    RETIRED = "retired"

#: Blocks a policy may require a design to have declared, and how to find them.
REQUIRABLE = {
    "separations": lambda s: s.separations,
    "guardrails": lambda s: s.guardrails,
    "decisions": lambda s: s.decisions,
    "evaluations": lambda s: s.lifecycle.evaluations,
    "output_contracts": lambda s: s.output_contracts,
    "observability_alerts": lambda s: s.observability.alerts,
}


class PolicyChange(BaseModel):
    """One recorded act on a policy (ADR-0078).

    History here is not a log the platform keeps — a policy is a document, and
    nothing watches it being edited. What makes it worth having is that it is
    **checked against the document it describes**: the newest entry must name
    this version and this fingerprint, so a substantive edit that nobody
    recorded is refused rather than merely undocumented.
    """

    version: str
    #: The fingerprint as of this act. Required for anything that changed the
    #: substance, because that is the half a version cannot prove.
    fingerprint: str = ""
    at: str                       # ISO date
    by: str
    action: str                   # drafted | reviewed | amended | approved | retired
    note: str = ""

    @model_validator(mode="after")
    def _check(self) -> "PolicyChange":
        if self.action not in ACTIONS:
            raise ValueError(
                f"policy change records action '{self.action}', which is not "
                f"one of {ACTIONS}"
            )
        try:
            date.fromisoformat(self.at)
        except ValueError as exc:
            raise ValueError(
                f"policy change is dated '{self.at}', which is not a date"
            ) from exc
        if not self.by:
            raise ValueError(
                f"a policy was {self.action} on {self.at} by nobody. An "
                "unattributed change is the thing this history exists to "
                "prevent (ADR-0078)"
            )
        return self


class PlatformPolicy(BaseModel):
    """House rules a fabric applies to every design it builds."""

    id: str
    #: Semver. Stamped into the IR so a verdict can be re-checked later.
    version: str = "0.1.0"
    description: str = ""

    # -- lifecycle (ADR-0077) ----------------------------------------------
    status: PolicyStatus = PolicyStatus.DRAFT
    #: Who accepted it. A policy approved by nobody is a file.
    approved_by: str = ""
    #: ISO date. With `review_interval_days`, decides staleness.
    approved_on: Optional[str] = None
    #: How long an approval is good for. `None` means it does not lapse —
    #: opting in is choosing the behaviour, so a fabric that sets one means it.
    review_interval_days: Optional[int] = None
    #: The policy version this replaces, for the reader. One-way on purpose:
    #: a superseded policy is retired, and retirement is the machine-readable
    #: half.
    supersedes: str = ""
    #: Append-only, oldest first. Checked against the document it describes.
    history: list[PolicyChange] = Field(default_factory=list)

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
        if self.status is PolicyStatus.APPROVED and not (
            self.approved_by and self.approved_on
        ):
            raise ValueError(
                f"platform policy '{self.id}' is approved and does not say by "
                "whom or when. An approval nobody signed and nothing dates is "
                "not an approval (ADR-0077)"
            )
        self._check_history()
        if self.review_interval_days is not None and self.review_interval_days < 1:
            raise ValueError(
                f"platform policy '{self.id}' has a review interval of "
                f"{self.review_interval_days} days, which is not an interval"
            )
        unknown = [b for b in self.require_declared if b not in REQUIRABLE]
        if unknown:
            raise ValueError(
                f"platform policy '{self.id}' requires {unknown}, which this "
                f"platform cannot check. Known blocks: {sorted(REQUIRABLE)}"
            )
        return self

    def _check_history(self) -> None:
        """Hold the history to the document, not the other way round."""
        if not self.history:
            if self.status is PolicyStatus.APPROVED:
                raise ValueError(
                    f"platform policy '{self.id}' is approved and records no "
                    "history. Who wrote these rules, and who accepted them, is "
                    "the question an auditor asks first (ADR-0078)"
                )
            return

        dates = [date.fromisoformat(c.at) for c in self.history]
        if dates != sorted(dates):
            raise ValueError(
                f"platform policy '{self.id}' records its history out of "
                "order; it is append-only, oldest first"
            )

        # A version is immutable: two entries naming it may not disagree about
        # what it contained (ADR-0062's rule, applied one layer up).
        seen: dict[str, str] = {}
        for change in self.history:
            if not change.fingerprint:
                continue
            prior = seen.setdefault(change.version, change.fingerprint)
            if prior != change.fingerprint:
                raise ValueError(
                    f"platform policy '{self.id}' records version "
                    f"'{change.version}' with two different fingerprints. A "
                    "version is immutable: changed substance is a new version"
                )

        head = self.history[-1]
        if head.version != self.version:
            raise ValueError(
                f"platform policy '{self.id}' is version '{self.version}' and "
                f"its newest recorded change is '{head.version}'. Record the "
                "change, or restore the version it describes"
            )
        if head.fingerprint and head.fingerprint != self.fingerprint:
            raise ValueError(
                f"platform policy '{self.id}' version '{self.version}' has "
                f"fingerprint '{self.fingerprint}' and its history records "
                f"'{head.fingerprint}'. Something substantive changed and "
                "nobody recorded it — which is exactly what a version alone "
                "cannot tell you (ADR-0078)"
            )

        if self.status is PolicyStatus.APPROVED:
            approvals = [
                c for c in self.history
                if c.action == "approved" and c.version == self.version
            ]
            if not approvals:
                raise ValueError(
                    f"platform policy '{self.id}' is approved and its history "
                    f"records no approval of version '{self.version}'"
                )
            last = approvals[-1]
            if (self.approved_by, self.approved_on) != (last.by, last.at):
                raise ValueError(
                    f"platform policy '{self.id}' says it was approved by "
                    f"'{self.approved_by}' on {self.approved_on}, and its "
                    f"history records '{last.by}' on {last.at}. The signature "
                    "and the recorded act are the same fact"
                )

    @property
    def last_change(self) -> Optional[PolicyChange]:
        return self.history[-1] if self.history else None

    @property
    def stamp(self) -> str:
        return f"{self.id}/{self.version}"

    @property
    def fingerprint(self) -> str:
        """A hash over everything that decides a verdict.

        A version is a name somebody types and can be typed again over changed
        substance. The fingerprint is what makes "this passed `house/1.0.0`"
        checkable rather than asserted: two builds claiming the same version
        with different fingerprints are visibly not the same rules.
        """
        material = json.dumps(
            {k: getattr(self, k) for k in SUBSTANTIVE},
            sort_keys=True, default=str,
        )
        return hashlib.sha256(material.encode()).hexdigest()[:12]

    def review_due(self) -> Optional[date]:
        if self.review_interval_days is None or not self.approved_on:
            return None
        return date.fromisoformat(self.approved_on) + timedelta(
            days=self.review_interval_days
        )

    def is_stale(self, on: Optional[date] = None) -> bool:
        due = self.review_due()
        return due is not None and (on or date.today()) > due

    def refusal(self, on: Optional[date] = None) -> str:
        """Why this policy may not judge a build, or an empty string.

        A draft may be *evaluated* — an author has to be able to see what a
        rule would do before asking anybody to approve it — and may not stamp
        a build. The two are different questions and collapsing them would
        make the policy untestable.
        """
        if self.status is PolicyStatus.RETIRED:
            return f"platform policy '{self.stamp}' is retired and judges nothing"
        if self.status is not PolicyStatus.APPROVED:
            return (
                f"platform policy '{self.stamp}' is {self.status.value} and has "
                "not been approved. Evaluate a design against it freely; it may "
                "not decide whether one is built"
            )
        if self.is_stale(on):
            return (
                f"platform policy '{self.stamp}' was approved on "
                f"{self.approved_on} and was due for review by "
                f"{self.review_due().isoformat()}. House rules nobody has "
                "confirmed still apply are not house rules — re-approve it, or "
                "drop the review interval if it was not meant"
            )
        return ""

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
        yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
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
