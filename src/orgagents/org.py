"""Organizational hierarchy: reporting lines, delegation and escalation."""
from __future__ import annotations

from datetime import date
from typing import Optional

from .continuity import ActingAssignment, open_assignment
from .ids import now_iso
from .missions import open_peers
from .models import Agent, AgentKind, OrgUnit
from .store import ACTING, AGENTS, ORG_UNITS, Store


class OrgChart:
    """Reads and mutates the reporting structure.

    Delegation rule: an agent may delegate *down* its own subtree, call
    registered peers laterally, and escalate *up* its management chain.
    Anything else has to go through a shared manager.
    """

    def __init__(self, store: Store) -> None:
        self.store = store
        #: Decisions no one principal may hold together (ADR-0070), loaded
        #: from the IR. Standing in for a failed leader consults these to
        #: decide what it may *not* confer (ADR-0094 rule 4). Left empty, a
        #: standing-in would confer everything and honestly report that it
        #: withheld nothing, which is why the loader fills it.
        self.separations: list[dict] = []

    # -- units -------------------------------------------------------------

    def add_unit(self, unit: OrgUnit) -> OrgUnit:
        self.store.put(ORG_UNITS, unit, parent=unit.parent_id)
        return unit

    def units(self) -> list[OrgUnit]:
        return self.store.list(ORG_UNITS, OrgUnit)

    def unit(self, unit_id: str) -> Optional[OrgUnit]:
        return self.store.get(ORG_UNITS, unit_id, OrgUnit)

    def unit_groups(self, unit_id: Optional[str]) -> list[str]:
        """Groups inherited down the org-unit chain."""
        groups: list[str] = []
        seen: set[str] = set()
        current = unit_id
        while current and current not in seen:
            seen.add(current)
            u = self.unit(current)
            if not u:
                break
            groups.extend(g for g in u.groups if g not in groups)
            current = u.parent_id
        return groups

    # -- agents ------------------------------------------------------------

    def add_agent(self, agent: Agent) -> Agent:
        """Persist an agent and keep both ends of the reporting edge in sync."""
        if agent.manager_agent_id:
            manager = self.agent(agent.manager_agent_id)
            if manager is None:
                raise ValueError(f"unknown manager {agent.manager_agent_id}")
            if agent.id not in manager.report_agent_ids:
                manager.report_agent_ids.append(agent.id)
                self.store.put(AGENTS, manager, parent=manager.org_unit_id)
        # Effective groups = own + inherited from the org unit.
        for g in self.unit_groups(agent.org_unit_id):
            if g not in agent.groups:
                agent.groups.append(g)
        self.store.put(AGENTS, agent, parent=agent.org_unit_id)
        return agent

    def agent(self, agent_id: str) -> Optional[Agent]:
        return self.store.get(AGENTS, agent_id, Agent)

    def agents(self, org_unit_id: Optional[str] = None) -> list[Agent]:
        return self.store.list(AGENTS, Agent, parent=org_unit_id)

    def reports(self, agent_id: str) -> list[Agent]:
        a = self.agent(agent_id)
        if not a:
            return []
        return [r for r in (self.agent(i) for i in a.report_agent_ids) if r]

    def chain_of_command(self, agent_id: str) -> list[Agent]:
        """From the agent up to the top of the org, inclusive."""
        chain: list[Agent] = []
        seen: set[str] = set()
        current = agent_id
        while current and current not in seen:
            seen.add(current)
            a = self.agent(current)
            if not a:
                break
            chain.append(a)
            current = a.manager_agent_id or ""
        return chain

    def subtree(self, agent_id: str) -> list[Agent]:
        """Every agent at or below `agent_id`."""
        out: list[Agent] = []
        stack = [agent_id]
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            a = self.agent(cur)
            if not a:
                continue
            out.append(a)
            stack.extend(a.report_agent_ids)
        return out

    def mission_peers(
        self, agent_id: str, on: Optional[date] = None
    ) -> list[str]:
        """Who a mission lets this agent work with *today*.

        Evaluated per call rather than baked in at compile time: a mission past
        its end date confers nothing, whether or not anybody swept it.
        """
        src = self.agent(agent_id)
        return open_peers(src.mission_grants, on) if src else []

    def can_delegate(
        self,
        from_agent_id: str,
        to_agent_id: str,
        on: Optional[date] = None,
    ) -> bool:
        """True when `from` may hand work directly to `to`, on the given day."""
        if from_agent_id == to_agent_id:
            return False
        src = self.agent(from_agent_id)
        if not src:
            return False
        # An agent that has left is not a principal in either direction
        # (ADR-0098). Checked first, before any reach rule, because the
        # shared-service path below would otherwise let anybody hand work to
        # somebody who is gone.
        if not src.is_active:
            return False
        target = self.agent(to_agent_id)
        if target is not None and not target.is_active:
            return False
        if to_agent_id in src.report_agent_ids or to_agent_id in src.peer_agent_ids:
            return True
        # Lateral reach on loan from a live mission (ADR-0039 v1.1.0).
        if to_agent_id in open_peers(src.mission_grants, on):
            return True
        # Anywhere in the subtree (skip-level delegation).
        if any(a.id == to_agent_id for a in self.subtree(from_agent_id)):
            return True
        # Shared-service agents are callable by anyone.
        dst = self.agent(to_agent_id)
        if dst and dst.kind is AgentKind.SERVICE:
            return True
        # Reach lent by standing in for a leader that cannot run (ADR-0094).
        # A stand-in that may not hand work to the failed leader's reports
        # cannot lead them, which would leave the team stopped rather than
        # covered. Checked last, so it can only ever widen reach that the
        # chart already refused, and evaluated through `acting_for`, so a
        # lapsed standing-in lends nothing.
        for assignment in self.standing_in_as(from_agent_id, on):
            if assignment.failed_agent_id == to_agent_id:
                continue      # standing in for it, not delegating to it
            if self.can_delegate(assignment.failed_agent_id, to_agent_id, on):
                return True
        return False



    # -- leaving (ADR-0098) ------------------------------------------------

    def decommission(self, agent_id: str, *, reason: str = "") -> dict:
        """Record that this agent has left, and say what it was holding.

        The record stays. Deleting it would take its sessions' `agent_id` with
        it and break every trace that ran through it, and an audit trail that
        loses the agents is not one. What ends is its standing as a principal.

        The return value names what was in flight, because "this agent left
        with four things outstanding" is exactly what somebody needs to see and
        exactly what a silent removal destroys.
        """
        agent = self.agent(agent_id)
        if agent is None:
            return {"ok": False, "error": f"no agent {agent_id}"}
        if not agent.is_active:
            return {"ok": True, "already": True, "agent_id": agent_id}

        # Standing-in in both directions ends: it cannot cover for anybody,
        # and nobody is covering a post that no longer exists (ADR-0094).
        covered_for = [a.failed_agent_id
                       for a in self.standing_in_as(agent_id)]
        for failed_id in covered_for:
            self.close_standing_in(failed_id)
        ended_cover = self.close_standing_in(agent_id)

        # Successions that named it are now broken. Removing it from the
        # design fails validation (`unknown_successor`), so this reports
        # rather than repairs: choosing somebody's replacement is not a thing
        # a loader should do quietly.
        orphaned = [a.id for a in self.agents()
                    if a.successor_agent_id == agent_id and a.id != agent_id]

        agent.decommissioned_at = now_iso()
        agent.decommission_reason = reason or "left the organization"
        self.store.put(AGENTS, agent, parent=agent.org_unit_id)
        return {
            "ok": True,
            "agent_id": agent_id,
            "reason": agent.decommission_reason,
            "reports": list(agent.report_agent_ids),
            "stopped_covering": covered_for,
            "cover_for_it_ended": ended_cover,
            "successions_now_broken": sorted(orphaned),
        }

    def active_agents(self) -> list[Agent]:
        """Everyone still standing as a principal."""
        return [a for a in self.agents() if a.is_active]

    # -- standing in for a leader that cannot run (ADR-0094) ---------------

    def successor_of(self, agent_id: str) -> Optional[Agent]:
        """Who stands in for this agent, and nothing about whether it should.

        An unset `successor_agent_id` resolves to the manager. That is not a
        fallback for want of a better idea: under ADR-0065 a manager already
        holds a superset of its reports' mandates, so it is the one stand-in
        that confers no new authority on anybody.
        """
        agent = self.agent(agent_id)
        if agent is None:
            return None
        if agent.successor_agent_id:
            return self.agent(agent.successor_agent_id)
        return self.escalation_target(agent_id)

    def stand_in_for(
        self,
        agent_id: str,
        *,
        separations: Optional[list[dict]] = None,
        reason: str = "",
        ends_on: str = "",
        adopted_handles: Optional[list[str]] = None,
    ) -> Optional[ActingAssignment]:
        """Open a standing-in assignment for a leader that cannot run.

        Returns `None` when there is nobody to stand in — the top of a chain
        with no declared successor. That is a refusal, not a promotion of
        somebody arbitrary: silence never grants authority.

        Standing in does not chain (rule 5). If the successor is *itself*
        already being stood in for, this refuses rather than walking on,
        because each hop is a step further from anyone who reviewed it.
        """
        failed = self.agent(agent_id)
        successor = self.successor_of(agent_id)
        if failed is None or successor is None or successor.id == failed.id:
            return None
        if self.acting_for(successor.id) is not None:
            return None                     # no chaining
        by_hierarchy = not failed.successor_agent_id
        rules = separations if separations is not None else self.separations
        assignment = open_assignment(
            failed_agent_id=failed.id,
            successor_agent_id=successor.id,
            leader_mandate=failed.mandate,
            successor_mandate=successor.mandate,
            separations=rules or [],
            by_hierarchy=by_hierarchy,
            reason=reason,
            ends_on=ends_on,
        )
        assignment.adopted_handles = list(adopted_handles or [])
        self.store.put(ACTING, assignment, parent=failed.id)
        return assignment

    def acting_assignments(self) -> list[ActingAssignment]:
        return self.store.list(ACTING, ActingAssignment, limit=2000)

    def acting_for(
        self, failed_agent_id: str, on: Optional[date] = None
    ) -> Optional[ActingAssignment]:
        """The live assignment standing in for this agent, if any.

        The window is evaluated here, per call, so a lapsed standing-in
        confers nothing from the moment it lapsed — whether or not anybody
        swept it. Mission grants work the same way, for the same reason.
        """
        return next(
            (a for a in self.acting_assignments()
             if a.failed_agent_id == failed_agent_id and a.is_open(on)),
            None,
        )

    def standing_in_as(
        self, successor_agent_id: str, on: Optional[date] = None
    ) -> list[ActingAssignment]:
        """Everyone this agent is currently acting for."""
        return [a for a in self.acting_assignments()
                if a.successor_agent_id == successor_agent_id and a.is_open(on)]

    def close_standing_in(self, failed_agent_id: str) -> int:
        """End every standing-in for this agent, because it is back."""
        closed = 0
        for a in self.acting_assignments():
            if a.failed_agent_id == failed_agent_id and a.status == "active":
                a.status = "closed"
                self.store.put(ACTING, a, parent=failed_agent_id)
                closed += 1
        return closed

    def mandate_holder(self, agent_id: str, decision: str) -> Optional[Agent]:
        """The nearest agent from here upward whose mandate covers `decision`.

        Authority narrows downward (ADR-0065), so a manager's effective
        mandate is a superset of its reports'. Walking up therefore finds the
        *smallest* unit that may take this decision, which is who it should be
        escalated to.

        `None` means nobody in the line holds it. That is a refusal, not a
        promotion to the top: silence never grants authority.
        """
        seen: set[str] = set()
        current = self.agent(agent_id)
        while current and current.id not in seen:
            seen.add(current.id)
            if decision in current.mandate and current.is_active:
                # A holder that is currently stood down points at whoever is
                # standing in *for this decision*. A manager standing in by
                # hierarchy confers nothing and so matches nothing here —
                # correctly, because walking up would have reached it anyway.
                # A decision the stand-in had withheld under a separation is
                # likewise not covered, so it keeps escalating rather than
                # landing on somebody a control says may not take it.
                acting = self.acting_for(current.id)
                if acting is not None and acting.covers(decision):
                    stand_in = self.agent(acting.successor_agent_id)
                    if stand_in is not None:
                        return stand_in
                return current
            current = (
                self.agent(current.manager_agent_id)
                if current.manager_agent_id
                else None
            )
        return None

    def mandate_holders(self, decision: str) -> list[Agent]:
        """Every agent whose mandate covers `decision`, anywhere in the chart.

        For explaining a refusal — "this belongs to Treasury" beats "nobody" —
        without routing the decision there. Naming a holder is not reaching
        one: an agent outside the requester's line is reached through the
        process that owns the decision, never by escalating past a control
        (ADR-0070).
        """
        return sorted(
            (a for a in self.agents() if decision in a.mandate),
            key=lambda a: a.name,
        )

    def escalation_target(self, agent_id: str) -> Optional[Agent]:
        a = self.agent(agent_id)
        if not a or not a.manager_agent_id:
            return None
        return self.agent(a.manager_agent_id)

    def to_tree(self, root_id: Optional[str] = None,
                agents: Optional[list[Agent]] = None) -> list[dict]:
        """Nested dicts for the designer UI's org-chart view; `agents`
        limits it to the ones a caller may see (ADR-0116)."""
        agents = self.agents() if agents is None else list(agents)
        by_id = {a.id: a for a in agents}
        children: dict[Optional[str], list[Agent]] = {}
        for a in agents:
            parent = a.manager_agent_id if a.manager_agent_id in by_id else None
            children.setdefault(parent, []).append(a)

        def build(a: Agent) -> dict:
            return {
                "id": a.id,
                "name": a.name,
                "title": a.title,
                "kind": a.kind.value,
                "human": a.human.display_name if a.human else None,
                "org_unit_id": a.org_unit_id,
                "children": [build(c) for c in children.get(a.id, [])],
            }

        roots = [by_id[root_id]] if root_id and root_id in by_id else children.get(None, [])
        return [build(r) for r in roots]
