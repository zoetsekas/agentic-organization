"""Organizational hierarchy: reporting lines, delegation and escalation."""
from __future__ import annotations

from typing import Optional

from .models import Agent, AgentKind, OrgUnit
from .store import AGENTS, ORG_UNITS, Store


class OrgChart:
    """Reads and mutates the reporting structure.

    Delegation rule: an agent may delegate *down* its own subtree, call
    registered peers laterally, and escalate *up* its management chain.
    Anything else has to go through a shared manager.
    """

    def __init__(self, store: Store) -> None:
        self.store = store

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

    def can_delegate(self, from_agent_id: str, to_agent_id: str) -> bool:
        """True when `from` may hand work directly to `to`."""
        if from_agent_id == to_agent_id:
            return False
        src = self.agent(from_agent_id)
        if not src:
            return False
        if to_agent_id in src.report_agent_ids or to_agent_id in src.peer_agent_ids:
            return True
        # Anywhere in the subtree (skip-level delegation).
        if any(a.id == to_agent_id for a in self.subtree(from_agent_id)):
            return True
        # Shared-service agents are callable by anyone.
        dst = self.agent(to_agent_id)
        return bool(dst and dst.kind is AgentKind.SERVICE)

    def escalation_target(self, agent_id: str) -> Optional[Agent]:
        a = self.agent(agent_id)
        if not a or not a.manager_agent_id:
            return None
        return self.agent(a.manager_agent_id)

    def to_tree(self, root_id: Optional[str] = None) -> list[dict]:
        """Nested dicts for the designer UI's org-chart view."""
        agents = self.agents()
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
