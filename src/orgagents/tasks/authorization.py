"""Who may give an agent work (ADR-0057 rule 5, ADR-0026).

Being able to edit a board is not authority to direct an agent. Assignment is
subject to the pairing model: the assigner must be one of the people paired
with *this* agent, in a capacity that directs it.

The check runs on our side of the port on purpose. We do not own the board, so
we cannot stop anyone creating a row on it; what we can do is refuse to act on
a row somebody who is not paired with the agent created.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from ..spec.model import AgentSpec, HumanCounterpart, HumanRole

#: The capacities that direct an agent. Reviewers receive output, stakeholders
#: are informed and escalation contacts are contacted — none of those is an
#: instruction-giving relationship, so none of them can open work.
ASSIGNING_ROLES = frozenset(
    {HumanRole.OWNER, HumanRole.APPROVER, HumanRole.OPERATOR}
)


class AssignmentDenied(PermissionError):
    """Raised when work was assigned by somebody who may not direct the agent."""

    def __init__(self, agent_id: str, assigner: str, reason: str) -> None:
        super().__init__(
            f"'{assigner or '<unknown>'}' may not assign work to agent "
            f"'{agent_id}': {reason}"
        )
        self.agent_id = agent_id
        self.assigner = assigner
        self.reason = reason


def _identities(human: Any) -> set[str]:
    """Every string a person is known by, across spec and runtime shapes."""
    return {
        str(v).strip().lower()
        for v in (
            getattr(human, "contact", None),
            getattr(human, "email", None),
            getattr(human, "user_id", None),
            getattr(human, "name", None),
            getattr(human, "display_name", None),
        )
        if v
    }


def _roles(human: Any) -> set[HumanRole]:
    roles = getattr(human, "roles", None)
    if roles:
        return {HumanRole(r) for r in roles}
    # A runtime counterpart carries no roles: it is the accountable owner.
    return {HumanRole.OWNER}


@dataclass(frozen=True)
class AgentPairing:
    """The people paired with one agent, and what each may do."""

    agent_id: str
    humans: tuple[Any, ...] = ()

    @classmethod
    def from_spec(cls, spec: AgentSpec) -> "AgentPairing":
        return cls(spec.id, tuple(spec.humans))

    @classmethod
    def from_humans(
        cls, agent_id: str, humans: Iterable[HumanCounterpart]
    ) -> "AgentPairing":
        return cls(agent_id, tuple(humans))

    def find(self, assigner: str) -> Optional[Any]:
        needle = (assigner or "").strip().lower()
        if not needle:
            return None
        return next((h for h in self.humans if needle in _identities(h)), None)

    def may_assign(self, assigner: str) -> bool:
        human = self.find(assigner)
        return human is not None and bool(_roles(human) & ASSIGNING_ROLES)

    def check(self, assigner: str) -> None:
        human = self.find(assigner)
        if human is None:
            raise AssignmentDenied(
                self.agent_id, assigner, "not paired with this agent"
            )
        roles = _roles(human)
        if not roles & ASSIGNING_ROLES:
            named = ", ".join(sorted(r.value for r in roles)) or "none"
            raise AssignmentDenied(
                self.agent_id,
                assigner,
                f"paired as {named}, which is not a capacity that directs it",
            )
