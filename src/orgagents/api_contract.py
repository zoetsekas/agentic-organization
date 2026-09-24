"""The public shape of the HTTP API: tags, a versioned prefix, the schema file.

Why a pass over a built app rather than `tags=` on every decorator
(ADR-0115): the ~100 routes in `api.py` are grouped by path prefix already,
and that prefix *is* the grouping a reader of `/docs` wants. Stating it once,
here, keeps the table of groups in one place where a new route falls into the
right group without anybody remembering to tag it — and a route that falls
into no group fails `tests/test_api_contract.py` instead of drifting into an
"untagged" heap.

Versioning. Every `/api/...` route is also served at `/api/v1/...`, the same
endpoint function with the same dependencies, so the two can never disagree
about auth, RBAC or validation. `/api/v1` is the public contract and is what
the OpenAPI document describes; the unversioned paths are the bundled UI's
and stay out of the schema. A breaking change to v1 means a `/api/v2` prefix
beside it, not an edit in place.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.routing import APIRoute

API_VERSION = "v1"
PUBLIC_PREFIX = f"/api/{API_VERSION}"

#: The committed schema. `orgagents api schema` writes it; a test fails when it
#: no longer matches the code, so a client generated from it is never stale.
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"

# (tag, description, path pattern). First match wins, so the narrow designer
# groups come before the broad `/api/designer` one. Patterns are matched
# against the *unversioned* path.
_GROUPS: list[tuple[str, str, str]] = [
    ("designer: identity",
     "Who the caller is and what they may do in each workspace (ADR-0032, "
     "ADR-0047). Identity comes from the configured auth mode, never from a "
     "client-sent role.",
     r"^/api/designer/whoami$"),
    ("designer: settings",
     "Installation-wide designer settings; `designer.settings` permission.",
     r"^/api/designer/settings$"),
    ("designer: workspaces",
     "Workspaces and their members. A workspace is the RBAC scope for every "
     "design in it (ADR-0032, ADR-0106).",
     r"^/api/designer/(workspaces|import)"),
    ("designer: review and publish",
     "Read-only review surfaces over stored revisions — the phase gate, "
     "authority, placements, workflow engines, the IR diff, preflight — and "
     "the publish *request*, which the fabric acts on under its own "
     "authority (ADR-0049, ADR-0050, ADR-0108).",
     r"^/api/designer/(systems/[^/]+/(gate|authority|placements|"
     r"workflow-engines|preflight|publish|diff)|audit)"),
    ("designer: designs",
     "Designs (stored as `systems`): create, open with validation, save with "
     "optimistic concurrency (`base_version`), locks, revisions and restore "
     "(ADR-0031, ADR-0033).",
     r"^/api/designer/(systems|examples)"),
    ("designer: model",
     "The UML profile and the model operations the canvas is a view of "
     "(ADR-0101, ADR-0102, ADR-0103): metamodel, palette, gestures, one "
     "operation on a draft, layout, and the issue-code catalog.",
     r"^/api/designer/(metamodel|palette|gestures|operations|layout|issue-codes)"),
    ("fabric",
     "The command centre: tenants, deployments, health, drift, quotas, common "
     "services, operator actions and the operator audit log (ADR-0051).",
     r"^/api/fabric/"),
    ("platform catalog",
     "The governed catalog of building blocks: search, publish, review, "
     "entitle, retire (ADR-0041, ADR-0062).",
     r"^/api/catalogs"),
    ("marketplace",
     "The marketplace of skills, plugins and workflows agents install.",
     r"^/api/catalog(/|$)"),
    ("components",
     "What the designer's palette can place: runtimes, sandboxes, workflows, "
     "channels, data planes, infrastructure options.",
     r"^/api/components"),
    ("organisation",
     "Org units and the hierarchy tree of the running platform.",
     r"^/api/org/"),
    ("agents",
     "Agents on the running platform: CRUD, harness preview, runs.",
     r"^/api/agents"),
    ("sessions",
     "Sessions, their events and traces, and resuming a paused session.",
     r"^/api/sessions"),
    ("operations",
     "Metrics, alerts and per-agent health.",
     r"^/api/ops/"),
    ("service",
     "Liveness and human-facing redirects. Unversioned; not part of the "
     "public contract.",
     r"^/(healthz|sessions/|$)"),
]

TAGS_METADATA: list[dict[str, str]] = [
    {"name": tag, "description": desc} for tag, desc, _ in _GROUPS
]

_COMPILED = [(tag, re.compile(pattern)) for tag, _, pattern in _GROUPS]


def tag_for(path: str) -> Optional[str]:
    """The group a path belongs to, or None if the table has no row for it."""
    if path.startswith(PUBLIC_PREFIX + "/"):
        path = "/api/" + path[len(PUBLIC_PREFIX) + 1:]
    for tag, pattern in _COMPILED:
        if pattern.search(path):
            return tag
    return None


def _summary(route: APIRoute) -> str:
    """A route's first docstring line, else its name read as words."""
    doc = (route.endpoint.__doc__ or "").strip()
    if doc:
        first = " ".join(doc.split("\n\n", 1)[0].split())
        # A summary is a label in a list, not a paragraph.
        return first if len(first) <= 90 else first[:87].rstrip() + "..."
    return route.name.replace("_", " ").capitalize()


def apply_contract(app: FastAPI) -> FastAPI:
    """Tag every route, add the `/api/v1` aliases, and describe the API.

    Idempotent: a second call finds the aliases already there and adds none.
    """
    app.openapi_tags = TAGS_METADATA
    app.description = (
        "The Agentic Designer platform API. **`/api/v1` is the public, "
        "versioned contract** (ADR-0115); the unversioned `/api/...` paths "
        "serve the bundled UI and are not described here. Identity follows "
        "the designer's auth mode (`none`, `trusted_proxy`, `oidc` — "
        "ADR-0047, ADR-0114); every designer route goes through the same "
        "service layer and RBAC whichever client calls it (ADR-0018). "
        "A Python client is `orgagents.client`; the MCP servers are "
        "`orgagents mcp designer|runtime`. See docs/DEVELOPERS.md."
    )
    existing = {(r.path, frozenset(r.methods or ())) for r in app.routes
                if isinstance(r, APIRoute)}
    for route in list(app.routes):
        if not isinstance(route, APIRoute):
            continue
        tag = tag_for(route.path)
        if tag:
            route.tags = [tag]
        if not route.summary:
            route.summary = _summary(route)
        # Python 3.13 started dedenting docstrings at compile time, so the
        # same route described itself differently on 3.11 and 3.14 and the
        # committed schema could never match both. cleandoc gives one answer.
        if route.description:
            route.description = inspect.cleandoc(route.description)
        if not route.path.startswith("/api/"):
            # Redirects for people (`/`, `/sessions/{id}`) are not API;
            # `/healthz` is, for probes, and stays unversioned.
            route.include_in_schema = route.path == "/healthz"
            continue
        if route.path.startswith(PUBLIC_PREFIX):
            continue
        versioned = PUBLIC_PREFIX + route.path[len("/api"):]
        methods = frozenset(route.methods or ())
        # The UI's alias stays callable but leaves the schema: one contract
        # is documented, not two copies of it.
        route.include_in_schema = False
        if (versioned, methods) in existing:
            continue
        app.router.add_api_route(
            versioned, route.endpoint,
            methods=list(methods),
            response_model=route.response_model,
            status_code=route.status_code,
            tags=route.tags,
            dependencies=route.dependencies,
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            responses=route.responses,
            deprecated=route.deprecated,
            name=f"{route.name}_{API_VERSION}",
            response_class=route.response_class,
            include_in_schema=True,
        )
        existing.add((versioned, methods))
    app.openapi_schema = None          # rebuild on next request
    return app


def schema_text(app: Optional[FastAPI] = None) -> str:
    """The OpenAPI document as committed: stable key order, trailing newline."""
    if app is None:
        import os
        import tempfile

        from .api import create_app
        # ignore_cleanup_errors: on Windows the store's SQLite file is still
        # open when the directory goes, and a leftover temp file is harmless.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            # The schema does not depend on data; a throwaway store keeps the
            # export from touching anybody's orgagents.db.
            saved = os.environ.get("ORGAGENTS_DESIGNER_PATH")
            os.environ["ORGAGENTS_DESIGNER_PATH"] = tmp
            try:
                app = create_app(str(Path(tmp) / "schema.db"))
                schema: dict[str, Any] = app.openapi()
            finally:
                if saved is None:
                    os.environ.pop("ORGAGENTS_DESIGNER_PATH", None)
                else:
                    os.environ["ORGAGENTS_DESIGNER_PATH"] = saved
    else:
        schema = app.openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_schema(path: Path = SCHEMA_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schema_text(), encoding="utf-8", newline="\n")
    return path
