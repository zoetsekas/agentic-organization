"""Enforcement of the tenant boundary at compile time (ADR-0050).

Two jobs, both refusals rather than warnings:

* **Before generation** — a spec that names anything belonging to another
  tenant is rejected. Cross-tenant reach is denied absolutely, not narrowed to
  a safer scope, because there is no safe scope across the boundary.
* **After generation** — a build that was asked for a tenant and produced an
  artifact whose name is not tenant-qualified is refused instead of emitted.
  An unqualified artifact is a collision waiting for the second tenant.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

import yaml

from .ir import SystemIR, TenantIR

# Terraform attributes whose value becomes a real object name in the account.
# Anchored at a resource's own indentation: a `name` nested inside a block is an
# environment variable or a label, not an object somebody else could collide
# with.
_TF_NAME_ATTRS = re.compile(
    r'^  (name|account_id|secret_id|bucket)\s*=\s*"([^"]*)"', re.M
)
# A reference rather than a literal: already qualified by whatever it resolves to.
_TF_INTERPOLATION = re.compile(r"^\$\{")
_TENANT_REFERENCE = re.compile(r"\btenants?[:/]([a-z0-9][a-z0-9-]*)")


class TenantIsolationError(RuntimeError):
    """A compile that would have crossed, or failed to draw, a tenant boundary."""

    def __init__(self, message: str, violations: Optional[list[str]] = None) -> None:
        super().__init__(message)
        self.violations = violations or []


# -- before generation -----------------------------------------------------


def _walk(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def cross_tenant_references(
    document: Any, tenant: TenantIR, foreign_prefixes: Iterable[str]
) -> list[str]:
    """Every place `document` reaches at something outside `tenant`."""
    foreign = {p for p in foreign_prefixes if p and p != tenant.namespace_prefix}
    violations: list[str] = []
    for path, text in _walk(document):
        for prefix in sorted(foreign):
            if text == prefix or re.search(rf"(^|[^a-z0-9-]){prefix}-", text):
                violations.append(
                    f"{path or '<root>'}: '{text}' names tenant '{prefix}'"
                )
                break
        else:
            match = _TENANT_REFERENCE.search(text)
            if match and match.group(1) not in (tenant.id, tenant.namespace_prefix):
                violations.append(
                    f"{path or '<root>'}: '{text}' refers to tenant "
                    f"'{match.group(1)}'"
                )
    return violations


def assert_within_tenant(
    document: Any, tenant: TenantIR, foreign_prefixes: Iterable[str]
) -> None:
    violations = cross_tenant_references(document, tenant, foreign_prefixes)
    if violations:
        raise TenantIsolationError(
            f"the spec reaches outside tenant '{tenant.id}':\n  "
            + "\n  ".join(violations),
            violations,
        )


# -- after generation ------------------------------------------------------


def _compose_violations(content: str, tenant: TenantIR) -> list[str]:
    try:
        doc = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:  # a compose file we cannot read is not a pass
        return [f"docker-compose.yaml is unreadable: {exc}"]
    bad: list[str] = []
    project = doc.get("name", "")
    if not tenant.owns(project):
        bad.append(f"compose project '{project}' is not tenant-qualified")
    for kind in ("networks", "volumes"):
        for name in (doc.get(kind) or {}):
            if not tenant.owns(str(name)):
                bad.append(f"compose {kind[:-1]} '{name}' is not tenant-qualified")
    for name, service in (doc.get("services") or {}).items():
        for mount in service.get("volumes", []) or []:
            source = str(mount).split(":")[0]
            # Bind mounts are relative to the generated directory, which is
            # already per-tenant; only named volumes cross service boundaries.
            if not source.startswith(".") and not source.startswith("/"):
                if not tenant.owns(source):
                    bad.append(
                        f"service '{name}' mounts unqualified volume '{source}'"
                    )
    return bad


def _terraform_violations(content: str, tenant: TenantIR) -> list[str]:
    bad = []
    for attr, value in _TF_NAME_ATTRS.findall(content):
        if not value or _TF_INTERPOLATION.match(value):
            continue
        if not tenant.owns(value):
            bad.append(f"{attr} = \"{value}\" is not tenant-qualified")
    return bad


def artifact_violations(ir: SystemIR, files: Iterable[Any]) -> list[str]:
    """Names in the IR and in the generated files that are not the tenant's."""
    tenant = ir.tenant
    if tenant is None:
        return []
    bad: list[str] = []
    if not tenant.owns(ir.name):
        bad.append(f"system name '{ir.name}' is not tenant-qualified")
    for identity in ir.identities:
        if not tenant.owns(identity.id):
            bad.append(f"identity '{identity.id}' is not tenant-qualified")
        bad += [
            f"secret ref '{ref}' is not tenant-qualified"
            for ref in identity.secret_refs
            if not tenant.owns(ref)
        ]
    for resource in ir.resources:
        if not tenant.owns(resource.id):
            bad.append(
                f"{resource.kind} '{resource.id}' is not tenant-qualified"
            )
    for gf in files:
        path = getattr(gf, "path", "")
        content = getattr(gf, "content", "")
        if path.endswith(("docker-compose.yaml", "docker-compose.yml")):
            bad += [f"{path}: {v}" for v in _compose_violations(content, tenant)]
        elif path.endswith(".tf"):
            bad += [f"{path}: {v}" for v in _terraform_violations(content, tenant)]
        elif path.endswith("MAPPING.md") and "## Tenant boundary" not in content:
            bad.append(
                f"{path}: no tenant boundary section; a target must name what "
                f"enforces the boundary (ADR-0050)"
            )
    return bad


def assert_artifacts_qualified(ir: SystemIR, files: Iterable[Any]) -> None:
    violations = artifact_violations(ir, files)
    if violations:
        raise TenantIsolationError(
            f"refusing to emit artifacts for tenant '{ir.tenant.id}': "
            f"{len(violations)} name(s) are not tenant-qualified:\n  "
            + "\n  ".join(violations),
            violations,
        )
