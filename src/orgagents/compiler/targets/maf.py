"""Microsoft Agent Framework as a compile target (ADR-0005).

The first target that emits **another platform's agent definition** rather than
infrastructure that runs ours. `local` and `terraform` deploy this platform's
runtime and let it pick an adapter; this one produces files somebody else's
runtime loads, which is the thing the Terraform analogy actually promises.

It also makes the honest half of that promise explicit. A design here carries
roles, permissions, mandates, separations, autonomy postures, placement and
people. MAF's declarative format carries an agent's instructions, model and
tools. Everything in the first list and not the second has to go **somewhere**,
and the three somewheres are: enforced by this platform's harness at the tool
boundary, enforced by an application we do not run, or enforced by nobody.

So every compile writes a `CONFORMANCE.md` naming which. That is ADR-0073's
rule — a control that reads as enforced and is not is worse than no control —
applied to a target instead of to a capability. A target that silently dropped
a mandate would be generating a lie.
"""
from __future__ import annotations

import json
from typing import Any

from ..base import GeneratedFile
from ..ir import SystemIR

#: What a MAF declarative agent file may contain, as of the format read in
#: LANDSCAPE §9. Anything else we hold has to be reported rather than emitted.
EXPRESSIBLE = (
    "instructions", "model id and options", "function tools with a JSON schema",
    "MCP tools with an approval mode and an allow-list", "an output schema",
)


class MicrosoftAgentFrameworkTarget:
    """Emit `kind: Prompt` agent files, and say what they cannot carry."""

    id = "maf"

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": "Microsoft Agent Framework (declarative)",
            "emits": "one `kind: Prompt` YAML per agent, plus a conformance report",
            "governance": "none of it — see CONFORMANCE.md",
            "note": (
                "MAF has no role, permission, mandate or separation model, so "
                "the authority half of a design cannot be emitted here and is "
                "enforced at this platform's harness boundary or not at all "
                "(ADR-0067 rule 5)."
            ),
            "caveats": [
                "These files carry no tenant boundary at all. An agent "
                "definition is not a deployment, so tenant isolation is the "
                "infrastructure target's job (ADR-0050) and loading these into "
                "a shared host puts two tenants' agents in one process.",
                "The emitted toolset is incomplete by construction: MCP mounts "
                "and built-in tool families are assembled by this platform's "
                "harness at run time and are not in the IR.",
                "No permission, mandate, separation or autonomy posture "
                "survives the translation. CONFORMANCE.md lists what that "
                "costs for a given design.",
            ],
        }

    # -- files -------------------------------------------------------------

    def generate(self, ir: SystemIR) -> list[GeneratedFile]:
        files = [
            GeneratedFile(
                f"agents/{agent.id}.agent.yaml", self._agent_yaml(ir, agent)
            ).with_header(ir)
            for agent in ir.agents
        ]
        files.append(
            GeneratedFile("CONFORMANCE.md", self._conformance(ir)).with_header(
                ir, comment="<!--"
            )
        )
        files.append(GeneratedFile("README.md", self._readme(ir)))
        return files

    # -- one agent ---------------------------------------------------------

    def _agent_yaml(self, ir: SystemIR, agent: Any) -> str:
        """A `kind: Prompt` file.

        Hand-written rather than dumped through a YAML library so the comments
        survive: a reader opening this in a MAF project needs to know which of
        their design's controls did *not* come with it.
        """
        lines = [
            "kind: Prompt",
            f"name: {_scalar(agent.name or agent.id)}",
            f"description: {_scalar(agent.description)}",
            "instructions: |-",
        ]
        for line in (agent.system_prompt() or "").splitlines() or [""]:
            lines.append(f"  {line}")

        model = agent.model or {}
        lines.append("model:")
        if model.get("model"):
            lines.append(f"  id: {_scalar(model['model'])}")
        if model.get("provider"):
            lines.append(f"  provider: {_maf_provider(model['provider'])}")
        options = {
            k: v for k, v in (
                ("temperature", model.get("temperature")),
                ("maxOutputTokens", model.get("max_output_tokens")),
            ) if v is not None
        }
        if options:
            lines.append("  options:")
            for key, value in options.items():
                lines.append(f"    {key}: {value}")

        surface = self._tool_surface(agent)
        gated = [t for t in surface if t["gated"]]
        if surface:
            lines.append("tools:")
            for tool in surface:
                lines.append("  - kind: function")
                lines.append(f"    name: {_scalar(tool['name'])}")
                lines.append(f"    description: {_scalar(tool['description'])}")
                if tool["gated"]:
                    # Verified against the format: `approvalMode` is a field on
                    # MAF's declarative `mcp` tool and NOT on its `function`
                    # tool, so this cannot be declared here. Emitting it
                    # silently ungated would be the worst of the options.
                    lines.append(
                        "    # NOT EXPRESSIBLE: this requires approval and "
                        "MAF's declarative function"
                    )
                    lines.append(
                        "    # tool has no `approvalMode` — only an `mcp` tool "
                        "does. Enforced by this"
                    )
                    lines.append(
                        "    # platform's harness, or by "
                        "@tool(approval_mode=...) in host code."
                    )
                if tool["schema"]:
                    lines.append("    parameters:")
                    for line in json.dumps(tool["schema"], indent=2).splitlines():
                        lines.append(f"      {line}")
        if agent.output_contract is not None:
            lines.append(
                "# outputSchema: this agent declares an output contract; map it "
                "by hand"
            )

        dropped = self._agent_gaps(agent, gated)
        if dropped:
            lines.append("")
            lines.append(
                "# Carried by the design and NOT by this file. Nothing below is"
            )
            lines.append("# enforced by loading this agent:")
            for item in dropped:
                lines.append(f"#   - {item}")
        return "\n".join(lines) + "\n"

    def _tool_surface(self, agent: Any) -> list[dict[str, Any]]:
        """The tools this design names, and whether each is gated.

        Two sources, because the IR has two. Declared `ToolSpec`s carry their
        own schema; capabilities do not, and are the tool surface for most
        designs — Northwind declares twelve agents and no `tools:` block at
        all, so reading only the first would emit twelve agents with nothing
        to call.

        What is **not** here, and cannot be: the toolset an agent actually
        sees is assembled by this platform's harness at run time from mounted
        MCP servers and built-in families. The IR does not carry it, so no
        target can emit it. That is reported rather than hidden.
        """
        out: list[dict[str, Any]] = []
        for tool in agent.tools:
            out.append({
                "name": tool.id, "description": tool.description,
                "schema": tool.input_schema, "gated": tool.requires_approval,
            })
        gated_caps = set(agent.requires_approval_for)
        for capability in agent.capabilities:
            if any(t["name"] == capability.id for t in out):
                continue
            out.append({
                "name": capability.id,
                "description": capability.description,
                "schema": {},
                "gated": capability.id in gated_caps
                or bool(capability.constraints.requires_approval),
            })
        return out

    def _agent_gaps(self, agent: Any, gated: list[Any]) -> list[str]:
        out: list[str] = []
        if agent.mandate.decisions:
            out.append(
                f"mandate: may decide {sorted(agent.mandate.decisions)}"
                + (f" under {agent.mandate.conditions}"
                   if agent.mandate.conditions else "")
            )
        if agent.permissions:
            out.append(f"{len(agent.permissions)} resolved permissions")
        if gated:
            out.append(
                f"{len(gated)} tool(s) requiring approval: "
                f"{sorted(t['name'] for t in gated)}"
            )
        if agent.guardrails:
            out.append(f"{len(agent.guardrails)} guardrail(s)")
        for placement in agent.placements:
            out.append(f"placement '{placement}' and its network policy")
        for environment in agent.environments:
            out.append(
                f"environment '{environment.id}': network "
                f"{environment.network.value}"
            )
        if agent.delegates_to:
            out.append(f"may delegate to {sorted(agent.delegates_to)}")
        if agent.humans:
            out.append(
                f"{len(agent.humans)} paired human(s), including the approver "
                "an approval would route to"
            )
        return out

    # -- the report --------------------------------------------------------

    def _conformance(self, ir: SystemIR) -> str:
        """What this target cannot enforce of the design it was given."""
        rows = [
            ("Instructions, model, tools", "emitted", "MAF",
             "The portable core. Every platform has it."),
            ("Tool input schemas", "partial", "MAF",
             "Declared tools carry one. A capability does not, so most of the "
             "emitted surface has no `parameters` and the host must supply "
             "them at registration."),
            ("The rest of the tool surface", "**not in the IR**",
             "our harness only",
             "MCP mounts and built-in tool families are assembled at run time "
             "from the agent's harness, never lowered into the IR — so no "
             "target can emit them and this file is an incomplete toolset by "
             "construction. The most portable thing in the whole ecosystem, "
             "and it is the part we do not compile."),
            ("Tool approval", "**not expressible**", "our harness, or host code",
             "`approvalMode` exists on an `mcp` tool and not on a `function` "
             "tool, so a gated function tool cannot be declared. MAF's own "
             "default is `never_require`."),
            ("Which human approves", "**no model**", "our harness",
             "MAF's `ApprovalMode` is `always_require | never_require`. It "
             "says whether, never who."),
            ("Roles and permissions", "**no model**", "our harness",
             "MAF has no permission type. Nothing to emit into."),
            ("Mandates (what may be decided)", "**no model**", "our harness",
             "No framework has this; it is why ADR-0067 rule 5 keeps "
             "enforcement at our tool boundary."),
            ("Separation of duties", "**no model**", "our phase gate",
             "Refused at compile time or not at all — there is no run-time "
             "place for it here."),
            ("Autonomy postures", "**no model**", "our harness",
             "Four postures collapse to a boolean at best."),
            ("People as principals", "**no model**", "our harness",
             "MAF's principal is `(tenant_id, user_id)` on a data label."),
            ("Placement and network policy", "**out of scope**",
             "the infrastructure target",
             "An agent definition is not a deployment; emit `local` or "
             "`terraform` alongside."),
            ("Data classes and egress rules", "**no model**", "our harness",
             "MAF's confidentiality labels are experimental (FIDES) and are "
             "not declarative."),
            ("Guardrails", "partial", "host code",
             "Expressible as middleware, never as this file."),
            ("Budgets and model policy", "partial", "host code",
             "`model.options` carries a token ceiling and nothing about spend."),
        ]
        counts = {
            "agents": len(ir.agents),
            "gated tools": sum(
                1 for a in ir.agents for t in a.tools if t.requires_approval
            ),
            "agents with a mandate": sum(
                1 for a in ir.agents if a.mandate.decisions
            ),
            "resolved permissions": sum(len(a.permissions) for a in ir.agents),
        }
        table = "\n".join(
            f"| {what} | {status} | {who} | {why} |" for what, status, who, why in rows
        )
        tally = "\n".join(f"- **{v}** {k}" for k, v in counts.items())
        return f"""# What this target cannot enforce

Generated for `{ir.name}`, target `maf`.

A design carries more than an agent definition can. This page names what, and
who enforces it instead — because a target that silently dropped a mandate
would be generating a lie, and a control that reads as enforced and is not is
worse than no control (ADR-0073).

## This design

{tally}

## Construct by construct

| Construct | In this target | Enforced by | Why |
|---|---|---|---|
{table}

## What that means when you run these files

Loading these agents into a Microsoft Agent Framework host gives you the
agents, and an incomplete set of tools — see "The rest of the tool surface"
above. It does not give you the organization: no permission is checked, no
mandate bounds a decision, no separation of duties survives, and an approval —
where MAF can be made to ask for one at all — goes to whoever the host code
decides rather than to the person whose mandate covers it.

That is not a defect in MAF. It is a runtime SDK and these are not runtime SDK
concerns. It is the reason this platform keeps governance at its own tool
boundary and never delegates it to a framework's middleware as the only check
(ADR-0067 rule 5) — and MAF's own agent-hooks record agrees, calling its
interception contract "a cooperative contract, not a security boundary".

To run this design **governed**, deploy the `local` or `terraform` target,
which carries the harness. To run it **portably**, use these files and accept
the table above.
"""

    def _readme(self, ir: SystemIR) -> str:
        return f"""# {ir.name} — Microsoft Agent Framework

One `kind: Prompt` file per agent, in `agents/`. Load them with
`agent_framework_declarative`'s loader, or the .NET `PromptAgentFactory`.

Function-tool `bindings` are deliberately absent: a binding names a callable
registered in the host process, which is a property of your application and not
of this design. Add them where you register the tools.

**Read `CONFORMANCE.md` first.** {len(ir.agents)} agents come across; the
authority model does not, and the report says what that costs.
"""


def _scalar(value: str) -> str:
    """A YAML scalar that survives colons, quotes and newlines."""
    return json.dumps(value or "")


def _maf_provider(provider: str) -> str:
    """Our provider name in MAF's vocabulary, or a comment saying we cannot."""
    return {
        "openai": "OpenAI",
        "azure_openai": "AzureOpenAI",
        "anthropic": "Anthropic",
    }.get(provider, provider)
