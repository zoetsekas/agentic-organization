"""Workflow graphs (ADR-0096) and the seam to an engine (ADR-0110).

The graph is a free-form dict and nothing checked its shape, so a step that
could never run was found at run time or not at all. That is the same failure
as an edge silently dropped: a declared step that does not happen, and
nothing saying so.
"""
from __future__ import annotations

from typing import Any

from ..model import SystemSpec
from .context import ValidationContext
from .findings import Finding
from .registry import rule

SECTION = "workflow graphs"


@rule(SECTION, {
    "workflow_external_without_interface", "workflow_approval_without_approver",
    "workflow_step_owner_unknown", "workflow_step_owner_lacks_capability",
    "workflow_step_sends_unheld_data", "workflow_without_nodes",
    "workflow_entry_unknown", "workflow_edge_from_unknown",
    "workflow_edge_to_unknown", "workflow_branch_target_unknown",
    "workflow_fork_without_join", "workflow_join_without_fork",
    "workflow_node_unreachable",
})
def workflow_graphs(ctx: ValidationContext) -> None:
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    agent_ids, team_ids = ctx.agent_ids, ctx.team_ids
    workflows_by_id = ctx.workflows_by_id
    person_ids, role_ids = ctx.person_ids, ctx.role_ids
    for workflow in spec.workflows:
        # -- the seam to an engine (ADR-0110) ------------------------------
        external = workflow.body.value == "external"
        if external:
            if workflow.interface.is_empty():
                err("workflow_external_without_interface",
                    f"workflow '{workflow.id}' is built outside this design "
                    "(body: external) and declares no interface, so nothing "
                    "about what it receives, calls or returns can be checked",
                    workflow.id)
            # An external body has no graph of ours to check.
            continue
        graph: Any = workflow.graph or {}
        nodes = {n.get("id"): n for n in graph.get("nodes", []) if n.get("id")}
        for node_id, node in nodes.items():
            kind = node.get("kind")
            owner = node.get("owner") or (
                node.get("agent") if kind == "agent" else "")
            if kind == "human" and not (
                    node.get("person") or node.get("role") or node.get("owner")):
                warn("workflow_approval_without_approver",
                     f"workflow '{workflow.id}' pauses at '{node_id}' for a "
                     "person without naming who: give it a `person` or a "
                     "`role`, or anyone who sees the pause may answer it",
                     workflow.id, strict=True)
            for ref, known, what in (
                    (node.get("person"), person_ids, "person"),
                    (node.get("role"), role_ids, "role")):
                if kind == "human" and ref and ref not in known:
                    err("workflow_step_owner_unknown",
                        f"workflow '{workflow.id}' step '{node_id}' names "
                        f"{what} '{ref}', which this design does not declare",
                        workflow.id)
            if owner and owner not in agent_ids and owner not in team_ids \
                    and owner not in person_ids:
                err("workflow_step_owner_unknown",
                    f"workflow '{workflow.id}' step '{node_id}' is owned by "
                    f"'{owner}', which is not an agent, team or person here",
                    workflow.id)
                continue
            called = workflows_by_id.get(node.get("workflow", "")) \
                if kind == "workflow" else None
            if called is None or called.interface.is_empty() or not owner:
                continue
            # The interface is checked as if it were the step, against the
            # step's owner: what it calls must be the owner's to call, and
            # what it is sent must be the owner's to send (ADR-0110).
            held: set[str]
            tools: set[str]
            endpoints: set[str]
            if owner in agent_ids:
                held = spec.effective_capabilities(owner)
                owner_agent = spec.agent(owner)
                tools = set(owner_agent.tools) if owner_agent else set()
                endpoints = set(owner_agent.endpoints) if owner_agent else set()
            elif owner in team_ids:
                held, tools, endpoints = spec.team_capabilities(owner), set(), set()
            else:
                # A person holds no capabilities; a person-owned step that
                # calls tools is the step a person should not be running.
                held, tools, endpoints = set(), set(), set()
            missing = sorted(
                [t for t in called.interface.tools
                 if t not in held and t not in tools]
                + [f"endpoint {e}" for e in called.interface.endpoints
                   if e not in endpoints])
            if missing:
                err("workflow_step_owner_lacks_capability",
                    f"workflow '{workflow.id}' step '{node_id}' calls "
                    f"'{called.id}', whose interface uses "
                    f"{', '.join(missing)}, which its owner '{owner}' does "
                    "not hold", workflow.id)
            sendable: set[str] = set()
            for cap_id in held:
                cap = spec.capability(cap_id)
                if cap is not None:
                    sendable |= set(cap.data_classes)
            unsendable = sorted(
                set(called.interface.receives_data_classes) - sendable)
            if unsendable:
                err("workflow_step_sends_unheld_data",
                    f"workflow '{workflow.id}' step '{node_id}' sends "
                    f"{', '.join(unsendable)} to '{called.id}', which its "
                    f"owner '{owner}' holds no capability over and so may "
                    "not send", workflow.id)
        if not nodes:
            warn("workflow_without_nodes",
                 f"workflow '{workflow.id}' declares no nodes, so invoking it "
                 "does nothing", workflow.id)
            continue
        entry = graph.get("entry") or next(iter(nodes))
        if entry not in nodes:
            err("workflow_entry_unknown",
                f"workflow '{workflow.id}' starts at '{entry}', which is not "
                "one of its nodes", workflow.id)
            continue

        # Every declared way out of a node, wherever it is declared.
        edges_out: dict[str, list[str]] = {}
        for edge in graph.get("edges", []):
            source, target = edge.get("from"), edge.get("to")
            if source not in nodes:
                err("workflow_edge_from_unknown",
                    f"workflow '{workflow.id}' has an edge from '{source}', "
                    "which is not one of its nodes", workflow.id)
                continue
            if target != "END" and target not in nodes:
                err("workflow_edge_to_unknown",
                    f"workflow '{workflow.id}' has an edge from '{source}' to "
                    f"'{target}', which is not one of its nodes", workflow.id)
                continue
            edges_out.setdefault(source, []).append(target)
        for node_id, node in nodes.items():
            if node.get("kind") != "branch":
                continue
            targets = [c.get("to") for c in node.get("cases", [])]
            if node.get("default"):
                targets.append(node["default"])
            for target in targets:
                if target != "END" and target not in nodes:
                    err("workflow_branch_target_unknown",
                        f"workflow '{workflow.id}' branches at '{node_id}' to "
                        f"'{target}', which is not one of its nodes",
                        workflow.id)
                    continue
                edges_out.setdefault(node_id, []).append(target)

        # Parallel work is drawn, so its shape is checked (ADR-0110): every
        # fork's branches meet at a join, and every join is where a fork's
        # branches meet.
        joined: set[str] = set()
        for fork_id in sorted(n for n, node in nodes.items()
                              if node.get("kind") == "fork"):
            found: set[str] = set()
            seen: set[str] = set()
            stack = list(edges_out.get(fork_id, []))
            while stack:
                current = stack.pop()
                if current == "END" or current in seen:
                    continue
                seen.add(current)
                if nodes.get(current, {}).get("kind") == "join":
                    found.add(current)
                    continue
                stack.extend(edges_out.get(current, []))
            if not found:
                err("workflow_fork_without_join",
                    f"workflow '{workflow.id}' forks at '{fork_id}' and its "
                    "branches never meet at a join, so nothing waits for "
                    "all of them to finish", workflow.id)
            joined |= found
        for join_id in sorted(n for n, node in nodes.items()
                              if node.get("kind") == "join"):
            if join_id not in joined:
                err("workflow_join_without_fork",
                    f"workflow '{workflow.id}' joins at '{join_id}', which no "
                    "fork's branches lead to, so it waits for branches that "
                    "never began", workflow.id)

        reached, stack = {entry}, [entry]
        while stack:
            current = stack.pop()
            for target in edges_out.get(current, []):
                if target != "END" and target not in reached:
                    reached.add(target)
                    stack.append(target)
        for node_id in sorted(set(nodes) - reached):
            err("workflow_node_unreachable",
                f"workflow '{workflow.id}' declares node '{node_id}', which "
                f"nothing reaches from '{entry}'. A step that cannot run is "
                "not a step: add the edge that leads to it, or remove it",
                workflow.id)


@rule(SECTION, {"workflow_external_unbound"}, stage="binding")
def workflow_binding_findings(spec: SystemSpec, binding: Any,
                              strict: bool = True) -> list[Finding]:
    """Findings that need a target's binding to decide (ADR-0096, ADR-0110).

    The spec alone cannot say whether an external workflow reaches an engine,
    because *which* engine runs a workflow is a binding choice (ADR-0056).
    So this is asked per target, by the compiler, with that target's
    binding — the same place a workflow either reaches its target or is
    declared absent. A binding that names no engine for an external workflow
    would run its interface through the built-in interpreter, which has no
    graph to run: the step would silently do nothing.

    `strict=False` is for a target compiled on the platform's default binding,
    with no binding document at all: nothing was chosen for that target, so
    its conformance report names every workflow it does not carry, and an
    unbound external workflow is a warning there rather than a refusal.
    """
    out: list[Finding] = []
    target = getattr(binding, "target", "") or "this target"
    for workflow in spec.workflows:
        if workflow.body.value != "external":
            continue
        bound = binding.workflow_binding(workflow.id) if binding is not None else None
        if bound is None or (bound.engine or "native") == "native":
            out.append(Finding(
                "error" if strict else "warning", "workflow_external_unbound",
                f"workflow '{workflow.id}' is built outside this design "
                f"(body: external), and the binding for {target} names no "
                "engine that runs it: bind it under `workflows:` with the "
                "engine and flow that hold its body",
                workflow.id))
    return out
