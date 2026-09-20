"""Generation engine: run phase 1, dispatch to targets, own the output (ADR-0014).

Generated output is compiler-owned. The engine writes a `manifest.json` of every
file it produced with a content hash, so a later compile can tell a stale file
from one a human edited — and refuses to clobber the latter without `--force`.
Anything under `overlays/` is user-owned and never touched.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..spec.binding import Binding, TargetBinding, default_binding
from ..spec.model import SystemSpec
from ..spec.validate import Finding, validate_spec
from .base import GeneratedFile, register_builtin_targets
from .ir import SystemIR, build_ir

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
    findings: list[Finding] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.target}: {len(self.written)} files written, "
            f"{len(self.skipped)} preserved, {len(self.findings)} findings "
            f"→ {self.out_dir}"
        )


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


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
) -> list[CompileResult]:
    """Validate, resolve to IR, then generate artifacts for each target."""
    findings = validate_spec(spec)
    blocking = [f for f in findings if f.severity == "error"]
    if blocking:
        raise CompileError(
            "spec validation failed:\n  " + "\n  ".join(str(f) for f in blocking)
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
        ir = build_ir(spec, target=target_id, binding=bound)
        files = target.generate(ir)
        target_dir = Path(out_dir) / target_id.replace(":", "-")
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
