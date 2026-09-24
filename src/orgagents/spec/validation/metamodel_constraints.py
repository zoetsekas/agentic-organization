"""The model's own constraints (ADR-0102, ADR-0103).

A draft may be incomplete; a design that is published may not. Every
constraint of the metamodel is an error here, integrity and completeness
alike, named `model_<constraint>`.
"""
from __future__ import annotations

from .context import ValidationContext
from .registry import rule


def constraint_codes() -> set[str]:
    """`model_<name>` for every constraint the metamodel declares."""
    from ...metamodel.constraints import CONSTRAINTS

    return {f"model_{c.name}" for c in CONSTRAINTS}


@rule("the model's own constraints", constraint_codes)
def model_constraints_hold(ctx: ValidationContext) -> None:
    # Imported here: the metamodel reads the spec model, so a module-level
    # import would close a cycle through this package's __init__.
    from ...metamodel.constraints import check as model_check

    for v in model_check(ctx.spec):
        ctx.err(f"model_{v.constraint}", f"{v.element}: {v.message}", v.element)
