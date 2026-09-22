"""Standing in for a leader that cannot run (ADR-0094).

A leader agent's authority is modelled carefully and its continuity was not
modelled at all. When one failed, its outstanding handles, its mandate and its
reports were left with no owner.

The shape of the answer turns on one fact the platform already establishes:
under ADR-0065 authority narrows downward, so **a manager already holds a
superset of its reports' mandates**. Standing in for a failed report costs the
manager nothing and grants it nothing — which is why that is the default, and
why everything in this module is about the other case: a *lateral* successor,
who does not already hold the mandate and therefore really is being handed
authority the org chart never gave it.

Three things bound that grant:

* it **expires**, evaluated per call, exactly as a mission's lateral reach does
  (ADR-0039), so a lapsed standing-in confers nothing whether or not anybody
  swept it;
* it **never carries a decision a separation of duties forbids** — those are
  withheld, named, and escalate to a human, while the rest proceeds;
* it is **visible**, because a decision that reads as the successor's own is a
  decision nobody can audit.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from .ids import new_id, now_iso
from .missions import window_is_open


class WithheldDecision(BaseModel):
    """A decision the successor may not take, and the rule that says so."""

    decision: str
    separation: str = ""
    reason: str = ""

    def describe(self) -> str:
        tail = f": {self.reason}" if self.reason else ""
        return (f"'{self.decision}' is withheld by separation "
                f"'{self.separation}'{tail}")


class ActingAssignment(BaseModel):
    """One agent standing in for another, for a while.

    Not a promotion and not a repair of the org chart. It is a record that for
    a named window, and for a named reason, somebody else may take some of a
    failed leader's decisions — and which ones they still may not.
    """

    id: str = Field(default_factory=lambda: new_id("act"))
    failed_agent_id: str
    successor_agent_id: str
    #: True when the successor is the failed agent's manager, who already held
    #: everything below it. Nothing is granted in that case, and there is
    #: nothing to expire.
    by_hierarchy: bool = False
    #: Decisions actually conferred. Empty on a `by_hierarchy` assignment,
    #: because the manager was already entitled to every one of them.
    decisions: list[str] = Field(default_factory=list)
    #: Decisions deliberately not conferred, each naming the rule.
    withheld: list[WithheldDecision] = Field(default_factory=list)
    reason: str = ""
    status: str = "active"          # active | closed
    started_at: str = Field(default_factory=now_iso)
    starts_on: str = ""             # ISO date; empty means "from now"
    ends_on: str = ""               # ISO date; empty means open-ended
    #: Handles the failed leader was holding when it stood down (ADR-0093).
    adopted_handles: list[str] = Field(default_factory=list)

    def is_open(self, on: Optional[date] = None) -> bool:
        """Whether this confers anything today.

        Evaluated per call rather than swept, for the same reason mission
        grants are: a window that closed confers nothing from the moment it
        closed, and a missed sweep should be untidy rather than unsafe.
        """
        if self.status != "active":
            return False
        return window_is_open("active", self.starts_on or None,
                              self.ends_on or None, on)

    def covers(self, decision: str, on: Optional[date] = None) -> bool:
        return self.is_open(on) and decision in self.decisions

    def describe(self) -> str:
        who = ("its manager, which granted nothing: it already held this "
               "mandate" if self.by_hierarchy else
               f"a lateral stand-in holding {len(self.decisions)} decision(s)")
        window = f" until {self.ends_on}" if self.ends_on else " open-ended"
        return (f"{self.successor_agent_id} acting for "
                f"{self.failed_agent_id} — {who}{window}")


def split_mandate(
    leader_mandate: Iterable[str],
    successor_holds: Iterable[str],
    separations: Iterable[dict[str, Any]],
) -> tuple[list[str], list[WithheldDecision]]:
    """Split a failed leader's mandate into what may and may not be conferred.

    A successor that already holds `approve_payment`, standing in for a leader
    that holds `raise_payment`, would hold both — collapsing a control that
    spec validation had *proved* impossible. The separation is the thing most
    under pressure in an outage and the thing least able to defend itself, so
    it wins: the conflicting decisions are withheld and everything else is
    conferred, rather than the whole succession being refused.

    Decisions are considered in sorted order and each granted one joins what
    the successor holds, so two halves of the same control can never both be
    conferred by this call.
    """
    held = set(successor_holds)
    rules = list(separations)
    granted: list[str] = []
    withheld: list[WithheldDecision] = []
    for decision in sorted(set(leader_mandate)):
        if decision in held:
            continue          # already theirs; nothing is being conferred
        conflict = next(
            (r for r in rules
             if decision in set(r.get("decisions", []))
             and (held & (set(r.get("decisions", [])) - {decision}))),
            None,
        )
        if conflict is not None:
            withheld.append(WithheldDecision(
                decision=decision,
                separation=str(conflict.get("id", "")),
                reason=str(conflict.get("reason", "")),
            ))
            continue
        granted.append(decision)
        held.add(decision)
    return granted, withheld


def open_assignment(
    *,
    failed_agent_id: str,
    successor_agent_id: str,
    leader_mandate: Iterable[str],
    successor_mandate: Iterable[str],
    separations: Iterable[dict[str, Any]] = (),
    by_hierarchy: bool = False,
    reason: str = "",
    ends_on: str = "",
) -> ActingAssignment:
    """Build the record of one agent standing in for another.

    `by_hierarchy` is the manager case: nothing is conferred, because nothing
    needs to be. Writing an empty grant rather than skipping the record is
    deliberate — "who was acting, and when" should be answerable the same way
    whichever kind of succession it was.
    """
    if by_hierarchy:
        return ActingAssignment(
            failed_agent_id=failed_agent_id,
            successor_agent_id=successor_agent_id,
            by_hierarchy=True,
            reason=reason,
        )
    granted, withheld = split_mandate(
        leader_mandate, successor_mandate, separations
    )
    return ActingAssignment(
        failed_agent_id=failed_agent_id,
        successor_agent_id=successor_agent_id,
        decisions=granted,
        withheld=withheld,
        reason=reason,
        ends_on=ends_on,
    )
