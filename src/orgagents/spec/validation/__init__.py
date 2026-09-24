"""Spec validation: structure, references and least privilege.

Findings are severity-tagged. `error` fails the build; `warning` is reported
and, for the rules ADR-0008 marks as mandatory in production, promoted to an
error when the design is judged as production (the fabric's platform policy
decides that where one is in force, ADR-0076).

Every check is a rule in the registry (`registry.py`), filed under the
validator section it belongs to; the sections line up with the UML profiles
(ADR-0112) and with the issue catalog's numbering. One module per section:

=========================  ==============================================
module                     section
=========================  ==============================================
``uniqueness``             uniqueness
``authority``              authority: mandates
``people``                 people as principals (and separation of duties)
``data``                   data semantics and contracts
``workflows``              workflow graphs (and the binding's engines)
``scale``                  scale
``succession``             succession
``policy``                 policy conditions
``placement``              placement
``control``                control ownership
``autonomy``               autonomy postures (and mandate overreach)
``structure``              organization structure
``references``             references (and system-level references)
``least_privilege``        least privilege
``triggers``               triggers
``channels``               channels
``flows``                  interaction flows
``unit_links``             unit links: association, not containment
``knowledge``              knowledge
``budgets_lifecycle``      budgets, lifecycle, compliance
``missions``               missions
``model_policy``           model policy
``guardrails``             guardrails, workspaces, contracts
``memory``                 memory contract
``capability_coverage``    capability coverage
``directory``              people who have left
``metamodel_constraints``  the model's own constraints
``platform_policy``        platform policy (applied last)
=========================  ==============================================

`validate_spec` builds one `ValidationContext` — the spec and the indexes
every rule reads — and runs the rules in registry order, so the findings come
out in the same order they always have.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Optional

from ..model import SystemSpec
from .context import ValidationContext
from .directory import directory_findings
from .findings import Finding, Severity, errors, matches
from .helpers import team_permissions
from .registry import SECTIONS, Rule, declared_codes, rule, rules
from .workflows import workflow_binding_findings

if TYPE_CHECKING:  # a runtime import would close a cycle: directory reads this package
    from ...directory import Directory

#: The rule modules, in section order. Importing one registers its rules.
RULE_MODULES: tuple[str, ...] = (
    "uniqueness", "authority", "people", "data", "workflows", "scale",
    "succession", "policy", "placement", "control", "autonomy", "structure",
    "references", "least_privilege", "triggers", "channels", "flows",
    "unit_links", "knowledge", "budgets_lifecycle", "missions", "model_policy",
    "guardrails", "memory", "capability_coverage", "directory",
    "metamodel_constraints", "platform_policy",
)


def _load_rules() -> None:
    for name in RULE_MODULES:
        importlib.import_module(f"{__name__}.{name}")


def validate_spec(
    spec: SystemSpec,
    directory: Optional["Directory"] = None,
    platform_policy: Optional[Any] = None,
) -> list[Finding]:
    """Return every finding; an empty list means the spec is sound.

    `directory`, when given, is additionally consulted about the people the
    spec pairs with agents (ADR-0047). It is optional because most callers have
    none, and a directory that knows nothing contributes nothing.
    """
    ctx = ValidationContext(spec, directory=directory,
                            platform_policy=platform_policy)
    for check in rules("spec"):
        check.fn(ctx)
    for final in rules("finalize"):
        final.fn(ctx)
    return ctx.out


_load_rules()

__all__ = [
    "Finding",
    "Severity",
    "Rule",
    "SECTIONS",
    "RULE_MODULES",
    "ValidationContext",
    "declared_codes",
    "directory_findings",
    "errors",
    "matches",
    "rule",
    "rules",
    "team_permissions",
    "validate_spec",
    "workflow_binding_findings",
]
