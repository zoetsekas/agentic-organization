"""Bring an older System Spec document forward to the current `spec_version`.

ADR-0004 makes the spec the single source of truth, and a source of truth that
cannot be read after a version bump is not one. Every bump therefore gets a
step here, registered in order, applied one at a time — a document at 1.0.0
reaches 1.2.0 by way of 1.1.0, never by a single bespoke jump.

Each step reports what it changed in plain sentences, because the value of a
migration is that a reviewer can see what happened to their document. A bump
that needs nothing of the data is still registered, as a step that says so: a
silent gap in the chain is indistinguishable from a forgotten one.

Migration is forward-only. A document from a *newer* compiler is refused
rather than stripped of the fields this one does not know about.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

from .model import SPEC_VERSION

CURRENT = SPEC_VERSION


class SpecVersionError(ValueError):
    """Raised when a document's `spec_version` is not supported."""


def _version_key(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in str(version).split("."):
        digits = "".join(c for c in piece if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0, 0, 0])[:3]


@dataclass(frozen=True)
class MigrationStep:
    """One hop between adjacent spec versions."""

    from_version: str
    to_version: str
    summary: str
    apply: Callable[[dict[str, Any]], list[str]]


# --------------------------------------------------------------------------
# 1.0.0 -> 1.1.0 — human pairing became a list (ADR-0026)
# --------------------------------------------------------------------------


def _walk_teams(team: Any) -> list[dict[str, Any]]:
    if not isinstance(team, dict):
        return []
    out = [team]
    for child in team.get("teams") or []:
        out.extend(_walk_teams(child))
    return out


def _agents_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for team in _walk_teams(data.get("organization")):
        for member in team.get("members") or []:
            if isinstance(member, dict):
                agents.append(member)
    return agents


def _lift_human_pairings(data: dict[str, Any]) -> list[str]:
    """`human:` (one accountable person) became `humans:` (a list of roles)."""
    changes: list[str] = []
    for agent in _agents_of(data):
        human = agent.get("human")
        if not human:
            continue
        agent_id = agent.get("id", "<unnamed agent>")
        if agent.get("humans"):
            # Both present: the newer field wins, and we say we dropped the old.
            agent.pop("human")
            changes.append(
                f"agent '{agent_id}': dropped the pre-1.1 'human:' field, which "
                f"'humans:' already supersedes"
            )
            continue
        agent["humans"] = [human]
        agent.pop("human")
        name = human.get("name") if isinstance(human, dict) else human
        changes.append(
            f"agent '{agent_id}': moved 'human: {name}' into 'humans:' as the "
            f"owner pairing (ADR-0026)"
        )
    if not changes:
        changes.append("no agent used the pre-1.1 'human:' field; nothing to lift")
    return changes


# --------------------------------------------------------------------------
# 1.1.0 -> 1.2.0 — missions and model policy (ADR-0039, ADR-0040)
# --------------------------------------------------------------------------


def _missions_and_model_policy(data: dict[str, Any]) -> list[str]:
    """Purely additive: `missions:` and `model_policy:` default sensibly.

    Nothing in a 1.1.0 document changes meaning under 1.2.0, and the defaults
    an omitted block gets (no missions, the `balanced` model class) are the
    behaviour a 1.1.0 document already had. Inventing a mission list or a
    narrower model policy here would be guessing at intent, so this step
    deliberately touches no data.
    """
    return [
        "no data change: 1.2.0 only adds optional 'missions:' (ADR-0039) and "
        "'model_policy:' (ADR-0040) blocks, whose defaults match 1.1.0 behaviour"
    ]


def _many_environments(data: dict[str, Any]) -> list[str]:
    """`environment:` becomes `environments:` on agents and sub-agents.

    A 1.2.0 agent declared at most one sandbox, so every such document maps
    exactly onto a one-element list and nothing changes meaning. The bump is
    not cosmetic though: from 1.3.0 an agent may hold several, and which one a
    call runs in is derived from the data classes it touches rather than fixed
    at the agent (ADR-0082).
    """
    changed: list[str] = []
    for agent in _agents_of(data):
        single = agent.pop("environment", None)
        if single is not None and not agent.get("environments"):
            agent["environments"] = [single]
            changed.append(
                f"agent '{agent.get('id', '?')}': environment "
                f"'{single.get('environment', single)}' became a one-element "
                "'environments:' list"
            )
        for sub in agent.get("subagents", []) or []:
            if not isinstance(sub, dict):
                continue
            sub_single = sub.pop("environment", None)
            if sub_single is not None and not sub.get("environments"):
                sub["environments"] = [sub_single]
                changed.append(
                    f"sub-agent '{sub.get('id', '?')}': environment "
                    f"'{sub_single}' became a one-element 'environments:' list"
                )
    return changed or [
        "no data change: no agent declared an environment to lift"
    ]


MIGRATIONS: list[MigrationStep] = [
    MigrationStep(
        from_version="1.0.0",
        to_version="1.1.0",
        summary="agent 'human:' becomes the 'humans:' pairing list (ADR-0026)",
        apply=_lift_human_pairings,
    ),
    MigrationStep(
        from_version="1.1.0",
        to_version="1.2.0",
        summary="optional missions and model policy blocks added (ADR-0039, ADR-0040)",
        apply=_missions_and_model_policy,
    ),
    MigrationStep(
        from_version="1.2.0",
        to_version="1.3.0",
        summary="agent and sub-agent 'environment:' becomes 'environments:' "
                "(ADR-0082)",
        apply=_many_environments,
    ),
]

EARLIEST = MIGRATIONS[0].from_version


def declared_version(data: dict[str, Any]) -> str:
    """The document's `spec_version`, defaulting to current when unstated."""
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        return CURRENT
    return str(metadata.get("spec_version") or CURRENT)


def needs_migration(data: dict[str, Any], to: str = CURRENT) -> bool:
    return _version_key(declared_version(data)) < _version_key(to)


def migrate(data: dict[str, Any], to: str = CURRENT) -> tuple[dict[str, Any], list[str]]:
    """Upgrade `data` to `to`, returning the new document and what changed.

    The input is never mutated: a caller that keeps the original around for a
    diff is the normal case, not an edge one.
    """
    declared = declared_version(data)
    target_key = _version_key(to)
    if _version_key(declared) > target_key:
        raise SpecVersionError(
            f"spec_version {declared} is newer than {to}; this compiler cannot "
            f"read it, and migration never downgrades a document"
        )
    if _version_key(declared) < _version_key(EARLIEST):
        raise SpecVersionError(
            f"spec_version {declared} predates {EARLIEST}, the oldest version "
            f"with a migration path"
        )

    out = copy.deepcopy(data)
    changes: list[str] = []
    for step in MIGRATIONS:
        if _version_key(declared) != _version_key(step.from_version):
            continue
        if _version_key(step.to_version) > target_key:
            break
        changes.append(f"{step.from_version} → {step.to_version}: {step.summary}")
        changes.extend(f"  • {line}" for line in step.apply(out))
        declared = step.to_version
        out.setdefault("metadata", {})
        if isinstance(out["metadata"], dict):
            out["metadata"]["spec_version"] = declared

    if _version_key(declared) < target_key:
        raise SpecVersionError(
            f"no migration path from spec_version {declared} to {to}"
        )
    return out, changes
