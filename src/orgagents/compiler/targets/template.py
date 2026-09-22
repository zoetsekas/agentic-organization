"""A target you write in templates rather than in Python (ADR-0092).

Every other target is a Python plugin: full control, and a real cost to entry.
Plenty of what people actually need is smaller than that — a Helm chart, a
Nomad job, an internal YAML the platform team already has a schema for, a
one-page inventory for an auditor. Writing a `Target` class for those is more
ceremony than the job deserves.

So this target renders a **directory of templates** against the IR. Point it at
your templates and it writes the corresponding files; no Python, no fork, no
entry point.

    orgagents compile acme.system.yaml --target template \\
        --template-dir ./my-templates --out build

**Substitution is deliberately `string.Template`** (`$name`, `${name}`), the
standard library's, not a full template engine. This codebase renders all of
its own output by building strings, and taking a template-engine dependency so
users can have loops would be a large decision made on their behalf. Iteration
is expressed in the *filename* instead — a template whose path contains
`{agent}` is rendered once per agent — which covers the per-agent case that
motivates most of these without inventing a language.

If you need conditionals, loops or anything cleverer: write a Python target and
register it (docs/PLUGINS.md). That seam exists and this one does not compete
with it — it is the cheap end of the same spectrum, and it says so in its own
conformance report rather than pretending otherwise (ADR-0073).
"""
from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Any, Optional

from ...plugins import ProviderDescriptor
from ..base import GeneratedFile
from ..ir import SystemIR

#: A template file carrying this in its path is rendered once per agent.
AGENT_TOKEN = "{agent}"

#: Templates are recognised by this suffix, which is then stripped from the
#: written path, so `job.nomad.tmpl` becomes `job.nomad`.
SUFFIX = ".tmpl"


def _join(values: Any) -> str:
    return ", ".join(str(v) for v in (values or []))


def system_context(ir: SystemIR) -> dict[str, str]:
    """What every template can reference."""
    return {
        "system_name": ir.name,
        "spec_version": ir.spec_version,
        "ir_version": ir.ir_version,
        "agent_count": str(len(ir.agents)),
        "agent_ids": _join(a.id for a in ir.agents),
        "tenant": getattr(getattr(ir, "tenant", None), "id", "") or "",
    }


def agent_context(ir: SystemIR, agent: Any) -> dict[str, str]:
    """What a per-agent template can reference, on top of the system's.

    Everything is a string: `string.Template` substitutes into text, and a
    template that wants structure should ask for the IR as JSON instead of
    this target inventing a type system.
    """
    model = agent.model or {}
    context = system_context(ir)
    context.update({
        "agent_id": agent.id,
        "agent_name": agent.name or agent.id,
        "agent_description": agent.description or "",
        "agent_instructions": getattr(agent, "instructions", "") or "",
        "system_prompt": agent.system_prompt() or "",
        "team": " / ".join(agent.team_path or []),
        "reports_to": agent.reports_to or "",
        "delegates_to": _join(sorted(agent.delegates_to)),
        "roles": _join(agent.role_ids),
        "capabilities": _join(c.id for c in agent.capabilities),
        "tools": _join(t.id for t in agent.tools),
        "requires_approval_for": _join(agent.requires_approval_for),
        "environments": _join(e.id for e in agent.environments),
        "placements": _join(agent.placements),
        "decisions": _join(sorted(agent.mandate.decisions)),
        "humans": _join(h.name or h.person for h in agent.humans),
        "model_provider": str(model.get("provider", "")),
        "model_name": str(model.get("model", "")),
        "runtime_adapter": getattr(agent, "runtime_adapter", "") or "",
        "permission_count": str(len(agent.permissions)),
    })
    return context


class TemplateTarget:
    """Render a directory of templates against the IR."""

    def __init__(self, template_dir: Optional[str] = None,
                 target_id: str = "template") -> None:
        self.id = target_id
        self.template_dir = Path(template_dir) if template_dir else None

    def descriptor(self) -> ProviderDescriptor:
        # It carries whatever the author's templates carry, which this target
        # cannot know. Claiming features on their behalf would be a guess, so
        # it claims only the portable core it always exposes.
        return ProviderDescriptor(
            id=self.id, title="Your templates", kind="target",
            summary="Renders a directory of templates against the IR.",
            supports=frozenset({"instructions", "tools", "model"}),
        )

    def describe(self) -> dict[str, Any]:
        where = str(self.template_dir) if self.template_dir else "(not set)"
        return {
            "id": self.id,
            "title": "Your templates",
            "summary": (f"Renders every *{SUFFIX} in a template directory "
                        f"against the IR — currently {where}."),
            "produces": ["whatever your templates name"],
            "caveats": [
                "Substitution is string.Template ($name), not a template "
                "engine: no loops, no conditionals. A path containing "
                f"'{AGENT_TOKEN}' renders once per agent, which is how "
                "iteration is expressed.",
                "This target enforces nothing. What your templates emit is "
                "yours to make faithful; read CONFORMANCE.md.",
                "An unknown $placeholder is left as written rather than "
                "guessed at, and named in CONFORMANCE.md.",
                "These files carry no tenant boundary. Nothing here namespaces "
                "or isolates a tenant; that is the infrastructure target's job "
                "(ADR-0050), and a template cannot take it on.",
            ],
        }

    # -- generation --------------------------------------------------------

    def generate(self, ir: SystemIR) -> list[GeneratedFile]:
        if self.template_dir is None:
            raise ValueError(
                "the template target needs a template directory; pass "
                "--template-dir")
        if not self.template_dir.is_dir():
            raise ValueError(f"template directory not found: {self.template_dir}")

        files: list[GeneratedFile] = []
        unresolved: dict[str, list[str]] = {}
        templates = sorted(self.template_dir.rglob(f"*{SUFFIX}"))
        for template in templates:
            relative = template.relative_to(self.template_dir).as_posix()
            body = template.read_text()
            if AGENT_TOKEN in relative:
                for agent in ir.agents:
                    context = agent_context(ir, agent)
                    path = relative.replace(AGENT_TOKEN, _safe(agent.id))
                    files.append(self._render(path, body, context, unresolved))
            else:
                files.append(
                    self._render(relative, body, system_context(ir), unresolved))

        files.append(GeneratedFile(
            "system.ir.json",
            json.dumps(ir.model_dump(mode="json"), indent=2) + "\n"))
        files.append(GeneratedFile(
            "CONFORMANCE.md", self._conformance(ir, templates, unresolved)))
        return files

    def _render(self, path: str, body: str, context: dict[str, str],
                unresolved: dict[str, list[str]]) -> GeneratedFile:
        """Substitute, leaving anything unknown exactly as written.

        `safe_substitute` rather than `substitute`: a template that mentions a
        `$variable` this platform does not supply is far more likely to be a
        shell variable or a literal than a mistake, and blowing up the whole
        compile over one would be the wrong trade. What was left alone is
        reported instead, so a real typo is still visible.
        """
        out = path[: -len(SUFFIX)] if path.endswith(SUFFIX) else path
        template = Template(body)
        missing = sorted({
            name for name in _placeholders(template) if name not in context})
        if missing:
            unresolved[out] = missing
        return GeneratedFile(out, template.safe_substitute(context))

    def _conformance(self, ir: SystemIR, templates: list[Path],
                     unresolved: dict[str, list[str]]) -> str:
        rendered = "\n".join(
            f"- `{t.relative_to(self.template_dir).as_posix()}`"
            for t in templates) or "- (none found)"
        left = "\n".join(
            f"| `{path}` | {', '.join(names)} |"
            for path, names in sorted(unresolved.items()))
        left_block = (
            "| File | Left as written |\n|---|---|\n" + left
            if left else "None — every `$placeholder` resolved.")
        return f"""# What this target cannot enforce

Generated for `{ir.name}`, target `{self.id}` (your templates, from
`{self.template_dir}`).

This target renders **your** templates. It does not know what they say, so it
cannot promise anything about what they carry — and a report that implied
otherwise would be the lie every other target's conformance page exists to
prevent (ADR-0073).

## What was rendered

{rendered}

## Placeholders left as written

`string.Template.safe_substitute` leaves an unknown `$name` untouched rather
than failing the compile, because it is usually a shell variable rather than a
mistake. If one of these is a typo, this is where you find it.

{left_block}

## What you are responsible for

| Construct | Who enforces it |
|---|---|
| Roles, permissions, mandates | this platform's harness — never your output |
| Separation of duties | the phase gate, at compile time |
| Approvals and who may give them | this platform's harness |
| Data classes and egress | this platform's harness |
| Anything your templates emit | you |

`system.ir.json` beside this file is the whole resolved design — permissions,
identities, placements, routing — if a template needs more than the
placeholders expose.

## When to stop using this target

If you find yourself wanting loops, conditionals or logic, you have outgrown
`string.Template`. Write a Python target and register it under the
`orgagents.targets` entry point (docs/PLUGINS.md); it gets the same IR with
none of these limits.
"""


def _placeholders(template: Template) -> set[str]:
    """Every `$name` / `${name}` a template mentions."""
    found: set[str] = set()
    for match in template.pattern.finditer(template.template):
        name = match.group("named") or match.group("braced")
        if name:
            found.add(name)
    return found


def _safe(identifier: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in identifier)
