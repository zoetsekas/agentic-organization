"""Who may reach whom over the bus, decided once, at compile time (ADR-0118).

An agent container is given the edges *it* has — who it may message, who it
may hand work to, who may reach it — and nothing else about the organization.
It does not load the design and work them out; a worker that derived its own
reach at run time from the whole spec would be one bug away from deriving a
wider one, and the broker could not be told the same answer.

Every edge comes from something the design already says. Nothing here is a
new concept:

======================  ========  ===========================================
source                  kind      edge
======================  ========  ===========================================
team leadership         delegate  a leader to each member of the team it
                                  leads, and to the leaders of its sub-teams
                                  (`delegates_to`, ADR-0006)
peers, shared services  delegate  `delegates_to` as the IR resolved it
delegate flow           delegate  `interaction_flows` of kind delegate
                                  (ADR-0024)
mission                 delegate  a mission's internal delegation, only
                                  between its dates (ADR-0039 v1.1.0)
consult/notify/escalate message   `interaction_flows` of those kinds: may
                                  ask or inform, never hand work over
reporting line          message   a member to the agent it reports to: the
                                  tree's own escalation path
unit link               message   `oversees` and `escalates_to`: the source
                                  unit's leader to the target's; `serves`:
                                  the served unit's leader to the serving
                                  one's. `partners_with` is inert and gives
                                  nothing (ADR-0081)
======================  ========  ===========================================

A `delegate` edge also permits a message. A reply travels back along the edge
it answers and needs none of its own.

The same function renders the broker's side: a NATS user per agent whose
publish permissions are exactly the inboxes its outbound edges reach, and
whose subscribe permission is its own reply inbox. The broker therefore
refuses what the worker would refuse, from a configuration produced by the
same code.
"""
from __future__ import annotations

from typing import Any, Optional

from ..bus import SubjectNamespace, _token

DELEGATE = "delegate"
MESSAGE = "message"

#: Env names (ADR-0015: names in the design, values in the environment).
BUS_ADMIN_USER = "orgagents_bus_admin"
BUS_ADMIN_PASSWORD_REF = "ORGAGENTS_BUS_ADMIN_PASSWORD"


def bus_password_ref(agent_id: str) -> str:
    return "ORGAGENTS_BUS_PASSWORD_" + "".join(
        c if c.isalnum() else "_" for c in agent_id).upper()


def bus_user(agent_id: str) -> str:
    return _token(agent_id)


def inbox_prefix(agent_id: str) -> str:
    """The reply-inbox prefix an agent's connection uses. Per agent, so no
    agent can subscribe to another's request replies (`_INBOX.>` would)."""
    return f"_INBOX_{_token(agent_id)}"


def subjects_for(ir: Any) -> SubjectNamespace:
    return SubjectNamespace(tenant=ir.tenant.id if getattr(ir, "tenant", None) else "local")


class _Edges:
    def __init__(self, agent_ids: list[str]) -> None:
        self.known = set(agent_ids)
        self.out: dict[str, dict[str, dict[str, Any]]] = {a: {} for a in agent_ids}

    def add(self, src: str, dst: str, kind: str, via: str,
            starts_on: str = "", ends_on: str = "") -> None:
        if not src or not dst or src == dst:
            return
        if src not in self.known or dst not in self.known:
            return
        edge = self.out[src].setdefault(dst, {"grants": []})
        grant = {"kind": kind, "via": via}
        if starts_on or ends_on:
            grant.update({"starts_on": starts_on or "", "ends_on": ends_on or ""})
        if grant not in edge["grants"]:
            edge["grants"].append(grant)


def compute_edges(ir: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """`{from: {to: {"grants": [{kind, via[, starts_on, ends_on]}]}}}`."""
    agents = {a.id: a for a in ir.agents}
    teams = {t.id: t for t in ir.teams}
    edges = _Edges(list(agents))

    def leader(team_id: str) -> str:
        team = teams.get(team_id)
        return team.leader_agent_id if team else ""

    for a in ir.agents:
        # The team it leads: its members, and the leaders of its sub-teams.
        members = ({m.id for m in ir.agents if m.team_id == a.leader_of}
                   | {t.leader_agent_id for t in ir.teams
                      if t.parent_id == a.leader_of and t.leader_agent_id}
                   ) if a.leader_of else set()
        for dst in a.standing_delegates_to:
            target = agents.get(dst)
            if dst in members:
                via = f"leads:{a.leader_of}"
            elif target is not None and target.shared_service:
                via = "shared_service"
            else:
                via = "delegates_to"
            edges.add(a.id, dst, DELEGATE, via)
        for dst in a.consults:
            edges.add(a.id, dst, MESSAGE, "flow:consult")
        for dst in a.notifies:
            edges.add(a.id, dst, MESSAGE, "flow:notify")
        for dst in a.escalation_flows:
            edges.add(a.id, dst, MESSAGE, "flow:escalate")
        if a.reports_to:
            edges.add(a.id, a.reports_to, MESSAGE, "reports_to")
        # Mission reach carries its window: the worker re-checks the date on
        # every send and every receipt, so a lapsed mission lends nothing.
        # Where standing structure already gives the edge, that is the grant.
        for grant in a.mission_grants:
            for dst in grant.peers:
                if dst not in a.standing_delegates_to:
                    edges.add(a.id, dst, DELEGATE, f"mission:{grant.mission}",
                              grant.starts_on or "", grant.ends_on or "")

    # Delegate flows are already in `standing_delegates_to`; name them.
    for flow in getattr(ir, "flows", []) or []:
        kind = getattr(flow.kind, "value", flow.kind)
        if kind == "delegate":
            edge = edges.out.get(flow.source, {}).get(flow.target)
            if edge:
                for g in edge["grants"]:
                    if g["kind"] == DELEGATE and g["via"] == "delegates_to":
                        g["via"] = "flow:delegate"

    for link in getattr(ir, "unit_links", []) or []:
        kind = getattr(link.kind, "value", link.kind)
        src, dst = leader(link.source), leader(link.target)
        if kind in ("oversees", "escalates_to"):
            edges.add(src, dst, MESSAGE, f"unit_link:{kind}:{link.source}->{link.target}")
        elif kind == "serves":
            edges.add(dst, src, MESSAGE, f"unit_link:serves:{link.source}->{link.target}")
        # partners_with: declared inert (ADR-0081), so it opens nothing.
    return edges.out


def separated_holdings(ir: Any) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """The separations, and which agent holds which separated decision."""
    rules = [{"id": s.id, "decisions": list(s.decisions), "reason": s.reason}
             for s in getattr(ir, "separations", []) or []]
    separated = {d for r in rules for d in r["decisions"]}
    holdings = {}
    for a in ir.agents:
        held = sorted(set(a.mandate.decisions) & separated)
        if held:
            holdings[a.id] = held
    return rules, holdings


def agent_links(ir: Any, edges: Optional[dict] = None) -> dict[str, dict[str, Any]]:
    """The per-agent bus configuration written into `agents/<id>.json`."""
    edges = edges if edges is not None else compute_edges(ir)
    subjects = subjects_for(ir)
    rules, holdings = separated_holdings(ir)
    inbound: dict[str, dict[str, dict[str, Any]]] = {a.id: {} for a in ir.agents}
    for src, outs in edges.items():
        for dst, edge in outs.items():
            inbound[dst][src] = edge
    out = {}
    for a in ir.agents:
        out[a.id] = {
            "agent": a.id,
            "subject_prefix": subjects.prefix,
            "stream": subjects.stream,
            "consumer": subjects.consumer(a.id),
            "user": bus_user(a.id),
            "inbox_prefix": inbox_prefix(a.id),
            "max_depth": a.max_delegation_depth,
            "outbound": dict(sorted(edges[a.id].items())),
            "inbound": dict(sorted(inbound[a.id].items())),
            # For separation across delegation: only the separations, and
            # only who holds a separated decision -- not the design.
            "separations": rules,
            "separated_holdings": holdings,
        }
    return out


def nats_permissions(ir: Any, agent_id: str,
                     links: Optional[dict[str, dict[str, Any]]] = None) -> dict[str, Any]:
    """What the broker lets one agent's connection do: publish into the
    inboxes it may reach (as itself), reply to those that may reach it, pull
    and ack its own consumer; subscribe to its own reply inbox only."""
    links = links if links is not None else agent_links(ir)
    me = links[agent_id]
    subjects = subjects_for(ir)
    stream, consumer = me["stream"], me["consumer"]
    publish = sorted(subjects.inbox(to, agent_id) for to in me["outbound"])
    publish += sorted(subjects.reply(frm, agent_id) for frm in me["inbound"])
    publish += [f"$JS.API.CONSUMER.MSG.NEXT.{stream}.{consumer}",
                f"$JS.API.CONSUMER.INFO.{stream}.{consumer}",
                f"$JS.ACK.{stream}.{consumer}.>"]
    return {"publish": publish, "subscribe": [f"{me['inbox_prefix']}.>"]}


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def nats_config(ir: Any, *, server_name: str = "bus") -> str:
    """The broker's configuration: JetStream, and one user per agent with the
    permissions above. Passwords are `$VAR` references resolved from the
    broker container's environment; no value is ever written here."""
    links = agent_links(ir)
    subjects = subjects_for(ir)
    admin_inbox = _quote("_INBOX_" + BUS_ADMIN_USER + ".>")
    entries = [
        "    # Creates the stream and the per-agent consumers (bus-init): an\n"
        "    # operator identity, never an agent's.\n"
        f"    {{ user: {_quote(BUS_ADMIN_USER)}, password: ${BUS_ADMIN_PASSWORD_REF},\n"
        f"      permissions: {{ publish: {{ allow: [{_quote('$JS.API.>')}, "
        f"{_quote(subjects.agents_wildcard)}] }},\n"
        f"                     subscribe: {{ allow: [{admin_inbox}] }} }} }}"
    ]
    for a in ir.agents:
        perms = nats_permissions(ir, a.id, links)
        pub = ", ".join(_quote(x) for x in perms["publish"])
        sub = ", ".join(_quote(x) for x in perms["subscribe"])
        entries.append(
            f"    # {a.id} reaches: {', '.join(links[a.id]['outbound']) or 'nobody'}\n"
            f"    {{ user: {_quote(bus_user(a.id))}, password: ${bus_password_ref(a.id)},\n"
            f"      permissions: {{ publish: {{ allow: [{pub}] }},\n"
            f"                     subscribe: {{ allow: [{sub}] }} }} }}")
    lines = [
        "# Generated by the local target (ADR-0118). Do not edit: every",
        "# permission below is an edge of the design, computed at compile time.",
        f"server_name: {_quote(server_name)}",
        "port: 4222",
        "http_port: 8222",
        'jetstream { store_dir: "/data" }',
        "authorization {",
        "  users = [",
        ",\n".join(entries),
        "  ]",
        "}",
    ]
    return "\n".join(lines) + "\n"
