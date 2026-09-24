"""The designer as an MCP server: `orgagents mcp designer` (ADR-0115).

An assistant connected here is a designer *user*, with that user's role in
each workspace and nothing more. It reads designs, validates them, explains
issue codes, reads the UML profile, applies model operations — the same
operations a canvas gesture sends (ADR-0102, ADR-0103) — saves with
optimistic concurrency, compares revisions, previews a compile and asks for a
publish. Every one of those is an `/api/v1` call under the caller's identity,
so a viewer's assistant cannot save and an editor's cannot publish, for the
same reason the viewer and the editor cannot.

What it deliberately does not offer: workspace membership, settings, lock
breaking, restore, deletion, and reading the audit log. Those are
administrative acts on other people's access or work; an assistant that could
take them on a prompt's say-so would be a larger blast radius than the
convenience is worth. They remain in the UI and on the REST API.
"""
from __future__ import annotations

import json
from typing import Any, Literal, Optional, Union

import yaml
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from ..client import DesignerClient
from ._common import Backend, ServerConfig, call, caller

INSTRUCTIONS = """\
Tools over the Agentic Designer, acting as one designer user with that user's
workspace roles (RBAC is enforced by the designer, not by you).

A design (a "system") is a UML-profile model of an organisation of AI agents.
Typical loop: list_designs -> get_design -> validate_design -> explain_issue
for each finding -> apply_model_operation (save=true with expected_version) ->
validate_design again -> compile_preview. Never guess an operation's shape:
call list_profile / describe_stereotype first. A 403 is the user's role
talking; say so rather than retrying. request_publish asks the fabric to run a
stored revision; it needs system.publish and deploys nothing by itself.
"""

OPERATION_DOC = """\
One model operation, as the canvas sends it (ADR-0102):
  {"op": "create", "kind": "agent", "id": "billing", "owner": "<team id>", "attrs": {...}}
  {"op": "update", "kind": "agent", "id": "billing", "attrs": {"name": "Billing"}}
  {"op": "delete", "kind": "agent", "id": "billing"}
  {"op": "link", "source": {"kind": "team", "id": "t"}, "target": {"kind": "agent", "id": "a"},
   "relationship": "<field>", "attrs": {...}}
  {"op": "unlink", "source": {...}, "target": {...}, "relationship": "<field>"}
  {"op": "set_leader", "team": "<team id>", "agent": "<agent id>"}
Kinds and relationship fields are in the UML profile (list_profile, describe_stereotype)."""


def _spec_arg(spec: Union[str, dict, None]) -> Optional[dict[str, Any]]:
    """A spec given as a mapping, or as YAML/JSON text."""
    if spec is None or isinstance(spec, dict):
        return spec
    loaded = yaml.safe_load(spec)
    if not isinstance(loaded, dict):
        raise ToolError("spec must be a mapping (YAML or JSON object)")
    return loaded


def _brief_validation(v: dict[str, Any]) -> dict[str, Any]:
    findings = v.get("findings") or []
    return {
        "ok": v.get("ok", False),
        "errors": sum(1 for f in findings if f.get("severity") == "error"),
        "warnings": sum(1 for f in findings if f.get("severity") == "warning"),
        "findings": [
            {k: f.get(k) for k in ("issue_id", "code", "severity", "where",
                                   "component", "title", "message") if f.get(k)}
            for f in findings
        ],
    }


def build_designer_server(config: ServerConfig,
                          backend: Optional[Backend] = None) -> FastMCP:
    backend = backend or Backend(config)
    server = FastMCP("orgagents-designer", instructions=INSTRUCTIONS,
                     streamable_http_path="/mcp")

    def dc(ctx: Context) -> DesignerClient:
        return backend.client(DesignerClient, caller(ctx, config))

    # -- reading ----------------------------------------------------------

    @server.tool()
    async def whoami(ctx: Context) -> dict:
        """Who this server acts as, and the role and permissions held in each
        workspace. Start here when a call is refused."""
        client = dc(ctx)
        return await call(client.whoami)

    @server.tool()
    async def list_workspaces(ctx: Context) -> list[dict]:
        """The workspaces this user belongs to, with their members' roles."""
        client = dc(ctx)
        spaces = await call(client.list_workspaces)
        return [{"id": w["id"], "name": w["name"],
                 "description": w.get("description", ""),
                 "members": [{"user_id": m["user_id"], "role": m["role"]}
                             for m in w.get("members", [])]} for w in spaces]

    @server.tool()
    async def list_designs(ctx: Context,
                           workspace_id: Optional[str] = None) -> list[dict]:
        """Designs visible to this user, optionally in one workspace: id,
        name, status, current version and active locks."""
        client = dc(ctx)
        rows = await call(lambda: client.list_designs(workspace_id))
        return [{k: r.get(k) for k in ("id", "workspace_id", "name", "status",
                                       "version", "updated_by", "updated_at",
                                       "locks") if k in r} for r in rows]

    @server.tool()
    async def get_design(ctx: Context, design_id: str,
                         format: Literal["yaml", "json"] = "yaml") -> str:
        """The stored design: its spec (the model), binding, version, and the
        caller's role. `format` chooses YAML (readable) or JSON."""
        client = dc(ctx)
        opened = await call(lambda: client.get_design(design_id))
        record = opened["record"]
        doc = {"id": record["id"], "name": record["name"],
               "workspace_id": record["workspace_id"],
               "version": record["version"], "status": record.get("status"),
               "your_role": opened.get("role"),
               "spec": record["spec"], "binding": record.get("binding")}
        if format == "json":
            return json.dumps(doc, indent=2)
        return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)

    @server.tool()
    async def validate_design(ctx: Context, design_id: str) -> dict:
        """Run the validator over the stored design. Each finding carries its
        catalogued issue id (e.g. OA-1201); explain_issue explains it."""
        client = dc(ctx)
        return _brief_validation(await call(lambda: client.validate(design_id)))

    @server.tool()
    async def explain_issue(ctx: Context, code: str) -> dict:
        """What an issue code means, why it matters and how to fix it — by
        number (OA-1201) or name (team_without_leader)."""
        client = dc(ctx)
        return await call(lambda: client.issue_code(code))

    @server.tool()
    async def list_profile(ctx: Context) -> dict:
        """The UML profile the model is built on (ADR-0101, ADR-0112): every
        stereotype (a kind you can create) with the metaclass it extends, and
        the relationship kinds. Use describe_stereotype for one kind's detail."""
        client = dc(ctx)
        mm = await call(client.metamodel)
        return {
            "profile": mm.get("profile"),
            "stereotypes": [{"kind": s["kind"], "name": s["name"],
                             "extends": s.get("extends"),
                             "doc": s.get("doc", "")}
                            for s in mm.get("stereotypes", [])],
            "relationship_kinds": mm.get("relationship_kinds", []),
            "enumerations": [e["name"] for e in mm.get("enumerations", [])],
        }

    @server.tool()
    async def describe_stereotype(ctx: Context, kind: str) -> dict:
        """One stereotype in full: what it extends, its properties, and every
        relationship it can be the source or target of — with the spec field
        (`relationship` in a link operation) and multiplicities."""
        client = dc(ctx)
        mm = await call(client.metamodel)
        wanted = kind.strip("«»").lower()
        match = next((s for s in mm.get("stereotypes", [])
                      if s["kind"].lower() == wanted
                      or s["name"].strip("«»").lower() == wanted), None)
        if match is None:
            raise ToolError(f"no stereotype {kind!r}; list_profile names them all")
        k = match["kind"]
        return {
            **match,
            "properties": [p for p in mm.get("properties", []) if p.get("owner") == k],
            "outgoing": [r for r in mm.get("relationships", []) if r.get("source") == k],
            "incoming": [r for r in mm.get("relationships", []) if r.get("target") == k],
            "specialisations": [s["kind"] for s in mm.get("stereotypes", [])
                                if s.get("extends", "").lower() == k],
        }

    @server.tool()
    async def diff_versions(ctx: Context, design_id: str,
                            from_version: Optional[int] = None,
                            to_version: Optional[int] = None) -> dict:
        """What changed between two stored revisions, ranked by consequence.
        Defaults to the current version against the one before it."""
        client = dc(ctx)
        return await call(lambda: client.diff(design_id, from_version, to_version))

    @server.tool()
    async def compile_preview(ctx: Context, design_id: str,
                              target: str = "local") -> dict:
        """Validate and compile the stored design for a target — `local`,
        `langgraph`, `adk`, `maf`, `terraform:aws|azure|gcp` — into a
        directory that is thrown away. Returns the verdict, refusals and the
        list of files the compile would write. Deploys nothing."""
        client = dc(ctx)
        verdict = await call(lambda: client.preflight(design_id, target))
        files = verdict.get("files") or []
        return {**{k: v for k, v in verdict.items() if k != "files"},
                "file_count": len(files), "files": files}

    # -- changing ---------------------------------------------------------

    @server.tool(description=(
        "Apply one model operation to a design — the same operation a canvas "
        "gesture sends. With save=true the result is saved as a new revision "
        "(needs system.edit), guarded by expected_version: if someone saved "
        "since, the save is refused or merged, never silently overwritten. "
        "With save=false, nothing is stored and the resulting spec is "
        "returned so operations can be chained on the draft via `spec`.\n\n"
        + OPERATION_DOC))
    async def apply_model_operation(ctx: Context, design_id: str,
                                    operation: dict,
                                    save: bool = False,
                                    expected_version: Optional[int] = None,
                                    spec: Union[dict, str, None] = None,
                                    message: str = "") -> dict:
        client = dc(ctx)
        opened = await call(lambda: client.get_design(design_id))
        stored = opened["record"]
        draft = _spec_arg(spec) or stored["spec"]
        result = await call(lambda: client.apply_operation(draft, operation))
        answer: dict[str, Any] = {k: result.get(k) for k in
                                  ("accepted", "effects", "violations", "incomplete")}
        if not result.get("accepted"):
            answer["saved"] = False
            return answer
        if not save:
            answer["saved"] = False
            answer["spec"] = result.get("spec")
            return answer
        base = expected_version if expected_version is not None else stored["version"]
        outcome = await call(lambda: client.save_design(
            design_id, spec=result["spec"], base_version=base,
            message=message or f"mcp: {operation.get('op', 'operation')}"))
        answer["saved"] = outcome.get("status") in ("saved", "merged")
        answer["save"] = _save_view(outcome)
        return answer

    @server.tool()
    async def save_design(ctx: Context, design_id: str,
                          spec: Union[dict, str], expected_version: int,
                          message: str = "") -> dict:
        """Save a whole spec (YAML/JSON text or a mapping) as a new revision.
        `expected_version` is the version you read (get_design): a save based
        on an older version is merged or refused with the conflicts, never
        overwrites someone else's work. Needs system.edit."""
        client = dc(ctx)
        outcome = await call(lambda: client.save_design(
            design_id, spec=_spec_arg(spec), base_version=expected_version,
            message=message or "mcp: save"))
        return _save_view(outcome)

    @server.tool()
    async def request_publish(ctx: Context, design_id: str, tenant_id: str,
                              target: str = "local") -> dict:
        """Ask the fabric to run the stored design for a tenant. Needs
        system.publish (admin/owner). The design is preflighted; a refusal
        comes back with the gate's findings. On success a deployment exists in
        `requested` — the fabric builds it under its own authority."""
        client = dc(ctx)
        return await call(lambda: client.request_publish(design_id, tenant_id, target))

    # -- resources ----------------------------------------------------------

    @server.resource("orgagents://issues", mime_type="application/json",
                     description="Every validator issue code, in number order.")
    async def issue_catalog() -> str:
        from ..spec.issue_codes import catalog
        return json.dumps(sorted(catalog().values(), key=lambda e: e["number"]),
                          indent=2)

    @server.resource("orgagents://issues/{code}", mime_type="application/json",
                     description="One issue code's explanation.")
    async def issue_entry(code: str) -> str:
        from ..spec.issue_codes import catalog, lookup
        entry = next((e for e in catalog().values() if e["id"] == code.upper()),
                     None) or lookup(code)
        if entry is None:
            raise ValueError(f"no issue code {code!r}")
        return json.dumps(entry, indent=2)

    @server.resource("orgagents://profile", mime_type="application/json",
                     description="The UML profile (ADR-0101): stereotypes, "
                                 "relationships, enumerations, properties.")
    async def profile() -> str:
        from ..metamodel import describe
        return json.dumps(describe(), indent=2, ensure_ascii=False)

    @server.resource("orgagents://designs/{design_id}", mime_type="application/yaml",
                     description="A stored design's spec, as YAML, read as "
                                 "this server's user.")
    async def design_document(design_id: str, ctx: Context) -> str:
        client = dc(ctx)
        opened = await call(lambda: client.get_design(design_id))
        return yaml.safe_dump(opened["record"]["spec"], sort_keys=False,
                              allow_unicode=True)

    # -- prompts ----------------------------------------------------------

    @server.prompt(description="Review a design's authority: mandates, "
                               "separations of duty, escalations.")
    def review_authority(design_id: str) -> str:
        return (
            f"Review the authority of design {design_id}.\n"
            "1. get_design to read it; validate_design and keep the findings "
            "whose section is about authority (mandates, separation, "
            "escalation, autonomy) — explain_issue each one.\n"
            "2. For every team and agent, state what it may decide (its "
            "mandate), who it escalates to, and any decision two separated "
            "principals both hold.\n"
            "3. Point out authority that is wider than the work needs, "
            "decisions nobody holds, and escalation chains that end nowhere.\n"
            "4. Propose fixes as model operations (do not save them) and say "
            "which issue each resolves. Only save if I ask."
        )

    @server.prompt(description="Add an agent to a team, the way the model "
                               "says it is done.")
    def add_agent_to_team(design_id: str, team_id: str, agent_id: str,
                          purpose: str = "") -> str:
        return (
            f"Add agent '{agent_id}' to team '{team_id}' in design {design_id}"
            + (f" — its purpose: {purpose}" if purpose else "") + ".\n"
            "1. describe_stereotype('agent') and describe_stereotype('team') to "
            "see the required properties and the membership relationship.\n"
            "2. get_design for the current version.\n"
            "3. apply_model_operation with op=create, kind=agent, owner="
            f"'{team_id}' and the attributes the profile requires; check "
            "`accepted` and the violations.\n"
            "4. validate_design; fix what the new agent introduced.\n"
            "5. Save with save=true and the expected_version you read, then "
            "compile_preview for `local` and report the result."
        )

    return server


def _save_view(outcome: dict[str, Any]) -> dict[str, Any]:
    record = outcome.get("record") or {}
    return {"status": outcome.get("status"), "message": outcome.get("message", ""),
            "version": record.get("version", outcome.get("current_version")),
            "base_version": outcome.get("base_version"),
            "conflicts": outcome.get("conflicts", [])}
