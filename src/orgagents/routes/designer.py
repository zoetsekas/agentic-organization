"""The designer: issue codes, identity, settings, workspaces, designs, locks, revisions, audit."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from ..designer import Layout, Member, Principal, SystemStatus, UserRole
from .context import ApiContext
from .designer_review import _designer_spec
from .models import (
    CreateSystemRequest,
    LockRequest,
    MemberRequest,
    SaveSystemRequest,
    WorkspaceRequest,
)


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router
    designer = ctx.designer
    principal = ctx.principal
    _guard = ctx.guard

    @router.get("/api/designer/issue-codes")
    def issue_codes() -> Any:
        """Every numbered issue code with its explanation, in number order."""
        from ..spec.issue_codes import catalog
        return sorted(catalog().values(), key=lambda e: e["number"])

    @router.get("/api/designer/issue-codes/{code}")
    def issue_code(code: str) -> Any:
        """One code's explanation — by name (`possible_escalation`) or number
        (`OA-1401`)."""
        from ..spec.issue_codes import catalog, lookup
        entry = next((e for e in catalog().values() if e["id"] == code.upper()),
                     None) or lookup(code)
        if entry is None:
            raise HTTPException(404, f"no issue code {code!r}")
        return entry

    @router.get("/api/designer/whoami")
    def designer_whoami(user: Principal = Depends(principal)) -> dict:
        return designer.whoami(user)

    @router.get("/api/designer/settings")
    def designer_get_settings() -> dict:
        return designer.settings.model_dump(mode="json")

    @router.put("/api/designer/settings")
    def designer_update_settings(changes: dict[str, Any],
                                 user: Principal = Depends(principal)) -> dict:
        return _guard(designer.update_settings, user, changes).model_dump(mode="json")

    @router.get("/api/designer/workspaces")
    def designer_workspaces(user: Principal = Depends(principal)) -> list[dict]:
        return [w.model_dump(mode="json") for w in designer.workspaces(user)]

    @router.post("/api/designer/workspaces")
    def designer_create_workspace(req: WorkspaceRequest,
                                  user: Principal = Depends(principal)) -> dict:
        return _guard(designer.create_workspace, user, req.name,
                      req.description).model_dump(mode="json")

    @router.put("/api/designer/workspaces/{workspace_id}")
    def designer_update_workspace(workspace_id: str, req: dict[str, Any],
                                  user: Principal = Depends(principal)) -> dict:
        return _guard(designer.update_workspace, user, workspace_id,
                      name=req.get("name"),
                      description=req.get("description")).model_dump(mode="json")

    @router.delete("/api/designer/workspaces/{workspace_id}")
    def designer_delete_workspace(workspace_id: str, cascade: bool = False,
                                  user: Principal = Depends(principal)) -> dict:
        return _guard(designer.delete_workspace, user, workspace_id,
                      cascade=cascade)

    @router.post("/api/designer/import")
    def designer_import(req: dict[str, Any],
                        user: Principal = Depends(principal)) -> dict:
        """A design from a file on the author's disk (ADR-0106)."""
        if not req.get("workspace_id"):
            raise HTTPException(422, {"message": "choose a workspace",
                                      "fields": {"workspace": "Choose a "
                                                 "workspace to import into."}})
        record = _guard(designer.import_system, user,
                        workspace_id=req["workspace_id"],
                        text=req.get("text") or "",
                        filename=req.get("filename") or "",
                        name=req.get("name") or "")
        return {"system_id": record.id, "name": record.name,
                "version": record.version}

    @router.get("/api/designer/systems/{system_id}/export")
    def designer_export(system_id: str, format: str = "yaml",
                        part: str = "spec", version: Optional[int] = None,
                        typed: bool = True,
                        user: Principal = Depends(principal)):
        """A design as YAML or JSON in which every element names its UML
        type (ADR-0113), as a file to save. `part=binding` exports the
        binding it was saved with; `version` an earlier revision."""
        from fastapi.responses import Response

        from ..spec import exchange
        from ..spec.binding import Binding

        if format not in exchange.FORMATS:
            raise HTTPException(422, f"format must be one of "
                                     f"{', '.join(exchange.FORMATS)}")
        if part not in ("spec", "binding"):
            raise HTTPException(422, "part must be spec or binding")
        raw, binding, at = _guard(designer.spec_at, user, system_id, version)
        if part == "binding":
            if not binding:
                raise HTTPException(404, "this design has no binding")
            doc = binding if "targets" in binding else {
                "spec": (raw.get("metadata") or {}).get("name", ""),
                "targets": [binding]}
            try:
                obj = Binding.model_validate(
                    exchange.strip_types(doc, Binding))
            except Exception as e:
                raise HTTPException(
                    422, f"this binding does not load: {e}") from e
        else:
            obj = _designer_spec(raw)
        text_out = exchange.dump(obj, format, typed=typed)
        stem = (raw.get("metadata") or {}).get("name") or system_id
        suffix = "system" if part == "spec" else "binding"
        media = "application/json" if format == "json" else "application/yaml"
        return Response(
            text_out, media_type=f"{media}; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="{stem}.{suffix}.{format}"',
                     "X-Design-Version": str(at)})

    @router.post("/api/designer/workspaces/{workspace_id}/members")
    def designer_add_member(workspace_id: str, req: MemberRequest,
                            user: Principal = Depends(principal)) -> dict:
        member = Member(user_id=req.user_id, display_name=req.display_name,
                        email=req.email, role=UserRole(req.role))
        return _guard(designer.add_member, user, workspace_id,
                      member).model_dump(mode="json")

    @router.delete("/api/designer/workspaces/{workspace_id}/members/{user_id}")
    def designer_remove_member(workspace_id: str, user_id: str,
                               user: Principal = Depends(principal)) -> dict:
        return _guard(designer.remove_member, user, workspace_id,
                      user_id).model_dump(mode="json")

    @router.get("/api/designer/systems")
    def designer_systems(workspace_id: Optional[str] = None,
                         user: Principal = Depends(principal)) -> list[dict]:
        return designer.list_systems(user, workspace_id)

    # -- the shipped examples, loadable (UI and CLI share one module) -------

    @router.get("/api/designer/examples")
    def designer_examples() -> list[dict]:
        """Every shipped example organisation, for the Load example picker."""
        from ..designer.examples import list_examples

        return [e.summary() for e in list_examples()]

    @router.post("/api/designer/examples/load")
    def designer_load_example(body: dict = Body(...),
                              user: Principal = Depends(principal)) -> dict:
        """Create a design from an example, laid out, in the given workspace.

        Through the designer service, so a reader who may not create in that
        workspace is refused here exactly as they would be by New…
        """
        from ..designer.examples import UnknownExample, load_example

        example_id = str(body.get("example") or "")
        try:
            return _guard(load_example, designer, user, example_id,
                          workspace_id=str(body.get("workspace_id") or ""),
                          name=str(body.get("name") or ""))
        except UnknownExample as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc

    @router.post("/api/designer/systems")
    def designer_create_system(req: CreateSystemRequest,
                               user: Principal = Depends(principal)) -> dict:
        return _guard(designer.create_system, user, workspace_id=req.workspace_id,
                      name=req.name, description=req.description,
                      spec=req.spec).model_dump(mode="json")

    @router.get("/api/designer/systems/{system_id}")
    def designer_open_system(system_id: str,
                             user: Principal = Depends(principal)) -> dict:
        return _guard(designer.open_system, user, system_id)

    @router.put("/api/designer/systems/{system_id}")
    def designer_save_system(system_id: str, req: SaveSystemRequest,
                             user: Principal = Depends(principal)) -> dict:
        outcome = _guard(
            designer.save_system, user, system_id, spec=req.spec,
            layout=Layout.model_validate(req.layout) if req.layout else None,
            name=req.name, description=req.description,
            status=SystemStatus(req.status) if req.status else None,
            base_version=req.base_version, strategy=req.strategy,
            resolutions=req.resolutions, message=req.message,
        )
        return outcome.as_dict()

    @router.post("/api/designer/operations")
    def designer_operation(req: dict[str, Any],
                           user: Principal = Depends(principal)) -> dict:
        """One gesture, as one model operation on the draft the canvas holds
        (ADR-0103). Stateless: nothing is stored — the canvas keeps its own
        draft, undo and save. 200 with `accepted: false` and the violations
        when the model refuses; 422 for a request naming no such operation,
        element or relationship, or a draft the model cannot read."""
        from ..designer.gestures import evaluate
        return _guard(evaluate, req.get("spec") or {}, req.get("request") or {})

    @router.get("/api/designer/gestures")
    def designer_gestures() -> dict:
        """The designer's specification: every gesture and its operation."""
        from ..designer.gestures import gestures
        return {"gestures": [g.__dict__ for g in gestures()]}

    @router.delete("/api/designer/systems/{system_id}")
    def designer_delete_system(system_id: str,
                               user: Principal = Depends(principal)) -> dict:
        return {"deleted": _guard(designer.delete_system, user, system_id)}

    @router.post("/api/designer/systems/{system_id}/lock")
    def designer_acquire_lock(system_id: str, req: LockRequest,
                              user: Principal = Depends(principal)) -> dict:
        return _guard(designer.acquire_lock, user, system_id, target=req.target,
                      scope=req.scope, note=req.note).model_dump(mode="json")

    @router.post("/api/designer/systems/{system_id}/lock/heartbeat")
    def designer_heartbeat(system_id: str, req: LockRequest,
                           user: Principal = Depends(principal)) -> dict:
        lock = designer.heartbeat(user, system_id, req.target)
        return lock.model_dump(mode="json") if lock else {"held": False}

    @router.delete("/api/designer/systems/{system_id}/lock")
    def designer_release_lock(system_id: str, target: str = "*",
                              user: Principal = Depends(principal)) -> dict:
        return {"released": designer.release_lock(user, system_id, target)}

    @router.post("/api/designer/systems/{system_id}/lock/break")
    def designer_break_lock(system_id: str, req: LockRequest,
                            user: Principal = Depends(principal)) -> dict:
        return {"broken": _guard(designer.break_lock, user, system_id, req.target)}

    @router.get("/api/designer/systems/{system_id}/revisions")
    def designer_revisions(system_id: str, limit: int = 50,
                           user: Principal = Depends(principal)) -> list[dict]:
        return [r.model_dump(mode="json")
                for r in _guard(designer.revisions, user, system_id, limit)]

    @router.post("/api/designer/systems/{system_id}/restore/{version}")
    def designer_restore(system_id: str, version: int,
                         user: Principal = Depends(principal)) -> dict:
        return _guard(designer.restore, user, system_id,
                      version).model_dump(mode="json")

    @router.get("/api/designer/audit")
    def designer_audit(system_id: Optional[str] = None, actor: Optional[str] = None,
                       action: Optional[str] = None, since: Optional[str] = None,
                       until: Optional[str] = None, limit: int = 200,
                       user: Principal = Depends(principal)) -> list[dict]:
        """The designer audit log (ADR-0043). Admins and owners only; there is
        no write side, because the log is append-only."""
        return [
            e.model_dump(mode="json")
            for e in _guard(designer.audit_events, user, system_id=system_id,
                            actor=actor, action=action, since=since, until=until,
                            limit=limit)
        ]


    return router
