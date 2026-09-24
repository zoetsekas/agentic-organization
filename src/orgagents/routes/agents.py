"""Agents on the running platform: CRUD, harness preview, runs."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..designer.audit import AuditAction as DesignerAuditAction
from ..models import Agent
from ..runtime_access import RUNTIME_MANAGE, RUNTIME_READ, RUNTIME_RUN, Grants
from .context import ApiContext
from .models import RunRequest


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router
    platform = ctx.platform
    runtime_access = ctx.runtime_access
    caller = ctx.caller
    _authorize = ctx.authorize
    _allowed = ctx.allowed
    _scope_of_agent_id = ctx.scope_of_agent_id
    _require_read_anywhere = ctx.require_read_anywhere
    _acting_as = ctx.acting_as
    _session_scope_check = ctx.session_scope_check

    # -- agents ------------------------------------------------------------

    def _readable_agent(g: Grants, agent_id: str) -> Agent:
        agent = platform.org.agent(agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        _authorize(g, RUNTIME_READ, runtime_access.workspace_of_agent(agent),
                   DesignerAuditAction.RUNTIME_READ, detail={"agent": agent_id})
        return agent

    @router.get("/api/agents")
    def list_agents(org_unit_id: Optional[str] = None,
                    g: Grants = Depends(caller)) -> list[dict]:
        _require_read_anywhere(g)
        return [a.model_dump() for a in platform.org.agents(org_unit_id)
                if g.allows(RUNTIME_READ, runtime_access.workspace_of_agent(a))]

    @router.post("/api/agents")
    def create_agent(agent: Agent, g: Grants = Depends(caller)) -> dict:
        scope = runtime_access.workspace_of_agent(agent)
        _authorize(g, RUNTIME_MANAGE, scope, DesignerAuditAction.AGENT_CREATE,
                   detail={"agent": agent.id})
        saved = platform.org.add_agent(agent)
        _allowed(g, DesignerAuditAction.AGENT_CREATE, RUNTIME_MANAGE, scope,
                 {"agent": saved.id})
        return saved.model_dump()

    @router.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str, g: Grants = Depends(caller)) -> dict:
        return _readable_agent(g, agent_id).model_dump()

    @router.put("/api/agents/{agent_id}")
    def update_agent(agent_id: str, agent: Agent,
                     g: Grants = Depends(caller)) -> dict:
        existing = platform.org.agent(agent_id)
        if existing is None:
            raise HTTPException(404, "agent not found")
        # Both where it is and where it would go: moving an agent into a
        # workspace is managing that workspace's runtime too.
        for scope in {runtime_access.workspace_of_agent(existing),
                      runtime_access.workspace_of_agent(agent)}:
            _authorize(g, RUNTIME_MANAGE, scope, DesignerAuditAction.AGENT_UPDATE,
                       detail={"agent": agent_id})
        agent.id = agent_id
        saved = platform.org.add_agent(agent)
        _allowed(g, DesignerAuditAction.AGENT_UPDATE, RUNTIME_MANAGE,
                 runtime_access.workspace_of_agent(saved), {"agent": agent_id})
        return saved.model_dump()

    @router.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str, g: Grants = Depends(caller)) -> dict:
        scope = _scope_of_agent_id(agent_id)
        _authorize(g, RUNTIME_MANAGE, scope, DesignerAuditAction.AGENT_DELETE,
                   detail={"agent": agent_id})
        deleted = platform.store.delete("agents", agent_id)
        _allowed(g, DesignerAuditAction.AGENT_DELETE, RUNTIME_MANAGE, scope,
                 {"agent": agent_id, "deleted": deleted})
        return {"deleted": deleted}

    @router.get("/api/agents/{agent_id}/harness")
    def agent_harness(agent_id: str, g: Grants = Depends(caller)) -> dict:
        agent = _readable_agent(g, agent_id)
        return {
            "harness": agent.harness.model_dump(),
            "system_prompt": platform.harness.system_prompt(agent),
            "tools": [b.model_dump() for b in platform.harness.bindings(agent)],
            "sandbox": (
                platform.sandboxes.container_spec(agent.sandbox) if agent.sandbox else None
            ),
            "skills": [s.model_dump() for s in platform.harness.skills(agent)],
        }

    @router.post("/api/agents/{agent_id}/run")
    def run_agent(agent_id: str, req: RunRequest,
                  g: Grants = Depends(caller)) -> dict:
        agent = platform.org.agent(agent_id)
        if agent is None:
            raise HTTPException(404, f"agent not found: {agent_id}")
        scope = runtime_access.workspace_of_agent(agent)
        _authorize(g, RUNTIME_RUN, scope, DesignerAuditAction.RUNTIME_RUN,
                   detail={"agent": agent_id})
        if req.session_id:
            _session_scope_check(g, req.session_id, RUNTIME_RUN,
                                 DesignerAuditAction.RUNTIME_RUN)
        try:
            result = platform.runtime.run(
                agent_id, req.prompt, session_id=req.session_id,
                created_by=_acting_as(g, req.created_by),
            )
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        _allowed(g, DesignerAuditAction.RUNTIME_RUN, RUNTIME_RUN, scope,
                 {"agent": agent_id, "session": result.session_id})
        return {
            "session_id": result.session_id,
            "session_url": result.session_url,
            "output": result.output,
            "state": result.state.value,
            "delegations": result.delegations,
            "error": result.error,
        }


    return router
