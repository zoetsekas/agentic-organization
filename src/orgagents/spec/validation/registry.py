"""The rule registry: every check the validator makes, with what it may say.

A rule is a function registered with `@rule(section, codes)`. The section is
the validator section it belongs to — the same name the issue catalog files
its codes under (`issue_catalog.yaml`), which is how codes are numbered — and
`codes` are every issue code the rule may emit. `tests/test_issue_codes.py`
holds the declaration to the rule's source and to the catalog, so a rule
cannot say something it has not declared, or declare something nobody can
look up.

Order is part of the contract: findings are shown in the order they are
found, and the Issues tab and several tests read them that way. Rules run
section by section in `SECTIONS` order and, within a section, in the order
they were registered. Every section's rules live in one module, so that
order is the order of the module's source.

Stages:

* ``spec`` — run by `validate_spec` over a `ValidationContext`.
* ``finalize`` — run after every ``spec`` rule, and may re-rank what they
  found (the fabric's platform policy, ADR-0076).
* ``binding`` — not run by `validate_spec`: findings that need a target's
  binding to decide (`workflow_binding_findings`). Registered so its codes
  are declared in the same place as everybody else's.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal, TypeVar, Union

Stage = Literal["spec", "finalize", "binding"]
Codes = Union[Iterable[str], Callable[[], Iterable[str]]]

#: Validator sections, in the order their rules run. The names are the issue
#: catalog's `section` values; numbers there are grouped the same way.
SECTIONS: tuple[str, ...] = (
    "uniqueness",
    "authority: mandates",
    "people as principals",
    "data semantics and contracts",
    "workflow graphs",
    "scale",
    "succession",
    "policy conditions",
    "placement",
    "control ownership",
    "autonomy postures",
    "organization structure",
    "references",
    "least privilege",
    "triggers",
    "channels",
    "interaction flows",
    "unit links: association, not containment",
    "knowledge",
    "budgets, lifecycle, compliance",
    "missions",
    "model policy",
    "guardrails, workspaces, contracts",
    "memory contract",
    "system-level skill/plugin/tool references",
    "capability coverage",
    "people who have left",
    "the model's own constraints",
    "platform policy",
)


@dataclass(frozen=True)
class Rule:
    """One registered check."""

    name: str
    section: str
    declared: Codes
    stage: Stage
    fn: Callable[..., Any]
    seq: int

    @property
    def codes(self) -> frozenset[str]:
        """What this rule may emit. A rule that relays another module's
        findings may declare them as a function, read when first asked, so
        that declaring them does not import that module early."""
        if callable(self.declared):
            return frozenset(self.declared())
        return frozenset(self.declared)

    @property
    def module(self) -> str:
        return str(self.fn.__module__)

    @property
    def order(self) -> tuple[int, int]:
        return (SECTIONS.index(self.section), self.seq)


_RULES: list[Rule] = []

F = TypeVar("F", bound=Callable[..., Any])


def rule(section: str, codes: Codes, *,
         stage: Stage = "spec") -> Callable[[F], F]:
    """Register a function as a rule of `section` that may emit `codes`."""
    if section not in SECTIONS:
        raise ValueError(f"unknown validator section {section!r}")
    declared: Codes = codes if callable(codes) else frozenset(codes)

    def register(fn: F) -> F:
        name = f"{fn.__module__}.{fn.__qualname__}"
        if any(r.name == name for r in _RULES):
            return fn                  # a module imported twice registers once
        _RULES.append(Rule(name=name, section=section, declared=declared,
                           stage=stage, fn=fn, seq=len(_RULES)))
        return fn

    return register


def rules(stage: Stage | None = None) -> list[Rule]:
    """Registered rules in run order, optionally only one stage's."""
    from . import _load_rules

    _load_rules()
    selected = [r for r in _RULES if stage is None or r.stage == stage]
    return sorted(selected, key=lambda r: r.order)


def declared_codes() -> set[str]:
    """Every code any registered rule may emit."""
    return {code for r in rules() for code in r.codes}
