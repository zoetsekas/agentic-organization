"""The shipped examples, loadable into the designer (UI and CLI).

Every example under `examples/<name>/*.system.yaml` is a worked organisation
the test suite keeps valid, and until this module there was no way to open one
in the designer: "New organisation" took a name and a description, the CLI had
no import, and the only route was hand-posting a spec to the HTTP API. People
were given a paste-in PowerShell script to do what the product should.

One module, called by both the route the UI uses and `orgagents examples`, so
the two cannot drift: an example that loads from one loads from the other, in
the same shape, laid out the same way.

A loaded example opens *laid out*. Create takes a layout, and an example that
arrived as a spec alone would open on an empty canvas with the whole
organisation sitting in the explorer — which reads as "nothing loaded". The
product's own `tree` algorithm places it (ADR-0100), so what a reader sees
first is what Arrange would have given them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from .layout import LayoutNode, arrange
from .models import MAIN_DIAGRAM, CanvasNode, Diagram, Layout


def examples_dir() -> Path:
    """Where the examples live.

    Resolved the way the web assets are — relative to the source tree — with
    an override for an installation that keeps them elsewhere. The Docker
    image copies `examples/` beside `src/`, which is why the default works
    there too.
    """
    override = os.environ.get("ORGAGENTS_EXAMPLES_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "examples"


@dataclass(frozen=True)
class Example:
    id: str
    name: str
    description: str
    path: Path
    agents: int
    teams: int
    workflows: int

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "path": str(self.path.relative_to(self.path.parents[1])),
            "agents": self.agents,
            "teams": self.teams,
            "workflows": self.workflows,
        }


class UnknownExample(KeyError):
    """No shipped example has this id."""


def _walk(unit: dict[str, Any], teams: list[str], agents: list[str]) -> None:
    teams.append(unit.get("id", ""))
    agents.extend(m.get("id", "") for m in unit.get("members", []) or [])
    for child in unit.get("teams", []) or []:
        _walk(child, teams, agents)


def _describe(path: Path, example_id: str) -> Example:
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    meta = spec.get("metadata") or {}
    teams: list[str] = []
    agents: list[str] = []
    if isinstance(spec.get("organization"), dict):
        _walk(spec["organization"], teams, agents)
    org = spec.get("organization") if isinstance(
        spec.get("organization"), dict) else {}
    # The organisation's own name ("Northwind Trading") rather than the
    # metadata slug ("northwind"): a picker of slugs asks the reader to already
    # know what each one is.
    description = " ".join(str(meta.get("description")
                                or org.get("description") or "").split())
    return Example(
        id=example_id,
        name=str(org.get("name") or meta.get("name") or example_id).strip(),
        description=description,
        path=path,
        agents=len([a for a in agents if a]),
        teams=len([t for t in teams if t]),
        workflows=len((org or {}).get("workflows") or []),
    )


def list_examples() -> list[Example]:
    """Every shipped example, one per system spec, in folder order.

    The id is the folder name, which is how the README and the archetype
    table already refer to them. A folder holding more than one spec gets
    `folder/stem` ids so no two collide.
    """
    root = examples_dir()
    if not root.is_dir():
        # An installation without the examples (a bare wheel, say) has none
        # to offer. An empty list rather than an error: the designer still
        # works, it simply has nothing to preload.
        return []
    found: list[Example] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        specs = sorted(folder.glob("*.system.yaml"))
        for spec_path in specs:
            stem = spec_path.name.removesuffix(".system.yaml")
            example_id = (folder.name if len(specs) == 1
                          else f"{folder.name}/{stem}")
            try:
                found.append(_describe(spec_path, example_id))
            except (OSError, yaml.YAMLError):
                continue            # a file nobody can read is not an example
    return found


def get_example(example_id: str) -> Example:
    for example in list_examples():
        if example.id == example_id:
            return example
    available = ", ".join(e.id for e in list_examples()) or "none"
    raise UnknownExample(
        f"no example '{example_id}'; available: {available}")


def initial_layout(spec: dict[str, Any]) -> Layout:
    """The organisation placed by the product's `tree` algorithm."""
    kinds: dict[str, str] = {}
    parents: dict[str, Optional[str]] = {}

    def walk(unit: dict[str, Any], parent: Optional[str]) -> None:
        unit_id = unit.get("id")
        if not unit_id:
            return
        kinds[unit_id] = "team"
        parents[unit_id] = parent
        for member in unit.get("members", []) or []:
            if member.get("id"):
                kinds[member["id"]] = "agent"
                parents[member["id"]] = unit_id
        for child in unit.get("teams", []) or []:
            walk(child, unit_id)

    if isinstance(spec.get("organization"), dict):
        walk(spec["organization"], None)
    placed = arrange(
        [LayoutNode(id=i, parent=parents[i]) for i in kinds],
        kind="organisation",
    ).positions
    nodes = {
        i: CanvasNode(id=i, kind=kinds[i], x=40 + placed[i]["x"],
                      y=70 + placed[i]["y"], width=240, height=80)
        for i in kinds
    }
    return Layout(diagrams={MAIN_DIAGRAM: Diagram(
        id=MAIN_DIAGRAM, name="Organisation", nodes=nodes)})


def load_example(designer: Any, principal: Any, example_id: str, *,
                 workspace_id: str = "", name: str = "") -> dict[str, Any]:
    """Create a design from a shipped example, laid out, and return it.

    Into `workspace_id` when one is given — the UI passes the workspace the
    reader is in — and otherwise into a new workspace named after the example,
    which is what the CLI wants: a user loading an example from a shell has no
    workspace open.

    Everything goes through the designer service, so the same membership and
    role checks apply as to any other create, and the audit log records it.
    """
    example = get_example(example_id)
    spec = yaml.safe_load(example.path.read_text(encoding="utf-8"))
    title = name or example.name
    created_workspace = False
    if not workspace_id:
        workspace = designer.create_workspace(principal, title,
                                              f"Loaded from examples/{example.id}")
        workspace_id = workspace.id
        created_workspace = True
    record = designer.create_system(
        principal, workspace_id=workspace_id, name=title,
        description=example.description or f"The {example.id} example",
        spec=spec, layout=initial_layout(spec),
    )
    return {
        "example": example.summary(),
        "workspace_id": workspace_id,
        "created_workspace": created_workspace,
        "system_id": record.id,
        "name": record.name,
        "version": record.version,
    }
