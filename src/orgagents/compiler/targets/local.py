"""Local target: Docker defines the system (ADR-0011 v1.1.0).

The whole deployment is expressed in Docker: a **Dockerfile per environment
class**, built from the binding's image and packages, plus a Compose file that
wires agents, MCP servers, the scheduler, channel bridges, memory and state
together. Nothing references an image that this output does not build or name
explicitly, because a compose file that pulls images nobody can build is a
demo, not a deployment.

The same IR feeds the cloud targets, so local is the identical system with
different bindings — not a mock. A single-process mode remains for machines
without a container runtime.
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
            "produces": ["Dockerfile", "docker/Dockerfile.<environment>",
                         ".dockerignore", "docker-compose.yaml", "Makefile",
                         ".env.example",
                         "system.ir.json", "agents/*.json", "triggers.json",
                         "channels.json", "memory.json", "REGISTRY.md",
                         "run_local.py", "README.md"],
            "caveats": ["Compose approximates network policy and cannot represent "
                        "cloud IAM; local runs do not verify those controls.",
                        "Tenant isolation on one host is a Compose project with "
                        "its own networks and named volumes — not a kernel or "
                        "account boundary."],
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
            GeneratedFile("Dockerfile", self._runtime_dockerfile(ir)).with_header(ir),
            GeneratedFile(".dockerignore", self._dockerignore()),
            GeneratedFile("requirements.txt", self._requirements(ir)),
        ] + [
            GeneratedFile(f"docker/Dockerfile.{env.id}",
                          self._environment_dockerfile(ir, env)).with_header(ir)
            for env in self._used_environments(ir)
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
        networks: dict[str, Any] = {ir.qualified("control"): {}}
        # Channel bridges always need egress to reach the chat provider.
        if any(c.human_facing for c in ir.channels) or any(
            a.environment and a.environment.network.value != "none" for a in ir.agents
        ):
            networks[ir.qualified("egress")] = {}
        if any(
            a.environment and a.environment.network.value == "none" for a in ir.agents
        ):
            # An isolated network with no gateway: services on it reach nothing.
            networks[ir.qualified("isolated")] = {"internal": True}
        return networks

    def _used_environments(self, ir: SystemIR) -> list[Any]:
        """Environment classes some agent actually runs in."""
        used = {a.environment.id: a.environment for a in ir.agents if a.environment}
        return [used[k] for k in sorted(used)]

    def _requirements(self, ir: SystemIR) -> str:
        """What the runtime image installs. Pin these for a reproducible build."""
        adapters = sorted({a.runtime_adapter for a in ir.agents})
        extras = {
            "langchain_deepagents": "orgagents[langgraph]",
            "langgraph_native": "orgagents[langgraph]",
            "openai_agents_sdk": "orgagents[openai]",
        }
        wanted = sorted({extras.get(a, "orgagents") for a in adapters}) or ["orgagents"]
        provider = ir.binding.model.provider
        provider_package = {
            "anthropic": "anthropic>=0.40", "openai": "openai>=1.40",
        }.get(provider, "")
        lines = [
            "# Generated from the compiled system. Pin versions before you ship.",
            *wanted,
        ]
        if provider_package:
            lines.append(provider_package)
        return "\n".join(lines) + "\n"

    def _dockerignore(self) -> str:
        return "\n".join([
            "# Keep build context small and free of anything secret.",
            ".git", ".venv", "__pycache__", "*.pyc", "*.db", ".env",
            "build/", "overlays/", "*.log",
        ]) + "\n"

    def _runtime_dockerfile(self, ir: SystemIR) -> str:
        """The image every platform service runs: designer, scheduler, bridges."""
        return f'''# The orgagents runtime for '{ir.name}'.
# Built once and reused by the designer, the scheduler, the channel bridges and
# the memory service, so they cannot drift apart.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1 \\
    ORGAGENTS_SYSTEM={ir.name}

WORKDIR /app

RUN apt-get update \\
 && apt-get install -y --no-install-recommends ca-certificates curl \\
 && rm -rf /var/lib/apt/lists/*

# The platform itself. Point this at your own package index or wheel in a
# regulated build; nothing here reaches the public internet at run time.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY agents/ /app/agents/
COPY triggers.json channels.json memory.json system.ir.json /app/

# Never run as root: the sandbox boundary is the platform's, not the image's.
RUN useradd --create-home --uid 10001 agent \\
 && chown -R agent:agent /app
USER agent

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \\
  CMD curl -fsS http://localhost:8000/healthz || exit 1

EXPOSE 8000
CMD ["orgagents", "serve", "--host", "0.0.0.0"]
'''

    def _environment_dockerfile(self, ir: SystemIR, env) -> str:
        """One image per environment class — the isolation boundary, built."""
        binding = ir.binding.environment_binding(env.id)
        image = binding.image if binding else TOOLCHAIN_IMAGES.get(
            env.toolchains[0].value if env.toolchains else "none", "python:3.11-slim")
        packages = " ".join(binding.packages) if binding and binding.packages else ""
        toolchains = ", ".join(t.value for t in env.toolchains) or "none"
        install = (
            f"RUN pip install --no-cache-dir {packages}" if packages
            else "# no additional packages for this environment class"
        )
        network_note = {
            "none": "This environment has NO network. Compose attaches it to an "
                    "internal network with no gateway.",
            "allowlist": f"Egress is limited to: "
                         f"{', '.join(env.egress_allowlist) or 'nothing declared'}.",
            "internal": "Egress is limited to internal services.",
            "open": "Egress is unrestricted — review whether this is intended.",
        }[env.network.value]
        return f'''# Execution environment '{env.id}' for '{ir.name}'.
# tier={env.tier.value} · network={env.network.value} · timeout={env.timeout_seconds}s
# toolchains: {toolchains}
# mounts: {", ".join(env.mounts) or "none"}
#
# {network_note}
FROM {image}

ENV PYTHONUNBUFFERED=1 \\
    ORGAGENTS_ENVIRONMENT={env.id}

WORKDIR /workspace

{install}

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY agents/ /app/agents/
COPY system.ir.json /app/system.ir.json

RUN useradd --create-home --uid 10001 agent 2>/dev/null || true \\
 && mkdir -p /workspace && chown -R agent /workspace /app
USER agent

CMD ["orgagents", "worker"]
'''

    def _agent_service(self, ir: SystemIR, agent) -> dict[str, Any]:
        env = agent.environment
        tier = env.tier.value if env else "minimal"
        toolchain = env.toolchains[0].value if env and env.toolchains else "none"
        posture = env.network.value if env else "none"
        limits = TIER_RESOURCES.get(tier, TIER_RESOURCES["minimal"])
        networks = [ir.qualified("control")]
        networks.append(
            ir.qualified("isolated" if posture == "none" else "egress")
        )
        service: dict[str, Any] = {
            # Built from this agent's environment class, so the container it runs
            # in *is* the isolation boundary the spec declared.
            "build": {
                "context": ".",
                "dockerfile": f"docker/Dockerfile.{env.id}" if env
                else "Dockerfile",
            },
            "image": f"{ir.name}/agent-{agent.id}:latest",
            "command": ["orgagents", "worker", agent.id],
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
                # The tenant is on the object itself, so an operator reading a
                # running container can tell whose it is without the manifest.
                "org.agentic.tenant": ir.tenant.id if ir.tenant else "",
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
                "networks": [ir.qualified("control")],
                "volumes": [f'{ir.qualified("state-data")}:/var/lib/postgresql/data'],
            },
            "telemetry": {
                "image": "otel/opentelemetry-collector-contrib:latest",
                "networks": [ir.qualified("control")],
                "ports": ["4317:4317"],
            },
            "designer": {
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "image": f"{ir.name}/platform:latest",
                "command": ["orgagents", "serve", "--host", "0.0.0.0"],
                "ports": ["8000:8000"],
                "networks": [ir.qualified("control")],
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
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "image": f"{ir.name}/platform:latest",
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
                "networks": [ir.qualified("control")],
                "depends_on": ["state"],
                "labels": {"org.agentic.triggers": str(len(ir.triggers))},
            }

        # Long-term memory needs somewhere to live that outlives a session.
        if ir.memory.long_term.enabled:
            memory = ir.binding.memory
            services["memory"] = {
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "image": f"{ir.name}/platform:latest",
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
                "networks": [ir.qualified("control")],
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
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "image": f"{ir.name}/platform:latest",
                "command": ["serve", "--channel", channel.id],
                "environment": env,
                "volumes": ["./channels.json:/app/channels.json:ro"],
                "networks": [ir.qualified("control"), ir.qualified("egress")],
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
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "image": binding.options.get("image", f"{ir.name}/platform:latest"),
                "command": [binding.command or "serve", *binding.args],
                "environment": (
                    {binding.dsn_secret_ref: f"${{{binding.dsn_secret_ref}}}"}
                    if binding.dsn_secret_ref
                    else {}
                ),
                "networks": [ir.qualified("control")],
                "labels": {"org.agentic.capability": cap.id},
            }
        return yaml.safe_dump(
            {
                "name": ir.name.lower().replace(" ", "-"),
                "services": services,
                "networks": self._networks(ir),
                "volumes": {ir.qualified("state-data"): {}},
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

images:        ## list the images this system defines
	docker compose config --images

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
        tenant_section = (
            f"""This stack belongs to tenant **{ir.tenant.id}** (isolation domain
`{ir.tenant.isolation_domain}`). Every Compose project name, network, named
volume, identity and secret reference below carries the prefix
`{ir.tenant.namespace_prefix}-`, so a second tenant's stack on this host shares
no Docker object with it.

What enforces the boundary here: the Compose **project name**, the per-tenant
**networks** and the per-tenant **named volumes**. That is a Docker-level
boundary, not a kernel or account one — containers still share this host's
kernel, and anyone with access to the Docker socket can reach every tenant on
it. Locally, the enforcement is **coarser than the model** ADR-0050 describes."""
            if ir.tenant
            else """This system was compiled without a tenant, so nothing here is
namespaced: it is safe on a host that runs one system and nothing else."""
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

## Tenant isolation

{tenant_section}

## Caveats

Compose approximates network posture with attached networks and cannot
represent cloud IAM at all. Isolated (`none`) environments are placed on an
internal network with no gateway, which is close — but a local run does **not**
verify the IAM bindings the cloud targets generate. Use a cloud target to test
those.
"""
