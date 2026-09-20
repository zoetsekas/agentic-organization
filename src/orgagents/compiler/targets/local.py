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

from ...runtime.engines import InvocationMode, UnknownEngine, engine
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
# Toolchain class -> sandbox image. ADR-0053 names four toolchain images and
# this vocabulary has eight classes, so the classes it does not name are mapped
# onto the nearest one it does rather than to an image nobody reviewed; ADR-0055
# records what that costs (a `browser` sandbox has no browser).
TOOLCHAIN_IMAGES = {
    "none": "gcr.io/distroless/static-debian12:nonroot",
    "scripting": "python:3.11-slim",
    "data_analysis": "python:3.11-slim",
    "software_build": "node:22-alpine",
    "browser": "python:3.11-slim",
    "document": "python:3.11-slim",
    "model_training": "python:3.11-slim",
    "network_client": "python:3.11-slim",
}
# Images with no shell and no package manager: the generated Dockerfile for one
# of these cannot RUN anything, which is the point of choosing it.
NO_RUNTIME_IMAGES = ("gcr.io/distroless/",)

# Pinned per ADR-0053; `latest` on a collector is a silent upgrade of the one
# component every plane exports to.
OTEL_COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.110.0"
STATE_IMAGE = "postgres:16-alpine"
# The artifact workspace (ADR-0036) is per tenant, with the tenant's own
# volume: a shared bucket is a cross-tenant read away from being one.
ARTIFACTS_IMAGE = "minio/minio:RELEASE.2024-09-13T20-26-02Z"
# Out-of-process workflow engines (ADR-0056). Pinned per ADR-0053 rule 2 — a
# floating tag on an engine that executes somebody's flows is an unreviewed
# upgrade of a component outside our sandbox. The tag below was checked against
# the registry and exists; no daemon exists here, so the image has never been
# pulled or run. Override it in the binding (`options.image`) if your registry
# mirrors a different build.
SERVICE_ENGINE_IMAGES = {
    # Verified against Docker Hub on 2026-09-20: 1.12.2 is a real published
    # release (digest sha256:79c02794…). The tag that was here before was not,
    # which is why ADR-0053 wants a resolved lock file rather than a version
    # somebody was fairly sure about.
    "langflow": "langflowai/langflow:1.12.2",
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
        if self._service_engines(ir):
            files.append(GeneratedFile("flows/README.md", self._flows_readme(ir)))
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

    def _service_engines(self, ir: SystemIR) -> list[Any]:
        """Workflow bindings whose engine runs in another process.

        The registry decides the mode, not the binding document, so a binding
        that claims `in_process` for a service engine still lands here.
        """
        out: dict[str, Any] = {}
        for wb in ir.binding.workflows:
            try:
                descriptor = engine(wb.engine)
            except UnknownEngine:
                continue
            if descriptor.mode is InvocationMode.OUT_OF_PROCESS:
                out.setdefault(wb.engine, wb)
        return [out[k] for k in sorted(out)]

    def _engine_secret_ref(self, binding: Any) -> str:
        return binding.secret_ref or f"{binding.engine.upper()}_SECRET_KEY"

    def _service_engine_services(self, ir: SystemIR) -> dict[str, Any]:
        """One container per out-of-process workflow engine (ADR-0056).

        It sits on the tenant's **egress** network and not on `control`, which
        is what makes the ADR's rule concrete in Compose: an agent whose
        environment is `network: none` is on `control` + `isolated` and so
        cannot reach this service at all. That is the environment class
        working, not a wiring bug.
        """
        out: dict[str, Any] = {}
        for wb in self._service_engines(ir):
            secret_ref = self._engine_secret_ref(wb)
            image = wb.options.get("image") or SERVICE_ENGINE_IMAGES.get(wb.engine)
            if not image:
                continue
            data_dir = f"/var/lib/{wb.engine}"
            environment = {
                # The engine authenticates as itself. No agent credential is
                # mounted here, by design (ADR-0056).
                secret_ref: f"${{{secret_ref}}}",
                f"{wb.engine.upper()}_CONFIG_DIR": data_dir,
            }
            environment.update(wb.options.get("environment") or {})
            out[wb.engine] = {
                "image": image,
                "environment": environment,
                "networks": [ir.qualified("egress")],
                "volumes": [
                    # Flows are authored outside the spec: mounted read-only so
                    # the engine cannot rewrite what was reviewed.
                    "./flows:/app/flows:ro",
                    f'{ir.qualified(wb.engine + "-data")}:{data_dir}',
                ],
                "labels": {
                    "org.agentic.tenant": ir.tenant.id if ir.tenant else "",
                    "org.agentic.workflow_engine": wb.engine,
                    "org.agentic.invocation": "out_of_process",
                    "org.agentic.outside_agent_sandbox": "true",
                },
            }
        return out

    def _networks(self, ir: SystemIR) -> dict[str, Any]:
        networks: dict[str, Any] = {ir.qualified("control"): {}}
        # Channel bridges always need egress to reach the chat provider.
        if (
            any(c.human_facing for c in ir.channels)
            or any(a.environment and a.environment.network.value != "none"
                   for a in ir.agents)
            or self._service_engines(ir)
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
        image = self._sandbox_image(ir, env)
        packages = " ".join(binding.packages) if binding and binding.packages else ""
        toolchains = ", ".join(t.value for t in env.toolchains) or "none"
        # A distroless base has no shell and no package manager, which is what
        # makes it the right image for an environment that executes nothing.
        # So this Dockerfile installs nothing and copies nothing runnable.
        runnable = not image.startswith(NO_RUNTIME_IMAGES)
        install = (
            f"RUN pip install --no-cache-dir {packages}" if packages and runnable
            else "# no additional packages for this environment class"
        )
        if packages and not runnable:
            install = (
                "# packages declared in the binding are ignored here: this base\n"
                "# image has no package manager, by design."
            )
        network_note = {
            "none": "This environment has NO network: the sandbox service is "
                    "given none at all, and the agent process that uses it sits "
                    "on an internal network with no gateway.",
            "allowlist": f"Egress is limited to: "
                         f"{', '.join(env.egress_allowlist) or 'nothing declared'}.",
            "internal": "Egress is limited to internal services.",
            "open": "Egress is unrestricted — review whether this is intended.",
        }[env.network.value]
        if runnable:
            body = """COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY agents/ /app/agents/
COPY system.ir.json /app/system.ir.json

RUN useradd --create-home --uid 10001 agent 2>/dev/null || true \\
 && mkdir -p /workspace && chown -R agent /workspace /app
USER agent

CMD ["orgagents", "worker"]"""
        else:
            body = """COPY system.ir.json /system.ir.json

# distroless ships a non-root user; there is no shell here to create one with.
USER nonroot"""
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

{body}
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
            # The agent process runs on the platform runtime image; the
            # environment class is the image its *code execution* happens in
            # (the sandbox-<class> services below, ADR-0055). Conflating the
            # two gave an agent in a `none` environment a distroless image with
            # no interpreter to run on.
            "build": {"context": ".", "dockerfile": "Dockerfile"},
            "image": f"{ir.name}/agent-{agent.id}:{ir.spec_version}",
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

    def _sandbox_services(self, ir: SystemIR) -> dict[str, Any]:
        """One build-only service per environment class in use (ADR-0053).

        These are not started by `up`: a sandbox is a thing code is executed
        *in*, on demand, not a long-running process. They are here so the image
        an environment class resolves to, and the network it is allowed, are
        visible and buildable in the same file as everything else rather than
        living only in the platform's head.
        """
        out: dict[str, Any] = {}
        for env in self._used_environments(ir):
            posture = env.network.value
            service: dict[str, Any] = {
                "profiles": ["sandboxes"],
                "build": {"context": ".", "dockerfile": f"docker/Dockerfile.{env.id}"},
                "image": f"{ir.name}/sandbox-{env.id}:{ir.spec_version}",
                "labels": {
                    "org.agentic.tenant": ir.tenant.id if ir.tenant else "",
                    "org.agentic.environment": env.id,
                    "org.agentic.network_posture": posture,
                    "org.agentic.sandbox_image": self._sandbox_image(ir, env),
                },
            }
            if posture == "none":
                # No networks at all, not an internal one: a zero-network
                # sandbox that can still resolve its neighbours is not one.
                service["network_mode"] = "none"
            else:
                service["networks"] = [ir.qualified("egress")]
            out[f"sandbox-{env.id}"] = service
        return out

    def _sandbox_image(self, ir: SystemIR, env) -> str:
        binding = ir.binding.environment_binding(env.id)
        if binding and binding.image:
            return binding.image
        return TOOLCHAIN_IMAGES.get(
            env.toolchains[0].value if env.toolchains else "none",
            TOOLCHAIN_IMAGES["none"],
        )

    def _compose(self, ir: SystemIR) -> str:
        services: dict[str, Any] = {
            "state": {
                "image": STATE_IMAGE,
                "environment": {
                    "POSTGRES_PASSWORD": "${STATE_PASSWORD}",
                    "POSTGRES_DB": "orgagents",
                },
                "networks": [ir.qualified("control")],
                "volumes": [f'{ir.qualified("state-data")}:/var/lib/postgresql/data'],
            },
            "telemetry": {
                "image": OTEL_COLLECTOR_IMAGE,
                "networks": [ir.qualified("control")],
                "ports": ["4317:4317"],
            },
            # The artifact workspace large tool output is offloaded to
            # (ADR-0036). This tenant's own instance on this tenant's own
            # volume; ADR-0053 rejects a shared bucket with a prefix per tenant.
            "artifacts": {
                "image": ARTIFACTS_IMAGE,
                "command": ["server", "/data", "--console-address", ":9001"],
                "environment": {
                    "MINIO_ROOT_USER": "${ARTIFACTS_USER}",
                    "MINIO_ROOT_PASSWORD": "${ARTIFACTS_PASSWORD}",
                },
                "networks": [ir.qualified("control")],
                "volumes": [f'{ir.qualified("artifacts-data")}:/data'],
            },
            "designer": {
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "image": f"{ir.name}/platform:{ir.spec_version}",
                "command": ["orgagents", "serve", "--host", "0.0.0.0"],
                "ports": ["8000:8000"],
                "networks": [ir.qualified("control")],
                "depends_on": ["state"],
            },
        }
        for agent in ir.agents:
            services[f"agent-{agent.id}"] = self._agent_service(ir, agent)
        services.update(self._sandbox_services(ir))
        services.update(self._service_engine_services(ir))

        # One scheduler for every trigger (ADR-0020). It holds no credentials of
        # its own: it wakes the owning agent, which runs under its own identity.
        if ir.triggers:
            scheduler = ir.binding.scheduler
            services["scheduler"] = {
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "image": f"{ir.name}/platform:{ir.spec_version}",
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
                "image": f"{ir.name}/platform:{ir.spec_version}",
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
                "image": f"{ir.name}/platform:{ir.spec_version}",
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
                "image": binding.options.get("image", f"{ir.name}/platform:{ir.spec_version}"),
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
                "volumes": {
                    ir.qualified("state-data"): {},
                    ir.qualified("artifacts-data"): {},
                    **{
                        ir.qualified(f"{wb.engine}-data"): {}
                        for wb in self._service_engines(ir)
                    },
                },
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
            "ARTIFACTS_USER=",
            "ARTIFACTS_PASSWORD=",
        ]
        lines += [
            f"{self._engine_secret_ref(wb)}=" for wb in self._service_engines(ir)
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

    def _flows_readme(self, ir: SystemIR) -> str:
        engines = ", ".join(f"`{wb.engine}`" for wb in self._service_engines(ir))
        return f"""# Flows for {ir.name}

Put the flow exports this system's out-of-process engines ({engines}) run in
this directory. They are mounted **read-only** into the engine container.

These flows are outside the system spec: nothing here is validated, diffed or
version-gated by `orgagents spec validate`, and a flow can change under a
system that was reviewed and approved. A flow also runs outside the calling
agent's sandbox — the engine's limits, network and filesystem apply, not the
agent environment class's (ADR-0056).
"""

    def _engine_section(self, ir: SystemIR) -> str:
        service_engines = self._service_engines(ir)
        if not service_engines:
            return (
                "Every workflow here runs **in this process**, under the calling "
                "agent's identity, permissions and sandbox. No workflow engine "
                "service is generated, so nothing about a workflow run leaves "
                "the agent's boundary."
            )
        rows = "\n".join(
            f"| `{wb.engine}` | `{self._service_engine_services(ir)[wb.engine]['image']}` "
            f"| `{self._engine_secret_ref(wb)}` | "
            f"{', '.join(wb.send_data_classes) or 'nothing'} |"
            for wb in service_engines
        )
        return f"""| Engine | Image | Credential | May be sent |
|---|---|---|---|
{rows}

**A flow that runs in one of these services runs
outside the agent's sandbox.** Its CPU and memory limits, its network posture and its filesystem
are the engine's, not the ones the agent's environment class declares. What
this platform governs is the **call**: the agent's egress allowlist decides
whether the engine is reachable at all (an agent whose environment is
`network: none` is on the isolated network and cannot reach it), what may be
sent is checked against the caller's data classification before anything
leaves, the engine authenticates with its own `secret_ref` and never with the
agent's, and the reply comes back as untrusted input through the tool-output
guardrail (ADR-0035, ADR-0056).

Flows are authored in the engine, not in the spec, and are mounted read-only
from `./flows`. That means a flow is a dependency the system spec cannot see
or version: it can change under a system that was already reviewed."""

    def _sandbox_section(self, ir: SystemIR) -> str:
        """What actually isolates a sandbox here, stated without overselling.

        The tenant boundary and the sandbox boundary are different claims
        (ADR-0054). Locally the honest answer is uncomfortable — a shared host
        kernel — and a reader who is not told that will assume otherwise,
        because the tenant section above sounds reassuring.
        """
        from ...sandboxes import EnvironmentFacts, detect_context, resolve_provider

        requested = (ir.sandbox_provider or "container") if hasattr(
            ir, "sandbox_provider") else "container"
        context = detect_context(target="local")
        statement = None
        gaps: dict[str, list[str]] = {}
        degraded: list[str] = []
        for env in ir.environments:
            facts = EnvironmentFacts.from_environment_class(
                env, tenant_id=ir.tenant.id if ir.tenant else None
            )
            resolution = resolve_provider(requested, facts, context)
            statement = statement or resolution.boundary
            if resolution.degraded and resolution.degradation:
                note = resolution.degradation.reason
                if note not in degraded:
                    degraded.append(note)
            if resolution.mapping.unexpressible:
                gaps[env.id] = list(resolution.mapping.unexpressible)
        if statement is None:
            return "This system declares no environment classes.\n"
        lines = [
            f"Provider in force: **{statement.provider}** ({statement.maturity}). "
            f"{statement.summary}",
            "",
            f"- **Enforces:** {'; '.join(statement.enforces)}",
            f"- **Does not enforce:** {'; '.join(statement.does_not_enforce)}",
            f"- **Kernel boundary:** {'yes' if statement.kernel_boundary else 'no'}",
            f"- **Tenant isolation:** {statement.tenant_scoping.value} — "
            f"{statement.tenant_note}",
            "- **Verified here:** no. No sandbox provider was run when this "
            "stack was generated (ADR-0054).",
        ]
        if degraded:
            lines += ["", f"> **Degraded from `{requested}`.** " + " ".join(degraded)
                      + " The boundary below is the fallback's, not the one asked for."]
        if gaps:
            distinct = {tuple(v) for v in gaps.values()}
            lines += ["", "What the environment class cannot express here:", ""]
            if len(distinct) == 1 and len(gaps) == len(ir.environments):
                lines += [f"- {item} (every environment class)"
                          for item in next(iter(distinct))]
            else:
                lines += [f"- `{env_id}`: " + "; ".join(items)
                          for env_id, items in gaps.items()]
        return "\n".join(lines) + "\n"

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
        sandbox_section = self._sandbox_section(ir)
        engine_section = self._engine_section(ir)
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

## Sandbox execution

{sandbox_section}

## Workflow engines

{engine_section}

## Caveats

Compose approximates network posture with attached networks and cannot
represent cloud IAM at all. Isolated (`none`) environments are placed on an
internal network with no gateway, which is close — but a local run does **not**
verify the IAM bindings the cloud targets generate. Use a cloud target to test
those.
"""
