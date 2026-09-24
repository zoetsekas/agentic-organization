"""The API's routes, one router per area; `orgagents.api.create_app` wires them.

Each module's `build(ctx)` registers its area's routes, closing over the
shared `ApiContext` (services, the authenticator, the authorisation helpers).
They register on the app's own `APIRouter` (`ctx.app.router`) rather than on
one of their own passed to `include_router`: FastAPI 0.141 includes a router
lazily, as one opaque entry in `app.routes`, and `api_contract.apply_contract`
must see every `APIRoute` to tag it and add its `/api/v1` twin (ADR-0115).
Registering on the app's router is what `@app.get` always did, so each route
is the object it was before the split.
`RUNTIME_ROUTERS` and `DESIGN_ROUTERS` are the order they are included in,
which is the order the routes were always registered in.

=====================  ==================================================
module                 paths
=====================  ==================================================
``org``                /api/org/...
``agents``             /api/agents...
``sessions``           /api/sessions..., /sessions/{id}
``marketplace``        /api/catalog...
``components``         /api/components...
``ops``                /api/ops/...
``catalogs``           /api/catalogs...
``designer``           /api/designer/ issue codes, identity, settings,
                       workspaces, designs, examples, locks, revisions, audit
``designer_review``    /api/designer/systems/{id}/ gate, workflow engines,
                       authority, placements, preflight, publish, diff
``designer_model``     /api/designer/ layout, metamodel, palette
``fabric``             /api/fabric/...
``ui``                 /ui, /command, /
=====================  ==================================================
"""
from __future__ import annotations

from . import (
    agents,
    catalogs,
    components,
    designer,
    designer_model,
    designer_review,
    fabric,
    marketplace,
    ops,
    org,
    sessions,
)
from .context import ApiContext

#: Runtime routers, then (after `/healthz`) the catalog, designer and fabric.
RUNTIME_ROUTERS = (org, agents, sessions, marketplace, components, ops)
DESIGN_ROUTERS = (catalogs, designer, designer_review, designer_model, fabric)

__all__ = ["ApiContext", "RUNTIME_ROUTERS", "DESIGN_ROUTERS"]
