"""Local target: a self-contained stack plus a single-process dev loop (ADR-0011).

Emits a Compose file, a Makefile, an env template that carries secret *names*
only, per-agent manifests and the IR itself. The same IR feeds the cloud
targets, so local is the identical system with different bindings — not a mock.
"""
from __future__ import annotations

import json
from typing import Any

import yaml

from ..base import GeneratedFile
from ..ir import SystemIR
from ..registry import registry_report

# Abstract environment vocabulary → local container settings. This table is the
# binding; nothing above it knows about images or CPU shares.
TIER_RESOURCES = {
    "minimal": {"cpus": "0.25", "memory": "256M"},
    "small": {"cpus": "1", "memory": "2G"},
    "medium": {"cpus": "2", "memory": "8G"},
    "large": {"cpus": "4", "memory": "16G"},
    "accelerated": {"cpus": "8", "memory": "32G"},
}
TOOLCHAIN_IMAGES = {
    "none": "python:3.11-slim",
    "scripting": "python:3.11-slim",
    "data_analysis": "python:3.11",
    "software_build": "python:3.11",
    "browser": "mcr.microsoft.com/playwright/python:v1.47-jammy",
    "document": "python:3.11",
    "model_training": "python:3.11",
    "network_client": "python:3.11-slim",
}


class LocalTarget:
    id = "local"

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": "Local Compose stack",
            "summary": "Runs the whole system on one machine, with a single-process "
                       "fallback for hosts without a container runtime.",
            "produces": ["docker-compose.yaml", "Makefile", ".env.example",
                         "system.ir.json", "agents/*.json", "triggers.json",
                         "channels.json", "memory.json", "REGISTRY.md",
                         "run_local.py", "README.md"],
            "caveats": ["Compose approximates network policy and cannot represent "
                        "cloud IAM; local runs do not verify those controls."],
        }

    # -- generation --------------------------------------------------------

    def generate(self, ir: SystemIR) -> list[GeneratedFile]:
        files = [
            GeneratedFile("docker-compose.yaml", self._compose(ir)).with_header(ir),
            GeneratedFile("Makefile", self._makefile(ir)).with_header(ir),
            GeneratedFile(".env.example", self._env(ir), preserve_if_exists=False)
            .with_header(ir),
            GeneratedFile("system.ir.json", json.dumps(ir.model_dump(mode="json"),
                                                       indent=2) + "\n"),
            GeneratedFile("run_local.py", self._single_process(ir)).with_header(ir),
            GeneratedFile("README.md", self._readme(ir)),
            GeneratedFile("REGISTRY.md", registry_report(ir)),
            GeneratedFile("triggers.json", json.dumps(
                [t.model_dump(mode="json") for t in ir.triggers], indent=2) + "\n"),
            GeneratedFile("channels.json", json.dumps(
                [c.model_dump(mode="json") for c in ir.channels], indent=2) + "\n"),
            GeneratedFile("memory.json", json.dumps(
                ir.memory.model_dump(mode="json"), indent=2) + "\n"),
        ]
        for agent in ir.agents:
            files.append(
                GeneratedFile(
                    f"agents/{agent.id}.json",
                    json.dumps(
                        {
                            "agent": agent.model_dump(mode="json"),
                            "system_prompt": agent.system_prompt(),
                        },
                        indent=2,
                    )
                    + "\n",
                )
            )
        return files

    # -- pieces ------------------------------------------------------------

    def _networks(self, ir: SystemIR) -> dict[str, Any]:
        networks: dict[str, Any] = {"control": {}}
        # Channel bridges always need egress to reach the chat provider.
        if any(c.human_facing for c in ir.channels) or any(
            a.environment and a.environment.network.value != "none" for a in ir.agents
        ):
            networks["egress"] = {}
        if any(
            a.environment and a.environment.network.value == "none" for a in ir.agents
        ):
            # An isolated network with no gateway: services on it reach nothing.
            networks["isolated"] = {"internal": True}
        return networks

    def _agent_service(self, ir: SystemIR, agent) -> dict[str, Any]:
        env = agent.environment
        tier = env.tier.value if env else "minimal"
        toolchain = env.toolchains[0].value if env and env.toolchains else "none"
        posture = env.network.value if env else "none"
        limits = TIER_RESOURCES.get(tier, TIER_RESOURCES["minimal"])
        networks = ["control"]
        networks.append("isolated" if posture == "none" else "egress")
        service: dict[str, Any] = {
            "image": TOOLCHAIN_IMAGES.get(toolchain, "python:3.11-slim"),
            "command": ["python", "-m", "orgagents.runtime.worker", agent.id],
            "environment": {
                "ORGAGENTS_AGENT_ID": agent.id,
                "ORGAGENTS_MANIFEST": f"/app/agents/{agent.id}.json",
                "ORGAGENTS_ADAPTER": agent.runtime_adapter,
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://telemetry:4317",
            },
            "volumes": ["./agents:/app/agents:ro"],
            "networks": networks,
            "depends_on": ["state"],
            "deploy": {"resources": {"limits": limits}},
            "labels": {
                "org.agentic.team": agent.team_id,
                "org.agentic.network_posture": posture,
                "org.agentic.identity": agent.identity.id if agent.identity else "",
            },
        }
        for ref in (agent.identity.secret_refs if agent.identity else []):
            service["environment"][ref] = f"${{{ref}}}"
        return service

    def _compose(self, ir: SystemIR) -> str:
        services: dict[str, Any] = {
            "state": {
                "image": "postgres:16-alpine",
                "environment": {
                    "POSTGRES_PASSWORD": "${STATE_PASSWORD}",
                    "POSTGRES_DB": "orgagents",
                },
                "networks": ["control"],
                "volumes": ["state-data:/var/lib/postgresql/data"],
            },
            "telemetry": {
                "image": "otel/opentelemetry-collector-contrib:latest",
                "networks": ["control"],
                "ports": ["4317:4317"],
            },
            "designer": {
                "image": "orgagents/platform:latest",
                "command": ["orgagents", "serve", "--host", "0.0.0.0"],
                "ports": ["8000:8000"],
                "networks": ["control"],
                "depends_on": ["state"],
            },
        }
        for agent in ir.agents:
            services[f"agent-{agent.id}"] = self._agent_service(ir, agent)

        # One scheduler for every trigger (ADR-0020). It holds no credentials of
        # its own: it wakes the owning agent, which runs under its own identity.
        if ir.triggers:
            scheduler = ir.binding.scheduler
            services["scheduler"] = {
                "image": "orgagents/platform:latest",
                "command": ["orgagents", "scheduler", "--manifest", "/app/triggers.json"],
                "environment": {
                    "ORGAGENTS_SCHEDULER": scheduler.provider if scheduler else "internal",
                    "ORGAGENTS_MAX_CONCURRENCY": str(
                        scheduler.max_concurrency if scheduler else 4
                    ),
                    "ORGAGENTS_DEAD_LETTER": (
                        scheduler.dead_letter if scheduler else ""
                    ),
                },
                "volumes": ["./triggers.json:/app/triggers.json:ro"],
                "networks": ["control"],
                "depends_on": ["state"],
                "labels": {"org.agentic.triggers": str(len(ir.triggers))},
            }

        # Long-term memory needs somewhere to live that outlives a session.
        if ir.memory.long_term.enabled:
            memory = ir.binding.memory
            services["memory"] = {
                "image": "orgagents/platform:latest",
                "command": ["orgagents", "memory", "serve"],
                "environment": {
                    "ORGAGENTS_SESSION_STORE": memory.session_store if memory
                    else "in_process",
                    "ORGAGENTS_LONG_TERM_STORE": memory.long_term_store if memory
                    else "relational",
                    "ORGAGENTS_MEMORY_RETENTION_DAYS": str(
                        ir.memory.long_term.retention_days or 0
                    ),
                },
                "networks": ["control"],
                "depends_on": ["state"],
                "labels": {
                    "org.agentic.memory.namespaces": ",".join(
                        n.id for n in ir.memory.namespaces
                    )
                },
            }

        # One bridge per human-facing channel (ADR-0021). The bridge is the only
        # component holding a workspace credential; agents talk to it, not to
        # Slack or Teams.
        for channel in ir.channels:
            if not channel.human_facing:
                continue
            env = {
                "ORGAGENTS_CHANNEL": channel.id,
                "ORGAGENTS_PROVIDER": channel.provider,
                "ORGAGENTS_ADDRESS": channel.address,
                "ORGAGENTS_SLA_MINUTES": str(channel.response_sla_minutes or 0),
                "ORGAGENTS_OUT_OF_HOURS": channel.out_of_hours,
            }
            if channel.bot_identity_ref:
                env[channel.bot_identity_ref] = f"${{{channel.bot_identity_ref}}}"
            services[f"channel-{channel.id}"] = {
                "image": "orgagents/channel-bridge:latest",
                "command": ["serve", "--channel", channel.id],
                "environment": env,
                "volumes": ["./channels.json:/app/channels.json:ro"],
                "networks": ["control", "egress"],
                "labels": {
                    "org.agentic.channel": channel.id,
                    "org.agentic.provider": channel.provider,
                    "org.agentic.purposes": ",".join(channel.purposes),
                },
            }
        for cap in ir.capabilities:
            binding = ir.binding.capability_binding(cap.id)
            if binding is None:
                continue
            services[f"mcp-{binding.server_name}"] = {
                "image": binding.options.get("image", "orgagents/mcp-runner:latest"),
                "command": [binding.command or "serve", *binding.args],
                "environment": (
                    {binding.dsn_secret_ref: f"${{{binding.dsn_secret_ref}}}"}
                    if binding.dsn_secret_ref
                    else {}
                ),
                "networks": ["control"],
                "labels": {"org.agentic.capability": cap.id},
            }
        return yaml.safe_dump(
            {
                "name": ir.name.lower().replace(" ", "-"),
                "services": services,
                "networks": self._networks(ir),
                "volumes": {"state-data": {}},
            },
            sort_keys=False,
            width=100,
        )

    def _makefile(self, ir: SystemIR) -> str:
        spec_file = f"{ir.name}.system.yaml"
        return f"""\
# {ir.name} — local development loop

.PHONY: up down logs seed ps single validate

up:            ## start the whole stack
\tdocker compose up -d --remove-orphans

down:          ## stop and remove the stack
\tdocker compose down -v

logs:          ## follow agent logs
\tdocker compose logs -f $(filter-out $@,$(MAKECMDGOALS))

ps:            ## show running services
\tdocker compose ps

seed:          ## load the compiled organization into the platform
\tdocker compose exec designer orgagents seed

single:        ## run everything in one process (no container runtime needed)
\tpython run_local.py

validate:      ## re-validate the source spec
\torgagents spec validate ../../{ir.name}.system.yaml
"""

    def _env(self, ir: SystemIR) -> str:
        refs = sorted(
            {r for a in ir.agents for r in (a.identity.secret_refs if a.identity else [])}
            | {c.bot_identity_ref for c in ir.channels if c.bot_identity_ref}
        )
        lines = [
            "# Secret NAMES only — never commit values (ADR-0015).",
            "# Populate from your secret manager before `make up`.",
            "STATE_PASSWORD=",
        ]
        lines += [f"{ref}=" for ref in refs]
        return "\n".join(lines) + "\n"

    def _single_process(self, ir: SystemIR) -> str:
        return f'''"""Run {ir.name} in a single process, backed by SQLite.

For machines without a container runtime, and for fast iteration. Identical
permission semantics to the Compose stack — only the bindings differ.
"""
import json
from pathlib import Path

from orgagents.platform import Platform
from orgagents.runtime.loader import load_system

IR_PATH = Path(__file__).parent / "system.ir.json"


def main() -> None:
    ir = json.loads(IR_PATH.read_text())
    platform = Platform("{ir.name.lower().replace(" ", "_")}.db")
    load_system(platform, ir)
    print(f"loaded {{len(ir['agents'])}} agents from the compiled system")
    for agent in ir["agents"]:
        print(f"  {{agent['id']:24}} team={{agent['team_id']:16}} "
              f"adapter={{agent['runtime_adapter']}}")
    print("\\nStart the designer UI with: orgagents serve")


if __name__ == "__main__":
    main()
'''

    def _readme(self, ir: SystemIR) -> str:
        triggers = "\n".join(
            f"| `{t.id}` | {t.schedule} | `{t.agent_id}` | "
            f"{', '.join(t.deliver_to) or '—'} |"
            for t in ir.triggers
        ) or "| — | — | — | — |"
        channels = "\n".join(
            f"| `{c.id}` | {c.provider} | {', '.join(c.purposes) or '—'} | "
            f"{str(c.response_sla_minutes) + ' min' if c.response_sla_minutes else '—'} | "
            f"{c.out_of_hours} |"
            for c in ir.channels if c.human_facing
        ) or "| — | — | — | — | — |"
        agents = "\n".join(
            f"| `{a.id}` | {' / '.join(a.team_path)} | "
            f"{a.environment.id if a.environment else '—'} | "
            f"{a.environment.network.value if a.environment else '—'} | "
            f"{len(a.permissions)} |"
            for a in ir.agents
        )
        return f"""# {ir.name} — local deployment

Generated from the system spec (spec_version {ir.spec_version}).
**Do not edit generated files**; put customizations in `overlays/`.

## Run it

```bash
cp .env.example .env     # fill in secret values from your secret manager
make up                  # start the stack
make seed                # load the compiled organization
open http://localhost:8000/ui/
```

No container runtime? `make single` runs everything in one process over SQLite.

## What was generated

| Agent | Team | Environment | Network | Permissions |
|---|---|---|---|---|
{agents}

## Scheduled and event-driven work

| Trigger | When | Runs | Delivers to |
|---|---|---|---|
{triggers}

The scheduler holds no credentials of its own: it wakes the owning agent, which
runs under its own identity and its own permission set, exactly as it would for
interactive work.

## Human channels

| Channel | Provider | Purposes | SLA | Out of hours |
|---|---|---|---|---|
{channels}

## Caveats

Compose approximates network posture with attached networks and cannot
represent cloud IAM at all. Isolated (`none`) environments are placed on an
internal network with no gateway, which is close — but a local run does **not**
verify the IAM bindings the cloud targets generate. Use a cloud target to test
those.
"""
