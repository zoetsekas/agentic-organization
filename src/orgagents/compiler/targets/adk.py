"""Google Agent Development Kit (ADK) + Vertex AI Agent Engine as a target.

The second platform target, after MAF (ADR-0005). Where MAF emits a
declarative `kind: Prompt` file, ADK's deployable unit is Python: an
`LlmAgent` object, deployed to Vertex AI Agent Engine. So this target emits a
small Python package — one module per agent, a root that wires the org tree
into ADK `sub_agents`, and an Agent Engine deployment script — plus the same
`CONFORMANCE.md` MAF writes, for the same reason.

ADK carries a little more of a design than MAF does, and the report says so
honestly: it has `sub_agents` (a delegation hierarchy, so our org tree comes
across as structure rather than being dropped), `before_*` / `after_*`
callbacks (hook points a guardrail *could* be enforced at, in host code), and
an `output_schema`. It still has no role, permission, mandate, separation,
autonomy or data-class model — that half of a design is enforced at this
platform's harness boundary or nowhere (ADR-0067 rule 5), exactly as with MAF.

A control that reads as enforced and is not is worse than no control
(ADR-0073), so a target that silently dropped a mandate into a Gemini agent
would be generating a lie. Hence the conformance page.
"""
from __future__ import annotations

import json
from typing import Any

from ..base import GeneratedFile
from ..ir import SystemIR
from ._wiring import (
    BACKENDS_SHIM,
    emit_wired_def,
    shim_imports,
    stub_module,
    tool_surface,
    wired_and_stubbed,
    workflow_conformance_rows,
)

#: What an ADK `LlmAgent` can actually carry, verified against the ADK API.
EXPRESSIBLE = (
    "name, description and instruction",
    "model (a Gemini model id, or a LiteLlm-wrapped provider)",
    "generate_content_config (temperature, max_output_tokens)",
    "tools (FunctionTool, with the callable supplied in host code)",
    "sub_agents (a delegation hierarchy)",
    "output_key and output_schema",
    "before_/after_ callbacks (hook points, enforced only if host code does)",
)

#: Our model-class / provider vocabulary → a Gemini model id. A binding may
#: name a model directly; absent that, a class maps to a sensible Gemini tier.
_GEMINI_BY_CLASS = {
    "frontier_reasoning": "gemini-2.5-pro",
    "balanced": "gemini-2.5-flash",
    "fast_cheap": "gemini-2.5-flash-lite",
}


class GoogleADKTarget:
    """Emit an ADK agent package for Vertex AI Agent Engine, and a conformance
    report for everything ADK cannot carry."""

    id = "adk"

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": "Google ADK + Vertex AI Agent Engine",
            "emits": (
                "a Python ADK package — one LlmAgent module per agent, a root "
                "that wires sub_agents, an Agent Engine deploy script — plus a "
                "conformance report"
            ),
            "governance": "none of it — see CONFORMANCE.md",
            "note": (
                "ADK has sub_agents, callbacks and an output schema, so more of "
                "a design's *shape* survives than under MAF; but it has no "
                "role, permission, mandate or separation model, so the "
                "authority half is enforced at this platform's harness boundary "
                "or not at all (ADR-0067 rule 5)."
            ),
            "caveats": [
                "These files carry no tenant boundary. An agent package is not "
                "a deployment; tenant isolation is the infrastructure target's "
                "job (ADR-0050), and Agent Engine's own project/location is set "
                "at deploy time.",
                "sub_agents is an LLM-driven delegation hierarchy, not this "
                "platform's authorized-delegation graph. It approximates the "
                "org chart; it does not enforce who may delegate to whom.",
                "A capability bound to a server (MCP or database, ADR-0085) is "
                "emitted as a working client; a tool that only narrows an "
                "existing grant is emitted as a typed stub in agents/tools.py "
                "for the host to implement.",
                "No permission, mandate, separation, autonomy posture or "
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
                    f"agents/{_mod(agent.id)}.py", self._agent_module(ir, agent)
                ).with_header(ir, comment="#")
            )
        files.append(
            GeneratedFile("agents/__init__.py", self._package_init(ir))
            .with_header(ir, comment="#")
        )
        files.append(
            GeneratedFile("agent_engine.py", self._deploy_script(ir))
            .with_header(ir, comment="#")
        )
        if self._any_wired(ir):
            files.append(
                GeneratedFile("agents/_backends.py", BACKENDS_SHIM)
                .with_header(ir, comment="#")
            )
        if self._any_stubbed(ir):
            # Engineer-owned: regeneration only appends new stubs (ADR-0089).
            files.append(
                GeneratedFile("agents/tools.py", stub_module(ir),
                              merge_additive=True))
        files.append(GeneratedFile("requirements.txt", self._requirements(ir)))
        files.append(
            GeneratedFile("CONFORMANCE.md", self._conformance(ir))
            .with_header(ir, comment="<!--")
        )
        files.append(GeneratedFile("README.md", self._readme(ir)))
        return files

    # -- one agent ---------------------------------------------------------

    def _agent_module(self, ir: SystemIR, agent: Any) -> str:
        model = self._model_id(agent)
        instruction = _pytext(agent.system_prompt() or "")
        tools = tool_surface(agent)
        wired, stubbed = wired_and_stubbed(ir, tools)
        needs = {t["backend"]["kind"] for t in wired}

        lines = [
            "from google.adk.agents import LlmAgent",
        ]
        if tools:
            lines.append("from google.adk.tools import FunctionTool")
        shim = shim_imports(needs)
        if shim:
            lines.append(f"from ._backends import {', '.join(shim)}")
        # Stubs to implement live in one shared module; import the ones this
        # agent has (ADR-0029). Each is a typed scaffold, not a bare raise.
        if stubbed:
            names = ", ".join(_mod(t["name"]) for t in stubbed)
            lines.append(f"from .tools import {names}")
        lines.append("")

        # Wired tools: a real client to the declared backend, not a stub.
        for tool in wired:
            lines += emit_wired_def(tool)
            lines.append("")

        lines.append(f"{_mod(agent.id)} = LlmAgent(")
        lines.append(f"    name={_pystr(agent.id)},")
        lines.append(f"    model={_pystr(model)},")
        lines.append(f"    description={_pystr(agent.description)},")
        lines.append(f"    instruction={instruction},")

        cfg = self._generate_content_config(agent)
        if cfg:
            lines.append("    generate_content_config={")
            for key, value in cfg.items():
                lines.append(f"        {_pystr(key)}: {value},")
            lines.append("    },")

        if tools:
            names = ", ".join(f"FunctionTool({_mod(t['name'])})" for t in tools)
            lines.append(f"    tools=[{names}],")

        if agent.output_contract is not None:
            lines.append(
                f"    output_key={_pystr(agent.id + '_result')},"
            )
            lines.append(
                "    # output_schema: this agent declares an output contract "
                "(ADR-0043);"
            )
            lines.append(
                "    #   map it to a pydantic model and pass output_schema=... "
                "by hand."
            )

        # Delegation → sub_agents, wired in __init__ to avoid import cycles.
        if agent.delegates_to:
            lines.append(
                "    # sub_agents wired in agents/__init__.py from "
                f"delegates_to={sorted(agent.delegates_to)}"
            )

        lines.append(")")

        gaps = self._agent_gaps(agent, tools)
        if gaps:
            lines.append("")
            lines.append("# Carried by the design and NOT by this module. "
                         "Nothing below is")
            lines.append("# enforced by loading this agent into ADK:")
            for item in gaps:
                lines.append(f"#   - {item}")
        return "\n".join(lines) + "\n"

    def _package_init(self, ir: SystemIR) -> str:
        """Import every agent and wire the delegation hierarchy, then name the
        root — which is what Agent Engine deploys."""
        lines = ['"""Every agent, with sub_agents wired from the org chart."""']
        for agent in ir.agents:
            lines.append(
                f"from .{_mod(agent.id)} import {_mod(agent.id)}"
            )
        lines.append("")
        for agent in ir.agents:
            if agent.delegates_to:
                kids = ", ".join(_mod(a) for a in sorted(agent.delegates_to))
                lines.append(f"{_mod(agent.id)}.sub_agents = [{kids}]")
        lines.append("")
        root = ir.root_agent_id if getattr(ir, "root_agent_id", None) else (
            ir.agents[0].id if ir.agents else "")
        lines.append(f"root_agent = {_mod(root)}" if root else "root_agent = None")
        lines.append("")
        return "\n".join(lines) + "\n"

    def _deploy_script(self, ir: SystemIR) -> str:
        return '''"""Deploy this design's root agent to Vertex AI Agent Engine.

    python agent_engine.py --project YOUR_PROJECT --location us-central1

The project and location are deployment facts, not design facts, so they are
flags here rather than baked in. This is the *ungoverned* deployment: it runs
the agents on Gemini and enforces none of the authority model. To run this
design governed, deploy the `local` or `terraform` target, which carries the
harness (see CONFORMANCE.md).
"""
import argparse

import vertexai
from vertexai import agent_engines

from agents import root_agent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--staging-bucket", default=None)
    args = parser.parse_args()

    vertexai.init(project=args.project, location=args.location,
                  staging_bucket=args.staging_bucket)
    remote = agent_engines.create(
        agent_engine=root_agent,
        requirements=["google-adk", "google-cloud-aiplatform[agent_engines]"],
        display_name={display},
    )
    print("deployed:", remote.resource_name)


if __name__ == "__main__":
    main()
'''.format(display=json.dumps(ir.name))

    def _any_wired(self, ir: SystemIR) -> bool:
        return any(
            t.get("backend")
            for agent in ir.agents
            for t in wired_and_stubbed(ir, tool_surface(agent))[0]
        )

    def _any_stubbed(self, ir: SystemIR) -> bool:
        return any(
            wired_and_stubbed(ir, tool_surface(agent))[1] for agent in ir.agents
        )

    def _requirements(self, ir: SystemIR) -> str:
        kinds = {
            t["backend"]["kind"]
            for agent in ir.agents
            for t in wired_and_stubbed(ir, tool_surface(agent))[0]
        }
        lines = [
            "google-adk>=1.0",
            "google-cloud-aiplatform[agent_engines]>=1.60",
        ]
        if "mcp" in kinds:
            lines.append("langchain-mcp-adapters>=0.1  # wired MCP capabilities")
        if "database" in kinds:
            lines.append("sqlalchemy>=2.0  # wired database capabilities")
        return "\n".join(lines) + "\n"

    # -- shared with the MAF target in spirit ------------------------------

    def _generate_content_config(self, agent: Any) -> dict[str, Any]:
        model = agent.model or {}
        cfg: dict[str, Any] = {}
        if model.get("temperature") is not None:
            cfg["temperature"] = model["temperature"]
        if model.get("max_tokens") is not None:
            cfg["max_output_tokens"] = model["max_tokens"]
        return cfg

    def _model_id(self, agent: Any) -> str:
        model = agent.model or {}
        if model.get("provider") in ("google", "gemini") and model.get("model"):
            return model["model"]
        if model.get("model", "").startswith("gemini"):
            return model["model"]
        # A non-Gemini binding is wrapped with LiteLlm in host code; name the
        # class's Gemini tier so the file is runnable, and flag it.
        classes = agent.model_policy.get("classes") if isinstance(
            agent.model_policy, dict) else None
        if classes:
            return _GEMINI_BY_CLASS.get(classes[0], "gemini-2.5-flash")
        return "gemini-2.5-flash"

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
                f"{len(gated)} tool(s) requiring approval "
                f"(ADK has no approval gate on a FunctionTool): "
                f"{sorted(t['name'] for t in gated)}"
            )
        if agent.guardrails:
            out.append(
                f"{len(agent.guardrails)} guardrail(s) — expressible as "
                "before_/after_ callbacks in host code, never in this module"
            )
        for placement in agent.placements:
            out.append(f"placement '{placement}' and its network policy")
        for environment in agent.environments:
            out.append(
                f"sandbox '{environment.id}': network "
                f"{environment.network.value}"
            )
        if agent.humans:
            out.append(
                f"{len(agent.humans)} paired human(s), including the approver "
                "an approval would route to"
            )
        return out

    # -- the report --------------------------------------------------------

    def _conformance(self, ir: SystemIR) -> str:
        rows = [
            *workflow_conformance_rows(
                ir, carried=False, platform="our runtime only",
                reason=(
                    "ADK has workflow agents — `SequentialAgent`, "
                    "`ParallelAgent`, `LoopAgent` — and they express an ordered "
                    "composition of *agents*. Our graph's nodes are tool calls, "
                    "pure transforms, branch predicates and human interrupts, and "
                    "a `transform` node evaluates a Python expression over "
                    "workflow state that nothing here will evaluate. Mapping the "
                    "one onto the other would produce something that runs, is not "
                    "the declared process, and reads as if it were. So these are "
                    "carried by the design and by this platform's own "
                    "interpreter, and by nothing in this directory. "
                ),
            ),
            ("Instructions, model, tools", "emitted", "ADK",
             "The portable core. Every platform has it."),
            ("Sub-agent hierarchy", "emitted (approximate)", "ADK",
             "Our org chart's delegation edges become ADK `sub_agents`. This "
             "is LLM-driven transfer, not authorized delegation: it carries "
             "the shape, not the rule about who may delegate to whom."),
            ("Output schema", "partial", "ADK + host code",
             "ADK has `output_schema`; the design carries an output-contract "
             "id, and mapping it to a pydantic model is a one-liner the host "
             "supplies."),
            ("Tool callables", "emitted for bound capabilities", "the "
             "servers catalog",
             "A capability the binding puts behind a declared server (MCP or "
             "database, ADR-0085) is emitted as a working client in "
             "`_backends.py` and wrapped in a FunctionTool — an MCP call, or a "
             "bounded SQL query enforcing the design's operation allowlist and "
             "row cap; credentials are env-var names, never values. A tool that "
             "only narrows an existing grant, or a capability with no server "
             "bound, is emitted as a typed, documented stub in `agents/tools.py` "
             "— one per tool, imported by each agent that has it — for the host "
             "to implement."),
            ("The rest of the tool surface", "**not in the IR**",
             "our harness only",
             "MCP mounts and built-in tool families are assembled by this "
             "platform's harness at run time and never lowered into the IR, so "
             "no target can emit them."),
            ("Tool approval", "**no model**", "our harness, or host callbacks",
             "ADK has no approval gate on a FunctionTool. A gated tool can be "
             "stopped only by a before_tool_callback the host writes."),
            ("Which human approves", "**no model**", "our harness",
             "ADK has no principal-with-a-mandate; an approval has nowhere to "
             "route by design."),
            ("Roles and permissions", "**no model**", "our harness",
             "ADK has no permission type. Nothing to emit into."),
            ("Mandates (what may be decided)", "**no model**", "our harness",
             "No framework has this; ADR-0067 rule 5 keeps enforcement at our "
             "tool boundary."),
            ("Separation of duties", "**no model**", "our phase gate",
             "Refused at compile time or not at all; there is no run-time "
             "place for it in ADK."),
            ("Autonomy postures", "**no model**", "our harness",
             "ADK runs an agent loop; it has no advisory/supervised/autonomous "
             "distinction to map four postures onto."),
            ("People as principals", "**no model**", "our harness",
             "ADK's session has a user id, not a mandate-holding principal."),
            ("Placement and network policy", "**out of scope**",
             "the infrastructure target",
             "An agent package is not a deployment; emit `terraform:gcp` "
             "alongside to place it, or run it on Agent Engine ungoverned."),
            ("Data classes and egress rules", "**no model**", "our harness",
             "ADK has no confidentiality-label or egress model."),
            ("Guardrails", "partial", "host callbacks",
             "Expressible as before_/after_ callbacks the host writes, never "
             "in the emitted module."),
            ("Budgets and model policy", "partial", "generate_content_config",
             "A token ceiling maps; spend does not."),
        ]
        counts = {
            "agents": len(ir.agents),
            "sub-agent edges": sum(len(a.delegates_to) for a in ir.agents),
            "agents with a mandate": sum(
                1 for a in ir.agents if a.mandate.decisions),
            "gated tools": sum(
                1 for a in ir.agents for t in a.tools if t.requires_approval),
            "resolved permissions": sum(len(a.permissions) for a in ir.agents),
        }
        table = "\n".join(
            f"| {w} | {s} | {who} | {why} |" for w, s, who, why in rows)
        tally = "\n".join(f"- **{v}** {k}" for k, v in counts.items())
        return f"""# What this target cannot enforce

Generated for `{ir.name}`, target `adk` (Google ADK + Vertex AI Agent Engine).

A design carries more than a Gemini agent package can. This page names what,
and who enforces it instead — because a target that silently dropped a mandate
would be generating a lie, and a control that reads as enforced and is not is
worse than no control (ADR-0073).

## This design

{tally}

## Construct by construct

| Construct | In this target | Enforced by | Why |
|---|---|---|---|
{table}

## What that means when you deploy these files

`python agent_engine.py --project ... --location ...` deploys the root agent
to Vertex AI Agent Engine and gives you the agents, their instructions, their
sub-agent hierarchy, and tool *stubs*. It does not give you the organization:
no permission is checked, no mandate bounds a decision, no separation of duties
survives, and an approval has nowhere to route because ADK has no
mandate-holding principal.

That is not a defect in ADK — it is an agent framework, and these are not
framework concerns. It is why this platform keeps governance at its own tool
boundary rather than delegating it to a framework as the only check
(ADR-0067 rule 5).

To run this design **governed**, deploy the `local` or `terraform:gcp` target,
which carries the harness. To run it **portably on Gemini**, use these files
and accept the table above.
"""

    def _readme(self, ir: SystemIR) -> str:
        return f"""# {ir.name} — Google ADK + Vertex AI Agent Engine

A Python ADK package:

- `agents/` — one `LlmAgent` per agent, with tool stubs.
- `agents/__init__.py` — wires `sub_agents` from the org chart and names
  `root_agent`.
- `agent_engine.py` — deploys `root_agent` to Vertex AI Agent Engine.
- `requirements.txt` — `google-adk`, `google-cloud-aiplatform`.

Run locally with `adk run agents`, or deploy with
`python agent_engine.py --project YOUR_PROJECT --location us-central1`.

Tools behind a declared server are wired for real: a capability the binding
puts on an MCP or database server (ADR-0085) is emitted in `agents/_backends.py`
as a working client — an MCP call, or a bounded SQL query that enforces the
design's operation allowlist and row cap. Set the credential env vars the
binding named. Only a capability with no server bound stays a stub you fill in.

**Read `CONFORMANCE.md` first.** {len(ir.agents)} agents come across; the
authority model does not, and the report says what that costs.
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
