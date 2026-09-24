"""What the validator says: a severity-tagged finding, and two small helpers."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning"]


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str
    where: str = ""

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.severity}: {self.code}{loc}: {self.message}"


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


def matches(pattern: str, value: str) -> bool:
    return fnmatch.fnmatch(value, pattern)
