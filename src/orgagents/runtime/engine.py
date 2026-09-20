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

from datetime import datetime, timezone

from ..catalog import Catalog
from ..data.planes import AccessDenied, DataPlanes
from ..harness.builder import HarnessBuilder
from ..messaging import ChannelKind, DeliveryError, MessageBus
from ..memory import MemoryError, MemoryManager, ResolvedMemory
from ..models import Agent, AgentKind, SessionState, Severity, WorkflowRef
from ..spec.model import MemoryNamespace, MemoryPolicy, MemoryTier, RecallMode, SharingScope
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
        self.memory = MemoryManager(store)
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
        self.preload_memories(agent, session.id, prompt)

        tools = self.harness.build(agent)
        tools.update(self._memory_tools(agent, session.id))
        tools.update(self._subagent_tools(agent, session.id))
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

    # -- memory (ADR-0028) -------------------------------------------------

    def memory_contract(self, agent: Agent) -> ResolvedMemory:
        """Rebuild the agent's resolved memory contract from its manifest."""
        policy = agent.memory or {}
        session = MemoryPolicy(
            tier=MemoryTier.SESSION,
            enabled=policy.get("session_enabled", True),
            max_items=policy.get("session_max_items", 500),
            recall=RecallMode(policy.get("session_recall", "automatic")),
            redact_data_classes=policy.get("redact_data_classes", []),
        )
        long_term = MemoryPolicy(
            tier=MemoryTier.LONG_TERM,
            enabled=policy.get("long_term_enabled", False),
            retention_days=policy.get("long_term_retention_days"),
            max_items=policy.get("long_term_max_items", 2000),
            recall=RecallMode(policy.get("recall", "on_demand")),
            promotion_allowed=policy.get("may_promote", False),
            promotion_requires_approval=policy.get("promotion_requires_approval", False),
            redact_data_classes=policy.get("redact_data_classes", []),
        )
        namespaces = [
            MemoryNamespace(
                id=n["id"],
                description=n.get("description", ""),
                scope=SharingScope(n.get("scope", "private")),
                groups=n.get("groups", []),
                data_classes=n.get("data_classes", []),
                retention_days=n.get("retention_days"),
            )
            for n in policy.get("namespaces", [])
        ]
        return ResolvedMemory(
            agent_id=agent.id,
            session=session,
            long_term=long_term,
            namespaces=namespaces,
            groups=tuple(agent.groups),
            readable_data_classes=tuple(policy.get("readable_data_classes", [])),
        )

    def _memory_tools(self, agent: Agent, session_id: str) -> dict[str, Any]:
        contract = self.memory_contract(agent)

        def remember(
            content: str, key: str = "", tags: Optional[list[str]] = None,
            data_class: str = "", long_term: bool = False, namespace: str = "",
        ) -> dict[str, Any]:
            """Remember something. Session-scoped unless long_term is set."""
            tier = MemoryTier.LONG_TERM if long_term else MemoryTier.SESSION
            if long_term and not namespace:
                return {"ok": False,
                        "error": "long-term memory needs a namespace; "
                                 f"yours are {[n.id for n in contract.namespaces]}"}
            try:
                entry = self.memory.remember(
                    contract, content, session_id=session_id, key=key, tier=tier,
                    namespace=namespace or "default", data_class=data_class,
                    tags=tags or [], source=session_id,
                )
            except (MemoryError, AccessDenied) as e:
                return {"ok": False, "error": str(e)}
            self.sessions.log(session_id, "memory_write", actor=agent.id,
                              payload={"id": entry.id, "tier": entry.tier.value,
                                       "namespace": entry.namespace, "key": entry.key})
            return {"ok": True, "id": entry.id, "tier": entry.tier.value,
                    "expires_at": entry.expires_at}

        def recall(query: str = "", scope: str = "all", limit: int = 5) -> list[dict]:
            """Recall memories. scope: session | long_term | all."""
            tier = {"session": MemoryTier.SESSION,
                    "long_term": MemoryTier.LONG_TERM}.get(scope)
            found = self.memory.recall(contract, query, session_id=session_id,
                                       tier=tier, limit=limit)
            self.sessions.log(session_id, "memory_recall", actor=agent.id,
                              payload={"query": query, "hits": len(found)})
            return [
                {"id": e.id, "tier": e.tier.value, "namespace": e.namespace,
                 "key": e.key, "content": e.content, "created_at": e.created_at}
                for e in found
            ]

        def promote(entry_id: str, namespace: str, approved: bool = False) -> dict:
            """Move a session memory into long-term storage, if policy allows."""
            try:
                entry = self.memory.promote(contract, entry_id, namespace=namespace,
                                            approved=approved)
            except (MemoryError, AccessDenied) as e:
                return {"ok": False, "error": str(e)}
            self.sessions.log(session_id, "memory_promote", actor=agent.id,
                              payload={"from": entry_id, "to": entry.id,
                                       "namespace": namespace})
            return {"ok": True, "id": entry.id, "namespace": namespace}

        def forget(entry_id: str) -> dict:
            """Forget one of your own memories."""
            return {"ok": self.memory.forget(contract, entry_id)}

        tools: dict[str, Any] = {"memory_recall": recall, "memory_forget": forget}
        if contract.session.enabled:
            tools["memory_remember"] = remember
        if contract.long_term.promotion_allowed:
            tools["memory_promote"] = promote
        return tools

    def preload_memories(self, agent: Agent, session_id: str, prompt: str) -> list[dict]:
        """Recall relevant long-term memories into a session that asked for it."""
        contract = self.memory_contract(agent)
        if contract.long_term.recall is not RecallMode.AUTOMATIC:
            return []
        found = self.memory.recall(contract, prompt, session_id=session_id,
                                   tier=MemoryTier.LONG_TERM, limit=5)
        if found:
            self.sessions.log(session_id, "memory_preload", actor=agent.id,
                              payload={"count": len(found),
                                       "keys": [e.key or e.id for e in found]})
        return [{"key": e.key, "content": e.content, "namespace": e.namespace}
                for e in found]

    # -- sub-agents as tools (ADR-0027) -----------------------------------

    def _subagent_tools(self, agent: Agent, session_id: str) -> dict[str, Any]:
        """Expose each sub-agent as a callable tool, run under the parent."""
        from .adapters import EchoAdapter, adapter_for
        from .subagents import SubAgentRunner, SubAgentTool
        from ..spec.model import SubAgentKind

        if not agent.subagents:
            return {}

        resolved = [
            SubAgentTool(
                id=sub["id"],
                name=sub.get("name", sub["id"]),
                kind=SubAgentKind(sub.get("kind", "custom")),
                purpose=sub.get("purpose", ""),
                instructions=sub.get("instructions", ""),
                parent_agent_id=agent.id,
                capabilities=tuple(sub.get("capabilities", [])),
                tools=tuple(sub.get("tools", [])),
                knowledge=tuple(sub.get("knowledge", [])),
                environment=sub.get("environment"),
                returns=sub.get("returns", ""),
                max_turns=sub.get("max_turns", 8),
                max_runtime_seconds=sub.get("max_runtime_seconds", 300),
                parallel_safe=sub.get("parallel_safe", True),
            )
            for sub in agent.subagents
        ]

        parent_tools = self.harness.build(agent)

        def invoke(tool: SubAgentTool, task: str, context: dict[str, Any]) -> Any:
            # A sub-agent sees only the slice of the parent's toolset it named.
            allowed = {
                name: fn for name, fn in parent_tools.items()
                if not tool.capabilities or any(
                    name.startswith(cap) or cap in name for cap in tool.capabilities
                )
            }
            adapter_cls = adapter_for(agent) if agent.subagents else EchoAdapter
            adapter = adapter_cls(agent, tool.system_prompt(), allowed)
            started = datetime.now(timezone.utc)
            output = adapter.run(task if not context else f"{task}\n\n{context}")
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            self.sessions.log(
                session_id, "subagent", actor=agent.id,
                payload={"subagent": tool.id, "kind": tool.kind.value, "task": task,
                         "tools_visible": sorted(allowed), "seconds": round(elapsed, 3)},
            )
            return output.text

        return SubAgentRunner(resolved, invoke=invoke).callables()

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
