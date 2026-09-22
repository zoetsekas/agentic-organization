"""Placement: where an agent's work lives, keyed by whose work it is (ADR-0069).

An environment class is a *profile* — toolchain, tier, network posture, egress.
It says what an agent needs, not whose work it is. Keying sandbox environments
by profile alone puts an HR agent and a Finance agent that both need `analysis`
in one place, which is the shape nobody would draw on purpose.

A **placement** is an org unit crossed with an environment class. HR ×
`analysis` and Finance × `analysis` are two sandbox environments with the same
shape. The model is the application platform's namespace: a name scope, a
network policy and storage; things inside share freely, things outside reach in
only over declared channels.

Three rules from the record are load-bearing here and are easy to lose:

* **Placement is opt-in and inherits** (rule 2). A team that declares nothing
  is placed in its nearest declaring ancestor, and the root always declares —
  so every agent has exactly one answer. The default is therefore the *widest*
  arrangement, which the record names as the price of not annoying small
  organizations.
* **The shared volume is the PROTECTED plane made concrete** (rule 4). It
  carries exactly the data classes the unit's groups already share, and is
  never a new grant: an agent that may not read a class does not acquire it by
  sharing a disk with somebody who may.
* **Only standing structure becomes a network rule** (rule 6). Mission-lent
  reach never does, because a generated rule does not expire and a mission
  window does. Temporary reach travels over the bus, where the inbound worker
  re-runs the org-chart check per message.

A placement is **not** a security boundary (rule 5). The tenant is (ADR-0050).
Borrowing the namespace model means borrowing its caveat: namespaces share a
kernel too.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: Separator between the unit and the environment class in a placement id.
#: Chosen so a placement id is a legal DNS label component after the ids
#: themselves are, which is what the targets need to name a network from it.
SEP = "--"


def placement_id(unit: str, environment: str) -> str:
    return f"{unit}{SEP}{environment}"


@dataclass(frozen=True)
class Placement:
    """One sandbox environment: a unit's agents that share an environment class."""

    id: str
    unit: str
    environment: str
    #: Co-resident agents, sorted. The boundary statement names these, because
    #: traffic between them is permitted and that is a widening (rule 7).
    agents: tuple[str, ...] = ()
    #: The unit's inherited groups, which scope the shared volume.
    groups: tuple[str, ...] = ()
    #: Data classes the volume may carry — exactly what those groups already
    #: share. Never wider: rule 4 bounds the volume, it does not grant.
    data_classes: tuple[str, ...] = ()

    @property
    def shares_a_volume(self) -> bool:
        return len(self.agents) > 1


@dataclass(frozen=True)
class PlacementRule:
    """One permitted flow between two placements, and what permits it."""

    source: str
    target: str
    #: The declared channel or flow that permits it, for the generated comment.
    via: str
    reason: str = ""


@dataclass
class PlacementMap:
    """Every placement in one organization, and who may reach whom."""

    placements: dict[str, Placement] = field(default_factory=dict)
    #: agent id -> every placement it is in. Absent means the agent declares
    #: no environment class and so has no sandbox environment to be placed in.
    #: A list, because an agent may run work in more than one (ADR-0082).
    home: dict[str, list[str]] = field(default_factory=dict)
    #: team id -> the unit whose placement it resolves to.
    boundary: dict[str, str] = field(default_factory=dict)
    #: Cross-placement traffic that is permitted. Everything absent is denied
    #: (rule 7); within a placement, traffic is permitted and unlisted.
    rules: tuple[PlacementRule, ...] = ()
    #: Units that declared themselves a boundary, root first.
    declared: tuple[str, ...] = ()

    def for_agent(self, agent_id: str) -> list[Placement]:
        """Every sandbox environment this agent runs in (ADR-0082).

        A list, because an agent may hold more than one. It was a single
        placement when an agent could only run in one sandbox, and reading
        "the" placement of an agent with two would have quietly answered
        about one of them.
        """
        return [self.placements[p] for p in self.home.get(agent_id, [])
                if p in self.placements]

    def co_resident(self, agent_id: str) -> list[str]:
        """Agents that share *any* sandbox environment with this one.

        Any, not all: sharing one volume and one process namespace is what
        the separation check is about, and sharing it in one place out of two
        is still sharing it.
        """
        out: list[str] = []
        for place in self.for_agent(agent_id):
            for other in place.agents:
                if other != agent_id and other not in out:
                    out.append(other)
        return out

    def same_placement(self, one: str, other: str) -> bool:
        """Whether two agents share at least one sandbox environment."""
        return bool(set(self.home.get(one, [])) & set(self.home.get(other, [])))

    def permits(self, source_agent: str, target_agent: str) -> bool:
        """Whether generated network policy lets one agent reach another.

        Within a placement, yes. Across, only over a declared channel or flow.
        An agent with no placement reaches nothing this way, which is correct:
        it has no sandbox environment for a rule to be written about.
        """
        if source_agent not in self.home or target_agent not in self.home:
            return False
        if self.same_placement(source_agent, target_agent):
            return True
        # With several placements each, a rule between any pair of them is
        # reach: a path that exists in one direction from one sandbox is a
        # path, however many others do not carry it.
        return any(
            r.source == src and r.target == dst
            for src in self.home[source_agent]
            for dst in self.home[target_agent]
            for r in self.rules
        )


def _walk(team: Any, inherited_unit: str, out: PlacementMap,
          declared: list[str]) -> None:
    """Assign every team the unit whose placement it belongs to."""
    unit = inherited_unit
    if getattr(team, "placement", False):
        unit = team.id
        if unit not in declared:
            declared.append(unit)
    out.boundary[team.id] = unit
    for child in getattr(team, "teams", []) or []:
        _walk(child, unit, out, declared)


def _unit_groups(spec: Any, unit_id: str) -> list[str]:
    """Groups inherited down the unit chain, nearest last.

    Walking *up* means a deep team carries its ancestors' groups, so a volume
    scoped to them may be wider than the team needs. Rule 4 bounds the volume
    by what the unit already shares; it does not make that minimal, and the
    record says so.
    """
    parent: dict[str, str] = {}
    for team in spec.teams():
        for child in team.teams:
            parent[child.id] = team.id
    by_id = {t.id: t for t in spec.teams()}
    groups: list[str] = []
    seen: set[str] = set()
    current: Optional[str] = unit_id
    while current and current not in seen:
        seen.add(current)
        team = by_id.get(current)
        if team is None:
            break
        groups.extend(g for g in team.groups if g not in groups)
        current = parent.get(current)
    return groups


def _environments_of(agent: Any) -> list[str]:
    """Every environment class an agent runs work in (ADR-0082).

    An agent with two sandboxes is in two placements, and that is the point:
    the blast radius of ledger analysis and of instructing a bank are not the
    same, so they do not share a volume and a process namespace.
    """
    return [o.environment for o in getattr(agent, "environments", []) or []]


def resolve(spec: Any) -> PlacementMap:
    """Resolve every agent's placement, the volumes, and the network rules."""
    out = PlacementMap()
    declared: list[str] = [spec.organization.id]
    # The root is always a boundary, so every agent has exactly one answer.
    _walk(spec.organization, spec.organization.id, out, declared)
    out.declared = tuple(declared)

    members: dict[tuple[str, str], list[str]] = {}
    for team in spec.teams():
        unit = out.boundary.get(team.id, spec.organization.id)
        for agent in team.members:
            environments = _environments_of(agent)
            if not environments:
                # No environment class, so no sandbox environment. Recording
                # this as unplaced is better than inventing a placement for an
                # agent that runs nowhere in particular.
                continue
            for environment in environments:
                members.setdefault((unit, environment), []).append(agent.id)
                out.home.setdefault(agent.id, []).append(
                    placement_id(unit, environment))

    protected = [
        dc for dc in spec.data_classes
        if getattr(dc.scope, "value", dc.scope) == "protected"
    ]
    for (unit, environment), agents in members.items():
        groups = _unit_groups(spec, unit)
        carried = sorted(
            dc.id for dc in protected if set(dc.groups) & set(groups)
        )
        pid = placement_id(unit, environment)
        out.placements[pid] = Placement(
            id=pid, unit=unit, environment=environment,
            agents=tuple(sorted(agents)), groups=tuple(groups),
            data_classes=tuple(carried),
        )

    out.rules = network_rules(spec, out)
    return out


def network_rules(spec: Any, resolved: PlacementMap) -> tuple[PlacementRule, ...]:
    """Cross-placement traffic that standing structure permits (rule 6).

    Standing structure only. A mission grant confers lateral reach for a
    window, and a generated rule has no window — baking one in would convert
    temporary reach into standing reach, which is the accident ADR-0065 rule 8
    exists to prevent. Mission peers are therefore read by nothing here, and a
    test asserts it.
    """
    found: dict[tuple[str, str], PlacementRule] = {}

    def permit(a: str, b: str, via: str, reason: str) -> None:
        """Permit every placement of one agent to reach every placement of the
        other.

        An agent with two sandboxes needs the reach from both, because the
        rule is about the *agent* being allowed to hand work over and the
        sandbox it happens to be in when it does is not knowable here. Within
        a placement nothing is written: traffic there is permitted and
        unlisted.
        """
        for home_a in resolved.home.get(a, []):
            for home_b in resolved.home.get(b, []):
                if home_a == home_b:
                    continue
                found.setdefault(
                    (home_a, home_b), PlacementRule(home_a, home_b, via, reason)
                )

    # 1. Declared lateral links and declared flows: the "declared channels" of
    #    rule 7, read off the spec rather than off `can_delegate`, which folds
    #    mission reach in.
    for team in spec.teams():
        for agent in team.members:
            for peer in agent.peers:
                permit(agent.id, peer, f"peer:{agent.id}", "a declared peer link")
                permit(peer, agent.id, f"peer:{agent.id}", "a declared peer link")
    for flow in spec.interaction_flows:
        permit(
            flow.source, flow.target, f"flow:{flow.source}->{flow.target}",
            f"a declared {getattr(flow.kind, 'value', flow.kind)} flow",
        )

    # 2. The manager chain, **transitively**. A leader may hand work
    #    skip-level to anyone beneath its unit (`OrgChart.can_delegate`), and
    #    a Docker network is not transitive — so permitting only
    #    leader-to-adjacent-leader left a chief executive unable to reach
    #    Treasury two levels down while the org chart said otherwise. That
    #    silent disagreement is the drift this record names as a
    #    disadvantage; generating the closure is what stops it being silent.
    by_team = {t.id: t for t in spec.teams()}
    children: dict[str, list[str]] = {
        t.id: [c.id for c in t.teams] for t in spec.teams()
    }

    def beneath(team_id: str) -> list[str]:
        out: list[str] = []
        stack, seen = [team_id], set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            team = by_team.get(current)
            if team is None:
                continue
            out.extend(m.id for m in team.members)
            stack.extend(children.get(current, []))
        return out

    for team in spec.teams():
        if not team.leader:
            continue
        for target in beneath(team.id):
            if target == team.leader:
                continue
            permit(team.leader, target, f"leads:{team.id}", "the manager chain")
            permit(target, team.leader, f"leads:{team.id}", "the manager chain")

    # 3. Shared services are callable from anywhere.
    services = [
        a.id for t in spec.teams() for a in t.members
        if getattr(a, "shared_service", False)
    ]
    for service in services:
        for team in spec.teams():
            for agent in team.members:
                permit(
                    agent.id, service, f"service:{service}",
                    "a shared service, callable from anywhere",
                )

    # 4. A declared channel with members on both sides.
    for channel in spec.channels:
        named = set(channel.members)
        agents = [
            a.id for t in spec.teams() for a in t.members
            if a.id in named or t.id in named
        ]
        for one in agents:
            for other in agents:
                if one != other:
                    permit(
                        one, other, f"channel:{channel.id}",
                        f"both are on channel '{channel.id}'",
                    )

    return tuple(sorted(found.values(), key=lambda r: (r.source, r.target)))


def standing_reach(spec: Any) -> list[tuple[str, str, str]]:
    """Ordered pairs standing structure says may hand work over, and why.

    Deliberately not `OrgChart.can_delegate`, which folds mission-lent reach
    in. This is the same set minus that, because a network rule has no window
    and a mission does (ADR-0069 rule 6).
    """
    by_team = {t.id: t for t in spec.teams()}
    children: dict[str, list[str]] = {
        t.id: [c.id for c in t.teams] for t in spec.teams()
    }

    def subtree_agents(team_id: str) -> list[str]:
        out: list[str] = []
        stack = [team_id]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            team = by_team.get(current)
            if team is None:
                continue
            out.extend(m.id for m in team.members)
            stack.extend(children.get(current, []))
        return out

    pairs: list[tuple[str, str, str]] = []
    services = [
        a.id for t in spec.teams() for a in t.members
        if getattr(a, "shared_service", False)
    ]
    for team in spec.teams():
        for agent in team.members:
            for peer in agent.peers:
                pairs.append((agent.id, peer, "a declared peer link"))
            if agent.id == team.leader:
                # Skip-level: a leader may reach anyone beneath its unit.
                for target in subtree_agents(team.id):
                    if target != agent.id:
                        pairs.append((agent.id, target, "skip-level delegation"))
            for service in services:
                if service != agent.id:
                    pairs.append((agent.id, service, "a shared service"))
    return pairs
