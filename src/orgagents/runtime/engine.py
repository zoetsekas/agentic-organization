"""The agent runtime: sessions, delegation, workflows and escalation.

`AgentRuntime` is the orchestrator the API and the CLI drive. It owns the
control loop around a runtime adapter:

1. open (or resume) a session and start a trace span;
2. assemble the harness toolset and compose the system prompt;
3. add delegation, workflow and messaging tools bound to *this* agent;
4. run the adapter, recording every tool call, delegation and workflow step;
5. escalate to the human counterpart when policy or failure counts require it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..catalog import Catalog
from ..data.planes import DataPlanes
from ..harness.builder import HarnessBuilder
from ..messaging import ChannelKind, DeliveryError, MessageBus
from ..models import Agent, AgentKind, SessionState, Severity, WorkflowRef
from ..observability import Observability, log_event
from ..org import OrgChart
from ..sessions import SessionManager
from ..store import AGENTS, WORKFLOWS, Store
from ..workflows.engine import WorkflowEngine
from .adapters import RuntimeAdapter, TurnOutput, adapter_for


@dataclass
class RunResult:
    session_id: str
    session_url: str
    output: str
    state: SessionState
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    delegations: list[dict[str, Any]] = field(default_factory=list)
    escalated_to: Optional[str] = None
    error: Optional[str] = None


class AgentRuntime:
    def __init__(
        self,
        store: Store,
        *,
        base_url: str = "http://localhost:8000",
        harness: Optional[HarnessBuilder] = None,
    ) -> None:
        self.store = store
        self.org = OrgChart(store)
        self.sessions = SessionManager(store, base_url)
        self.bus = MessageBus(store)
        self.planes = DataPlanes(store)
        self.catalog = Catalog(store, base_url)
        self.obs = Observability(store)
        self.harness = harness or HarnessBuilder(store)
        self._depth = 0

    # -- public API --------------------------------------------------------

    def run(
        self,
        agent_id: str,
        prompt: str,
        *,
        session_id: Optional[str] = None,
        created_by: str = "",
        parent_session_id: Optional[str] = None,
    ) -> RunResult:
        agent = self.org.agent(agent_id)
        if agent is None:
            raise KeyError(f"no agent {agent_id}")

        session = (
            self.sessions.get(session_id)
            if session_id
            else self.sessions.create(
                agent_id,
                title=prompt[:80],
                created_by=created_by,
                parent_session_id=parent_session_id,
            )
        )
        if session is None:
            raise KeyError(f"no session {session_id}")

        self.sessions.set_state(session.id, SessionState.RUNNING)
        self.sessions.log(session.id, "message", actor=created_by or "human",
                          payload={"role": "user", "content": prompt})

        tools = self.harness.build(agent)
        tools.update(self._delegation_tools(agent, session.id))
        tools.update(self._workflow_tools(agent, session.id))
        tools.update(self._messaging_tools(agent, session.id))
        tools.update(self._escalation_tools(agent, session.id))

        adapter_cls = adapter_for(agent)
        adapter: RuntimeAdapter = adapter_cls(
            agent,
            self.harness.system_prompt(agent),
            tools,
            subagents=self._subagent_specs(agent),
        )

        result = RunResult(
            session_id=session.id,
            session_url=self.sessions.url(session.id),
            output="",
            state=SessionState.RUNNING,
        )
        try:
            out: TurnOutput = adapter.run(prompt)
            result.output = out.text
            result.tool_calls = out.tool_calls
            self.sessions.record_usage(session.id, tokens=out.tokens, turns=1)
            self.sessions.log(session.id, "message", actor=agent.id,
                              payload={"role": "assistant", "content": out.text})
            result.state = self.sessions.set_state(
                session.id, SessionState.COMPLETED
            ).state
        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"
            self.sessions.log(session.id, "error", actor=agent.id,
                              payload={"error": result.error})
            self.sessions.set_state(session.id, SessionState.FAILED)
            result.state = SessionState.FAILED
            self.obs.raise_alert(
                "agent_run_failed",
                result.error,
                severity=Severity.ERROR,
                agent_id=agent.id,
                session_id=session.id,
            )
            escalation = self.org.escalation_target(agent.id)
            if agent.human:
                result.escalated_to = agent.human.email
            elif escalation:
                result.escalated_to = escalation.name
        log_event(
            "agent_run",
            agent=agent.name,
            session=session.id,
            state=result.state.value,
            error=result.error,
        )
        result.delegations = [
            e.payload
            for e in self.sessions.events(session.id)
            if e.type == "delegation"
        ]
        return result

    # -- tool families bound to a session ---------------------------------

    def _delegation_tools(self, agent: Agent, session_id: str) -> dict[str, Any]:
        def delegate(to_agent_id: str, task: str) -> dict[str, Any]:
            """Hand a task to a direct report, peer or shared-service agent."""
            if not self.org.can_delegate(agent.id, to_agent_id):
                return {
                    "ok": False,
                    "error": f"{agent.name} may not delegate to {to_agent_id}",
                }
            if self._depth >= agent.harness.max_subagent_depth:
                return {"ok": False, "error": "max sub-agent depth reached"}
            self._depth += 1
            try:
                child = self.run(
                    to_agent_id,
                    task,
                    created_by=agent.id,
                    parent_session_id=session_id,
                )
            finally:
                self._depth -= 1
            self.sessions.log(
                session_id,
                "delegation",
                actor=agent.id,
                payload={
                    "to": to_agent_id,
                    "task": task,
                    "child_session": child.session_id,
                    "child_session_url": child.session_url,
                    "state": child.state.value,
                },
            )
            return {
                "ok": child.error is None,
                "output": child.output,
                "session_url": child.session_url,
                "error": child.error,
            }

        def spawn_subagent(name: str, task: str, instructions: str = "") -> dict[str, Any]:
            """Create an ephemeral sub-agent under this agent and run one task."""
            sub = Agent(
                name=f"{agent.name}/{name}",
                title=name,
                kind=AgentKind.SUBAGENT,
                org_unit_id=agent.org_unit_id,
                manager_agent_id=agent.id,
                human=agent.human,
                harness=agent.harness.model_copy(deep=True),
                sandbox=agent.sandbox,
                groups=list(agent.groups),
            )
            sub.harness.system_prompt = instructions or (
                f"You are a sub-agent of {agent.name} handling: {task}"
            )
            sub.harness.max_subagent_depth = max(agent.harness.max_subagent_depth - 1, 0)
            self.org.add_agent(sub)
            return delegate(sub.id, task)

        return {"delegate": delegate, "spawn_subagent": spawn_subagent}

    def _workflow_tools(self, agent: Agent, session_id: str) -> dict[str, Any]:
        def run_workflow(workflow_id: str, inputs: Optional[dict] = None) -> dict[str, Any]:
            """Execute an encoded LangGraph workflow the agent is entitled to."""
            if workflow_id not in agent.workflow_ids:
                return {"ok": False, "error": f"{agent.name} may not run {workflow_id}"}
            ref = self.store.get(WORKFLOWS, workflow_id, WorkflowRef)
            if ref is None:
                return {"ok": False, "error": f"no workflow {workflow_id}"}
            engine = WorkflowEngine(
                tool_caller=lambda name, args: self.harness.call(agent, name, **args).value,
                agent_caller=lambda aid, args: self._delegation_tools(agent, session_id)[
                    "delegate"
                ](aid, args.get("task", "")),
                workflows={
                    w: self.store.get(WORKFLOWS, w, WorkflowRef)
                    for w in agent.workflow_ids
                },
            )
            res = engine.run(ref, inputs or {})
            self.sessions.log(
                session_id,
                "workflow",
                actor=agent.id,
                payload={
                    "workflow": ref.name,
                    "path": res.path,
                    "interrupted_at": res.interrupted_at,
                    "error": res.error,
                },
            )
            if res.interrupted_at:
                self.sessions.set_state(session_id, SessionState.WAITING_HUMAN)
            return {
                "ok": res.ok,
                "state": res.state,
                "path": res.path,
                "interrupted_at": res.interrupted_at,
                "error": res.error,
            }

        def list_workflows() -> list[dict[str, str]]:
            """List the workflows this agent may run."""
            out = []
            for wid in agent.workflow_ids:
                w = self.store.get(WORKFLOWS, wid, WorkflowRef)
                if w:
                    out.append({"id": w.id, "name": w.name, "description": w.description})
            return out

        return {"run_workflow": run_workflow, "list_workflows": list_workflows}

    def _messaging_tools(self, agent: Agent, session_id: str) -> dict[str, Any]:
        def send_message(
            to_agent_id: Optional[str] = None,
            subject: str = "",
            body: str = "",
            channel: str = "internal_bus",
            channel_address: str = "",
            requires_response: bool = False,
        ) -> dict[str, Any]:
            """Message another agent directly or over an enterprise channel."""
            try:
                msg = self.bus.send(
                    agent,
                    to_agent_id=to_agent_id,
                    channel=ChannelKind(channel),
                    channel_address=channel_address,
                    subject=subject,
                    body=body,
                    session_id=session_id,
                    requires_response=requires_response,
                )
            except DeliveryError as e:
                return {"ok": False, "error": str(e)}
            self.sessions.log(
                session_id, "message_sent", actor=agent.id,
                payload={"message_id": msg.id, "to": to_agent_id, "channel": channel},
            )
            return {"ok": True, "message_id": msg.id, "payload": msg.payload}

        def read_inbox(limit: int = 20) -> list[dict[str, Any]]:
            """Read messages addressed to this agent."""
            return [m.model_dump() for m in self.bus.inbox(agent.id, limit)]

        return {"send_message": send_message, "read_inbox": read_inbox}

    def _escalation_tools(self, agent: Agent, session_id: str) -> dict[str, Any]:
        def ask_human(question: str, options: Optional[list[str]] = None) -> dict[str, Any]:
            """Pause the session and ask the human counterpart for a decision."""
            self.sessions.log(
                session_id, "approval", actor=agent.id,
                payload={"question": question, "options": options or []},
            )
            self.sessions.set_state(session_id, SessionState.WAITING_HUMAN)
            human = agent.human
            if human:
                for channel in human.notify_channels:
                    try:
                        self.bus.send(
                            agent,
                            channel=channel,
                            channel_address=human.email,
                            subject="Decision needed",
                            body=question,
                            session_id=session_id,
                            requires_response=True,
                        )
                    except DeliveryError:
                        continue
            return {
                "status": "waiting_human",
                "notified": human.email if human else None,
                "session_url": self.sessions.url(session_id),
            }

        def escalate(reason: str) -> dict[str, Any]:
            """Escalate up the reporting line."""
            manager = self.org.escalation_target(agent.id)
            self.sessions.log(
                session_id, "escalation", actor=agent.id,
                payload={"reason": reason, "to": manager.id if manager else None},
            )
            if manager is None:
                return ask_human(f"No manager to escalate to. {reason}")
            return {"escalated_to": manager.name, "manager_agent_id": manager.id}

        return {"ask_human": ask_human, "escalate": escalate}

    # -- helpers -----------------------------------------------------------

    def _subagent_specs(self, agent: Agent) -> list[dict[str, Any]]:
        """Describe direct reports to frameworks that take sub-agent configs."""
        specs = []
        for r in self.org.reports(agent.id):
            specs.append(
                {
                    "name": r.name,
                    "description": r.description or r.title,
                    "prompt": r.harness.system_prompt
                    or f"You are {r.name}, {r.title}.",
                    "agent_id": r.id,
                }
            )
        return specs

    def resume(self, session_id: str, human_response: str, actor: str = "human") -> RunResult:
        """Resume a session that was waiting on its human counterpart."""
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(f"no session {session_id}")
        self.sessions.log(
            session_id, "approval_response", actor=actor,
            payload={"response": human_response},
        )
        return self.run(session.agent_id, human_response, session_id=session_id,
                        created_by=actor)
