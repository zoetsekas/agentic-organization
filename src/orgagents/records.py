"""Architecture Decision Records and Workstream Records as checked data.

Records are markdown files with YAML front matter under ``docs/decisions``
(ADR-nnnn) and ``docs/workstreams`` (WS-nnn). This module parses them,
enforces the governance rules, and regenerates the indexes — so the record set
stays a navigable graph instead of drifting prose.

Rules enforced by ``validate()``
--------------------------------
* identifiers are unique and match the filename;
* ``status`` is from the allowed lifecycle for that record kind;
* ``version`` is semver and matches the newest changelog entry;
* every cross-reference (``supersedes``, ``related``, ``workstreams``,
  ``decisions``, ``depends_on``) resolves to a record that exists;
* supersession is symmetric — if A supersedes B then B records
  ``superseded_by: [A]`` and carries status ``Superseded``;
* the required narrative sections are present, so a record always answers
  why / who / what / where / how / when and both sides of the trade-off.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

import yaml

DECISIONS_DIR = "docs/decisions"
WORKSTREAMS_DIR = "docs/workstreams"

ADR_STATUSES = ("Proposed", "Accepted", "Rejected", "Deprecated", "Superseded")
WS_STATUSES = ("Proposed", "Active", "Blocked", "Paused", "Complete", "Cancelled")

ADR_SECTIONS = (
    "Context",            # WHY
    "Decision",           # WHAT
    "Scope",              # WHERE
    "Implementation",     # HOW
    "Timeline",           # WHEN
    "Advantages",
    "Disadvantages",
    "Alternatives considered",
    "Verification",
    "Changelog",
)
WS_SECTIONS = (
    "Objective",          # WHY
    "Deliverables",       # WHAT
    "Scope",              # WHERE
    "Approach",           # HOW
    "Milestones",         # WHEN
    "Dependencies",
    "Advantages",
    "Disadvantages",
    "Exit criteria",
    "Changelog",
)

ID_RE = {"adr": re.compile(r"^ADR-\d{4}$"), "ws": re.compile(r"^WS-\d{3}$")}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
CHANGELOG_ENTRY_RE = re.compile(r"^\|\s*(\d+\.\d+\.\d+)\s*\|\s*([\d-]+)\s*\|")

Kind = Literal["adr", "ws"]

# Front-matter keys holding references to other records.
REF_KEYS = ("supersedes", "superseded_by", "related", "workstreams", "decisions",
            "depends_on")


@dataclass
class Record:
    kind: Kind
    path: Path
    meta: dict[str, Any]
    body: str

    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    @property
    def title(self) -> str:
        return str(self.meta.get("title", ""))

    @property
    def status(self) -> str:
        return str(self.meta.get("status", ""))

    @property
    def version(self) -> str:
        return str(self.meta.get("version", ""))

    def refs(self, key: str) -> list[str]:
        value = self.meta.get(key) or []
        return [value] if isinstance(value, str) else [str(v) for v in value]

    def sections(self) -> list[str]:
        return [m.group(1).strip() for m in re.finditer(r"^##\s+(.+)$", self.body, re.M)]

    def changelog(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        in_section = False
        for line in self.body.splitlines():
            if line.startswith("## "):
                in_section = line.strip().lower() == "## changelog"
                continue
            if in_section:
                m = CHANGELOG_ENTRY_RE.match(line.strip())
                if m:
                    out.append((m.group(1), m.group(2)))
        return out

    def link(self) -> str:
        return f"[{self.id}]({self.path.name}) — {self.title}"


@dataclass
class RecordSet:
    records: dict[str, Record] = field(default_factory=dict)

    def of_kind(self, kind: Kind) -> list[Record]:
        return sorted(
            (r for r in self.records.values() if r.kind == kind), key=lambda r: r.id
        )

    def get(self, record_id: str) -> Optional[Record]:
        return self.records.get(record_id)


def parse(path: Path, kind: Kind) -> Record:
    text = path.read_text()
    m = FRONT_MATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path}: missing YAML front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: front matter must be a mapping")
    return Record(kind=kind, path=path, meta=meta, body=m.group(2))


def load(root: str | Path = ".") -> RecordSet:
    root = Path(root)
    rs = RecordSet()
    for kind, folder, prefix in (
        ("adr", DECISIONS_DIR, "ADR-"),
        ("ws", WORKSTREAMS_DIR, "WS-"),
    ):
        directory = root / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob(f"{prefix}*.md")):
            record = parse(path, kind)  # type: ignore[arg-type]
            rs.records[record.id] = record
    return rs


def validate(rs: RecordSet) -> list[str]:
    """Return a list of governance violations; empty means the set is sound."""
    errors: list[str] = []

    def err(record: Record, message: str) -> None:
        errors.append(f"{record.path.as_posix()}: {message}")

    for record in rs.records.values():
        statuses = ADR_STATUSES if record.kind == "adr" else WS_STATUSES
        sections = ADR_SECTIONS if record.kind == "adr" else WS_SECTIONS

        if not ID_RE[record.kind].match(record.id):
            err(record, f"invalid id '{record.id}'")
        if record.path.stem.split("-")[0:2] != record.id.split("-"):
            err(record, f"filename does not start with id '{record.id}'")
        if not record.title:
            err(record, "missing title")
        if record.status not in statuses:
            err(record, f"status '{record.status}' not in {statuses}")
        if not SEMVER_RE.match(record.version):
            err(record, f"version '{record.version}' is not semver")
        for key in ("date", "deciders" if record.kind == "adr" else "owner"):
            if not record.meta.get(key):
                err(record, f"missing required front-matter key '{key}'")

        present = {s.lower() for s in record.sections()}
        for required in sections:
            if required.lower() not in present:
                err(record, f"missing required section '## {required}'")

        changelog = record.changelog()
        if not changelog:
            err(record, "changelog has no entries")
        elif changelog[0][0] != record.version:
            err(
                record,
                f"newest changelog entry {changelog[0][0]} != version {record.version}",
            )

        for key in REF_KEYS:
            for ref in record.refs(key):
                if rs.get(ref) is None:
                    err(record, f"{key} references unknown record '{ref}'")

        # Supersession must be symmetric and reflected in the status.
        for ref in record.refs("supersedes"):
            target = rs.get(ref)
            if target is None:
                continue
            if record.id not in target.refs("superseded_by"):
                err(record, f"supersedes {ref}, but {ref} does not list it back")
            if target.status != "Superseded":
                err(target, f"superseded by {record.id} but status is '{target.status}'")
        if record.refs("superseded_by") and record.status != "Superseded":
            err(record, f"has superseded_by but status is '{record.status}'")

    return sorted(errors)


def _group(records: Iterable[Record], key: str) -> dict[str, list[Record]]:
    out: dict[str, list[Record]] = {}
    for r in records:
        out.setdefault(str(r.meta.get(key, "—")), []).append(r)
    return out


def render_decision_index(rs: RecordSet) -> str:
    rows = [
        "<!-- generated by `orgagents records index`; do not edit by hand -->",
        "# Architecture Decision Records",
        "",
        "| ID | Title | Status | Ver | Scope | Workstreams | Supersedes |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rs.of_kind("adr"):
        rows.append(
            f"| [{r.id}]({r.path.name}) | {r.title} | {r.status} | {r.version} | "
            f"{', '.join(r.refs('scope')) or '—'} | "
            f"{', '.join(r.refs('workstreams')) or '—'} | "
            f"{', '.join(r.refs('supersedes')) or '—'} |"
        )
    rows += ["", "## By status", ""]
    for status, items in sorted(_group(rs.of_kind("adr"), "status").items()):
        rows.append(f"- **{status}** — " + ", ".join(f"[{r.id}]({r.path.name})" for r in items))
    rows += ["", "See [README.md](README.md) for the process and lifecycle.", ""]
    return "\n".join(rows)


def render_workstream_index(rs: RecordSet) -> str:
    rows = [
        "<!-- generated by `orgagents records index`; do not edit by hand -->",
        "# Workstream Records",
        "",
        "| ID | Title | Status | Ver | Owner | Decisions | Depends on |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rs.of_kind("ws"):
        rows.append(
            f"| [{r.id}]({r.path.name}) | {r.title} | {r.status} | {r.version} | "
            f"{r.meta.get('owner', '—')} | "
            f"{', '.join(r.refs('decisions')) or '—'} | "
            f"{', '.join(r.refs('depends_on')) or '—'} |"
        )
    rows += ["", "## By status", ""]
    for status, items in sorted(_group(rs.of_kind("ws"), "status").items()):
        rows.append(f"- **{status}** — " + ", ".join(f"[{r.id}]({r.path.name})" for r in items))
    rows += ["", "See [README.md](README.md) for the process and lifecycle.", ""]
    return "\n".join(rows)


def write_indexes(rs: RecordSet, root: str | Path = ".") -> list[Path]:
    root = Path(root)
    written = []
    for folder, render in (
        (DECISIONS_DIR, render_decision_index),
        (WORKSTREAMS_DIR, render_workstream_index),
    ):
        directory = root / folder
        if not directory.is_dir():
            continue
        path = directory / "index.md"
        path.write_text(render(rs) + "\n")
        written.append(path)
    return written


def graph(rs: RecordSet) -> dict[str, Any]:
    """Record graph for the UI: nodes plus typed edges."""
    edges = []
    for r in rs.records.values():
        for key in REF_KEYS:
            for ref in r.refs(key):
                edges.append({"from": r.id, "to": ref, "type": key})
    return {
        "nodes": [
            {
                "id": r.id,
                "kind": r.kind,
                "title": r.title,
                "status": r.status,
                "version": r.version,
                "path": r.path.as_posix(),
            }
            for r in sorted(rs.records.values(), key=lambda x: x.id)
        ],
        "edges": edges,
    }


def next_id(rs: RecordSet, kind: Kind) -> str:
    prefix, width = ("ADR", 4) if kind == "adr" else ("WS", 3)
    used = [
        int(r.id.split("-")[1]) for r in rs.of_kind(kind) if r.id.split("-")[-1].isdigit()
    ]
    return f"{prefix}-{max(used, default=0) + 1:0{width}d}"


def scaffold(rs: RecordSet, kind: str, title: str, root: str | Path = ".") -> str:
    """Create a new record from the template with the next free id."""
    if kind not in ("adr", "ws"):
        raise ValueError("kind must be 'adr' or 'ws'")
    root = Path(root)
    folder = root / (DECISIONS_DIR if kind == "adr" else WORKSTREAMS_DIR)
    record_id = next_id(rs, kind)  # type: ignore[arg-type]
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    path = folder / f"{record_id}-{slug}.md"
    today = __import__("datetime").date.today().isoformat()
    template = (folder / "_template.md").read_text()
    placeholder = "ADR-nnnn" if kind == "adr" else "WS-nnn"
    body = (
        template.replace(placeholder, record_id)
        .replace("YYYY-MM-DD", today)
        .replace("One-line statement of the decision", title)
        .replace("One-line statement of the work", title)
    )
    path.write_text(body)
    return path.as_posix()
