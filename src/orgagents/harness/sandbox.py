"""Sandbox environment templates and the runner that instantiates them.

A sandbox template is the *governed* unit of code execution: administrators
publish templates, the designer UI offers them as a dropdown, and an agent may
only execute inside a template that has been published for it. Each template
fixes the image, toolchain, resource envelope, network posture, which data
planes may be mounted, and whether the filesystem survives the session.

Eight templates ship by default, covering the recurring shapes of enterprise
agent work. Add organization-specific ones with `SandboxRunner.publish`.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..models import SandboxSpec, SandboxTemplate, Visibility
from ..store import SANDBOX_TEMPLATES as TEMPLATE_COLLECTION
from ..store import Store

# --------------------------------------------------------------------------
# Built-in templates
# --------------------------------------------------------------------------

SANDBOX_TEMPLATES: list[SandboxTemplate] = [
    SandboxTemplate(
        id="sbx_minimal_reasoning",
        name="minimal-reasoning",
        description=(
            "No code execution. For agents that only reason, delegate and call "
            "MCP tools. Cheapest and safest default."
        ),
        base_image="scratch",
        toolchain=[],
        cpu="0.25",
        memory="256Mi",
        disk="1Gi",
        timeout_s=120,
        network="none",
        mounts=[Visibility.PRIVATE],
        filesystem="ephemeral",
    ),
    SandboxTemplate(
        id="sbx_data_analysis",
        name="data-analysis",
        description=(
            "Python data stack for analysts: dataframe work, statistics and "
            "chart rendering over rows pulled through the relational harness."
        ),
        base_image="python:3.11-slim",
        toolchain=["python3.11", "pip"],
        packages=["pandas", "numpy", "scipy", "matplotlib", "pyarrow", "duckdb"],
        cpu="2",
        memory="8Gi",
        disk="20Gi",
        timeout_s=900,
        network="egress_allowlist",
        egress_allowlist=["pypi.org", "files.pythonhosted.org"],
        mounts=[Visibility.PRIVATE, Visibility.PROTECTED, Visibility.PUBLIC],
        filesystem="session_persistent",
    ),
    SandboxTemplate(
        id="sbx_software_engineering",
        name="software-engineering",
        description=(
            "Full developer environment: git, language toolchains, package "
            "managers and test runners. Used by engineering agents that open PRs."
        ),
        base_image="ghcr.io/org/agent-dev:3.11-node20",
        toolchain=["git", "python3.11", "node20", "go1.22", "make", "docker-cli"],
        packages=["uv", "pytest", "ruff", "pnpm"],
        cpu="4",
        memory="16Gi",
        disk="50Gi",
        timeout_s=3600,
        network="egress_allowlist",
        egress_allowlist=[
            "github.com",
            "api.github.com",
            "pypi.org",
            "registry.npmjs.org",
            "proxy.golang.org",
        ],
        mounts=[Visibility.PRIVATE, Visibility.PROTECTED],
        secret_refs=["GITHUB_APP_TOKEN"],
        filesystem="agent_persistent",
    ),
    SandboxTemplate(
        id="sbx_browser_automation",
        name="browser-automation",
        description=(
            "Headless Chromium plus Playwright for agents that operate web "
            "applications, scrape approved sources or capture screenshots."
        ),
        base_image="mcr.microsoft.com/playwright/python:v1.47-jammy",
        toolchain=["python3.11", "chromium", "playwright"],
        packages=["playwright", "beautifulsoup4"],
        cpu="2",
        memory="4Gi",
        disk="20Gi",
        timeout_s=600,
        network="egress_allowlist",
        egress_allowlist=["*.corp.internal"],
        mounts=[Visibility.PRIVATE],
        filesystem="ephemeral",
    ),
    SandboxTemplate(
        id="sbx_document_processing",
        name="document-processing",
        description=(
            "Office and PDF toolchain for agents that read contracts, produce "
            "reports or fill templates. No network by default."
        ),
        base_image="python:3.11-slim",
        toolchain=["python3.11", "libreoffice", "poppler-utils", "tesseract"],
        packages=["python-docx", "openpyxl", "python-pptx", "pypdf", "pdfplumber"],
        cpu="2",
        memory="4Gi",
        disk="20Gi",
        timeout_s=900,
        network="none",
        mounts=[Visibility.PRIVATE, Visibility.PROTECTED],
        filesystem="session_persistent",
    ),
    SandboxTemplate(
        id="sbx_ml_training",
        name="ml-training",
        description=(
            "GPU environment for model fine-tuning and heavy inference. "
            "Requires capacity approval; budgeted separately."
        ),
        base_image="nvcr.io/nvidia/pytorch:24.07-py3",
        toolchain=["python3.11", "cuda12.4"],
        packages=["torch", "transformers", "datasets", "accelerate"],
        cpu="8",
        memory="64Gi",
        disk="200Gi",
        gpu="1xA100",
        timeout_s=21600,
        network="egress_allowlist",
        egress_allowlist=["huggingface.co", "cdn-lfs.huggingface.co"],
        mounts=[Visibility.PRIVATE, Visibility.PROTECTED],
        filesystem="agent_persistent",
    ),
    SandboxTemplate(
        id="sbx_regulated_data",
        name="regulated-data-clean-room",
        description=(
            "Air-gapped clean room for PII/PCI/PHI work: zero egress, audited "
            "mounts, ephemeral disk wiped on exit, no secrets beyond the scoped "
            "database connection."
        ),
        base_image="python:3.11-slim",
        toolchain=["python3.11"],
        packages=["pandas", "numpy"],
        cpu="2",
        memory="8Gi",
        disk="10Gi",
        timeout_s=1800,
        network="none",
        mounts=[Visibility.PRIVATE],
        env={"PYTHONHASHSEED": "0", "AUDIT_MODE": "strict"},
        filesystem="ephemeral",
    ),
    SandboxTemplate(
        id="sbx_integration_runner",
        name="integration-runner",
        description=(
            "Network-facing runner for agents that call internal APIs and "
            "enterprise SaaS through the egress proxy. Ships HTTP tooling only."
        ),
        base_image="python:3.11-slim",
        toolchain=["python3.11", "curl", "jq"],
        packages=["httpx", "tenacity"],
        cpu="1",
        memory="2Gi",
        disk="10Gi",
        timeout_s=600,
        network="internal",
        egress_allowlist=["*.corp.internal", "api.company.com"],
        mounts=[Visibility.PRIVATE, Visibility.PROTECTED],
        secret_refs=["INTERNAL_API_TOKEN"],
        filesystem="ephemeral",
    ),
]


def template_by_name(name: str) -> Optional[SandboxTemplate]:
    return next((t for t in SANDBOX_TEMPLATES if t.name == name), None)


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


class SandboxRunner:
    """Materializes a template into a running workspace.

    Two backends: ``local`` (subprocess in a temp workspace, for development
    and tests) and ``container`` (renders the equivalent container run spec for
    the platform's orchestrator). The container backend is intentionally a spec
    producer — the platform, not the agent process, launches workloads.
    """

    def __init__(self, store: Optional[Store] = None, backend: str = "local") -> None:
        self.store = store
        self.backend = backend
        if store is not None and store.count(TEMPLATE_COLLECTION) == 0:
            store.put_many(TEMPLATE_COLLECTION, SANDBOX_TEMPLATES)

    # -- template management ----------------------------------------------

    def templates(self) -> list[SandboxTemplate]:
        if self.store is None:
            return list(SANDBOX_TEMPLATES)
        stored = self.store.list(TEMPLATE_COLLECTION, SandboxTemplate, limit=1000)
        return stored or list(SANDBOX_TEMPLATES)

    def template(self, template_id: str) -> Optional[SandboxTemplate]:
        return next((t for t in self.templates() if t.id == template_id), None)

    def publish(self, template: SandboxTemplate) -> SandboxTemplate:
        if self.store is None:
            SANDBOX_TEMPLATES.append(template)
        else:
            self.store.put(TEMPLATE_COLLECTION, template)
        return template

    # -- resolution --------------------------------------------------------

    def resolve(self, spec: SandboxSpec) -> SandboxTemplate:
        template = self.template(spec.template_id)
        if template is None:
            raise KeyError(f"unknown sandbox template '{spec.template_id}'")
        resolved = template.model_copy(deep=True)
        resolved.env.update(spec.env)
        # Overrides may only narrow the timeout, never extend it.
        if spec.timeout_s is not None:
            resolved.timeout_s = min(resolved.timeout_s, spec.timeout_s)
        # Extra egress is only honored when the template already allows egress.
        if spec.egress_extra and resolved.network in ("egress_allowlist", "internal"):
            resolved.egress_allowlist = sorted(
                set(resolved.egress_allowlist) | set(spec.egress_extra)
            )
        return resolved

    def container_spec(self, spec: SandboxSpec, workspace: str = "/workspace") -> dict[str, Any]:
        """Render the orchestrator-facing run spec for a sandbox."""
        t = self.resolve(spec)
        return {
            "image": t.base_image,
            "resources": {
                "cpu": t.cpu,
                "memory": t.memory,
                "ephemeral-storage": t.disk,
                **({"nvidia.com/gpu": t.gpu} if t.gpu else {}),
            },
            "network_policy": {
                "mode": t.network,
                "allowlist": t.egress_allowlist,
            },
            "env": t.env,
            "secrets": t.secret_refs,
            "mounts": [
                {"plane": m.value, "path": f"{workspace}/data/{m.value}"} for m in t.mounts
            ],
            "workspace": workspace,
            "volume": {
                "ephemeral": {"emptyDir": {}},
                "session_persistent": {"claim": "session"},
                "agent_persistent": {"claim": "agent"},
            }[t.filesystem],
            "timeout_s": t.timeout_s,
            "security": {
                "privileged": t.privileged,
                "read_only_root": not t.privileged,
                "run_as_non_root": True,
                "drop_capabilities": ["ALL"],
            },
        }

    # -- execution ---------------------------------------------------------

    def run(
        self,
        spec: SandboxSpec,
        command: str,
        *,
        files: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Execute a command in the sandbox and capture the result."""
        t = self.resolve(spec)
        if self.backend != "local":
            return {
                "backend": self.backend,
                "submitted": True,
                "spec": self.container_spec(spec),
                "command": command,
            }
        with tempfile.TemporaryDirectory(prefix="sbx-") as workdir:
            for rel, content in (files or {}).items():
                path = Path(workdir) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            env = {"PATH": os.environ.get("PATH", ""), "HOME": workdir, **t.env}
            try:
                proc = subprocess.run(
                    shlex.split(command),
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=t.timeout_s,
                )
                return {
                    "backend": "local",
                    "template": t.name,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[-64_000:],
                    "stderr": proc.stderr[-16_000:],
                    "timed_out": False,
                }
            except subprocess.TimeoutExpired:
                return {
                    "backend": "local",
                    "template": t.name,
                    "exit_code": 124,
                    "stdout": "",
                    "stderr": f"timed out after {t.timeout_s}s",
                    "timed_out": True,
                }
