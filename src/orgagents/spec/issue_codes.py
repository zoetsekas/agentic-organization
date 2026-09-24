"""Numbered, explained issue codes.

A finding's `code` (`possible_escalation`) is a name for a program; somebody
looking at the Issues tab also needs a number they can quote and an account of
what the rule is, why it exists and how to satisfy it. Both live in
`issue_catalog.yaml`, next to the validator that emits the codes.

Numbers are grouped by the validator section a code belongs to (`OA-0100`
uniqueness, `OA-0200` mandates, ...), and once published a number is never
reused or moved: a new code takes the next free number in its section.
`tests/test_issue_codes.py` holds the catalog to every code the validator can
emit.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

CATALOG_PATH = Path(__file__).with_name("issue_catalog.yaml")

# Schema errors carry pydantic's own error type as their code (`missing`,
# `extra_forbidden`, ...). There are too many to number one by one, and they
# all mean the same thing to a designer: the file is not the shape of a design.
SCHEMA_ERROR = "schema_error"


def display_number(number: int) -> str:
    return f"OA-{number:04d}"


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, Any]]:
    """Every catalogued code, by code, with its `id` (`OA-0101`) filled in."""
    entries = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or []
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        entry = dict(entry)
        entry["id"] = display_number(int(entry["number"]))
        out[entry["code"]] = entry
    return out


def lookup(code: Optional[str]) -> Optional[dict[str, Any]]:
    """The catalog entry for a finding's code; schema errors share one entry."""
    if not code:
        return None
    entries = catalog()
    if code in entries:
        return entries[code]
    if code.startswith("model_"):
        return None
    return entries.get(SCHEMA_ERROR)
