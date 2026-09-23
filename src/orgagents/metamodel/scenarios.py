"""Scenarios played out on the model alone (ADR-0102).

Each scenario is a small story an organisation designer will act out — hire,
move, share, deploy, delete — written as operations on the metamodel with the
outcome the model must give: accepted with these effects, or refused by this
constraint. They are run by the test suite and rendered as UML object
diagrams (`orgagents metamodel scenarios`), so the model's behaviour can be
reviewed as pictures before any designer or compiler code depends on it.

They all start from `base()`: one small organisation that satisfies every
constraint, so each scenario shows exactly what its own steps change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..spec import model as spec_model
from . import operations as op
from .constraints import check
from .instances import collect

Spec = spec_model.SystemSpec


def base() -> Spec:
    """Acme, small: two teams under the organisation, a sub-team, a handful
    of building blocks, and one of each kind of link."""
    return Spec.model_validate({
        "metadata": {"name": "acme"},
        "organization": {
            "id": "acme", "name": "Acme", "leader": "ceo", "placement": True,
            "members": [{"id": "ceo", "capabilities": ["read_ledger"],
                         "environments": [{"environment": "secure"}]}],
            "teams": [
                {"id": "ops", "leader": "ops_lead", "members": [
                    {"id": "ops_lead", "roles": [{"role": "payer"}],
                     "environments": [{"environment": "secure"}],
                     "mandate": {"decisions": ["approve_payment"]}},
                    {"id": "analyst", "capabilities": ["read_ledger"],
                     "environments": [{"environment": "sandbox"}],
                     "knowledge": ["handbook"]},
                ], "teams": [
                    {"id": "ap", "leader": "clerk", "members": [
                        {"id": "clerk", "capabilities": ["read_ledger"]}]},
                ]},
                {"id": "risk", "leader": "risk_lead", "members": [
                    {"id": "risk_lead", "capabilities": ["read_ledger"],
                     "mandate": {"decisions": ["release_payment"]}}]},
            ],
            "capabilities": [{"id": "read_ledger", "data_classes": ["ledger"]},
                             {"id": "pay_supplier"}],
            "data_classes": [{"id": "ledger"}],
            "role_definitions": [{"id": "payer",
                                  "capabilities": ["pay_supplier"]}],
            "environments": [{"id": "sandbox"}, {"id": "secure"}],
            "knowledge": [{"id": "handbook"}],
            "tools": [{"id": "ledger_lookup", "wraps": "read_ledger"}],
            "people": [{"id": "ana"}],
            "decisions": [{"id": "approve_payment"}, {"id": "release_payment"}],
            "separations": [{"id": "four_eyes",
                             "decisions": ["approve_payment",
                                           "release_payment"]}],
            "triggers": [{"id": "nightly", "agent": "analyst"}],
            "interaction_flows": [{"source": "ops_lead", "target": "analyst",
                                   "kind": "delegate"}],
            "unit_links": [{"source": "risk", "target": "ops",
                            "kind": "oversees", "reason": "second line"}],
            "workflows": [{"id": "month_end", "graph": {
                "entry": "reconcile",
                "nodes": [{"id": "reconcile", "kind": "agent",
                           "agent": "analyst"},
                          {"id": "lookup", "kind": "tool",
                           "tool": "ledger_lookup"}],
                "edges": [{"from": "reconcile", "to": "lookup"},
                          {"from": "lookup", "to": "END"}]}}],
        },
    })


@dataclass
class Step:
    """One operation and the outcome the model must give."""

    run: Callable[[Spec], op.Result]
    text: str
    #: None: accepted. Otherwise the constraint that must refuse it.
    refused_by: Optional[str] = None


@dataclass
class Scenario:
    id: str
    title: str
    story: str
    steps: list[Step]
    #: What must be true after the steps, as (description, predicate).
    then: list[tuple[str, Callable[[Spec], bool]]] = field(default_factory=list)
    #: The instances the object diagram shows, with their neighbours.
    focus: list[str] = field(default_factory=list)


@dataclass
class Played:
    scenario: Scenario
    spec: Spec
    outcomes: list[tuple[Step, op.Result]]
    failures: list[str]


def _agent(s: Spec, id_: str) -> Any:
    return s.agent(id_)


def _team(s: Spec, id_: str) -> Any:
    return next(t for t in s.teams() if t.id == id_)


S = Step

SCENARIOS: list[Scenario] = [
    Scenario(
        "hire", "Hire an agent into a team",
        "Ops takes on a second analyst. The new agent is a part of Ops: it "
        "belongs to exactly one team, and nothing else about it is implied.",
        [S(lambda s: op.create(s, "agent", "analyst_2", owner="ops"),
           "create Agent analyst_2 in Team ops")],
        [("analyst_2 is a member of ops",
          lambda s: "analyst_2" in {m.id for m in _team(s, "ops").members})],
        ["ops", "analyst_2"]),
    Scenario(
        "hire_duplicate", "Hire an agent whose id is taken",
        "Risk tries to hire an 'analyst'. An agent is in exactly one team, "
        "so a second instance with the same id is refused, not merged.",
        [S(lambda s: op.create(s, "agent", "analyst", owner="risk"),
           "create Agent analyst in Team risk", "ids_are_unique")],
        [("analyst is still only in ops",
          lambda s: s.team_of("analyst").id == "ops")],
        ["risk", "ops", "analyst"]),
    Scenario(
        "move", "Move an agent to another team",
        "The analyst moves from Ops to Risk. Membership is composition: "
        "linking the part to a new whole moves it, and every association it "
        "has — its trigger, its flow, its knowledge — goes with it untouched.",
        [S(lambda s: op.link(s, ("team", "risk"), ("agent", "analyst"), "member"),
           "link Team risk —member→ Agent analyst")],
        [("analyst is in risk and not ops",
          lambda s: s.team_of("analyst").id == "risk"),
         ("the nightly trigger still fires analyst",
          lambda s: s.triggers[0].agent == "analyst"),
         ("ops_lead still delegates to analyst",
          lambda s: s.interaction_flows[0].target == "analyst")],
        ["ops", "risk", "analyst", "nightly"]),
    Scenario(
        "move_leader", "Move a team's leader out",
        "Ops's leader moves to Risk. The leader {subsets members}, so leaving "
        "the team ends the leadership — said as an effect, not left dangling.",
        [S(lambda s: op.link(s, ("team", "risk"), ("agent", "ops_lead"), "member"),
           "link Team risk —member→ Agent ops_lead")],
        [("ops has no leader", lambda s: _team(s, "ops").leader == "")],
        ["ops", "risk", "ops_lead"]),
    Scenario(
        "share_knowledge", "Share one knowledge source between agents",
        "The handbook is associated with the analyst and the risk lead. An "
        "association, not a part: one Knowledge instance, two links, and "
        "neither agent owns it.",
        [S(lambda s: op.link(s, ("agent", "risk_lead"),
                             ("knowledge", "handbook")),
           "link Agent risk_lead —consults→ Knowledge handbook")],
        [("both agents consult the handbook",
          lambda s: "handbook" in _agent(s, "analyst").knowledge
          and "handbook" in _agent(s, "risk_lead").knowledge),
         ("there is still one handbook", lambda s: len(s.knowledge) == 1)],
        ["analyst", "risk_lead", "handbook"]),
    Scenario(
        "deploy", "Deploy an agent into a second environment, then undeploy",
        "The analyst is deployed into the secure sandbox with its own "
        "network setting — a DeploymentSpecification — and later undeployed "
        "from the ordinary one.",
        [S(lambda s: op.link(s, ("agent", "analyst"),
                             ("environment", "secure"), network="none"),
           "deploy Agent analyst → Environment secure {network: none}"),
         S(lambda s: op.unlink(s, ("agent", "analyst"),
                               ("environment", "sandbox")),
           "undeploy Agent analyst from Environment sandbox")],
        [("analyst runs only in secure, with its setting",
          lambda s: [(e.environment, e.network and e.network.value)
                     for e in _agent(s, "analyst").environments]
          == [("secure", "none")])],
        ["analyst", "secure", "sandbox"]),
    Scenario(
        "subagent", "Give an agent a sub-agent within its reach",
        "The analyst gets a summariser that reads the ledger in its sandbox. "
        "A SubAgent is a Worker, not an Agent: a part of the analyst, run "
        "under its identity, and no wider than it.",
        [S(lambda s: op.create(s, "subagent", "summariser", owner="analyst",
                               capabilities=["read_ledger"],
                               environments=["sandbox"]),
           "create SubAgent summariser in Agent analyst "
           "{read_ledger; sandbox}")],
        [("analyst owns summariser",
          lambda s: [x.id for x in _agent(s, "analyst").subagents]
          == ["summariser"])],
        ["analyst", "summariser"]),
    Scenario(
        "subagent_too_wide", "Give a sub-agent more than its parent has",
        "The analyst's sub-agent is given pay_supplier, which the analyst "
        "does not hold, and the secure sandbox, which it does not run in.",
        [S(lambda s: op.create(s, "subagent", "payer_bot", owner="analyst",
                               capabilities=["pay_supplier"]),
           "create SubAgent payer_bot {pay_supplier} in Agent analyst",
           "subagent_within_parent"),
         S(lambda s: op.create(s, "subagent", "vault_bot", owner="analyst",
                               environments=["secure"]),
           "create SubAgent vault_bot {secure} in Agent analyst",
           "subagent_within_parent")],
        [("analyst still has no sub-agents",
          lambda s: not _agent(s, "analyst").subagents)],
        ["analyst", "pay_supplier", "secure"]),
    Scenario(
        "subagent_through_role", "A sub-agent may use what its parent's role grants",
        "The ops lead holds pay_supplier only through the payer role. Its "
        "sub-agent may still use it: the parent's reach is what it holds "
        "effectively, roles included.",
        [S(lambda s: op.create(s, "subagent", "remitter", owner="ops_lead",
                               capabilities=["pay_supplier"]),
           "create SubAgent remitter {pay_supplier} in Agent ops_lead")],
        [("ops_lead owns remitter",
          lambda s: bool(_agent(s, "ops_lead").subagents))],
        ["ops_lead", "payer", "remitter"]),
    Scenario(
        "oversee_own_subtree", "A unit overseeing the unit that contains it",
        "Accounts Payable, inside Ops, is set to oversee Ops. An overseer "
        "inside what it oversees is not oversight.",
        [S(lambda s: op.link(s, ("team", "ap"), ("team", "ops"), "unit link",
                             kind="oversees"),
           "link Team ap —oversees→ Team ops",
           "unit_links_respect_containment")],
        [("no link from ap", lambda s: all(l.source != "ap"
                                           for l in s.unit_links))],
        ["ops", "ap"]),
    Scenario(
        "escalate_into_subtree", "A unit escalating to its own sub-team",
        "Ops is set to escalate to Accounts Payable. An escalation must "
        "leave the unit, or it goes nowhere.",
        [S(lambda s: op.link(s, ("team", "ops"), ("team", "ap"), "unit link",
                             kind="escalates_to"),
           "link Team ops —escalates_to→ Team ap",
           "unit_links_respect_containment")],
        [], ["ops", "ap"]),
    Scenario(
        "leader", "Replace a leader, and refuse one who is not a member",
        "Ops's leadership passes to the analyst; then Risk's lead is "
        "proposed for Ops, and refused — the leader {subsets members}.",
        [S(lambda s: op.set_leader(s, "ops", "analyst"),
           "set Team ops leader := analyst"),
         S(lambda s: op.set_leader(s, "ops", "risk_lead"),
           "set Team ops leader := risk_lead", "leader_is_member")],
        [("analyst leads ops", lambda s: _team(s, "ops").leader == "analyst")],
        ["ops", "analyst", "risk_lead"]),
    Scenario(
        "separation", "Give one agent both halves of a separation",
        "The ops lead may approve payments; being given release as well "
        "would let one agent do both halves of four-eyes.",
        [S(lambda s: _mandate(s, "ops_lead", "release_payment"),
           "add Decision release_payment to Agent ops_lead's mandate",
           "separations_hold")],
        [], ["ops_lead", "four_eyes"]),
    Scenario(
        "delete_capability", "Delete a capability that a role still grants",
        "pay_supplier is retired. Its links are destroyed with it — the "
        "payer role no longer grants it — and the role itself survives. "
        "Narrowing is safe, so it is done and said, not refused.",
        [S(lambda s: op.delete(s, "capability", "pay_supplier"),
           "delete Capability pay_supplier")],
        [("payer grants nothing",
          lambda s: s.role("payer").capabilities == []),
         ("the payer role remains", lambda s: s.role("payer") is not None)],
        ["payer", "ops_lead"]),
    Scenario(
        "delete_fired_agent", "Delete an agent a trigger must fire",
        "Deleting the analyst is refused while the nightly trigger fires it: "
        "a trigger fires exactly one agent, so that link is not destroyed "
        "and the dangling reference names the trigger. Deleting the trigger "
        "is not enough either: month_end's first step calls the analyst, and "
        "a step that calls an agent must name one.",
        [S(lambda s: op.delete(s, "agent", "analyst"),
           "delete Agent analyst", "references_resolve"),
         S(lambda s: op.delete(s, "trigger", "nightly"),
           "delete Trigger nightly"),
         S(lambda s: op.delete(s, "agent", "analyst"),
           "delete Agent analyst", "actions_name_what_they_call")],
        [("analyst is still there — month_end still calls it",
          lambda s: s.agent("analyst") is not None)],
        ["analyst", "nightly", "month_end"]),
    Scenario(
        "delete_agent", "Delete an agent nothing requires",
        "The clerk leaves. Its team loses a member and its leader; nothing "
        "else referred to it.",
        [S(lambda s: op.delete(s, "agent", "clerk"), "delete Agent clerk")],
        [("clerk is gone", lambda s: s.agent("clerk") is None),
         ("ap has no leader", lambda s: _team(s, "ap").leader == "")],
        ["ap"]),
    Scenario(
        "delete_called_tool", "Delete a tool a workflow step calls",
        "Deleting ledger_lookup would leave month_end's lookup step calling "
        "nothing, so it is refused and names the step.",
        [S(lambda s: op.delete(s, "tool", "ledger_lookup"),
           "delete Tool ledger_lookup", "actions_name_what_they_call")],
        [], ["month_end", "ledger_lookup"]),
    Scenario(
        "policy", "A policy about a role, and one about nobody",
        "A deny on the ledger for the payer role is accepted: a Role is a "
        "Principal and a DataClass a Resource. One naming a subject that "
        "does not exist is refused.",
        [S(lambda s: op.create(s, "policy", "no_ledger_for_payers",
                               effect="deny", subjects=["payer"],
                               resource_kinds=["data_class"],
                               resources=["ledger"]),
           "create Policy no_ledger_for_payers {deny payer → ledger}"),
         S(lambda s: op.create(s, "policy", "ghost_rule", effect="deny",
                               subjects=["ghost"]),
           "create Policy ghost_rule {deny ghost}", "references_resolve")],
        [("one policy", lambda s: [p.id for p in s.policies]
          == ["no_ledger_for_payers"])],
        ["no_ledger_for_payers", "payer", "ledger"]),
    Scenario(
        "separation_of_one", "A separation that keeps one decision apart",
        "A separation keeps two or more decisions apart; one naming a single "
        "decision separates nothing.",
        [S(lambda s: op.create(s, "separation", "lonely",
                               decisions=["approve_payment"]),
           "create Separation lonely {approve_payment}",
           "multiplicities_hold")],
        [], ["four_eyes"]),
    Scenario(
        "successor", "Name a successor, and refuse an agent as its own",
        "The analyst stands in for the ops lead; the ops lead cannot stand "
        "in for itself.",
        [S(lambda s: _attr(s, "ops_lead", successor="analyst"),
           "set Agent ops_lead successor := analyst"),
         S(lambda s: _attr(s, "analyst", successor="analyst"),
           "set Agent analyst successor := analyst", "no_self_successor")],
        [("ops_lead's successor is analyst",
          lambda s: _agent(s, "ops_lead").successor == "analyst")],
        ["ops_lead", "analyst"]),
    Scenario(
        "root_unplaced", "Stop the organisation being a placement boundary",
        "Every agent must have exactly one placement; the root always "
        "declares one, so it cannot be switched off.",
        [S(lambda s: _org_attr(s, placement=False),
           "set Organization acme placement := false", "root_places")],
        [], ["acme"]),
    Scenario(
        "edge_to_nowhere", "A workflow edge to a step that does not exist",
        "month_end is given an edge from lookup to a step nobody declared.",
        [S(lambda s: _edge(s, "month_end", "lookup", "publish"),
           "add ControlFlow lookup → publish to Workflow month_end",
           "control_flow_ends")],
        [], ["month_end"]),
    Scenario(
        "flow_to_self", "A flow from an agent to itself",
        "The analyst is set to consult itself. A flow joins two agents.",
        [S(lambda s: op.link(s, ("agent", "analyst"), ("agent", "analyst"),
                             "flow", kind="consult"),
           "link Agent analyst —consult→ Agent analyst",
           "flows_join_two_agents")],
        [], ["analyst"]),
]


def _attr(s: Spec, agent: str, **attrs: Any) -> op.Result:
    """Set an agent's own attributes, checked like any operation."""
    def change(work: Spec, effects: list[str]) -> None:
        for k, v in attrs.items():
            setattr(work.agent(agent), k, v)
    return op._transact(s, change)


def _org_attr(s: Spec, **attrs: Any) -> op.Result:
    def change(work: Spec, effects: list[str]) -> None:
        for k, v in attrs.items():
            setattr(work.organization, k, v)
    return op._transact(s, change)


def _edge(s: Spec, workflow: str, source: str, target: str) -> op.Result:
    def change(work: Spec, effects: list[str]) -> None:
        wf = next(w for w in work.workflows if w.id == workflow)
        wf.graph.edges.append(spec_model.ControlFlow.model_validate(
            {"from": source, "to": target}))
    return op._transact(s, change)


def _mandate(s: Spec, agent: str, decision: str) -> op.Result:
    """Mandates are attributes, not links; changing one is still checked."""
    def change(work: Spec, effects: list[str]) -> None:
        a = work.agent(agent)
        a.mandate = a.mandate or spec_model.Mandate()
        a.mandate.decisions.append(decision)
    return op._transact(s, change)


def play(scenario: Scenario) -> Played:
    """Run a scenario from the base; report every outcome that differed from
    what it declares."""
    spec = base()
    failures: list[str] = []
    outcomes: list[tuple[Step, op.Result]] = []
    for step in scenario.steps:
        result = step.run(spec)
        outcomes.append((step, result))
        if step.refused_by is None and not result.accepted:
            failures.append(f"'{step.text}' was refused: "
                            f"{[str(v) for v in result.violations]}")
        elif step.refused_by is not None:
            if result.accepted:
                failures.append(f"'{step.text}' was accepted; "
                                f"{step.refused_by} should refuse it")
            elif step.refused_by not in op.violations_of(result):
                failures.append(f"'{step.text}' was refused by "
                                f"{sorted(op.violations_of(result))}, not "
                                f"{step.refused_by}")
        spec = result.spec
    for text, predicate in scenario.then:
        if not predicate(spec):
            failures.append(f"then: {text} — false")
    if check(spec):
        failures.append(f"the final model is invalid: {check(spec)}")
    return Played(scenario, spec, outcomes, failures)


# -- presentation ------------------------------------------------------------

def to_object_diagram(played: Played) -> str:
    """The model after the scenario, as a UML object diagram of the focus
    instances and whatever they are linked to."""
    from . import PROFILE, RelKind, Shape
    model = collect(played.spec)
    focus = set(played.scenario.focus)
    shown = {i.ref: i for i in model if i.id in focus
             and i.kind not in ("action", "control_flow", "system")}
    edges: list[str] = []

    def oid(inst: Any) -> str:
        return f"{inst.kind}_{inst.id}".replace("#", "_").replace("-", "_")

    for inst in list(model):
        if inst.kind in ("system", "control_flow", "action"):
            continue
        for rel in PROFILE.relationships:
            if not rel.field or rel.source not in (inst.kind,) and \
                    inst.kind not in _subs(rel.source):
                continue
            if rel.shape is Shape.RECORD:
                continue
            values: list[str] = []
            if rel.shape is Shape.PART:
                obj: Any = inst.obj
                for step in rel.field.split("."):
                    obj = getattr(obj, step, [])
                values = [getattr(x, "id", "") for x in obj] if \
                    isinstance(obj, list) else []
            else:
                from .constraints import _values
                values = _values(rel, inst.obj)
            for v in values:
                tgt = model.get(rel.target, v)
                if tgt is None or not ({inst.id, tgt.id} & focus):
                    continue
                if inst.kind in ("organization",) and tgt.id not in focus:
                    continue
                shown.setdefault(inst.ref, inst)
                shown.setdefault(tgt.ref, tgt)
                arrow = "*--" if rel.kind is RelKind.COMPOSITION else (
                    "..>" if rel.kind in (RelKind.USAGE, RelKind.DEPLOYMENT,
                                          RelKind.REALIZATION) else "-->")
                label = rel.stereotype
                if rel.shape is Shape.REF_OBJECTS:
                    # The association-class instance's own attributes.
                    link_obj = next(x for x in getattr(inst.obj, rel.field)
                                    if getattr(x, rel.key) == v)
                    attrs = link_obj.model_dump(mode="json",
                                                exclude_defaults=True)
                    attrs.pop(rel.key, None)
                    if attrs:
                        label += "\\n{" + ", ".join(
                            f"{k}: {val}" for k, val in attrs.items()) + "}"
                edges.append(f"{oid(inst)} {arrow} {oid(tgt)} : {label}")
    for rec_field in ("interaction_flows", "unit_links"):
        for rec in getattr(played.spec.organization, rec_field):
            kind = "agent" if rec_field == "interaction_flows" else "team"
            a, b = model.get(kind, rec.source), model.get(kind, rec.target)
            if a and b and ({a.id, b.id} & focus):
                shown.setdefault(a.ref, a)
                shown.setdefault(b.ref, b)
                edges.append(f"{oid(a)} --> {oid(b)} : "
                             f"{spec_model.FlowKind(rec.kind).value if rec_field == 'interaction_flows' else spec_model.UnitLinkKind(rec.kind).value}")

    out = [f"@startuml {played.scenario.id}", "!pragma layout smetana",
           f"title {played.scenario.title}"]
    names = {s.kind: s.name for s in PROFILE.stereotypes}
    for inst in shown.values():
        label = f'"{inst.id} : {names.get(inst.kind, inst.kind)}"'
        extra = ""
        if inst.kind == "team" or inst.kind == "organization":
            extra = f"leader = {inst.obj.leader or '—'}"
        elif inst.kind == "subagent":
            extra = f"capabilities = {inst.obj.capabilities}"
        out.append(f"object {label} as {oid(inst)}" +
                   (f" {{\n  {extra}\n}}" if extra else ""))
    out.extend(dict.fromkeys(edges))
    out.append("@enduml")
    return "\n".join(out) + "\n"


def _subs(kind: str) -> list[str]:
    from . import specialisations
    return specialisations(kind)


def catalogue() -> str:
    """Every scenario as Markdown: story, steps and the model's answer."""
    lines = ["# Model scenarios", "",
             "Generated by `orgagents metamodel scenarios` from "
             "`orgagents/metamodel/scenarios.py` (ADR-0102). Each is played "
             "on the model alone, from the same small base organisation, by "
             "the test suite; the diagram is the model afterwards.", ""]
    for sc in SCENARIOS:
        played = play(sc)
        lines += [f"## {sc.title}", "", sc.story, "",
                  "| Step | Model's answer |", "|---|---|"]
        for step, result in played.outcomes:
            if result.accepted:
                answer = "accepted" + (
                    ": " + "; ".join(result.effects) if result.effects else "")
            else:
                answer = "refused — " + "; ".join(
                    str(v) for v in result.violations)
            lines.append(f"| {step.text} | {answer.replace('|', '/')} |")
        if sc.then:
            lines += ["", "Then: " + "; ".join(t for t, _ in sc.then) + "."]
        lines += ["", f"![{sc.id}](scenarios/{sc.id}.png)", ""]
    return "\n".join(lines)
