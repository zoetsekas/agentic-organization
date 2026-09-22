"""LangGraph Platform / LangSmith as a target (ADR-0005).

The third platform target, after MAF and ADK. Its deployable unit is a
LangGraph *graph* served by LangGraph Platform, with tracing and evaluation in
LangSmith. The `langchain_deepagents` runtime this platform already ships
(`orgagents.runtime.adapters.DeepAgentsAdapter`) builds exactly such a graph
with `deepagents.create_deep_agent`, so this target emits the same shape as a
deployable package: one graph module per agent, a `langgraph.json` deployment
manifest that LangGraph Platform reads, a `.env` template for the LangSmith and
model credentials, and the same `CONFORMANCE.md` the other platform targets
write, for the same reason.

This is the answer to "langchain deep agents, deployed to the LangChain /
LangSmith platform" — the counterpart to running the same `langchain_deepagents`
runtime under `terraform:gcp`, which is "langchain deep agents, deployed to
Google Cloud". The runtime is one choice (a binding's `runtime.adapter`); where
it is deployed is another (the target). They compose.

deepagents carries more of a design than ADK does — it has `subagents` (a
delegation hierarchy), `interrupt_on` (a real human-in-the-loop gate on named
tools), and a filesystem-permission model — so more of the authority half
survives here than under MAF or ADK, and the conformance page says exactly how
much. But it still has no role, permission, mandate, separation, autonomy or
data-class model; that half is enforced at this platform's harness boundary or
nowhere (ADR-0067 rule 5). A control that reads as enforced and is not is worse
than no control (ADR-0073), which is why the report exists.
"""
from __future__ import annotations

import json
from typing import Any

from ..base import GeneratedFile
from ..ir import SystemIR
from ._wiring import BACKENDS_SHIM, emit_wired_def, shim_imports, wired_and_stubbed

#: What a deepagents graph on LangGraph Platform can actually carry.
EXPRESSIBLE = (
    "system_prompt (instructions), model, tools",
    "subagents (a spawned delegation hierarchy, one level)",
    "interrupt_on (a human-in-the-loop gate on named tools)",
    "filesystem permissions (the virtual filesystem, governed)",
    "a model-call limit (max turns)",
    "LangSmith tracing and evaluation (a deployment fact, wired by .env)",
)

#: Our model-class vocabulary → a sensible default when a binding names no
#: model. deepagents takes a "provider:model" string.
_DEFAULT_BY_CLASS = {
    "frontier_reasoning": "anthropic:claude-sonnet-4-5",
    "balanced": "anthropic:claude-haiku-4-5",
    "fast_cheap": "anthropic:claude-haiku-4-5",
}


class LangGraphPlatformTarget:
    """Emit a deepagents graph package for LangGraph Platform + LangSmith, and
    a conformance report for everything the graph cannot carry."""

    id = "langgraph"

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": "LangGraph Platform + LangSmith (deepagents)",
            "emits": (
                "a deepagents package — one graph module per agent, a "
                "langgraph.json deployment manifest, a .env template for "
                "LangSmith and model credentials — plus a conformance report"
            ),
            "governance": "partial — subagents and interrupt_on survive; the "
            "authority model does not. See CONFORMANCE.md",
            "note": (
                "This is the same `langchain_deepagents` runtime this platform "
                "runs under `terraform:gcp`, packaged for the LangChain hosted "
                "platform instead of your own cloud. deepagents has subagents, "
                "an interrupt gate and a filesystem-permission model, so more "
                "of a design survives than under MAF or ADK; but it has no "
                "role, permission, mandate or separation model, so the "
                "authority half is enforced at this platform's harness boundary "
                "or not at all (ADR-0067 rule 5)."
            ),
            "caveats": [
                "These files carry no tenant boundary. A graph package is not a "
                "deployment; tenant isolation is the infrastructure target's "
                "job (ADR-0050), and LangGraph Platform's own project is set at "
                "deploy time.",
                "deepagents `subagents` spawn one level deep. A deeper org tree "
                "is flattened to each agent's direct reports; the report says "
                "so.",
                "A tool needs its callable at registration. The tool surface an "
                "agent really sees is assembled by this platform's harness at "
                "run time and is not in the IR, so the emitted tools are stubs.",
                "`interrupt_on` pauses the graph for a human, but *which* human "
                "(the paired approver with the mandate) is this platform's "
                "model, not deepagents'. The gate survives; the routing does "
                "not.",
                "No role, permission, mandate, separation, autonomy posture or "
                "data-class egress rule survives. CONFORMANCE.md lists what "
                "that costs.",
            ],
        }

    # -- files -------------------------------------------------------------

    def generate(self, ir: SystemIR) -> list[GeneratedFile]:
        files: list[GeneratedFile] = []
        for agent in ir.agents:
            files.append(
                GeneratedFile(
                    f"graphs/{_mod(agent.id)}.py", self._agent_module(ir, agent)
                ).with_header(ir, comment="#")
            )
        files.append(
            GeneratedFile("graphs/__init__.py", self._package_init(ir))
            .with_header(ir, comment="#")
        )
        if self._any_wired(ir):
            files.append(
                GeneratedFile("graphs/_backends.py", BACKENDS_SHIM)
                .with_header(ir, comment="#")
            )
        files.append(GeneratedFile("langgraph.json", self._manifest(ir)))
        files.append(GeneratedFile("requirements.txt", self._requirements(ir)))
        files.append(
            GeneratedFile(".env.example", self._env_example(ir),
                          preserve_if_exists=True)
        )
        files.append(
            GeneratedFile("CONFORMANCE.md", self._conformance(ir))
            .with_header(ir, comment="<!--")
        )
        files.append(GeneratedFile("README.md", self._readme(ir)))
        return files

    # -- one agent ---------------------------------------------------------

    def _agent_module(self, ir: SystemIR, agent: Any) -> str:
        by_id = {a.id: a for a in ir.agents}
        model = self._model_id(agent)
        system_prompt = _pytext(agent.system_prompt() or "")
        tools = self._tool_surface(agent)
        gated = [t["name"] for t in tools if t["gated"]]
        wired, stubbed = wired_and_stubbed(ir, tools)
        needs = {t["backend"]["kind"] for t in wired}

        lines = [
            "from deepagents import create_deep_agent",
        ]
        if tools:
            lines.append("from langchain_core.tools import StructuredTool")
        shim = shim_imports(needs)
        if shim:
            lines.append(f"from ._backends import {', '.join(shim)}")
        lines.append("")

        # Wired tools: a real client to the declared backend, not a stub.
        for tool in wired:
            lines += emit_wired_def(tool)
            lines.append("")
        # Stubs remain only where the design binds no backend (ADR-0085).
        for tool in stubbed:
            lines += [
                f"def {_mod(tool['name'])}(**kwargs):",
                f'    """{tool["description"] or tool["name"]}"""',
                "    # No server bound for this capability; the callable is the "
                "host's.",
                f'    raise NotImplementedError("bind {tool["name"]} in host '
                'code")',
                "",
            ]

        # deepagents subagents from the org chart's direct delegation edges.
        subagents = []
        for child_id in sorted(agent.delegates_to):
            child = by_id.get(child_id)
            if child is None:
                continue
            subagents.append({
                "name": child.id,
                "description": child.description,
                "prompt": child.system_prompt() or child.description,
            })

        if subagents:
            lines.append("SUBAGENTS = [")
            for sub in subagents:
                lines.append("    {")
                lines.append(f'        "name": {_pystr(sub["name"])},')
                lines.append(
                    f'        "description": {_pystr(sub["description"])},')
                lines.append(f'        "prompt": {_pytext(sub["prompt"])},')
                lines.append("    },")
            lines.append("]")
            lines.append("")

        lines.append(f"{_mod(agent.id)} = create_deep_agent(")
        lines.append(f"    model={_pystr(model)},")
        if tools:
            names = ", ".join(
                f"StructuredTool.from_function(func={_mod(t['name'])}, "
                f"name={_pystr(t['name'])})" for t in tools)
            lines.append(f"    tools=[{names}],")
        else:
            lines.append("    tools=[],")
        lines.append(f"    system_prompt={system_prompt},")
        if subagents:
            lines.append("    subagents=SUBAGENTS,")
        if gated:
            # A real human-in-the-loop gate, in the framework's own terms.
            pairs = ", ".join(f"{_pystr(name)}: True" for name in gated)
            lines.append(f"    interrupt_on={{{pairs}}},")
        limit = self._max_turns(agent)
        if limit:
            lines.append(
                f"    # max turns {limit}: add ModelCallLimitMiddleware in "
                "host code (deepagents middleware=...)")
        lines.append(")")

        gaps = self._agent_gaps(agent, tools)
        if gaps:
            lines.append("")
            lines.append("# Carried by the design and NOT by this graph. "
                         "Nothing below is")
            lines.append("# enforced by serving this graph on LangGraph "
                         "Platform:")
            for item in gaps:
                lines.append(f"#   - {item}")
        return "\n".join(lines) + "\n"

    def _package_init(self, ir: SystemIR) -> str:
        """Import every graph and name the root — the org's entry point."""
        lines = ['"""Every agent graph; `root` is the org entry point."""']
        for agent in ir.agents:
            lines.append(f"from .{_mod(agent.id)} import {_mod(agent.id)}")
        lines.append("")
        root = ir.root_agent_id if getattr(ir, "root_agent_id", None) else (
            ir.agents[0].id if ir.agents else "")
        lines.append(f"root = {_mod(root)}" if root else "root = None")
        lines.append("")
        return "\n".join(lines) + "\n"

    def _manifest(self, ir: SystemIR) -> str:
        """The langgraph.json LangGraph Platform reads: which graphs to serve,
        the dependency root and the env file."""
        graphs: dict[str, str] = {}
        root = ir.root_agent_id if getattr(ir, "root_agent_id", None) else (
            ir.agents[0].id if ir.agents else "")
        if root:
            graphs["org"] = f"./graphs/__init__.py:root"
        for agent in ir.agents:
            graphs[agent.id] = f"./graphs/{_mod(agent.id)}.py:{_mod(agent.id)}"
        manifest = {
            "dependencies": ["."],
            "graphs": graphs,
            "env": ".env",
            "python_version": "3.12",
        }
        return json.dumps(manifest, indent=2) + "\n"

    def _any_wired(self, ir: SystemIR) -> bool:
        return any(
            t.get("backend")
            for agent in ir.agents
            for t in wired_and_stubbed(ir, self._tool_surface(agent))[0]
        )

    def _requirements(self, ir: SystemIR) -> str:
        kinds = {
            t["backend"]["kind"]
            for agent in ir.agents
            for t in wired_and_stubbed(ir, self._tool_surface(agent))[0]
        }
        lines = [
            "deepagents>=0.0.5",
            "langgraph>=0.2",
            "langgraph-cli[inmem]>=0.1",
            "langchain>=0.3",
            "langchain-anthropic>=0.2",
            "# add the langchain-<provider> package your binding's model uses",
        ]
        if "mcp" in kinds:
            lines.append("langchain-mcp-adapters>=0.1  # wired MCP capabilities")
        if "database" in kinds:
            lines.append("sqlalchemy>=2.0  # wired database capabilities")
        return "\n".join(lines) + "\n"

    def _env_example(self, ir: SystemIR) -> str:
        return (
            "# LangSmith tracing and the hosted platform. Get a key at "
            "https://smith.langchain.com\n"
            "LANGSMITH_API_KEY=\n"
            "LANGSMITH_TRACING=true\n"
            f"LANGSMITH_PROJECT={ir.name}\n"
            "\n"
            "# The model provider your binding names (deepagents takes a "
            "'provider:model' string).\n"
            "ANTHROPIC_API_KEY=\n"
            "# OPENAI_API_KEY=\n"
            "# GOOGLE_API_KEY=\n"
        )

    # -- shared helpers ----------------------------------------------------

    def _tool_surface(self, agent: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        gated = set(agent.requires_approval_for)
        by_cap = {c.id: c for c in agent.capabilities}
        for tool in agent.tools:
            cap = by_cap.get(tool.wraps) if tool.wraps_kind == "capability" else None
            out.append(self._constraints({
                "name": tool.id, "description": tool.description,
                "gated": tool.requires_approval or tool.id in gated,
            }, cap))
        for capability in agent.capabilities:
            if any(t["name"] == capability.id for t in out):
                continue
            out.append(self._constraints({
                "name": capability.id,
                "description": capability.description,
                "gated": capability.id in gated
                or bool(capability.constraints.requires_approval),
            }, capability))
        return out

    def _constraints(self, tool: dict[str, Any], cap: Any) -> dict[str, Any]:
        """Attach a capability's read bound (operation allowlist, row cap) so a
        database-backed tool can enforce it."""
        if cap is not None:
            tool["allowed_operations"] = list(cap.constraints.allowed_operations)
            tool["max_rows"] = cap.constraints.max_rows or 0
        else:
            tool["allowed_operations"] = []
            tool["max_rows"] = 0
        return tool

    def _model_id(self, agent: Any) -> str:
        model = agent.model or {}
        provider = model.get("provider")
        name = model.get("model")
        if provider and name:
            return f"{provider}:{name}"
        if name and ":" in name:
            return name
        classes = agent.model_policy.classes if hasattr(
            agent.model_policy, "classes") else None
        if classes:
            return _DEFAULT_BY_CLASS.get(classes[0], "anthropic:claude-haiku-4-5")
        return "anthropic:claude-haiku-4-5"

    def _max_turns(self, agent: Any) -> int:
        model = agent.model or {}
        return int(model.get("max_turns") or 0)

    def _agent_gaps(self, agent: Any, tools: list[dict[str, Any]]) -> list[str]:
        out: list[str] = []
        if agent.mandate.decisions:
            out.append(
                f"mandate: may decide {sorted(agent.mandate.decisions)}"
                + (f" under {agent.mandate.conditions}"
                   if agent.mandate.conditions else "")
            )
        if agent.permissions:
            out.append(f"{len(agent.permissions)} resolved permissions")
        gated = [t for t in tools if t["gated"]]
        if gated:
            out.append(
                f"{len(gated)} tool(s) gated by interrupt_on — the graph pauses "
                f"for a human, but the paired approver who may resume it is "
                f"this platform's model, not deepagents': "
                f"{sorted(t['name'] for t in gated)}"
            )
        if agent.guardrails:
            out.append(
                f"{len(agent.guardrails)} guardrail(s) — expressible as "
                "deepagents middleware in host code, never in this graph")
        for placement in agent.placements:
            out.append(f"placement '{placement}' and its network policy")
        for environment in agent.environments:
            out.append(
                f"sandbox '{environment.id}': network "
                f"{environment.network.value}")
        if agent.humans:
            out.append(
                f"{len(agent.humans)} paired human(s), including the approver "
                "an interrupt would route to")
        return out

    # -- the report --------------------------------------------------------

    def _conformance(self, ir: SystemIR) -> str:
        rows = [
            ("Instructions, model, tools", "emitted", "deepagents",
             "The portable core. Every platform has it."),
            ("Sub-agent hierarchy", "emitted (one level)", "deepagents",
             "Our org chart's direct delegation edges become deepagents "
             "`subagents`, spawned as isolated sub-runs. A deeper tree is "
             "flattened to each agent's direct reports; deepagents nests one "
             "level."),
            ("Human-in-the-loop gate", "emitted", "deepagents `interrupt_on`",
             "A gated tool becomes a real interrupt: the graph pauses for a "
             "human before that tool runs. This is more than MAF or ADK carry."),
            ("Which human approves", "**no model**", "our harness",
             "deepagents pauses; it has no principal-with-a-mandate to resume "
             "it, so *which* human approves is routed by this platform, not the "
             "graph."),
            ("Filesystem permissions", "emitted", "deepagents",
             "The virtual filesystem is governed by FilesystemPermission "
             "rules; the harness derives the same rules it runs with."),
            ("Tool callables", "emitted for bound capabilities", "the "
             "servers catalog",
             "A capability the binding puts behind a declared server (MCP or "
             "database, ADR-0085) is emitted as a working client in "
             "`_backends.py` — an MCP call, or a bounded SQL query enforcing "
             "the design's operation allowlist and row cap. Only a capability "
             "with no server bound stays a stub, and credentials are env-var "
             "names, never values."),
            ("The rest of the tool surface", "**not in the IR**",
             "our harness only",
             "MCP mounts and built-in tool families are assembled by this "
             "platform's harness at run time and never lowered into the IR, so "
             "no target can emit them."),
            ("Roles and permissions", "**no model**", "our harness",
             "deepagents has no permission type. Nothing to emit into."),
            ("Mandates (what may be decided)", "**no model**", "our harness",
             "No framework has this; ADR-0067 rule 5 keeps enforcement at our "
             "tool boundary."),
            ("Separation of duties", "**no model**", "our phase gate",
             "Refused at compile time or not at all; there is no run-time place "
             "for it in a graph."),
            ("Autonomy postures", "**no model**", "our harness",
             "deepagents runs an agent loop; it has no "
             "advisory/supervised/autonomous distinction to map four postures "
             "onto."),
            ("People as principals", "**no model**", "our harness",
             "A LangGraph thread has a user id, not a mandate-holding "
             "principal."),
            ("Placement and network policy", "**out of scope**",
             "the infrastructure target",
             "A graph package is not a deployment; emit `terraform:gcp` "
             "alongside to place it on your own cloud, or serve it on LangGraph "
             "Platform ungoverned."),
            ("Data classes and egress rules", "**no model**", "our harness",
             "deepagents has no confidentiality-label or egress model."),
            ("Guardrails", "partial", "deepagents middleware",
             "Expressible as middleware the host writes, never in the emitted "
             "graph."),
            ("Budgets and model policy", "partial", "middleware",
             "A turn ceiling maps to ModelCallLimitMiddleware; spend does "
             "not."),
            ("Tracing and evaluation", "wired", "LangSmith",
             "LANGSMITH_* in .env turns on tracing and opens the door to "
             "LangSmith evaluations — a deployment fact, not a design one."),
        ]
        counts = {
            "agents": len(ir.agents),
            "sub-agent edges": sum(len(a.delegates_to) for a in ir.agents),
            "agents with a mandate": sum(
                1 for a in ir.agents if a.mandate.decisions),
            "interrupt-gated tools": sum(
                len(a.requires_approval_for) for a in ir.agents),
            "resolved permissions": sum(len(a.permissions) for a in ir.agents),
        }
        table = "\n".join(
            f"| {w} | {s} | {who} | {why} |" for w, s, who, why in rows)
        tally = "\n".join(f"- **{v}** {k}" for k, v in counts.items())
        return f"""# What this target cannot enforce

Generated for `{ir.name}`, target `langgraph` (LangGraph Platform + LangSmith,
deepagents runtime).

A design carries more than a deepagents graph can. This page names what, and
who enforces it instead — because a target that silently dropped a mandate
would be generating a lie, and a control that reads as enforced and is not is
worse than no control (ADR-0073).

deepagents is the most faithful of the platform targets: `subagents` carry the
delegation shape and `interrupt_on` is a real human-in-the-loop gate. But the
authority model — who may decide what, which duties stay separated, which
human resumes a paused run — is still this platform's, not the framework's.

## This design

{tally}

## Construct by construct

| Construct | In this target | Enforced by | Why |
|---|---|---|---|
{table}

## What that means when you deploy these files

`langgraph up` (local) or a LangGraph Platform deployment serves these graphs,
with LangSmith tracing on. You get the agents, their instructions, their
one-level sub-agent hierarchy, a genuine interrupt gate on the tools the design
marks for approval, and tool *stubs*. You do not get the organization: no
permission is checked, no mandate bounds a decision, no separation of duties
survives, and while the graph pauses for a human, *which* human — the paired
approver with the mandate — is this platform's model, not deepagents'.

That is not a defect in deepagents or LangGraph — they are an agent framework
and a serving platform, and these are not their concerns. It is why this
platform keeps governance at its own tool boundary rather than delegating it to
a framework as the only check (ADR-0067 rule 5).

To run this design **governed**, deploy the `local` or `terraform:gcp` target,
which carries the harness — the same `langchain_deepagents` runtime, on your
own cloud. To run it on the **hosted LangChain platform**, use these files and
accept the table above.
"""

    def _readme(self, ir: SystemIR) -> str:
        return f"""# {ir.name} — LangGraph Platform + LangSmith (deepagents)

A deepagents graph package for the hosted LangChain platform:

- `graphs/` — one `create_deep_agent` graph per agent, with tool stubs and
  `subagents` wired from the org chart.
- `graphs/__init__.py` — names `root`, the org's entry point.
- `langgraph.json` — the deployment manifest LangGraph Platform reads: `org`
  plus one graph per agent.
- `.env.example` — LangSmith and model-provider credentials.
- `requirements.txt` — `deepagents`, `langgraph`, `langchain`.

Run locally with `langgraph dev` (or `langgraph up`), or deploy to LangGraph
Platform from this directory. Tracing and evaluation light up in LangSmith once
`LANGSMITH_API_KEY` is set.

This is the **same `langchain_deepagents` runtime** this platform runs under
`terraform:gcp` — the difference is only where it is served: the hosted
LangChain platform here, your own Google Cloud there. The runtime is a binding
choice; the destination is a target. They compose.

Tools behind a declared server are wired for real: a capability the binding
puts on an MCP or database server (ADR-0085) is emitted in `graphs/_backends.py`
as a working client — an MCP call, or a bounded SQL query that enforces the
design's operation allowlist and row cap. Set the credential env vars the
binding named. Only a capability with no server bound stays a stub you fill in.

**Read `CONFORMANCE.md` first.** {len(ir.agents)} agents come across, with
their delegation hierarchy and a real interrupt gate; the authority model does
not, and the report says what that costs.
"""


def _mod(identifier: str) -> str:
    """A safe Python identifier from a spec id."""
    safe = "".join(c if c.isalnum() else "_" for c in identifier)
    return safe if safe and not safe[0].isdigit() else f"a_{safe}"


def _pystr(value: str) -> str:
    return json.dumps(value or "")


def _pytext(value: str) -> str:
    """A triple-quoted Python string literal for a multi-line instruction."""
    if not value:
        return '""'
    escaped = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'"""{escaped}"""'
