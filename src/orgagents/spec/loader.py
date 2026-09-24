"""Load, dump and version-check System Spec and Binding documents."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .binding import Binding
from .exchange import strip_types
from .migrations import CURRENT, SpecVersionError, declared_version, migrate
from .model import SystemSpec

__all__ = [
    "SpecVersionError",
    "load_spec",
    "load_spec_text",
    "load_spec_with_migration",
    "load_spec_text_with_migration",
    "load_binding",
    "dump_spec",
]


def _parse(text: str) -> dict[str, Any]:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("a system spec must be a mapping")
    return data


def load_spec_text_with_migration(text: str) -> tuple[SystemSpec, list[str]]:
    """Load a spec, upgrading an older document, and say what that cost.

    The change list is empty when the document was already current, so callers
    can distinguish "read as written" from "read after migration" without a
    second version comparison of their own. A document newer than this
    compiler raises instead: dropping the fields we do not know about would
    quietly downgrade someone's design.
    """
    # A typed document (ADR-0113) has its types checked against their
    # positions and removed; an untyped one passes through unchanged.
    data = strip_types(_parse(text), SystemSpec)
    declared = declared_version(data)
    changes: list[str] = []
    if declared != CURRENT:
        data, changes = migrate(data, to=CURRENT)
    return SystemSpec.model_validate(data), changes


def load_spec_with_migration(path: str | Path) -> tuple[SystemSpec, list[str]]:
    return load_spec_text_with_migration(Path(path).read_text(encoding="utf-8"))


def load_spec_text(text: str) -> SystemSpec:
    return load_spec_text_with_migration(text)[0]


def load_spec(path: str | Path) -> SystemSpec:
    return load_spec_text(Path(path).read_text(encoding="utf-8"))


def load_binding(path: str | Path) -> Binding:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Binding.model_validate(strip_types(data, Binding))


def dump_spec(spec: SystemSpec) -> str:
    """Serialize a spec back to YAML, stable enough to round-trip and diff."""
    data = spec.model_dump(mode="json", exclude_defaults=True)
    # `spec_version` equals the default whenever a document is current, so
    # excluding defaults would drop the one field a migration exists to set.
    data.setdefault("metadata", {})["spec_version"] = spec.metadata.spec_version
    return yaml.safe_dump(data, sort_keys=False, width=100, allow_unicode=True)
