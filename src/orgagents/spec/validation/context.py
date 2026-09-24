"""What every rule reads: the spec, the indexes built over it, and the sink.

The single-function validator built these up front and closed over them; a
rule reads them from here instead. The resolved mandate map is computed once,
on first use, because resolving authority walks the whole organization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, Optional

from ..model import (
    AgentSpec,
    Capability,
    DataClass,
    Role,
    SystemSpec,
    Team,
    WorkflowSpec,
)
from .findings import Finding, Severity

if TYPE_CHECKING:  # a runtime import would close a cycle: directory reads this package
    from ...directory import Directory
    from ...mandates import MandateMap


@dataclass
class ValidationContext:
    spec: SystemSpec
    directory: Optional["Directory"] = None
    platform_policy: Optional[Any] = None
    out: list[Finding] = field(default_factory=list)

    def __post_init__(self) -> None:
        spec, policy = self.spec, self.platform_policy
        # Strictness is the fabric's call where a policy is in force (ADR-0076).
        # `metadata.environment` is a field the design declares about itself, so
        # a design that called itself development was simply not judged by the
        # strict rules.
        self.production: bool = (
            policy.treat_as == "production"
            if policy and policy.treat_as
            else spec.metadata.environment == "production"
        )
        self.teams: list[Team] = spec.teams()
        self.agents: list[AgentSpec] = spec.agents()
        self.agent_ids: list[str] = [a.id for a in self.agents]
        self.team_ids: list[str] = [t.id for t in self.teams]
        self.agents_by_id: dict[str, AgentSpec] = {a.id: a for a in self.agents}
        self.teams_by_id: dict[str, Team] = {t.id: t for t in self.teams}
        self.declared_decisions: set[str] = {d.id for d in spec.decisions}
        self.classes_by_id: dict[str, DataClass] = {d.id: d for d in spec.data_classes}
        self.workflows_by_id: dict[str, WorkflowSpec] = {w.id: w for w in spec.workflows}
        self.person_ids: set[str] = {p.id for p in spec.people}
        self.role_ids: set[str] = {r.id for r in spec.roles}
        self.channel_ids: set[str] = {c.id for c in spec.channels}
        self.cap_by_id: dict[str, Capability] = {c.id: c for c in spec.capabilities}
        self.role_by_id: dict[str, Role] = {r.id: r for r in spec.roles}

    # -- the sink ------------------------------------------------------------

    def err(self, code: str, message: str, where: str = "") -> None:
        self.out.append(Finding("error", code, message, where))

    def warn(self, code: str, message: str, where: str = "",
             strict: bool = False) -> None:
        severity: Severity = "error" if (strict and self.production) else "warning"
        self.out.append(Finding(severity, code, message, where))

    # -- authority, resolved once ---------------------------------------------

    @cached_property
    def mandates(self) -> "MandateMap":
        """Every unit's and agent's effective authority (ADR-0065)."""
        from ...mandates import resolve

        return resolve(self.spec.organization, self.declared_decisions,
                       self.spec.people)

    @property
    def separation_mandates(self) -> Optional["MandateMap"]:
        """The resolved map where separations are declared, else None.

        The rules that compare two principals' authority only have something
        to compare when a separation of duties is declared (ADR-0070).
        """
        return self.mandates if self.spec.separations else None
