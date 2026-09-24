"""People who have left (ADR-0047): pairings the directory disputes."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..model import SystemSpec
from .context import ValidationContext
from .findings import Finding, Severity
from .registry import rule

if TYPE_CHECKING:  # a runtime import would close a cycle: directory reads this package
    from ...directory import Directory

CODES = {"departed_owner", "departed_human", "human_unknown_to_directory"}


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
    from ...directory import reconcile  # deferred: `directory` imports this package

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
            out.append(Finding(
                severity, "departed_owner" if issue.is_owner else "departed_human",
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


@rule("people who have left", CODES)
def directory_agrees(ctx: ValidationContext) -> None:
    ctx.out.extend(directory_findings(ctx.spec, ctx.directory))
