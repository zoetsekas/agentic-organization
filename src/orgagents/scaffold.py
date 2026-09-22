"""Turning the phase gate's refusals into the YAML that answers them.

The gate already knows precisely what a design is missing — each failing
`Check` carries a stable id, a title and a one-line `fix` (ADR-0019). Until
now it named the gap and left the author at a blank page; the fastest real
path to a first spec was "copy the nearest of the shipped examples", which
works but is a poor welcome.

This module closes that: one table maps a gate check to the block of spec
that satisfies it, and two commands use it — `spec new` writes a starter
design carrying every block as a commented TODO, and `phase --scaffold`
prints just the blocks *this* design is still missing.

The blocks are deliberately **commented out and marked TODO**. A scaffold
that silently declared a budget, a data class or a production gate would be
inventing governance the organization has not agreed, which is exactly the
lie the phase gate exists to prevent (ADR-0073). The author has to mean it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class GateBlock:
    """The spec a gate check is asking for."""

    check_id: str
    title: str
    #: The YAML to add, already commented. Top-level unless `per_agent`.
    body: str
    #: True when the edit belongs inside each agent rather than at the root.
    per_agent: bool = False


#: One entry per definition-phase check that a starter design fails. The
#: order is the order an author should work in: what data exists, who is
#: accountable, where work runs, then how it is bounded.
GATE_BLOCKS: tuple[GateBlock, ...] = (
    GateBlock(
        "system_owner",
        "The system has a named owner",
        """# TODO name the team accountable for this system, under `metadata:`
#   owner: <the team that answers for this organization>
""",
    ),
    GateBlock(
        "data_classified",
        "Data is classified",
        """# TODO declare the classes of data your agents touch (ADR-0017).
# Scope is public | protected | private. `groups` limits who may see a class.
# data_classes:
#   - id: public_knowledge
#     description: Published material, safe to quote.
#     scope: public
#   - id: customer_pii
#     description: Customer identity and contact details.
#     scope: private
#     groups: [commerce]
#     may_appear_in_traces: false
""",
    ),
    GateBlock(
        "environments_declared",
        "Execution environments are declared",
        """# TODO declare the isolation classes agents run in (ADR-0009), then
# reference them per agent with `environments: [{environment: <id>}]`.
# network is none | allowlist | open; mounts are data_class ids.
# environments:
#   - id: reasoning
#     description: No code execution; delegation and tool calls only.
#     network: none
#   - id: analysis
#     description: Dataframe work over extracts.
#     network: allowlist
#     mounts: [public_knowledge]
""",
    ),
    GateBlock(
        "agents_have_roles",
        "Every agent holds a role",
        """# TODO declare roles, then give each agent `roles: [<id>]`.
# A role is what states an agent's accountability — its responsibilities in
# plain sentences, and the permissions they justify.
# roles:
#   - id: team_member
#     title: Team member
#     responsibilities:
#       - Do the work this team owns, and say so when you cannot.
#     permissions:
#       - {action: read, resource_kind: data_class, resource: public_knowledge}
""",
    ),
    GateBlock(
        "agents_have_humans",
        "Every agent is paired with at least one human",
        """# TODO declare the people who answer for these agents, then pair them
# per agent with `humans: [{person: <id>, roles: [owner]}]`.
# Agents do not own their outcomes; a person does (ADR-0026).
# people:
#   - id: p_owner
#     name: <name>
#     contact: <email>
#     position: <job title>
#     unit: <team id>
""",
    ),
    GateBlock(
        "human_channels_declared",
        "At least one human-facing channel exists",
        """# TODO agents need a way to reach people (ADR-0021). `ask` is where an
# approval lands; `notify` is where an alert goes.
# channels:
#   - id: ops_desk
#     channel_class: team_chat
#     description: Where alerts and approvals land for humans.
#     purposes: [ask, notify]
#     human_facing: true
""",
    ),
    GateBlock(
        "memory_namespaces_declared",
        "Long-term memory has somewhere to live",
        """# TODO declare namespaces; memory without a scope cannot be governed
# (ADR-0028). Set `enabled: false` instead if agents should not remember
# across sessions.
# memory:
#   session:
#     retention_minutes: 180
#   long_term:
#     enabled: true
#     retention_days: 365
#     promotion_allowed: true
#     promotion_requires_approval: true
#   namespaces:
#     - id: lessons_learned
#       description: What worked, de-identified.
""",
    ),
    GateBlock(
        "budget_declared",
        "Spend is bounded",
        """# TODO no agent gets unbounded spend. on_breach is throttle | stop |
# notify.
# budgets:
#   - id: company_monthly
#     scope_kind: system
#     scope: <this system's metadata.name>
#     period: monthly
#     limit_usd: 1000
#     on_breach: throttle
""",
    ),
    GateBlock(
        "lifecycle_owner",
        "The lifecycle has an accountable owner",
        """# TODO name who owns promotion and retirement, and gate production
# (ADR-0022). This one block answers both lifecycle checks.
# lifecycle:
#   stage: development
#   owner: <the accountable team>
#   review_cadence_days: 90
#   gates:
#     - to_stage: production
#       requires: [evaluations_passed, owner_assigned]
#       min_pass_rate: 1.0
""",
    ),
    GateBlock(
        "production_gate",
        "A promotion gate guards production",
        """# (covered by the `lifecycle:` block above — a gate lives in `gates:`)
""",
    ),
)

#: The implementation-phase gap is a separate document, so it is templated
#: separately: a binding names the vendor the design deliberately does not.
BINDING_TEMPLATE = """# Binding for '{name}' — where the vendor-neutral design meets a real stack.
# The spec names no vendor; this file does (ADR-0002). Generate with:
#   orgagents compile {name}.system.yaml --binding {name}.binding.yaml \\
#     --target {target}
targets:
  - target: {target}
    runtime:
      adapter: langchain_deepagents
    model:
      provider: anthropic
      model: claude-sonnet-5
    # TODO declare each backing system once, then point capabilities at it
    # by id (ADR-0085).
    # servers:
    #   - {{id: crm, kind: http_api, transport: http, url: "https://crm.internal/mcp"}}
    # capabilities:
    #   - {{capability: customer_lookup, server: crm}}
    # TODO one entry per environment class the spec declares.
    # environments:
    #   - {{environment: reasoning, image: "python:3.12-slim", cpu: "1", memory: 2Gi}}
"""


def _blocks_by_id() -> dict[str, GateBlock]:
    return {b.check_id: b for b in GATE_BLOCKS}


# --------------------------------------------------------------------------
# Catalog-aware blocks
#
# The platform catalog already holds reviewed building blocks — environment
# templates, guardrails, permission sets, MCP servers, models — each with an
# approval status (ADR-0031). A scaffold that printed a generic placeholder
# while the organization's own *approved* shelf sat unused was wasting the
# review that put them there.
#
# Only `approved` entries are ever offered. Scaffolding a `restricted` or
# `proposed` entry would route an author around the very approval the status
# records, which is the same class of mistake as declaring governance nobody
# agreed.
# --------------------------------------------------------------------------


def _approved(entries: Optional[list], kind: str) -> list:
    """Approved entries of one kind, by name. Duck-typed so a test can pass
    simple stand-ins and the CLI can pass real `CatalogEntry` objects."""
    out = []
    for entry in entries or []:
        entry_kind = getattr(getattr(entry, "kind", None), "value",
                             getattr(entry, "kind", ""))
        status = getattr(getattr(entry, "status", None), "value",
                         getattr(entry, "status", ""))
        if entry_kind == kind and status == "approved":
            out.append(entry)
    return sorted(out, key=lambda e: getattr(e, "name", ""))


def _shelf_note(kind: str) -> str:
    return (f"# These are your platform's APPROVED {kind.replace('_', ' ')}s "
            f"—\n# see `orgagents catalogs list --kind {kind}`. Uncomment what "
            "you need.")


def _environments_block(entries: list) -> str:
    lines = ["# TODO declare the isolation classes agents run in (ADR-0009), "
             "then",
             "# reference them per agent with `environments: "
             "[{environment: <id>}]`.",
             _shelf_note("environment_template"),
             "# environments:"]
    for entry in entries:
        attrs = getattr(entry, "attributes", {}) or {}
        lines.append(f"#   - id: {entry.name}")
        lines.append(f"#     description: {entry.summary}")
        if attrs.get("tier"):
            lines.append(f"#     tier: {attrs['tier']}")
        if attrs.get("network"):
            lines.append(f"#     network: {attrs['network']}")
        chains = attrs.get("toolchains") or []
        if chains:
            lines.append(f"#     toolchains: [{', '.join(chains)}]")
    return "\n".join(lines) + "\n"


def _roles_block(entries: list) -> str:
    lines = ["# TODO declare roles, then give each agent `roles: [<id>]`.",
             "# A role is what states an agent's accountability — its "
             "responsibilities in",
             "# plain sentences, and the permissions they justify.",
             _shelf_note("permission_set"),
             "# roles:"]
    for entry in entries:
        attrs = getattr(entry, "attributes", {}) or {}
        safe = entry.name.replace("-", "_")
        lines.append(f"#   - id: {safe}")
        lines.append(f"#     title: {entry.summary.rstrip('.')}")
        lines.append("#     responsibilities:")
        lines.append(f"#       - {entry.summary}")
        perms = attrs.get("permissions") or []
        if perms:
            lines.append("#     permissions:")
            for perm in perms:
                lines.append(
                    f"#       - {{action: {perm.get('action')}, "
                    f"resource_kind: {perm.get('resource_kind')}, "
                    f"resource: {perm.get('resource')}}}")
    return "\n".join(lines) + "\n"


def _guardrails_block(entries: list) -> str:
    lines = ["# RECOMMENDED (a warning, not a gate failure): permissions "
             "decide what",
             "# agents may reach; a guardrail checks what may pass.",
             _shelf_note("guardrail"),
             "# guardrails:"]
    for entry in entries:
        attrs = getattr(entry, "attributes", {}) or {}
        lines.append(f"#   - id: {entry.name.replace('-', '_')}")
        lines.append(f"#     description: {entry.summary}")
        for key in ("applies_to", "checks"):
            if attrs.get(key):
                lines.append(f"#     {key}: [{', '.join(attrs[key])}]")
        if attrs.get("on_violation"):
            lines.append(f"#     on_violation: {attrs['on_violation']}")
    return "\n".join(lines) + "\n"


def catalog_blocks(entries: Optional[list]) -> dict[str, str]:
    """Blocks rebuilt from the approved catalog, keyed by gate check id.

    A kind with no approved entry is absent, and the generic template stands.
    """
    out: dict[str, str] = {}
    envs = _approved(entries, "environment_template")
    if envs:
        out["environments_declared"] = _environments_block(envs)
    perms = _approved(entries, "permission_set")
    if perms:
        out["agents_have_roles"] = _roles_block(perms)
    return out


def recommended_blocks(entries: Optional[list]) -> list[tuple[str, str]]:
    """(title, body) for blocks worth offering that no gate check demands."""
    rails = _approved(entries, "guardrail")
    return [("Guardrails (recommended)", _guardrails_block(rails))] if rails else []


def starter_spec(name: str, *, owner: str = "", leader: str = "lead",
                 catalog: Optional[list] = None) -> str:
    """A design that is valid the moment it is written, plus the gate's
    remaining work as commented blocks.

    Valid-now matters: an author can `validate` and `compile` immediately and
    see something real, then work the commented list to pass the gate. The
    alternative — a template that does not load — teaches nothing.
    """
    owner_line = f"  owner: {owner}\n" if owner else (
        "  # TODO owner: <the team accountable for this organization>\n")
    head = f"""# {name} — an agentic organization.
#
# This design is VALID as written: `orgagents spec validate` passes and it
# compiles for the `local` target. It does not yet pass the phase gate, which
# is the point — run:
#
#     orgagents phase {name}.system.yaml --target local
#
# to see what is still missing, and uncomment the blocks below as your
# organization actually decides them. Nothing here is declared on your behalf:
# a budget or a data class the business has not agreed is a lie the gate
# exists to catch.

metadata:
  name: {name}
  spec_version: "1.4.0"
  version: "0.1.0"
{owner_line}
organization:
  id: {name}
  name: {name}
  # The org leader's mandate is never silent: name the decisions it may take,
  # or `[]` to say it decides none (ADR-0071).
  leader: {leader}
  mandate:
    decisions: []
  members:
    - id: {leader}
      name: {leader}
      description: Leads this organization.
      mandate:
        decisions: []
      # TODO roles: [team_member]
      # TODO humans: [{{person: p_owner, roles: [owner]}}]
      # TODO environments: [{{environment: reasoning}}]

# ---------------------------------------------------------------------------
# The phase gate's remaining checks, in the order worth working them.
# Each block below answers one check. Uncomment and fill what is true.
# ---------------------------------------------------------------------------

"""
    from_catalog = catalog_blocks(catalog)
    parts = [head]
    for block in GATE_BLOCKS:
        if block.check_id == "production_gate":
            continue                      # folded into the lifecycle block
        parts.append(f"# ---- {block.title} " + "-" * max(
            0, 58 - len(block.title)) + "\n")
        parts.append(from_catalog.get(block.check_id, block.body))
        parts.append("\n")
    for title, body in recommended_blocks(catalog):
        parts.append(f"# ---- {title} " + "-" * max(0, 58 - len(title)) + "\n")
        parts.append(body)
        parts.append("\n")
    return "".join(parts)


def scaffold_for(failures: list, *, name: str = "system",
                 target: Optional[str] = None,
                 catalog: Optional[list] = None) -> str:
    """The YAML blocks that answer the checks this design still fails.

    `failures` is the phase report's failing checks. A failure with no block
    (a check about something only the author can decide, like a separation of
    duties) is listed as a note rather than invented.
    """
    blocks = _blocks_by_id()
    from_catalog = catalog_blocks(catalog)
    lines: list[str] = [
        f"# Scaffold for '{name}' — the blocks the phase gate is still asking",
        "# for. Uncomment and fill what is true for your organization; nothing",
        "# is declared on your behalf.",
        "",
    ]
    seen: set[str] = set()
    unhandled: list[str] = []
    for check in failures:
        cid = getattr(check, "id", "")
        if cid in seen:
            continue
        seen.add(cid)
        if cid == "binding_exists":
            continue                      # a separate document; see below
        block = blocks.get(cid)
        if block is None:
            unhandled.append(
                f"#   - {getattr(check, 'title', cid)}: "
                f"{getattr(check, 'fix', '') or 'decide and declare it'}")
            continue
        if block.check_id == "production_gate" and "lifecycle_owner" in seen:
            continue
        lines.append(f"# ---- {block.title} " + "-" * max(
            0, 58 - len(block.title)))
        lines.append(from_catalog.get(block.check_id, block.body).rstrip("\n"))
        lines.append("")

    if unhandled:
        lines.append("# ---- Only you can decide these " + "-" * 32)
        lines.append("# No template can write these honestly:")
        lines.extend(unhandled)
        lines.append("")

    if any(getattr(c, "id", "") == "binding_exists" for c in failures):
        lines.append("# ---- A binding for the target " + "-" * 33)
        lines.append(f"# Write this to {name}.binding.yaml (a separate file):")
        for line in binding_template(name, target or "local",
                                     catalog=catalog).splitlines():
            # The template is a real file elsewhere, so it carries its own
            # comments; only its YAML needs commenting out here.
            if not line:
                lines.append("#")
            else:
                lines.append(line if line.lstrip().startswith("#")
                             else f"# {line}")
        lines.append("")
    return "\n".join(lines) + "\n"


def binding_template(name: str, target: str = "local", *,
                     catalog: Optional[list] = None) -> str:
    """The binding a target still needs, naming the approved shelf where there
    is one. Models and MCP servers are implementation facts, so they belong
    here and never in the spec (ADR-0002)."""
    text = BINDING_TEMPLATE.format(name=name, target=target)
    models = _approved(catalog, "model")
    servers = _approved(catalog, "mcp_server")
    if not models and not servers:
        return text
    extra = ["", "# ---- From your approved catalog " + "-" * 31]
    if models:
        extra.append("# Approved models (`orgagents catalogs models`):")
        for entry in models:
            attrs = getattr(entry, "attributes", {}) or {}
            provider = attrs.get("provider", "")
            extra.append(f"#   {entry.name}"
                         + (f"  (provider: {provider})" if provider else "")
                         + f" — {entry.summary}")
    if servers:
        extra.append("# Approved MCP servers — declare each once under"
                     " `servers:` (ADR-0085):")
        for entry in servers:
            attrs = getattr(entry, "attributes", {}) or {}
            bits = [f"id: {entry.name.replace('-', '_')}", "kind: mcp"]
            if attrs.get("transport"):
                bits.append(f"transport: {attrs['transport']}")
            if attrs.get("command"):
                bits.append(f"command: {attrs['command']}")
            if attrs.get("engine"):
                bits.append(f"engine: {attrs['engine']}")
            extra.append(f"#   - {{{', '.join(bits)}}}  # {entry.summary}")
    return text + "\n".join(extra) + "\n"
