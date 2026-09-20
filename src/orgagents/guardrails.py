"""Boundary checks on what enters and leaves an agent (ADR-0035).

Permissions decide what an agent may *reach*. Guardrails decide what may
*pass*. They answer different questions, and a system that only answers the
first will happily let a correctly-permissioned agent paste a customer's
identifiers into a chat channel.

Every check is named and declared — no regex soup buried in a prompt — and
every trip produces a record naming the guardrail, the check and what it found,
so a refusal is explainable to the person who hit it.

*How* a check forms its verdict is pluggable (ADR-0045): the deterministic
pattern classifier is the default, and a deployment can supply a model-backed
one. The patterns themselves live in `classifiers` alongside the protocol.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .classifiers import (  # re-exported: this is where callers look for them
    INJECTION_PHRASES,
    PATTERNS,
    PII_CHECKS,
    SECRET_CHECKS,
    Classification,
    Classifier,
    PatternClassifier,
)
from .spec.model import (
    Guardrail,
    GuardrailAction,
    GuardrailCheck,
    GuardrailKind,
)

DEFAULT_CLASSIFIER = PatternClassifier()


@dataclass
class Violation:
    guardrail: str
    check: GuardrailCheck
    action: GuardrailAction
    detail: str
    matches: list[str] = field(default_factory=list)
    # Which classifier decided this, and whether it was working (ADR-0045).
    source: str = "pattern"
    degraded: bool = False

    def describe(self) -> str:
        return f"{self.guardrail}/{self.check.value}: {self.detail}"


@dataclass
class GuardrailResult:
    """What the boundary decided about one piece of content."""

    allowed: bool
    content: Any
    violations: list[Violation] = field(default_factory=list)
    redacted: bool = False
    escalate_to: Optional[str] = None

    @property
    def blocked(self) -> bool:
        return not self.allowed

    def reason(self) -> str:
        return "; ".join(v.describe() for v in self.violations) or "no violation"


class GuardrailEngine:
    """Runs the declared guardrails over content crossing a boundary."""

    def __init__(self, guardrails: list[Guardrail],
                 classifier: Optional[Classifier] = None) -> None:
        self.guardrails = [g for g in guardrails if g.enabled]
        self.classifier = classifier or DEFAULT_CLASSIFIER

    def for_kind(self, kind: GuardrailKind) -> list[Guardrail]:
        return [g for g in self.guardrails if kind in g.applies_to]

    # -- checks ------------------------------------------------------------

    def _run_check(self, guardrail: Guardrail, check: GuardrailCheck, text: str,
                   context: dict[str, Any]) -> Classification:
        """Form a verdict on one check, through the configured classifier."""
        return self.classifier.classify(guardrail, check, text, context)

    # -- evaluation --------------------------------------------------------

    def check(self, content: Any, kind: GuardrailKind, *,
              context: Optional[dict[str, Any]] = None,
              extra: Optional[list[Guardrail]] = None) -> GuardrailResult:
        """Evaluate every guardrail for this boundary against the content."""
        context = context or {}
        text = content if isinstance(content, str) else str(content)
        working = text
        violations: list[Violation] = []
        blocked = False
        redacted = False
        escalate_to: Optional[str] = None

        for guardrail in [*self.for_kind(kind), *(extra or [])]:
            for check in guardrail.checks:
                verdict = self._run_check(guardrail, check, working, context)
                if not verdict.matches:
                    continue
                candidate = verdict.redacted or working
                violation = Violation(
                    guardrail.id, check, guardrail.on_violation, verdict.detail,
                    [str(m) for m in verdict.matches], source=verdict.source,
                    degraded=verdict.degraded)
                violations.append(violation)
                if guardrail.on_violation is GuardrailAction.BLOCK:
                    blocked = True
                elif guardrail.on_violation is GuardrailAction.REDACT:
                    working = candidate
                    redacted = True
                elif guardrail.on_violation is GuardrailAction.ESCALATE:
                    blocked = True
                    escalate_to = guardrail.escalate_channel or escalate_to

        return GuardrailResult(
            allowed=not blocked,
            content=working if isinstance(content, str) else content,
            violations=violations,
            redacted=redacted,
            escalate_to=escalate_to,
        )


# --------------------------------------------------------------------------
# Output contracts (ADR-0037)
# --------------------------------------------------------------------------


def validate_shape(value: Any, schema: dict[str, Any], path: str = "") -> list[str]:
    """Check a value against a small JSON-Schema subset.

    Supports `type`, `properties`, `required`, `items` and `enum` — enough to
    make "returns a list of findings with a source" checkable, and small enough
    to read.
    """
    errors: list[str] = []
    expected = schema.get("type")
    where = path or "value"

    if expected == "object":
        if not isinstance(value, dict):
            return [f"{where}: expected an object, got {type(value).__name__}"]
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}.{key}: required field is missing")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                errors += validate_shape(value[key], sub, f"{where}.{key}")
    elif expected == "array":
        if not isinstance(value, list):
            return [f"{where}: expected an array, got {type(value).__name__}"]
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                errors += validate_shape(item, item_schema, f"{where}[{index}]")
    elif expected in ("string", "number", "integer", "boolean"):
        python_type = {"string": str, "number": (int, float), "integer": int,
                       "boolean": bool}[expected]
        if not isinstance(value, python_type):
            errors.append(
                f"{where}: expected {expected}, got {type(value).__name__}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']}")
    return errors
