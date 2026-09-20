"""Boundary checks on what enters and leaves an agent (ADR-0035).

Permissions decide what an agent may *reach*. Guardrails decide what may
*pass*. They answer different questions, and a system that only answers the
first will happily let a correctly-permissioned agent paste a customer's
identifiers into a chat channel.

Every check is named and declared — no regex soup buried in a prompt — and
every trip produces a record naming the guardrail, the check and what it found,
so a refusal is explainable to the person who hit it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from .spec.model import (
    Guardrail,
    GuardrailAction,
    GuardrailCheck,
    GuardrailKind,
)

# Patterns are deliberately conservative: a guardrail that cries wolf is one
# people switch off. Each is documented by what it is *meant* to catch.
PATTERNS: dict[str, re.Pattern[str]] = {
    # Credentials and keys.
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer": re.compile(r"\b(?:bearer|token|api[_-]?key)\s*[:=]\s*\S{12,}", re.I),
    "dsn": re.compile(r"\b\w+://[^\s:@/]+:[^\s@/]+@\S+"),
    # Identifying data.
    "email": re.compile(r"\b[\w.%-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    "card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    "phone": re.compile(r"\+\d{1,3}[\s-]?\(?\d{2,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}\b"),
}
SECRET_CHECKS = ("aws_key", "private_key", "bearer", "dsn")
PII_CHECKS = ("email", "card", "ssn", "iban", "phone")

# Phrases that appear when text is trying to steer the agent rather than inform
# it. Matching one is a signal to treat the content as data, not a verdict.
INJECTION_PHRASES = (
    "ignore previous instructions", "ignore all previous", "disregard the above",
    "you are now", "system prompt", "reveal your instructions",
    "act as an unrestricted", "developer mode", "print your system message",
    "override your guardrails", "bypass the policy",
)


@dataclass
class Violation:
    guardrail: str
    check: GuardrailCheck
    action: GuardrailAction
    detail: str
    matches: list[str] = field(default_factory=list)

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


def _redact(text: str, pattern: re.Pattern[str], label: str) -> str:
    return pattern.sub(f"[redacted:{label}]", text)


class GuardrailEngine:
    """Runs the declared guardrails over content crossing a boundary."""

    def __init__(self, guardrails: list[Guardrail]) -> None:
        self.guardrails = [g for g in guardrails if g.enabled]

    def for_kind(self, kind: GuardrailKind) -> list[Guardrail]:
        return [g for g in self.guardrails if kind in g.applies_to]

    # -- checks ------------------------------------------------------------

    def _run_check(self, guardrail: Guardrail, check: GuardrailCheck, text: str,
                   context: dict[str, Any]) -> tuple[list[str], str, str]:
        """Return (matches, detail, redacted_text) for one check."""
        redacted = text
        matches: list[str] = []

        if check is GuardrailCheck.SECRETS:
            for name in SECRET_CHECKS:
                found = PATTERNS[name].findall(text)
                if found:
                    matches.append(name)
                    redacted = _redact(redacted, PATTERNS[name], name)
            return matches, f"credential-shaped content: {', '.join(matches)}", redacted

        if check is GuardrailCheck.PII:
            for name in PII_CHECKS:
                if PATTERNS[name].search(text):
                    matches.append(name)
                    redacted = _redact(redacted, PATTERNS[name], name)
            return matches, f"identifying data: {', '.join(matches)}", redacted

        if check is GuardrailCheck.PROMPT_INJECTION:
            lowered = text.lower()
            matches = [p for p in INJECTION_PHRASES if p in lowered]
            return (matches, f"instruction-like content: {matches[:3]}", redacted)

        if check is GuardrailCheck.DATA_CLASS:
            carried = set(context.get("data_classes", []))
            hit = sorted(carried & set(guardrail.data_classes))
            return hit, f"carries restricted data class(es): {hit}", redacted

        if check is GuardrailCheck.URL_ALLOWLIST:
            urls = re.findall(r"https?://\S+", text)
            allowed = guardrail.allowed_urls
            bad = [
                u for u in urls
                if not any(
                    (urlparse(u).hostname or "").endswith(a.lstrip("*."))
                    for a in allowed
                )
            ]
            return bad, f"links outside the allowlist: {bad[:3]}", redacted

        if check is GuardrailCheck.PATTERN:
            for pattern in guardrail.patterns:
                compiled = re.compile(pattern, re.I)
                if compiled.search(text):
                    matches.append(pattern)
                    redacted = compiled.sub("[redacted]", redacted)
            return matches, f"matched declared pattern(s): {matches}", redacted

        if check is GuardrailCheck.MAX_LENGTH:
            limit = guardrail.max_length or 0
            if limit and len(text) > limit:
                return ([f"{len(text)}>{limit}"],
                        f"content is {len(text)} characters, over the {limit} limit",
                        text[:limit])
            return [], "", redacted

        if check is GuardrailCheck.SCHEMA:
            errors = context.get("schema_errors") or []
            return list(errors), f"does not match the declared shape: {errors[:3]}", text

        return [], "", redacted

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
                matches, detail, candidate = self._run_check(guardrail, check,
                                                             working, context)
                if not matches:
                    continue
                violation = Violation(guardrail.id, check, guardrail.on_violation,
                                      detail, [str(m) for m in matches])
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
