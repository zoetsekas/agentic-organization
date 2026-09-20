"""Load, dump and version-check System Spec and Binding documents."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .binding import Binding
from .model import SPEC_VERSION, SystemSpec


class SpecVersionError(ValueError):
    """Raised when a document's `spec_version` is not supported."""


def _check_version(data: dict[str, Any]) -> None:
    declared = str((data.get("metadata") or {}).get("spec_version", SPEC_VERSION))
    major = declared.split(".")[0]
    if major != SPEC_VERSION.split(".")[0]:
        raise SpecVersionError(
            f"spec_version {declared} is not supported by this compiler "
            f"(expects {SPEC_VERSION.split('.')[0]}.x); run `orgagents spec migrate`"
        )


def load_spec_text(text: str) -> SystemSpec:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("a system spec must be a mapping")
    _check_version(data)
    return SystemSpec.model_validate(data)


def load_spec(path: str | Path) -> SystemSpec:
    return load_spec_text(Path(path).read_text())


def load_binding(path: str | Path) -> Binding:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return Binding.model_validate(data)


def dump_spec(spec: SystemSpec) -> str:
    """Serialize a spec back to YAML, stable enough to round-trip and diff."""
    data = spec.model_dump(mode="json", exclude_defaults=True)
    return yaml.safe_dump(data, sort_keys=False, width=100, allow_unicode=True)
