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

from ...bus import SubjectNamespace
from ...runtime.engines import InvocationMode, UnknownEngine, engine
from ..base import GeneratedFile
from ..ir import SystemIR
from ..links import (BUS_ADMIN_PASSWORD_REF, BUS_ADMIN_USER, agent_links, bus_password_ref,
                     bus_user, nats_config)
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
#: Ordered widest-last, so "the widest sandbox an agent has" is a max().
_TIER_ORDER = ("minimal", "small", "medium", "large", "accelerated")
_POSTURE_ORDER = ("none", "allowlist", "internal", "open")


def _widest_environment(environments: list) -> Any:
    """The sandbox with the widest reach, for a decision that admits one.

    An agent may run in several (ADR-0082) and a deployed workload is one
    shape, so where a target can express only one it takes the widest:
    under-sizing or under-permitting a service would make the design
    undeployable. The narrower sandboxes still bound what its *code
    execution* may reach, which is where the isolation actually lives.
    """
    if not environments:
        return None
    return max(environments, key=lambda e: (
        _POSTURE_ORDER.index(e.network.value)
        if e.network.value in _POSTURE_ORDER else 0,
        _TIER_ORDER.index(e.tier.value)
        if e.tier.value in _TIER_ORDER else 0,
    ))


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
# S3-compatible artifact store for the tenant workspace (ADR-0036).
# SeaweedFS rather than MinIO: MinIO's Docker Hub repository no longer serves
# tags at all, and its images now live on quay.io, which cannot be reached from
# every network that builds this. An image we cannot pin is not a pin
# (ADR-0053 v1.3.0). Tag and digest verified against the registry 2026-09-20.
ARTIFACTS_IMAGE = "chrislusf/seaweedfs:3.97"
# Out-of-process workflow engines (ADR-0056). Pinned per ADR-0053 rule 2 — a
# floating tag on an engine that executes somebody's flows is an unreviewed
# upgrade of a component outside our sandbox. The tag below was checked against
# the registry and exists; no daemon exists here, so the image has never been
# pulled or run. Override it in the binding (`options.image`) if your registry
# mirrors a different build.
# The asynchronous message bus (ADR-0059). One instance per tenant, on the
# tenant's network, with the tenant's own volume — the same rule ADR-0053 rule 3
# applies to Postgres and the artifact store, for the same reason: a broker
# shared across tenants is one ACL mistake from a cross-tenant leak. Tag and
# digest verified against the registry 2026-09-20; no daemon exists here, so
# this service is generated and parsed, never started.
BUS_IMAGE = "nats:2.15.0-alpine"

# The human channel surface (ADR-0061). One instance per tenant, on the tenant's
# own network, with the tenant's own volume and bot tokens — a chat server
# shared across tenants is a cross-tenant channel whatever its own permission
# model claims. Team Edition because its bot accounts are API-mintable and its
# interactive dialogs can deliver an authenticated click; the tag and digest
# were verified against the registry on 2026-09-20 and nothing has been pulled
# or run here, so no capability claim below is first-hand.
CHANNEL_SURFACE_IMAGES = {
    "mattermost": "mattermost/mattermost-team-edition:11.11.0",
}
# Its database is the Postgres this stack already pins (ADR-0053): Mattermost
# supports Postgres and adding a second major version per tenant would be a
# second patch cadence for no gain.
CHANNEL_SURFACE_DB_IMAGE = STATE_IMAGE

SERVICE_ENGINE_IMAGES = {
    # Verified against Docker Hub on 2026-09-20: 1.12.2 is a real published
    # release (digest sha256:79c02794…). The tag that was here before was not,
    # which is why ADR-0053 wants a resolved lock file rather than a version
    # somebody was fairly sure about.
    "langflow": "langflowai/langflow:1.12.2",
}

#: What every container that runs an agent's work gets (ADR-0114): an image
#: filesystem it cannot change, no Linux capabilities, no way to gain
#: privilege through a setuid binary, and a bounded process count. Anything
#: that has to be written goes to a tmpfs named per service.
CONTAINER_HARDENING: dict[str, Any] = {
    "read_only": True,
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges:true"],
    "pids_limit": 256,
}

#: Host ports are published on the loopback address only (ADR-0114). A local
#: stack is for the person at this machine; publishing on every interface put
#: the designer and the workers' neighbours on whatever network the laptop
#: had joined.
PUBLISH_HOST = "127.0.0.1"


def env_suffix(agent_id: str) -> str:
    """`buyer_agent` -> `BUYER_AGENT`: the per-agent part of a variable name."""
    return "".join(c if c.isalnum() else "_" for c in agent_id).upper()


def worker_token_ref(agent_id: str) -> str:
    """The variable holding one worker's service token (ADR-0114)."""
    return f"ORGAGENTS_WORKER_TOKEN_{env_suffix(agent_id)}"


#: The variable holding the approval issuer's public keys (`kid:key,...`).
#: Public: every worker is given the same list and can verify with it, but
#: none can sign (ADR-0114 v1.1). The private key is the issuer's alone.
APPROVAL_PUBLIC_KEYS_REF = "ORGAGENTS_APPROVAL_PUBLIC_KEYS"

#: Where a worker keeps what must outlive a restart -- the nonces of releases
#: it has used, and its audit log. A named volume per agent; the rest of the
#: root filesystem stays read-only.
WORKER_STATE_DIR = "/var/lib/orgagents"


WHEELS_README = """# wheels/

Anything here is installed into the runtime image **before** `requirements.txt`
and wins over any package index. It is how a stack is built from an orgagents
checkout that is not published anywhere:

```bash
pip wheel /path/to/orgagents-checkout --no-deps -w wheels/
docker compose build
```

Built wheels are ignored by git: they are a build input, not source. The
Dockerfiles copy `wheel[s]`, a pattern, so a checkout where this folder does
not exist at all still builds from the package index alone.
"""


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
                         "system.ir.json", "agents/*.json", "nats/nats.conf",
                         "triggers.json",
                         "channels.json", "memory.json", "REGISTRY.md",
                         "run_local.py", "README.md", "wheels/README.md"],
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
            GeneratedFile("overlays/README.md", self._overlays_readme(ir),
                          preserve_if_exists=True),
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
            # The broker's users and subject permissions: the design's edges,
            # enforced by NATS as well as by both workers (ADR-0117).
            GeneratedFile("nats/nats.conf", nats_config(ir, server_name=ir.qualified("bus"))),
            GeneratedFile("wheels/README.md", WHEELS_README),
            # A built wheel is a build input, not source: never committed.
            GeneratedFile("wheels/.gitignore", "*.whl\n"),
        ] + [
            GeneratedFile(f"docker/Dockerfile.{env.id}",
                          self._environment_dockerfile(ir, env)).with_header(ir)
            for env in self._used_environments(ir)
        ]
        if self._service_engines(ir):
            files.append(GeneratedFile("flows/README.md", self._flows_readme(ir)))
        links = agent_links(ir)
        for agent in ir.agents:
            files.append(
                GeneratedFile(
                    f"agents/{agent.id}.json",
                    json.dumps(
                        {
                            "agent": agent.model_dump(mode="json"),
                            "system_prompt": agent.system_prompt(),
                            # Who it may reach and who may reach it, and the
                            # separations a delegation must keep: computed
                            # here, never by the worker (ADR-0117).
                            "links": links[agent.id],
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
        # `control` carries the platform's own traffic — state, bus,
        # telemetry, the workers' HTTP — and none of it needs the internet, so
        # it has no gateway (ADR-0114). Anything that does need out is on
        # `egress` as well; anything a person reaches from the host is on
        # `ingress` as well.
        networks: dict[str, Any] = {
            ir.qualified("control"): {"internal": True},
            # A published port needs a network with a gateway, and Docker
            # gives an internal network none. This one takes the published
            # ports and nothing else, and no agent joins it. Masquerading is
            # off, which on a Linux engine means a container on it can be
            # reached from the host and cannot reach out; Docker Desktop's VM
            # networking still routes it out, so there it is a narrowing, not
            # a barrier (ADR-0114 says so).
            ir.qualified("ingress"): {
                "driver_opts": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
            },
        }
        # Channel bridges always need egress to reach the chat provider.
        if (
            any(c.human_facing for c in ir.channels)
            or any(e.network.value != "none"
                   for a in ir.agents for e in a.environments)
            or self._service_engines(ir)
        ):
            networks[ir.qualified("egress")] = {}
        if any(
            all(e.network.value == "none" for e in a.environments) if a.environments
            else True for a in ir.agents
        ):
            # An isolated network with no gateway: services on it reach nothing.
            networks[ir.qualified("isolated")] = {"internal": True}
        # One internal network per placement (ADR-0069). This is what makes
        # rule 7's default deny real in Compose: Docker routes only between
        # containers that share a network, so an agent reaches another
        # placement exactly when a standing rule attached it to that
        # placement's network, and not otherwise.
        for placement in self._placements(ir):
            networks[self._placement_network(ir, placement.id)] = {"internal": True}
        # One internal network per backing system (ADR-0109), joined only by
        # the system and the agents holding a capability bound to it.
        for server in self._servers(ir):
            networks[self._server_network(ir, server)] = {"internal": True}
        return networks

    def _used_environments(self, ir: SystemIR) -> list[Any]:
        """Environment classes some agent actually runs in.

        Still per class, because the *image* is a property of the profile: two
        placements sharing `analysis` build the same Dockerfile. What is no
        longer keyed this way is the sandbox *environment* — see `_placements`.
        """
        used = {e.id: e for a in ir.agents for e in a.environments}
        return [used[k] for k in sorted(used)]

    def _placements(self, ir: SystemIR) -> list[Any]:
        """Sandbox environments, keyed by whose work it is (ADR-0069).

        This is the keying change the record names. `_used_environments` put an
        HR agent and a Finance agent that both need `analysis` on one key,
        because an environment class says what an agent needs and not whose
        work it is.
        """
        return sorted(ir.placements, key=lambda p: p.id)

    def _placement_network(self, ir: SystemIR, placement_id: str) -> str:
        """The Compose network standing in for one placement's namespace."""
        return ir.qualified(f"place-{placement_id}")

    def _environment_of(self, ir: SystemIR, placement_id: str) -> Any:
        wanted = next(
            (p.environment for p in ir.placements if p.id == placement_id), None
        )
        return next(
            (e for e in self._used_environments(ir) if e.id == wanted), None
        )

    def _requirements(self, ir: SystemIR) -> str:
        """What the runtime image installs. Pin these for a reproducible build."""
        adapters = sorted({a.runtime_adapter for a in ir.agents})
        extras = {
            "langchain_deepagents": "orgagents[langgraph]",
            "langgraph_native": "orgagents[langgraph]",
            "openai_agents_sdk": "orgagents[openai]",
        }
        wanted = sorted({extras.get(a, "orgagents") for a in adapters}) or ["orgagents"]
        # Every agent container is on the tenant's bus (ADR-0117).
        wanted.append("orgagents[bus]")
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

# The platform itself. A wheel dropped in wheels/ is installed first and wins
# over any index (`pip wheel <orgagents checkout> --no-deps -w wheels/`), which
# is how a checkout that is not published anywhere gets built; otherwise point
# this at your own package index. Nothing here reaches the public internet at
# run time.
# `wheel[s]` is a pattern, so a checkout with no wheels/ folder still builds.
COPY requirements.txt wheel[s] /wheels/
RUN if ls /wheels/*.whl >/dev/null 2>&1; then pip install /wheels/*.whl; fi \\
 && pip install --find-links /wheels -r /wheels/requirements.txt

COPY agents/ /app/agents/
COPY triggers.json channels.json memory.json system.ir.json /app/

# Never run as root: the sandbox boundary is the platform's, not the image's.
RUN useradd --create-home --uid 10001 agent \\
 && mkdir -p /var/lib/orgagents \\
 && chown -R agent:agent /app /var/lib/orgagents
USER agent

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \\
  CMD curl -fsS http://localhost:8000/healthz || exit 1

EXPOSE 8000
CMD ["orgagents", "serve", "--host", "0.0.0.0"]
'''

    def _placement_section(self, ir: SystemIR) -> str:
        """The boundary statement for each placement (ADR-0069 rule 7).

        Traffic *within* a placement is permitted, and that is a widening, so
        the co-resident agents are named here rather than left implicit. What
        is not listed is denied: a placement reaches another only where
        standing structure put a rule in, and never from a mission grant.
        """
        if not ir.placements:
            return (
                "No agent declares an environment class, so this system has no "
                "sandbox environments and nothing to place."
            )
        rows = ["| Placement | Unit | Environment | Co-resident agents | Reaches |",
                "|---|---|---|---|---|"]
        for placement in self._placements(ir):
            reaches = sorted(
                r.target for r in ir.placement_rules if r.source == placement.id
            )
            rows.append(
                f"| `{placement.id}` | {placement.unit} | "
                f"`{placement.environment}` | "
                f"{', '.join(f'`{a}`' for a in placement.agents)} | "
                f"{', '.join(f'`{r}`' for r in reaches) or '— nothing'} |"
            )
        shared = [p for p in self._placements(ir) if p.shares_a_volume]
        note = ""
        if shared:
            note = (
                "\n\nAgents inside one placement share a volume at "
                "`/srv/shared` and a process namespace, and reach each other "
                "without a rule. That is the widening; the table above is who "
                "it covers. The volume carries only the data classes the "
                "unit's groups already share and is **never a new grant** — an "
                "agent without a grant on a class does not acquire it by "
                "sharing a disk with somebody who has one."
            )
        return "\n".join(rows) + note

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
            body = """COPY requirements.txt wheel[s] /wheels/
RUN if ls /wheels/*.whl >/dev/null 2>&1; then pip install --no-cache-dir /wheels/*.whl; fi \\
 && pip install --no-cache-dir --find-links /wheels -r /wheels/requirements.txt

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

    def _subjects(self, ir: SystemIR) -> SubjectNamespace:
        return SubjectNamespace(tenant=ir.tenant.id if ir.tenant else "local")

    def _agent_service(self, ir: SystemIR, agent) -> dict[str, Any]:
        # One service per agent, sized and permitted for the *widest* sandbox
        # it runs in (ADR-0082). A deployed workload is one shape, and
        # under-sizing or under-permitting it would make the design
        # undeployable; the narrower sandboxes still bound what its code
        # execution may reach, which is where the isolation actually lives —
        # the `sandbox-<class>` services below are one per class.
        env = _widest_environment(agent.environments)
        tier = env.tier.value if env else "minimal"
        toolchain = env.toolchains[0].value if env and env.toolchains else "none"
        posture = env.network.value if env else "none"
        limits = TIER_RESOURCES.get(tier, TIER_RESOURCES["minimal"])
        networks = [ir.qualified("control")]
        networks.append(
            ir.qualified("isolated" if posture == "none" else "egress")
        )
        # Its own placement, then every placement standing structure permits it
        # to reach (ADR-0069 rules 6 and 7). `reaches` is resolved at the phase
        # gate and already excludes mission-lent reach, which must never become
        # a rule: a generated rule does not expire and a mission window does.
        networks.extend(
            self._placement_network(ir, pid) for pid in agent.reaches
        )
        # And each backing system it holds a capability on — no other
        # (ADR-0109). Reach between placements is standing structure; reach
        # into a system is a grant, and only a grant.
        networks.extend(
            self._server_network(ir, server)
            for server in self._agent_servers(ir, agent)
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
                # Where the bus is, which subjects are ours, and this
                # agent's own broker user: it can publish only to the
                # inboxes its edges reach, as itself (ADR-0117).
                "ORGAGENTS_BUS": "${ORGAGENTS_BUS:-nats}",
                "ORGAGENTS_BUS_URL": "nats://nats:4222",
                "ORGAGENTS_BUS_SUBJECT_PREFIX": self._subjects(ir).prefix,
                "ORGAGENTS_BUS_USER": bus_user(agent.id),
                "ORGAGENTS_BUS_PASSWORD": f"${{{bus_password_ref(agent.id)}}}",
                # This worker's own service token (no agent is given
                # another's, so reaching a neighbour's port is not being able
                # to make it act), and the issuer's *public* keys it verifies
                # a person's signed approval with: it can check a release and
                # never mint one (ADR-0114).
                "ORGAGENTS_WORKER_TOKEN": f"${{{worker_token_ref(agent.id)}}}",
                "ORGAGENTS_APPROVAL_PUBLIC_KEYS": f"${{{APPROVAL_PUBLIC_KEYS_REF}}}",
                "ORGAGENTS_STATE_DIR": WORKER_STATE_DIR,
            },
            "volumes": ["./agents:/app/agents:ro",
                        f"{ir.qualified(f'agent-state-{agent.id}')}:{WORKER_STATE_DIR}"],
            "networks": networks,
            "depends_on": {
                "state": {"condition": "service_started"},
                "nats": {"condition": "service_healthy"},
                # Its consumer exists before it pulls from it.
                "bus-init": {"condition": "service_completed_successfully"},
            },
            # The process bound goes with the other limits: Compose refuses a
            # service that sets `pids_limit` beside `deploy.resources.limits`.
            "deploy": {"resources": {"limits": {
                **limits, "pids": CONTAINER_HARDENING["pids_limit"]}}},
            # Scratch in /tmp; used nonces and the audit log on the state
            # volume, so a restart forgets neither (ADR-0114).
            **{k: v for k, v in CONTAINER_HARDENING.items() if k != "pids_limit"},
            "tmpfs": ["/tmp"],
            "labels": {
                # The tenant is on the object itself, so an operator reading a
                # running container can tell whose it is without the manifest.
                "org.agentic.tenant": ir.tenant.id if ir.tenant else "",
                "org.agentic.team": agent.team_id,
                # An agent in several sandboxes carries all of them: a label
                # naming one of two would answer the wrong question when
                # somebody greps for what is in a placement (ADR-0082).
                "org.agentic.placement": ",".join(agent.placements),
                "org.agentic.network_posture": posture,
                "org.agentic.identity": agent.identity.id if agent.identity else "",
            },
        }
        for ref in (agent.identity.secret_refs if agent.identity else []):
            service["environment"][ref] = f"${{{ref}}}"
        # The credential of each capability it holds on a backing system, by
        # name; the worker presents it per call (ADR-0109).
        for ref in self._agent_server_secrets(ir, agent):
            service["environment"][ref] = f"${{{ref}}}"
        return service

    def _channel_surfaces(self, ir: SystemIR) -> list[str]:
        """Bound chat products that need a server of their own, deduplicated."""
        seen: list[str] = []
        for channel in ir.channels:
            if not channel.human_facing:
                continue
            if channel.provider in CHANNEL_SURFACE_IMAGES and channel.provider not in seen:
                seen.append(channel.provider)
        return seen

    def _channel_surface_services(self, ir: SystemIR) -> dict[str, Any]:
        """The chat server humans actually use, one per tenant (ADR-0061).

        Generated only when a channel is bound to a product we host. A binding
        that points at somebody else's workspace (Slack, Teams, an OpenClaw
        gateway) gets no service here — there is nothing for us to run.
        """
        out: dict[str, Any] = {}
        for product in self._channel_surfaces(ir):
            db = f"{product}-db"
            out[db] = {
                "image": CHANNEL_SURFACE_DB_IMAGE,
                "environment": {
                    "POSTGRES_USER": "mmuser",
                    "POSTGRES_PASSWORD": "${CHANNEL_DB_PASSWORD}",
                    "POSTGRES_DB": product,
                },
                "networks": [ir.qualified("control")],
                "volumes": [f'{ir.qualified(f"{db}-data")}:/var/lib/postgresql/data'],
                "labels": {"org.agentic.tenant": ir.tenant.id if ir.tenant else ""},
            }
            out[product] = {
                "image": CHANNEL_SURFACE_IMAGES[product],
                "environment": {
                    "MM_SQLSETTINGS_DRIVERNAME": "postgres",
                    "MM_SQLSETTINGS_DATASOURCE": (
                        "postgres://mmuser:${CHANNEL_DB_PASSWORD}@"
                        f"{db}:5432/{product}?sslmode=disable"
                    ),
                    # Bots are minted through the API by the bridge (rule 2);
                    # without this the adapter's first call fails at bind time.
                    "MM_SERVICESETTINGS_ENABLEBOTACCOUNTCREATION": "true",
                },
                # Both networks: humans reach it from outside, and the channel
                # bridge reaches it from the control network. The agents do not
                # — they talk to the bridge, which is the only component
                # holding a bot token.
                "networks": [ir.qualified("control"), ir.qualified("egress")],
                "depends_on": [db],
                "volumes": [f'{ir.qualified(f"{product}-data")}:/mattermost/data'],
                "labels": {
                    "org.agentic.tenant": ir.tenant.id if ir.tenant else "",
                    "org.agentic.channel_surface": product,
                },
            }
        return out

    def _sandbox_services(self, ir: SystemIR) -> dict[str, Any]:
        """One build-only service per environment class in use (ADR-0053).

        These are not started by `up`: a sandbox is a thing code is executed
        *in*, on demand, not a long-running process. They are here so the image
        an environment class resolves to, and the network it is allowed, are
        visible and buildable in the same file as everything else rather than
        living only in the platform's head.
        """
        out: dict[str, Any] = {}
        for placement in self._placements(ir):
            env = self._environment_of(ir, placement.id)
            if env is None:
                continue
            posture = env.network.value
            service: dict[str, Any] = {
                "profiles": ["sandboxes"],
                # The image is still per environment class — that is what a
                # profile is — but the sandbox *environment* is per placement,
                # so HR and Finance sharing `analysis` build one image and get
                # two places (ADR-0069 rule 1).
                "build": {"context": ".", "dockerfile": f"docker/Dockerfile.{env.id}"},
                "image": f"{ir.name}/sandbox-{env.id}:{ir.spec_version}",
                "labels": {
                    "org.agentic.tenant": ir.tenant.id if ir.tenant else "",
                    "org.agentic.placement": placement.id,
                    "org.agentic.unit": placement.unit,
                    "org.agentic.environment": env.id,
                    "org.agentic.network_posture": posture,
                    "org.agentic.sandbox_image": self._sandbox_image(ir, env),
                    # Traffic between these is permitted, which is a widening,
                    # so the co-resident set is on the object (rule 7).
                    "org.agentic.co_resident": ",".join(placement.agents),
                },
            }
            # Code runs here, so it gets the same hardening as the agent,
            # with its workspace as the one writable place (ADR-0114).
            service.update(CONTAINER_HARDENING)
            service["tmpfs"] = ["/tmp", "/workspace:mode=1777"]
            if posture == "none":
                # No networks at all, not an internal one: a zero-network
                # sandbox that can still resolve its neighbours is not one.
                service["network_mode"] = "none"
            else:
                service["networks"] = [
                    ir.qualified("egress"),
                    self._placement_network(ir, placement.id),
                ]
            if placement.shares_a_volume:
                # The PROTECTED plane made concrete (rule 4). It carries what
                # the unit's groups already share and is never a new grant:
                # read-only here, and the permission resolver still decides
                # what an agent may open.
                service["volumes"] = [
                    f"{ir.qualified('place-' + placement.id)}:/srv/shared:ro"
                ]
            out[f"sandbox-{placement.id}"] = service
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
        subjects = self._subjects(ir)
        services: dict[str, Any] = {
            "state": {
                "image": STATE_IMAGE,
                "environment": {
                    "POSTGRES_PASSWORD": "${STATE_PASSWORD}",
                    "POSTGRES_DB": "orgagents",
                },
                "networks": [ir.qualified("control")],
                "volumes": [f'{ir.qualified("state-data")}:/var/lib/postgresql/data'],
                # Each infrastructure service says when it is ready, so
                # `up --wait` means ready and not merely started (ADR-0109).
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U postgres -d orgagents"],
                    "interval": "10s", "timeout": "3s", "retries": 5,
                },
            },
            # Agents export to it over `control`; its OTLP port is not
            # published, so nothing off this stack can write spans into it
            # (ADR-0114).
            "telemetry": {
                "image": OTEL_COLLECTOR_IMAGE,
                "networks": [ir.qualified("control")],
            },
            # The artifact workspace large tool output is offloaded to
            # (ADR-0036). This tenant's own instance on this tenant's own
            # volume; ADR-0053 rejects a shared bucket with a prefix per tenant.
            "artifacts": {
                "image": ARTIFACTS_IMAGE,
                # SeaweedFS's own flags. The MinIO-style `server /data
                # --console-address` this used to say is not a command SeaweedFS
                # knows, so the workspace exited on start (ADR-0109).
                "command": ["server", "-dir=/data", "-s3"],
                "environment": {
                    "MINIO_ROOT_USER": "${ARTIFACTS_USER}",
                    "MINIO_ROOT_PASSWORD": "${ARTIFACTS_PASSWORD}",
                },
                "networks": [ir.qualified("control")],
                "volumes": [f'{ir.qualified("artifacts-data")}:/data'],
                "healthcheck": {
                    "test": ["CMD", "wget", "-qO-", "http://127.0.0.1:9333/cluster/status"],
                    "interval": "10s", "timeout": "3s", "retries": 5,
                },
            },
            # The asynchronous transport between agent containers (ADR-0059).
            # JetStream is on so a channel that declares durability has
            # somewhere to persist; channels that do not declare it stay on
            # core NATS and pay nothing for it.
            "nats": {
                "image": BUS_IMAGE,
                # JetStream, the monitoring port (health check only, never
                # published) and one user per agent whose subject permissions
                # are the design's edges (ADR-0117). The broker holds every
                # agent's password; each agent holds only its own.
                "command": ["-c", "/etc/nats/nats.conf"],
                "environment": {
                    BUS_ADMIN_PASSWORD_REF: f"${{{BUS_ADMIN_PASSWORD_REF}}}",
                    **{bus_password_ref(a.id): f"${{{bus_password_ref(a.id)}}}"
                       for a in ir.agents},
                },
                "healthcheck": {
                    "test": ["CMD", "wget", "-qO-", "http://127.0.0.1:8222/healthz"],
                    "interval": "10s", "timeout": "3s", "retries": 5,
                },
                "networks": [ir.qualified("control")],
                "volumes": [f'{ir.qualified("bus-data")}:/data',
                            "./nats/nats.conf:/etc/nats/nats.conf:ro"],
                "labels": {
                    # Subjects are tenant-prefixed as well as per-instance, so
                    # two tenants wrongly pointed at one broker still would not
                    # collide (ADR-0059 rule 1).
                    "org.agentic.subject_prefix": subjects.prefix,
                    "org.agentic.tenant": ir.tenant.id if ir.tenant else "",
                },
            },
            # Creates the tenant's stream and one durable consumer per
            # agent, then exits. It holds the bus operator's password, which
            # no agent does; agents can pull from their own consumer and
            # nothing else (ADR-0117).
            "bus-init": {
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "image": f"{ir.name}/platform:{ir.spec_version}",
                "command": ["orgagents", "bus-init", "--ir", "/app/system.ir.json"],
                "environment": {
                    "ORGAGENTS_BUS_URL": "nats://nats:4222",
                    "ORGAGENTS_BUS_ADMIN_USER": BUS_ADMIN_USER,
                    BUS_ADMIN_PASSWORD_REF: f"${{{BUS_ADMIN_PASSWORD_REF}}}",
                },
                "networks": [ir.qualified("control")],
                "depends_on": {"nats": {"condition": "service_healthy"}},
                "restart": "no",
                "healthcheck": {"disable": True},
                **{k: v for k, v in CONTAINER_HARDENING.items()},
                "tmpfs": ["/tmp"],
            },
            "designer": {
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "image": f"{ir.name}/platform:{ir.spec_version}",
                "command": ["orgagents", "serve", "--host", "0.0.0.0"],
                # Single-user local, said out loud, and reachable from this
                # machine only (ADR-0114).
                "environment": {"ORGAGENTS_DESIGNER_AUTH": "none"},
                "ports": [f"{PUBLISH_HOST}:8000:8000"],
                "networks": [ir.qualified("control"), ir.qualified("ingress")],
                "depends_on": ["state"],
            },
        }
        for agent in ir.agents:
            services[f"agent-{agent.id}"] = self._agent_service(ir, agent)
        services.update(self._sandbox_services(ir))
        services.update(self._service_engine_services(ir))
        services.update(self._channel_surface_services(ir))

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
            # `internal` is the platform's own inbox: there is no workspace to
            # bridge to and no credential to hold, so a bridge would be a
            # container with nothing to do (ADR-0109).
            if channel.provider == "internal":
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
        services.update(self._server_services(ir))
        return yaml.safe_dump(
            {
                "name": ir.name.lower().replace(" ", "-"),
                "services": services,
                "networks": self._networks(ir),
                "volumes": {
                    ir.qualified("state-data"): {},
                    ir.qualified("artifacts-data"): {},
                    ir.qualified("bus-data"): {},
                    **{ir.qualified(f"agent-state-{a.id}"): {} for a in ir.agents},
                    **{
                        ir.qualified(f"{wb.engine}-data"): {}
                        for wb in self._service_engines(ir)
                    },
                    **{
                        vol: {}
                        for product in self._channel_surfaces(ir)
                        for vol in (
                            ir.qualified(f"{product}-data"),
                            ir.qualified(f"{product}-db-data"),
                        )
                    },
                    # One shared volume per placement that has somebody to
                    # share it with (ADR-0069 rule 4).
                    **{
                        ir.qualified(f"place-{p.id}"): {}
                        for p in self._placements(ir)
                        if p.shares_a_volume
                    },
                },
            },
            sort_keys=False,
            width=100,
        )

    # -- backing systems (ADR-0109) -----------------------------------------

    def _servers(self, ir: SystemIR) -> dict[str, list[Any]]:
        """Each bound server, with the capability bindings that land on it."""
        out: dict[str, list[Any]] = {}
        for cap in ir.capabilities:
            binding = ir.binding.capability_binding(cap.id)
            if binding is None or not binding.server_name:
                continue
            out.setdefault(binding.server_name, []).append(binding)
        return {k: out[k] for k in sorted(out)}

    def _server_network(self, ir: SystemIR, server: str) -> str:
        return ir.qualified(f"srv-{server}")

    def _agent_bindings(self, ir: SystemIR, agent) -> list[Any]:
        out = []
        for cap in agent.capabilities:
            binding = ir.binding.capability_binding(cap.id)
            if binding is not None and binding.server_name:
                out.append(binding)
        return out

    def _agent_servers(self, ir: SystemIR, agent) -> list[str]:
        """The servers an agent holds a capability on — and so may route to."""
        return sorted({b.server_name for b in self._agent_bindings(ir, agent)})

    def _agent_server_secrets(self, ir: SystemIR, agent) -> list[str]:
        return sorted({b.dsn_secret_ref for b in self._agent_bindings(ir, agent)
                       if b.dsn_secret_ref})

    def _server_services(self, ir: SystemIR) -> dict[str, Any]:
        """One service per backing system, on a network of its own (ADR-0109).

        These used to sit on `control`, which every agent joins, so every agent
        could route to every system and the placement networks bounded nothing
        that mattered: a buyer could reach the ledger. Now each server has an
        internal network that only the agents holding a capability bound to it
        join. Two sides of a separation bound to different servers are then
        two networks apart — the barrier the phase gate checked on paper
        (ADR-0071), as routing.

        One service per *server*, not per capability: before, a server with
        five capabilities was written five times and kept whichever came last,
        label and credential included.
        """
        out: dict[str, Any] = {}
        for server, bindings in self._servers(ir).items():
            first = bindings[0]
            secrets = sorted({b.dsn_secret_ref for b in bindings if b.dsn_secret_ref})
            image = next((b.options.get("image") for b in bindings
                          if b.options.get("image")), None)
            service: dict[str, Any] = {
                "image": image or f"{ir.name}/platform:{ir.spec_version}",
                "command": [first.command or "serve", *first.args],
                "environment": {ref: f"${{{ref}}}" for ref in secrets},
                "networks": [self._server_network(ir, server)],
                "labels": {
                    "org.agentic.tenant": ir.tenant.id if ir.tenant else "",
                    "org.agentic.server": server,
                    "org.agentic.capability": ",".join(
                        sorted(b.capability for b in bindings)),
                },
            }
            if not image:
                service = {"build": {"context": ".", "dockerfile": "Dockerfile"},
                           **service}
            out[f"mcp-{server}"] = service
        return out

    def _overlays_readme(self, ir: SystemIR) -> str:
        """How to extend *this* target. The mechanism differs per target, so a
        single 'put customizations in overlays/' line was never the whole
        truth (ADR-0092)."""
        return f"""# Your overlays — {ir.name} (local)

Everything beside this folder is generated and will be rewritten. **This folder
is yours**: the compiler creates it and then never reads or writes inside it.

## How it works here

Any `*.yaml` (or `*.yml`) you put here is merged over the generated stack by
Compose's own layering, in sorted order. The generated `Makefile` builds the
`-f` chain for you, so `make up` already includes your overlays.

```bash
make overlays     # which overlay files are in effect
make config       # the merged stack, overlays applied
make up           # start it, overlays and all
```

## Example — swap an image and add a volume

`overlays/10-local-dev.yaml`:

```yaml
services:
  designer:
    image: my-registry/designer:dev
    volumes:
      - ./src:/app/src:ro
```

Name files so they sort in the order you want them applied: `10-`, `20-`, and
so on. Later files win, which is Compose's rule, not ours.

## What this cannot do

An overlay changes the *deployment*, never the design. It cannot grant an agent
a permission, widen a sandbox or remove an approval — those are resolved once
from the spec into the IR, and an overlay is applied long after. If you need a
different permission, change the design and recompile; a control you can edit
away in a compose file was never a control.
"""

    def _makefile(self, ir: SystemIR) -> str:
        spec_file = f"{ir.name}.system.yaml"
        return f"""\
# {ir.name} — local development loop

.PHONY: up down logs seed ps single validate overlays

# Anything you drop in overlays/*.yaml is merged over the generated stack, in
# sorted order, by Compose's own layering (ADR-0092). Your files are yours: the
# compiler never writes or reads inside overlays/, so a regeneration cannot
# lose them. This is how you change an image, add a volume or bolt on a
# sidecar without editing a generated file.
COMPOSE_OVERLAYS := $(sort $(wildcard overlays/*.yaml) $(wildcard overlays/*.yml))
COMPOSE := docker compose -f docker-compose.yaml \\
\t$(foreach f,$(COMPOSE_OVERLAYS),-f $(f))

overlays:      ## show which overlay files are in effect
\t@echo "$(if $(COMPOSE_OVERLAYS),$(COMPOSE_OVERLAYS),none — drop a *.yaml in overlays/)"

up:            ## start the whole stack (generated + your overlays)
\t$(COMPOSE) up -d --remove-orphans

down:          ## stop and remove the stack
\t$(COMPOSE) down -v

logs:          ## follow agent logs
\t$(COMPOSE) logs -f $(filter-out $@,$(MAKECMDGOALS))

ps:            ## show running services
\t$(COMPOSE) ps

config:        ## show the merged stack, overlays applied
\t$(COMPOSE) config

seed:          ## load the compiled organization into the platform
\t$(COMPOSE) exec designer orgagents seed

single:        ## run everything in one process (no container runtime needed)
\tpython run_local.py

images:        ## list the images this system defines
	$(COMPOSE) config --images

validate:      ## re-validate the source spec
\torgagents spec validate ../../{ir.name}.system.yaml
"""

    def _env(self, ir: SystemIR) -> str:
        refs = sorted(
            {r for a in ir.agents for r in (a.identity.secret_refs if a.identity else [])}
            | {c.bot_identity_ref for c in ir.channels if c.bot_identity_ref}
            | {b.dsn_secret_ref for bs in self._servers(ir).values() for b in bs
               if b.dsn_secret_ref}
        )
        lines = [
            "# Secret NAMES only — never commit values (ADR-0015).",
            "# Populate from your secret manager before `make up`.",
            "STATE_PASSWORD=",
            "ARTIFACTS_USER=",
            "ARTIFACTS_PASSWORD=",
        ]
        if self._channel_surfaces(ir):
            lines.append("CHANNEL_DB_PASSWORD=")
        lines += [
            f"{self._engine_secret_ref(wb)}=" for wb in self._service_engines(ir)
        ]
        lines += [f"{ref}=" for ref in refs]
        # One service token per worker, and the approval issuer's public
        # keys for all of them (ADR-0114). The matching private key goes to
        # the issuer only, never into a worker's environment.
        # The bus (ADR-0117): the operator user that creates the stream, and
        # one broker password per agent.
        lines += ["# Bus operator (bus-init only) and one broker password per agent (ADR-0117).",
                  f"{BUS_ADMIN_PASSWORD_REF}="]
        lines += [f"{bus_password_ref(a.id)}=" for a in ir.agents]
        lines += ["# Approval issuer's public keys, kid:base64url[,kid:...] (ADR-0114).",
                  f"{APPROVAL_PUBLIC_KEYS_REF}=",
                  "# Per worker (ADR-0114): its service token."]
        for agent in ir.agents:
            lines += [f"{worker_token_ref(agent.id)}="]
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
            f"{', '.join(e.id for e in a.environments) or '—'} | "
            f"{', '.join(e.network.value for e in a.environments) or '—'} | "
            f"{len(a.permissions)} |"
            for a in ir.agents
        )
        placement_section = self._placement_section(ir)
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

## Placements

{placement_section}

## Tenant isolation

{tenant_section}

## Sandbox execution

{sandbox_section}

## Workflow engines

{engine_section}

## Caveats

A placement is **not** a security boundary. The tenant is. Borrowing the
namespace model means borrowing its caveat: namespaces on an application
platform share a kernel, and so do these.

Generated network policy is a snapshot. A reorganisation changes who may
delegate to whom immediately and these rules only at the next apply; for that
window the two disagree and nothing at run time says so.

Compose approximates network posture with attached networks and cannot
represent cloud IAM at all. Isolated (`none`) environments are placed on an
internal network with no gateway, which is close — but a local run does **not**
verify the IAM bindings the cloud targets generate. Use a cloud target to test
those.
"""
