"""A worked third-party deployment target (ADR-0091).

This is a complete, working example of the seam: a target that lives in
somebody else's distribution, is discovered through an entry point, and never
touches the orgagents package. Read it as the reference for writing your own.

Acme runs agents on an on-prem Nomad cluster behind their own gateway. None of
that is orgagents' business — which is the point. The design stays
vendor-neutral (ADR-0002); this target is where Acme's stack is named.

Four things it demonstrates, and each is a rule rather than a style choice:

1. **It consumes the IR only.** Permissions, identities, placements and routing
   are already resolved. Re-deriving a permission here is how two targets come
   to disagree about who may do what, so a target that imported
   `orgagents.spec` would be refused by the platform's own test suite
   (ADR-0005).
2. **It publishes a `descriptor`.** That is what lets the designer offer the
   right fields for this target instead of guessing, and it is a claim about
   behaviour: everything listed in `supports` must really survive.
3. **It writes a conformance report.** Acme's cluster cannot hold a mandate or
   a separation of duties, so the output says so plainly. A control that reads
   as enforced and is not is worse than no control (ADR-0073).
4. **It leaves the operator room.** The generated files are regenerated every
   compile; anything Acme hand-writes beside them is never touched.
"""
from __future__ import annotations

import json
from typing import Any

from orgagents.compiler.base import GeneratedFile
from orgagents.plugins import ProviderDescriptor


class AcmeOnPremTarget:
    """Emit Nomad job files and a gateway route table for Acme's cluster."""

    id = "acme:onprem"

    # -- how the platform sees this target ---------------------------------

    def descriptor(self) -> ProviderDescriptor:
        """What this target genuinely carries.

        `supports` is drawn from `orgagents.plugins.FEATURES`; a word the
        platform does not know is refused at construction, so this cannot drift
        into marketing. Acme's cluster runs a container and routes to it: the
        instructions, model and tool list travel, and an approval gate does
        not, so it is not claimed.
        """
        return ProviderDescriptor(
            id=self.id,
            title="Acme on-prem (Nomad)",
            kind="target",
            summary="Nomad jobs and a gateway route table for Acme's cluster.",
            supports=frozenset({"instructions", "tools", "model", "subagents"}),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": "Acme on-prem (Nomad)",
            "summary": "Generates Nomad job files and a gateway route table "
                       "for Acme's own cluster.",
            "produces": ["jobs/<agent>.nomad", "gateway-routes.json",
                         "CONFORMANCE.md"],
            "caveats": [
                "Nomad schedules containers; it holds no permission, mandate "
                "or approval. Everything in the authority model is enforced at "
                "the orgagents harness boundary or nowhere.",
                "These files carry no tenant boundary. One cluster namespace "
                "per tenant is an Acme platform decision this target does not "
                "make for you.",
                "The gateway route table is descriptive. Acme's gateway must "
                "be configured to honour it; nothing here applies it.",
            ],
        }

    # -- generation --------------------------------------------------------

    def generate(self, ir: Any) -> list[GeneratedFile]:
        files: list[GeneratedFile] = []
        for agent in ir.agents:
            files.append(
                GeneratedFile(f"jobs/{_safe(agent.id)}.nomad",
                              self._job(ir, agent)).with_header(ir, comment="#")
            )
        files.append(GeneratedFile("gateway-routes.json",
                                   self._routes(ir)))
        files.append(GeneratedFile("CONFORMANCE.md", self._conformance(ir)))
        return files

    def _job(self, ir: Any, agent: Any) -> str:
        model = agent.model or {}
        gaps = self._gaps(agent)
        lines = [
            f'job "{agent.id}" {{',
            '  datacenters = ["dc1"]',
            '  type        = "service"',
            "",
            "  meta {",
            f'    orgagents_system = "{ir.name}"',
            f'    orgagents_agent  = "{agent.id}"',
            f'    team             = "{" / ".join(agent.team_path or [])}"',
            f'    reports_to       = "{agent.reports_to or ""}"',
            "  }",
            "",
            f'  group "{agent.id}" {{',
            "    count = 1",
            '    task "agent" {',
            '      driver = "docker"',
            "      config {",
            '        image = "registry.acme.internal/orgagents-agent:latest"',
            "      }",
            "      env {",
            f'        ORGAGENTS_AGENT_ID = "{agent.id}"',
            f'        ORGAGENTS_MODEL    = '
            f'"{model.get("provider", "")}:{model.get("model", "")}"',
            "      }",
            "    }",
            "  }",
            "}",
        ]
        if gaps:
            lines.append("")
            lines.append("# Carried by the design and NOT enforced by Nomad.")
            lines.append("# Deploy an infrastructure target alongside if you "
                         "need these held:")
            lines += [f"#   - {gap}" for gap in gaps]
        return "\n".join(lines) + "\n"

    def _gaps(self, agent: Any) -> list[str]:
        """What this target drops, named per agent rather than in general.

        A reader looking at one job file should not have to cross-reference a
        report to learn that the approval it mentions is not enforced here.
        """
        out: list[str] = []
        if agent.mandate.decisions:
            out.append(f"mandate: may decide {sorted(agent.mandate.decisions)}")
        if agent.permissions:
            out.append(f"{len(agent.permissions)} resolved permissions")
        if agent.requires_approval_for:
            out.append(
                f"approval required for {sorted(agent.requires_approval_for)} "
                "— Nomad has nowhere to route it")
        for environment in agent.environments:
            out.append(f"sandbox '{environment.id}' "
                       f"(network {environment.network.value})")
        return out

    def _routes(self, ir: Any) -> str:
        """Who may reach whom, for Acme's gateway to enforce.

        The delegation edges are already resolved in the IR, so this is a
        transcription rather than a decision — which is exactly what a target
        should be doing.
        """
        return json.dumps({
            "system": ir.name,
            "note": "Descriptive. Acme's gateway must be configured to honour "
                    "this; generating it does not apply it.",
            "routes": [
                {"from": agent.id, "to": sorted(agent.delegates_to)}
                for agent in ir.agents if agent.delegates_to
            ],
        }, indent=2) + "\n"

    def _conformance(self, ir: Any) -> str:
        counts = {
            "agents": len(ir.agents),
            "agents with a mandate": sum(
                1 for a in ir.agents if a.mandate.decisions),
            "resolved permissions": sum(len(a.permissions) for a in ir.agents),
            "gated actions": sum(
                len(a.requires_approval_for) for a in ir.agents),
        }
        tally = "\n".join(f"- **{v}** {k}" for k, v in counts.items())
        return f"""# What this target cannot enforce

Generated for `{ir.name}`, target `acme:onprem`.

Acme's cluster schedules containers. It has no notion of a role, a permission,
a mandate or an approval, so none of the authority model survives into these
files — and saying otherwise would be the lie this page exists to prevent
(ADR-0073).

## This design

{tally}

## Construct by construct

| Construct | In this target | Enforced by |
|---|---|---|
| Instructions, model, tools | emitted | the agent container |
| Delegation edges | emitted (as gateway routes) | Acme's gateway, once configured |
| Roles and permissions | **no model** | the orgagents harness |
| Mandates | **no model** | the orgagents harness |
| Separation of duties | **no model** | the orgagents phase gate, at compile time |
| Approvals, and who may give them | **no model** | the orgagents harness |
| Sandboxes and network posture | **no model** | an infrastructure target |
| Data classes and egress | **no model** | the orgagents harness |

## What to do about it

Deploying these files gives Acme the agents and their wiring. It does not give
Acme the organization. Run the `local` or `terraform:*` target alongside if the
authority model needs to be held at run time rather than documented here.
"""


def _safe(identifier: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in identifier)
