"""Spec validation — kept importable here; the rules live in `validation/`.

`orgagents.spec.validation` is the rule registry and one module per validator
section. This module re-exports what callers have always imported from
`orgagents.spec.validate`, so no import had to change.
"""
from __future__ import annotations

from .validation import (
    Finding,
    Severity,
    directory_findings,
    errors,
    matches,
    team_permissions,
    validate_spec,
    workflow_binding_findings,
)

__all__ = [
    "Finding",
    "Severity",
    "directory_findings",
    "errors",
    "matches",
    "team_permissions",
    "validate_spec",
    "workflow_binding_findings",
]
