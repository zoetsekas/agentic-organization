"""The designer's read-only review surfaces, preflight and the publish request."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..compiler import build_ir as _build_ir
from ..compiler.diff import IncomparableIRError as _IncomparableIRError
from ..compiler.diff import diff_ir as _diff_ir
from ..designer import Principal
from ..designer.audit import AuditOutcome as DesignerAuditOutcome
from ..spec.binding import TargetBinding as _TargetBinding
from ..spec.loader import load_spec_text as _load_spec_text
from .context import ApiContext
from .fabric import _deployment_view


def _designer_spec(raw: dict[str, Any]):
    """Parse a stored spec, or refuse in the UI's own terms.

    A design mid-edit is routinely incomplete, so "this does not compile
    yet" is the normal answer here, not a failure: 422 with the loader's
    own message, which the UI shows in place of the review panel.
    """
    import yaml

    try:
        return _load_spec_text(yaml.safe_dump(raw))
    except Exception as e:
        raise HTTPException(
            422, f"this design does not compile yet: {type(e).__name__}: {e}"
        ) from e

def _designer_ir(raw: dict[str, Any], binding: Optional[dict[str, Any]]):
    spec = _designer_spec(raw)
    # The binding a revision was saved with decides its target, so a
    # re-target shows up as the incomparability it is rather than silently
    # diffing two different compilations.
    bound = None
    if binding and binding.get("target"):
        try:
            bound = _TargetBinding.model_validate(binding)
        except Exception as e:
            raise HTTPException(422, f"stored binding is unusable: {e}") from e
    try:
        return _build_ir(spec, target=bound.target if bound else "local",
                         binding=bound)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            422, f"this design does not compile yet: {type(e).__name__}: {e}"
        ) from e


def build(ctx: ApiContext) -> APIRouter:
    router = ctx.app.router
    designer = ctx.designer
    principal = ctx.principal
    _guard = ctx.guard
    designer_evaluations = ctx.designer_evaluations
    fabric_tenants = ctx.fabric_tenants
    fabric_deployments = ctx.fabric_deployments

    # -- designer: review surfaces (WS-009) --------------------------------
    #
    # Both routes are read-only. The gate answers "what does the evidence say
    # today" and must never run evaluations on the way: a route that quietly
    # produced the evidence it then reported on would defeat the gate. The diff
    # only reads revisions that are already stored.


    @router.get("/api/designer/systems/{system_id}/gate")
    def designer_gate(system_id: str, stage: str = "production",
                      user: Principal = Depends(principal)) -> dict:
        """The evaluation gate for every agent in a design. Reads only."""
        from ..spec.model import LifecycleStage

        raw, _binding, _version = _guard(designer.spec_at, user, system_id)
        spec = _designer_spec(raw)
        try:
            to_stage = LifecycleStage(stage)
        except ValueError as e:
            raise HTTPException(422, f"unknown lifecycle stage '{stage}'") from e
        verdicts = designer_evaluations.gate_states(spec, to_stage=to_stage)
        return {
            "agents": {
                agent_id: {
                    "state": v.state.value,
                    "reason": v.reason,
                    "required": v.required,
                }
                for agent_id, v in verdicts.items()
            }
        }

    @router.get("/api/designer/systems/{system_id}/workflow-engines")
    def designer_workflow_engines(system_id: str,
                                  user: Principal = Depends(principal)) -> dict:
        """Which engine runs each workflow, from the binding the design was
        saved with (ADR-0110). Reads only.

        The designer draws the governed workflow; a step whose body lives in
        an engine shows that engine and links out to it (*Open in*), and this
        is where it learns both. A design saved with no binding has none to
        report, and says so rather than guessing a default.
        """
        from ..designer.service import stored_target_binding
        from ..runtime.engines import UnknownEngine
        from ..runtime.engines import engine as engine_of

        _raw, binding, _version = _guard(designer.spec_at, user, system_id)
        bound = stored_target_binding(binding)
        if bound is None:
            return {"target": None, "workflows": {}}
        out: dict[str, Any] = {}
        for wb in bound.workflows:
            try:
                mode = engine_of(wb.engine).mode.value
            except UnknownEngine:
                mode = "unknown"
            out[wb.workflow or "*"] = {
                "engine": wb.engine, "mode": mode, "flow": wb.flow,
                "editor_url": wb.editor_url or None,
                "send_data_classes": list(wb.send_data_classes),
            }
        return {"target": bound.target, "workflows": out}

    @router.get("/api/designer/systems/{system_id}/authority")
    def designer_authority(system_id: str,
                           user: Principal = Depends(principal)) -> dict:
        """What each agent may decide, and how much it does alone. Reads only.

        Effective authority, never what a unit declared: a mandate is the
        intersection with every unit above it (ADR-0065), and showing the
        declaration would let a reader believe an agent holds something its
        line excludes. `line` names the units that produced it, so a refusal
        can be explained to somebody who did not write the spec.
        """
        from ..mandates import resolve as resolve_mandates
        from ..spec.model import AutonomyPosture
        from ..spec.validate import validate_spec

        raw, _binding, _version = _guard(designer.spec_at, user, system_id)
        spec = _designer_spec(raw)
        vocabulary = [d.id for d in spec.decisions]
        resolved = resolve_mandates(spec.organization, vocabulary, spec.people)

        caps = {c.id: c for c in spec.capabilities}
        roles = {r.id: r for r in spec.roles}

        def postures(agent, team_roles: list) -> list[dict]:
            held: set[str] = set(agent.capabilities)
            for assignment in list(agent.roles) + list(team_roles):
                role = roles.get(getattr(assignment, "role", assignment))
                if role:
                    held.update(role.capabilities)
            out = []
            for cap_id in sorted(c for c in held if c in caps):
                cap = caps[cap_id]
                declared = agent.autonomy.get(cap_id)
                posture: AutonomyPosture = declared or cap.autonomy
                out.append({
                    "capability": cap_id,
                    "posture": posture.value,
                    "tightened": bool(declared and declared != cap.autonomy),
                    "decision": cap.decision,
                    "requires_approval": bool(cap.constraints.requires_approval),
                    "enforced_by": cap.constraints.enforcement.enforced_by.value,
                    "enforced_in": cap.constraints.enforcement.enforced_in,
                })
            return out

        agents: dict[str, dict] = {}

        def walk(team, inherited: list) -> None:
            team_roles = inherited + [r.role for r in team.roles]
            for agent in team.members:
                effective = resolved.for_agent(agent.id)
                agents[agent.id] = {
                    "name": agent.name or agent.id,
                    "team": team.id,
                    "decisions": sorted(effective.decisions),
                    "conditions": [dict(c) for c in effective.conditions],
                    # Root first: the units whose declarations produced this.
                    "line": list(effective.line),
                    "declared": sorted(agent.mandate.decisions)
                    if agent.mandate else None,
                    "activities": postures(agent, team_roles),
                }
            for child in team.teams:
                walk(child, team_roles)

        walk(spec.organization, [])

        # Findings the authority model produces, so the UI can show a refusal
        # where the thing it refuses is being edited rather than in a log.
        codes = {
            "separation_violated", "mandate_overreach", "undeclared_decision",
            "root_leader_without_mandate", "advisory_mutates",
            "person_holds_access", "duplicate_person",
            "owner_approves_own_agent",
            "autonomous_without_decision", "autonomous_without_mandate",
            "supervised_without_approval", "human_decides_but_agent_holds",
            "autonomy_widened", "unenforceable_platform_control",
            "both_without_authority", "platform_bound_wider_than_application",
            "autonomy_without_evidence", "supervised_by_its_own_owner",
            "human_decides_with_no_holder", "application_control_unnamed",
        }
        findings = [
            {"severity": f.severity, "code": f.code, "where": f.where,
             "message": f.message}
            for f in validate_spec(spec) if f.code in codes
        ]

        return {
            "vocabulary": [
                {"id": d.id, "title": d.title or d.id} for d in spec.decisions
            ],
            "separations": [
                {"id": r.id, "decisions": list(r.decisions), "reason": r.reason,
                 "enforced_by": r.enforcement.enforced_by.value,
                 "enforced_in": r.enforcement.enforced_in}
                for r in spec.separations
            ],
            "teams": {
                tid: {"decisions": sorted(eff.decisions), "line": list(eff.line)}
                for tid, eff in resolved.teams.items()
            },
            "agents": agents,
            # People are principals for authority (ADR-0079), so a reader can
            # see where an escalation lands. No capabilities or permissions
            # appear here because a person holds none — their access is their
            # employer's to mediate, not ours.
            "people": {
                person.id: {
                    "name": person.name or person.id,
                    "position": person.position,
                    "unit": resolved.person_unit.get(person.id, ""),
                    "decisions": sorted(resolved.for_person(person.id).decisions),
                    "line": list(resolved.for_person(person.id).line),
                    "declared": sorted(person.mandate.decisions)
                    if person.mandate else None,
                    "pairings": sorted(
                        {
                            f"{a.id}:{r.value}"
                            for a in spec.agents()
                            for h in a.humans
                            if h.principal() == person.id
                            for r in h.roles
                        }
                    ),
                }
                for person in spec.people
            },
            "findings": findings,
        }

    @router.get("/api/designer/systems/{system_id}/placements")
    def designer_placements(system_id: str,
                            user: Principal = Depends(principal)) -> dict:
        """Where each agent's work lives, and what may cross. Reads only.

        A placement is an org unit crossed with an environment class
        (ADR-0069) — the namespace model an enterprise already has. What is
        drawn from this is a *region*, so it must be the thing that compiles:
        `agents` is who shares a volume and a process namespace, and `rules` is
        the whole of what crosses. Everything absent is denied.

        A placement is **not** a security boundary. The tenant is (ADR-0050).
        The response says so rather than leaving a reader to infer it from a
        picture of boxes.
        """
        from ..placements import resolve as resolve_placements
        from ..spec.validate import validate_spec

        raw, _binding, _version = _guard(designer.spec_at, user, system_id)
        spec = _designer_spec(raw)
        resolved = resolve_placements(spec)

        codes = {
            "single_placement", "placement_denies_delegation",
            "separated_agents_co_resident", "placement_violation",
            "egress_on_isolated_env", "environment_widened",
        }
        environments = {e.id: e for e in spec.environments}

        return {
            "is_a_security_boundary": False,
            "note": (
                "A placement is a naming and policy scope. The tenant is the "
                "absolute boundary; namespaces share a kernel."
            ),
            "declared": list(resolved.declared),
            "placements": [
                {
                    "id": placement.id,
                    "unit": placement.unit,
                    "environment": placement.environment,
                    "network": (
                        environments[placement.environment].network.value
                        if placement.environment in environments else "none"
                    ),
                    "egress_allowlist": (
                        list(environments[placement.environment].egress_allowlist)
                        if placement.environment in environments else []
                    ),
                    "agents": list(placement.agents),
                    "groups": list(placement.groups),
                    # What the shared volume carries. Never a grant.
                    "data_classes": list(placement.data_classes),
                    "shares_a_volume": placement.shares_a_volume,
                    "reaches": sorted(
                        r.target for r in resolved.rules
                        if r.source == placement.id
                    ),
                }
                for placement in sorted(
                    resolved.placements.values(), key=lambda p: p.id
                )
            ],
            "rules": [
                {"source": r.source, "target": r.target, "via": r.via,
                 "reason": r.reason}
                for r in resolved.rules
            ],
            # Agents with no environment class have no sandbox environment, so
            # they sit in no region. Saying which is better than a picture that
            # quietly omits them.
            "unplaced": sorted(
                a.id for a in spec.agents() if a.id not in resolved.home
            ),
            "findings": [
                {"severity": f.severity, "code": f.code, "where": f.where,
                 "message": f.message}
                for f in validate_spec(spec) if f.code in codes
            ],
        }

    def _active_platform_policy():
        """The fabric's house rules, if this installation has any (ADR-0076).

        Read from the environment because the policy belongs to the fabric and
        not to a design: a designer who could choose the policy their design is
        judged against is not being judged. `None` means no policy is
        configured, which is a real state and not an error — but a preflight
        that ran without one while the real compile applies one would be
        flattering, so the answer says which was used.
        """
        path = os.environ.get("ORGAGENTS_PLATFORM_POLICY", "")
        if not path:
            return None
        try:
            from ..platform_policy import load as _load_policy

            return _load_policy(path)
        except Exception as exc:
            # A broken policy file must not silently become "no policy".
            raise HTTPException(
                503,
                "the configured platform policy could not be read, so no "
                "design can be judged against it",
            ) from exc

    def _preflight(spec_dict: dict, binding_dict: Optional[dict],
                   target: str) -> dict:
        """Would this design compile, and what would it produce?

        The phase gate, run without deploying anything (ADR-0005). It compiles
        into a temporary directory that is discarded: a preflight that wrote
        artifacts somewhere would be a deployment nobody asked for.

        It applies the fabric's platform policy when there is one, because a
        preflight that passes and a real compile that refuses is worse than no
        preflight at all — the second time that happens, nobody reads the
        first one again (ADR-0076).
        """
        import tempfile

        from ..compiler.base import register_builtin_targets
        from ..compiler.engine import CompileError, compile_system
        from ..spec.model import SystemSpec
        from ..spec.validate import validate_spec

        register_builtin_targets()
        policy = _active_platform_policy()
        try:
            spec = SystemSpec.model_validate(spec_dict)
        except Exception as exc:                  # a draft mid-edit
            return {"ok": False, "stage": "spec",
                    "refusals": [{"severity": "error", "code": "spec_invalid",
                                  "where": "", "message": str(exc)}],
                    "warnings": [], "files": [], "target": target}

        findings = validate_spec(spec, platform_policy=policy)
        refusals = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        def view(f: Any) -> dict[str, Any]:
            return {"severity": f.severity, "code": f.code,
                    "where": f.where, "message": f.message}
        if refusals:
            return {"ok": False, "stage": "validate",
                    "refusals": [view(f) for f in refusals],
                    "warnings": [view(f) for f in warnings],
                    "files": [], "target": target}

        binding = None
        if binding_dict:
            from ..spec.binding import Binding

            try:
                binding = Binding.model_validate(binding_dict)
            except Exception as exc:
                return {"ok": False, "stage": "binding",
                        "refusals": [{"severity": "error",
                                      "code": "binding_invalid",
                                      "where": "", "message": str(exc)}],
                        "warnings": [view(f) for f in warnings],
                        "files": [], "target": target}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = compile_system(
                    spec, targets=[target], out_dir=Path(tmp), binding=binding,
                    platform_policy=policy,
                )[0]
                files = sorted(f.path for f in result.files)
        except CompileError as exc:
            return {"ok": False, "stage": "compile",
                    "refusals": [{"severity": "error", "code": "compile_failed",
                                  "where": target, "message": str(exc)}],
                    "warnings": [view(f) for f in warnings],
                    "files": [], "target": target}
        return {"ok": True, "stage": "compiled", "refusals": [],
                "warnings": [view(f) for f in warnings],
                "files": files, "target": target,
                "platform_policy": policy.stamp if policy else None}

    @router.post("/api/designer/systems/{system_id}/preflight")
    def designer_preflight(system_id: str, body: Optional[dict] = None,
                           user: Principal = Depends(principal)) -> dict:
        """Validate and compile the stored design, deploying nothing.

        A reviewer's act, not an editor's: it answers "would this be refused"
        before anybody asks for it to be run, which is the question the UI
        could not put to the platform at all.
        """
        target = (body or {}).get("target", "local")
        spec_dict, binding_dict, version = _guard(
            designer.spec_at, user, system_id
        )
        return {**_preflight(spec_dict, binding_dict, target),
                "version": version}

    @router.post("/api/designer/systems/{system_id}/publish")
    def designer_publish(system_id: str, body: Optional[dict] = None,
                         user: Principal = Depends(principal)) -> dict:
        """Ask the fabric to run a design, or say exactly why it refused.

        The designer **requests**; it does not deploy. The deployment is
        created in `requested`, and the fabric compiles it for real and moves
        it on under its own authority (ADR-0049). Two reasons, both load
        bearing: the tenant is assigned by the fabric and never named by a
        design, because a design that could name its own tenant could widen
        its own boundary (ADR-0050); and the platform policy is the fabric's
        to apply.

        So what happens here is a preflight and a request. A refusal is
        returned as the reason with the gate's own findings, and is written to
        the audit log — a refused publish is the phase gate saying no to a
        named person about a named design, and nothing else would keep that.
        """
        payload = body or {}
        tenant_id = str(payload.get("tenant_id") or "").strip()
        target = payload.get("target", "local")
        spec_dict, binding_dict, version, name = _guard(
            designer.publish_candidate, user, system_id
        )
        if not tenant_id:
            designer.record_publish(
                user, system_id, outcome=DesignerAuditOutcome.FAILED,
                reason="no tenant named", version=version,
            )
            raise HTTPException(422, {
                "error": "a publish names the tenant it is for",
                "reason": "The tenant is assigned by the fabric and never by "
                          "a design (ADR-0050), so it has to be named here.",
            })
        if fabric_tenants.get(tenant_id) is None:
            designer.record_publish(
                user, system_id, outcome=DesignerAuditOutcome.FAILED,
                reason=f"unknown tenant '{tenant_id}'", version=version,
            )
            raise HTTPException(404, f"no tenant '{tenant_id}'")

        verdict = _preflight(spec_dict, binding_dict, target)
        if not verdict["ok"]:
            designer.record_publish(
                user, system_id, outcome=DesignerAuditOutcome.DENIED,
                reason=f"refused at {verdict['stage']}", version=version,
                tenant_id=tenant_id,
            )
            raise HTTPException(422, {
                "error": f"this design is refused at the {verdict['stage']} "
                         "stage and was not requested",
                "verdict": verdict,
            })

        deployment = fabric_deployments.request(
            tenant_id, name=name or system_id, system_id=system_id,
            revision=str(version), target=target,
        )
        designer.record_publish(
            user, system_id, outcome=DesignerAuditOutcome.SUCCESS,
            version=version, tenant_id=tenant_id,
            deployment_id=deployment.id, target=target,
        )
        return {
            "deployment": _deployment_view(deployment),
            "verdict": verdict,
            "version": version,
            "note": (
                "Requested, not deployed. The fabric compiles this for the "
                "tenant under its own authority and moves it on from "
                "`requested`; nothing has been built yet."
            ),
        }

    @router.get("/api/designer/systems/{system_id}/diff")
    def designer_diff(system_id: str,
                      from_version: Optional[int] = Query(default=None, alias="from"),
                      to_version: Optional[int] = Query(default=None, alias="to"),
                      user: Principal = Depends(principal)) -> dict:
        """What moved between two revisions of one design, ranked by consequence."""
        left, right = _guard(designer.review_pair, user, system_id,
                             left=from_version, right=to_version)
        try:
            result = _diff_ir(_designer_ir(left[0], left[1]),
                              _designer_ir(right[0], right[1]))
        except _IncomparableIRError as e:
            # A refusal, not a failure to find the pair: 409 carries the
            # refusal's own reasoning so the UI can show it verbatim.
            raise HTTPException(409, str(e)) from e
        payload = result.to_dict()
        return {"changes": payload["changes"], "summary": payload["summary"]}


    return router
