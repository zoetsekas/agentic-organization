# Tool implementations for 'ayc' — engineer-owned (ADR-0089).
# This file is YOURS to edit. Regeneration is additive-only: it never
# rewrites or removes a function here, it only appends a stub for a
# tool the design newly requires. Implement each body in place.

"""Tool implementations.

Each function is a tool the design gives one or more agents but whose
code is the host's — a wrapper that narrows an existing grant, or a
capability with no server bound. Fill in the body; the signature and
docstring come from the design. Every agent that has the tool imports
it from here, so implement it once.
"""
from __future__ import annotations

from typing import Any


def stock_lookup(sku: str) -> dict:
    """Look up the Fishbowl stock level for one SKU, already scoped.

    Wraps capability 'stock_check' (ADR-0029): implement by calling it and narrowing to this tool's contract.

    Args:
        sku (str)
    Returns:
        dict with on_hand: number, location: string
    """
    raise NotImplementedError("TODO: implement stock_lookup")
