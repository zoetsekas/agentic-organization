"""The UML profile the canvas is a view of: layout, metamodel, palette."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from .context import ApiContext
from .palette import LINK_RULES, palette_tree


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router

    @router.post("/api/designer/layout")
    def designer_layout(body: dict = Body(...)) -> dict:
        """Place a set of nodes, by algorithm or by what the diagram is.

        Server-side because a layout is a function from a graph to coordinates,
        which makes "no two nodes overlap" and "a child sits below its parent"
        assertions rather than opinions (ADR-0100). A layout that ran only in
        the canvas would be the one part of this platform nothing checked.
        """
        from ..designer.layout import LayoutEdge, LayoutNode, arrange

        nodes = [
            LayoutNode(id=str(n["id"]), parent=n.get("parent") or None,
                       label=str(n.get("label") or ""),
                       lane=str(n.get("lane") or ""))
            for n in body.get("nodes", []) if n.get("id")
        ]
        edges = [
            LayoutEdge(source=str(e["source"]), target=str(e["target"]))
            for e in body.get("edges", [])
            if e.get("source") and e.get("target")
        ]
        try:
            result = arrange(nodes, edges,
                             algorithm=str(body.get("algorithm") or ""),
                             kind=str(body.get("kind") or "organisation"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "algorithm": result.algorithm,
            "positions": result.positions,
            "notes": result.notes,
            "lanes": result.lanes,
            "width": result.width,
            "height": result.height,
        }

    @router.get("/api/designer/metamodel")
    def designer_metamodel() -> dict:
        """The UML profile the designer is built on (ADR-0101): every kind as a
        stereotype of a UML metaclass, and every relationship as a UML kind
        with multiplicities and the spec field it lives in."""
        from ..metamodel import describe

        return describe()

    @router.get("/api/designer/palette")
    def designer_palette() -> dict:
        """What the canvas can place, the fields each kind needs, and what
        may be linked to what.

        The link rules travel with the palette because they are the same kind
        of fact: a canvas that decided for itself which components connect
        could draw a relationship the spec has no field for.
        """
        from ..metamodel import palette_profiles

        return {"groups": palette_tree(), "links": LINK_RULES,
                "profiles": palette_profiles()}


    return router
