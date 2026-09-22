"""Generation engine: run phase 1, dispatch to targets, own the output (ADR-0014).

Generated output is compiler-owned. The engine writes a `manifest.json` of every
file it produced with a content hash, so a later compile can tell a stale file
from one a human edited — and refuses to clobber the latter without `--force`.
Anything under `overlays/` is user-owned and never touched.
"""
from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ..spec.binding import Binding, TargetBinding, default_binding
from ..spec.model import SystemSpec
from ..spec.validate import Finding, validate_spec
from .base import GeneratedFile, register_builtin_targets
from .ir import SystemIR, TenantIR, apply_model_approvals, build_ir
from .tenancy import assert_artifacts_qualified, assert_within_tenant

MANIFEST = "manifest.json"
OVERLAY_DIR = "overlays"


class CompileError(RuntimeError):
    pass


@dataclass
class CompileResult:
    target: str
    out_dir: Path
    ir: SystemIR
    files: list[GeneratedFile] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: Engineer-owned files the compiler added new stubs to (path -> names added).
    merged: dict[str, list[str]] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def summary(self) -> str:
        added = sum(len(v) for v in self.merged.values())
        merged = f", {added} stub(s) merged into {len(self.merged)} file(s)" \
            if self.merged else ""
        return (
            f"{self.target}: {len(self.written)} files written, "
            f"{len(self.skipped)} preserved, {len(self.findings)} findings"
            f"{merged} → {self.out_dir}"
        )


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _top_level_defs(source: str) -> dict[str, str]:
    """Map each top-level function name to its source text.

    Best-effort: if the file does not parse (an engineer mid-edit), returns
    nothing, so the merge appends nothing rather than corrupting their work.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segment = ast.get_source_segment(source, node)
            if segment:
                out[node.name] = segment
    return out


def _merge_additive(existing: str, generated: str) -> tuple[str, list[str]]:
    """Append only the generated top-level functions the file is missing.

    An engineer's implemented (or half-implemented) function is never rewritten
    or removed; a function the design newly requires is appended as a stub. If
    the existing file does not parse, nothing is appended (ADR-0089).
    """
    have = _top_level_defs(existing)
    if not have and existing.strip():
        return existing, []          # unparseable; leave it untouched
    incoming = _top_level_defs(generated)
    added = [name for name in incoming if name not in have]
    if not added:
        return existing, []
    banner = ("\n\n"
              "# --- Added by regeneration: new tools to implement. Existing\n"
              "# --- functions above were left untouched (ADR-0089).\n")
    blocks = "\n\n\n".join(incoming[name] for name in added)
    return existing.rstrip() + "\n" + banner + "\n" + blocks + "\n", added


def _load_manifest(out_dir: Path) -> dict[str, str]:
    path = out_dir / MANIFEST
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text()).get("files", {})
    except json.JSONDecodeError:
        return {}


def compile_system(
    spec: SystemSpec,
    *,
    targets: Optional[list[str]] = None,
    out_dir: str | Path = "build",
    binding: Optional[Binding] = None,
    force: bool = False,
    write: bool = True,
    catalog: Optional[Any] = None,
    tenant: Optional[TenantIR] = None,
    foreign_prefixes: Optional[Iterable[str]] = None,
    platform_policy: Optional[Any] = None,
) -> list[CompileResult]:
    """Validate, resolve to IR, then generate artifacts for each target.

    With a `tenant`, the compile is tenant-scoped (ADR-0050): the spec is first
    refused if it reaches outside the tenant, and the generated artifacts are
    refused if any of their names is not the tenant's.
    """
    # A policy that has not been approved, or whose approval has lapsed, may
    # be evaluated and may not decide whether something is built (ADR-0077).
    if platform_policy is not None:
        blocked = platform_policy.refusal()
        if blocked:
            raise CompileError(blocked)

    # The fabric's rules are applied here, not after: a design that fails the
    # house policy produces no artifacts at all (ADR-0076).
    findings = validate_spec(spec, platform_policy=platform_policy)
    blocking = [f for f in findings if f.severity == "error"]
    if blocking:
        where = (
            f" under platform policy '{platform_policy.stamp}'"
            if platform_policy else ""
        )
        raise CompileError(
            f"spec validation failed{where}:\n  "
            + "\n  ".join(str(f) for f in blocking)
        )

    if tenant is not None:
        assert_within_tenant(
            spec.model_dump(mode="json"), tenant, foreign_prefixes or ()
        )

    registry = register_builtin_targets()
    target_ids = targets or spec.deployment.targets or ["local"]
    results: list[CompileResult] = []

    for target_id in target_ids:
        target = registry.get(target_id)
        if target is None:
            raise CompileError(
                f"unknown target '{target_id}'; available: {', '.join(registry.ids())}"
            )
        bound: TargetBinding = (
            binding.for_target(target_id) if binding else None
        ) or default_binding(target_id)
        ir = build_ir(spec, target=target_id, binding=bound, tenant=tenant,
                      platform_policy=platform_policy)
        if catalog is not None:
            # A bound model outside the agent's policy stops the build: an
            # unapproved model is not a warning (ADR-0040).
            ir = apply_model_approvals(ir, catalog)
            refused = [
                f"{a.id}: {a.model_approval.approval_reason}"
                + (f"; try one of: {', '.join(a.model_approval.alternatives)}"
                   if a.model_approval.alternatives else "")
                for a in ir.agents
                if a.model_approval and not a.model_approval.approved
            ]
            # A sub-agent's model is refused the same way the agent's own is.
            refused += [
                f"{a.id} (sub-agents): {a.model_approval.subagent_approval_reason}"
                + (f"; try one of: "
                   f"{', '.join(a.model_approval.subagent_alternatives)}"
                   if a.model_approval.subagent_alternatives else "")
                for a in ir.agents
                if a.model_approval and not a.model_approval.subagent_approved
            ]
            if refused:
                raise CompileError(
                    "the bound model is not permitted for:\n  " + "\n  ".join(refused)
                )
        files = target.generate(ir)
        if tenant is not None:
            # Refused, not emitted: an unqualified artifact under a tenant's
            # name is worse than no artifact at all.
            assert_artifacts_qualified(ir, files)
        # Two tenants never share an output directory either: the generated
        # tree is the first place a collision would show up.
        base = Path(out_dir) / tenant.namespace_prefix if tenant else Path(out_dir)
        target_dir = base / target_id.replace(":", "-")
        result = CompileResult(target_id, target_dir, ir, files, findings=findings)
        if write:
            _write(result, force=force)
        results.append(result)
    return results


def _write(result: CompileResult, *, force: bool) -> None:
    out_dir = result.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    previous = _load_manifest(out_dir)
    manifest: dict[str, str] = {}

    for gf in result.files:
        path = out_dir / gf.path
        digest = _digest(gf.content)
        manifest[gf.path] = digest

        if path.exists():
            if gf.preserve_if_exists:
                result.skipped.append(gf.path)
                continue
            if gf.merge_additive:
                # The engineer owns this file; only append defs it is missing,
                # never rewrite one they may have implemented (ADR-0089).
                existing = path.read_text()
                merged, added = _merge_additive(existing, gf.content)
                manifest[gf.path] = _digest(merged)
                if added:
                    path.write_text(merged)
                    result.merged[gf.path] = added
                else:
                    result.skipped.append(gf.path)
                continue
            current = _digest(path.read_text())
            was_generated = previous.get(gf.path)
            if was_generated and current != was_generated and not force:
                raise CompileError(
                    f"{path} was modified since it was generated; move the change "
                    f"into {OVERLAY_DIR}/ or re-run with --force to discard it"
                )
            if current == digest:
                result.written.append(gf.path)
                continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(gf.content)
        if gf.executable:
            path.chmod(0o755)
        result.written.append(gf.path)

    (out_dir / OVERLAY_DIR).mkdir(exist_ok=True)
    (out_dir / MANIFEST).write_text(
        json.dumps(
            {
                "system": result.ir.name,
                "target": result.target,
                "spec_version": result.ir.spec_version,
                "ir_version": result.ir.ir_version,
                "files": manifest,
            },
            indent=2,
        )
        + "\n"
    )
