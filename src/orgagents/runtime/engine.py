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

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from datetime import datetime, timezone

from ..catalog import Catalog
from ..continuity import ActingAssignment
from ..tasks.model import TaskPriority
from ..data.planes import AccessDenied, DataPlanes
from ..harness.builder import HarnessBuilder
from ..ids import now_iso
from ..messaging import ChannelKind, DeliveryError, MessageBus
from ..context import ArtifactWorkspace, ContextManager, ResolvedContext, Turn
from ..classifiers import Classifier
from ..guardrails import GuardrailEngine, GuardrailResult, validate_shape
from ..memory import MemoryError, MemoryManager, ResolvedMemory
from ..models import Agent, AgentKind, SessionState, Severity, WorkflowRef
from ..spec.model import (
    ArtifactStore,
    ContextPolicy,
    Guardrail,
    GuardrailKind,
    MemoryNamespace,
    MemoryPolicy,
    MemoryTier,
    OutputContract,
    OutputViolationAction,
    RecallMode,
    SharingScope,
)
from ..observability import Observability, log_event
from ..org import OrgChart
from ..sessions import TERMINAL_STATES, SessionManager
from ..store import AGENTS, WORKFLOWS, Store
from ..spec.binding import WorkflowBinding
from .adapters import (
    BudgetExceeded,
    RuntimeAdapter,
    TurnBudget,
    TurnOutput,
    adapter_for,
)
from .endpoints import Transport, boundary_for
from .engines import ServiceEngine, engine_for_binding


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


@dataclass
class _Assignment:
    """One handle a leader holds over work it commissioned (ADR-0093).

    The durable facts — who asked whom, what state the work is in, what it
    spent — live on the child session, not here. This holds only what a
    process can: the worker running the child, and whether the result has
    already been charged to the parent.
    """

    handle: str                     # the child session id, which *is* the handle
    parent_session_id: str
    by_agent_id: str
    to_agent_id: str
    task: str
    future: "Future[RunResult]"
    assigned_at: str
    priority: str = TaskPriority.UNKNOWN.value
    assigned_at_mark: int = 0       # the parent's message count when assigned
    collected: bool = False
    abandoned: bool = False


class AgentRuntime:
    def __init__(
        self,
        store: Store,
        *,
        base_url: str = "http://localhost:8000",
        harness: Optional[HarnessBuilder] = None,
        classifier: Optional[Classifier] = None,
        summarizer: Optional[Any] = None,
        max_contract_retries: Optional[int] = None,
        workflow_bindings: Optional[list[WorkflowBinding]] = None,
        workflow_transport: Optional[Transport] = None,
        tenant_id: Optional[str] = None,
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
        self.workspace = ArtifactWorkspace(store)
        self.context = ContextManager(self.workspace)
        # Supplied by the deployment (ADR-0045); unset means the deterministic
        # pattern classifier and the structural summarizer, both of which need
        # no model call and no network.
        self.classifier = classifier
        self.summarizer = summarizer
        self.max_contract_retries = max_contract_retries
        # Which engine runs which workflow is a binding choice (ADR-0056).
        # Unbound workflows run on the native in-process interpreter, which is
        # what every deployment did before engines were pluggable.
        self.workflow_bindings = list(workflow_bindings or [])
        # Supplied by the deployment: there is no HTTP client in here, so an
        # out-of-process engine refuses rather than improvising one.
        self.workflow_transport = workflow_transport
        self.tenant_id = tenant_id
        # Asynchronous delegation (ADR-0093). Depth is no longer a counter:
        # a handle can outlive the turn that created it, so depth is derived
        # from the `parent_session_id` chain instead — see `delegation_depth`.
        self._assignments: dict[str, _Assignment] = {}
        self._assign_lock = threading.RLock()
        self._pool: Optional[ThreadPoolExecutor] = None
        # The live budget of each running session, so a settled child's spend
        # can be charged to the parent that commissioned it (rule 4).
        self._budgets: dict[str, TurnBudget] = {}
        # Declarations from the IR, filled by the loader (ADR-0099).
        self._ir_agents: list[dict[str, Any]] = []
        self._ir_data_classes: list[dict[str, Any]] = []
        # Deployment hooks over the assembled toolset, applied before the
        # policy wrapper: `(agent, session_id, tools) -> tools`. A worker uses
        # one to put the bus's `delegate`/`send_message` in place of the
        # in-process ones, and to refuse a tool a separation forbids to the
        # delegation chain it is running for (ADR-0118). A hook can add,
        # replace or remove; it can never get around `guarded`.
        self.tool_hooks: list[Callable[[Agent, str, dict[str, Any]], dict[str, Any]]] = []
        self.tool_guards: list[Callable[[Agent, str, dict[str, Any]], dict[str, Any]]] = []

    def _by_priority(self, handles: list[str]) -> list[str]:
        """Handles, most important first, stably.

        Ordering is the whole of what priority does to a handle, alongside
        naming the lowest-priority one in a refusal and appearing on the audit
        event. It does not preempt, queue or schedule — ADR-0093 refuses
        rather than queues, on purpose, and a field that quietly did nothing
        would be one more dead control (ADR-0097).
        """
        def rank(handle: str) -> int:
            with self._assign_lock:
                assignment = self._assignments.get(handle)
            if assignment is None:
                return TaskPriority.UNKNOWN.rank
            return TaskPriority(assignment.priority).rank
        return sorted(handles, key=rank)

    def team_workload(self, agent_id: str) -> list[dict[str, Any]]:
        """Every unsettled run across this agent's subtree.

        A leader could already ask what *it* had outstanding. "What is my team
        doing right now" had nothing to call, although all of it existed: every
        run is a session, every session names an agent, and the org chart says
        who reports to whom. This joins them.

        Bounded by the subtree and nothing wider. Delegation reach and
        visibility are the same question, and a view that reached further —
        across mission peers, say — would quietly widen every mission grant
        ever written: a mission lends the right to hand over work, not the
        right to watch.
        """
        out: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for member in self.org.subtree(agent_id):
            standing = self.org.acting_for(member.id)
            for session in self.sessions.list(member.id, limit=200):
                if session.state in TERMINAL_STATES:
                    continue
                try:
                    started = datetime.fromisoformat(session.created_at)
                except ValueError:
                    started = now
                out.append({
                    "agent_id": member.id,
                    "agent": member.name,
                    "session_id": session.id,
                    "session_url": self.sessions.url(session.id),
                    "title": session.title,
                    "state": session.state.value,
                    "waiting_human": session.state is SessionState.WAITING_HUMAN,
                    "running_for_s": round(
                        (now - started).total_seconds(), 1),
                    "tokens": session.token_usage,
                    # An agent being stood in for is not just busy, it is
                    # covered by somebody else (ADR-0094), and a leader
                    # reading this needs to know which.
                    "stood_in_for_by": (
                        standing.successor_agent_id if standing else None),
                })
        out.sort(key=lambda row: (not row["waiting_human"],
                                  -row["running_for_s"]))
        return out

    def offboard(self, agent_id: str, *, reason: str = "") -> dict[str, Any]:
        """Let an agent go, and settle what it was in the middle of.

        `OrgChart.decommission` ends its standing as a principal. This is the
        other half: work it was running has nobody to finish it, and a session
        left running forever is the hang ADR-0093 rule 5 exists to prevent —
        a leader waiting on it never gets a turn in which to notice.

        What this cannot do is revoke anything outside the platform. The IAM
        bindings, service accounts and secrets are the generated Terraform's,
        and applying the new configuration is what actually closes those doors.
        Saying so here rather than letting an organization believe more
        happened than did (ADR-0073).
        """
        report = self.org.decommission(agent_id, reason=reason)
        if not report.get("ok") or report.get("already"):
            return report

        abandoned: list[str] = []
        for session in self.sessions.list(agent_id, limit=500):
            if session.state in TERMINAL_STATES:
                continue
            self.sessions.log(
                session.id, "agent_departed", actor="runtime",
                payload={"error": "the agent running this has left the "
                                  "organization; nobody is going to finish it",
                         "reason": report["reason"]},
            )
            self.sessions.set_state(session.id, SessionState.FAILED)
            abandoned.append(session.id)
        report["abandoned_sessions"] = abandoned
        report["external_access_note"] = (
            "identities, IAM bindings and secrets live in the generated "
            "infrastructure; applying it is what closes those doors"
        )
        if abandoned or report["successions_now_broken"]:
            self.obs.raise_alert(
                "agent_departed",
                f"{agent_id} left with {len(abandoned)} run(s) in flight; "
                f"successions now broken: "
                f"{report['successions_now_broken'] or 'none'}",
                severity=Severity.WARNING,
                agent_id=agent_id,
            )
        return report

    # -- data contracts (ADR-0099) -----------------------------------------

    def data_impact(self, data_class_id: str) -> list[dict[str, Any]]:
        """Every agent that relies on this class, and what it relies on.

        The question an incident actually asks. The diff engine could already
        say which agents an authority change affects; nothing could say it for
        a data change, although the declarations needed to answer it are the
        ones an agent's own design carries.

        This reads the IR's declarations and nothing else. It cannot tell you
        whether a field exists — the producing system owns that — so a class
        nobody declared a dependency on comes back empty, which means "nobody
        said", not "nothing is affected".
        """
        out: list[dict[str, Any]] = []
        for agent in getattr(self, "_ir_agents", None) or []:
            for dependency in agent.get("data_dependencies", []) or []:
                if dependency.get("data_class") != data_class_id:
                    continue
                out.append({
                    "agent_id": agent.get("id"),
                    "fields": list(dependency.get("fields") or []),
                    "whole_class": not dependency.get("fields"),
                    "max_age_seconds": dependency.get("max_age_seconds"),
                    "on_stale": dependency.get("on_stale", "refuse"),
                    "produced_by": dependency.get("produced_by"),
                })
        return sorted(out, key=lambda row: row["agent_id"] or "")

    def stale_action(self, agent_id: str, data_class_id: str,
                     age_seconds: float) -> dict[str, Any]:
        """What this agent said to do with data this old.

        Staleness is a state, not a crash, and which state is the design's call
        rather than this function's. An agent that made no freshness claim gets
        `ok`, because silence is not a promise — but the reply says the claim
        was absent rather than met, so a reader can tell the two apart.
        """
        for agent in getattr(self, "_ir_agents", None) or []:
            if agent.get("id") != agent_id:
                continue
            for dependency in agent.get("data_dependencies", []) or []:
                if dependency.get("data_class") != data_class_id:
                    continue
                window = dependency.get("max_age_seconds")
                if window is None:
                    return {"ok": True, "claimed": False,
                            "note": "this agent made no freshness claim about "
                                    f"'{data_class_id}'"}
                if age_seconds <= window:
                    return {"ok": True, "claimed": True, "stale": False}
                action = dependency.get("on_stale", "refuse")
                return {
                    "ok": action != "refuse",
                    "claimed": True,
                    "stale": True,
                    "action": action,
                    "age_seconds": round(age_seconds, 1),
                    "max_age_seconds": window,
                    "note": (
                        f"'{data_class_id}' is {round(age_seconds)}s old and "
                        f"{agent_id} relies on it being at most {window}s; "
                        f"the design says {action}"
                    ),
                }
        return {"ok": True, "claimed": False,
                "note": f"{agent_id} declares no dependency on "
                        f"'{data_class_id}'"}

    # -- leader continuity (ADR-0094) --------------------------------------

    def consecutive_failures(self, agent_id: str) -> int:
        """How many of this agent's most recent sessions failed in a row.

        `escalate_to_human_after_failures` has been declared on every harness
        and told to the model in its system prompt, while nothing counted. A
        threshold nobody counts is the class of control this platform refuses
        to ship, so it is counted here.
        """
        recent = sorted(self.sessions.list(agent_id, limit=50),
                        key=lambda s: s.created_at, reverse=True)
        count = 0
        for session in recent:
            if session.state is SessionState.FAILED:
                count += 1
                continue
            if session.state is SessionState.RUNNING:
                continue          # the run in progress is not yet a verdict
            break
        return count

    def stand_down(self, agent_id: str, *, reason: str = "",
                   ends_on: str = "") -> Optional[ActingAssignment]:
        """Hand a failed leader's work and mandate to whoever stands in.

        Returns `None` when nobody does — the top of a chain with no declared
        successor, or a successor already standing in for somebody else, which
        rule 5 refuses rather than chaining. A refusal here leaves the work
        escalating to humans, which is the honest outcome: silence never
        grants authority.
        """
        agent = self.org.agent(agent_id)
        if agent is None or self.org.acting_for(agent_id) is not None:
            return None
        # Work the failed leader commissioned and never collected. Without
        # this the handles settle (ADR-0093 rule 5) with nobody to act on what
        # came back.
        handles: list[str] = []
        for session in self.sessions.list(agent_id, limit=50):
            handles += self.unsettled_handles(session.id)
        assignment = self.org.stand_in_for(
            agent_id,
            reason=reason or (
                f"{self.consecutive_failures(agent_id)} consecutive failed "
                "sessions"
            ),
            ends_on=ends_on,
            adopted_handles=sorted(set(handles)),
        )
        if assignment is None:
            self.obs.raise_alert(
                "no_successor",
                f"{agent.name} has stood down and nobody stands in for it: "
                "its decisions escalate to a human until it is back",
                severity=Severity.ERROR,
                agent_id=agent_id,
            )
            return None
        self.obs.raise_alert(
            "standing_in",
            assignment.describe() + (
                f"; withheld {[w.decision for w in assignment.withheld]}"
                if assignment.withheld else ""
            ),
            severity=Severity.WARNING,
            agent_id=assignment.successor_agent_id,
        )
        return assignment

    # -- asynchronous delegation (ADR-0093) --------------------------------

    def delegation_depth(self, session_id: str) -> int:
        """How deep this session sits in the delegation tree.

        Depth used to be a process-local counter incremented around a nested
        `run`. That is only meaningful while delegation is a call stack: a
        handle that outlives the turn which created it has no frame to sit in,
        and two workers sharing one counter would each see the other's depth.

        So depth is read from the tree — the number of ancestors reached by
        walking `parent_session_id` upward. For every synchronous case that
        exists today this is the same number the counter produced, and unlike
        the counter it is correct under concurrency and survives a restart.
        """
        depth = 0
        seen: set[str] = set()
        session = self.sessions.get(session_id)
        while session is not None and session.parent_session_id:
            parent_id = session.parent_session_id
            if parent_id in seen:          # a cycle cannot happen; not trusted to
                break                      # be impossible, because this loop runs
            seen.add(parent_id)            # inside a tool call
            depth += 1
            session = self.sessions.get(parent_id)
        return depth

    def _worker_pool(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=8, thread_name_prefix="orgagents-assign"
            )
        return self._pool

    def assigned_handles(self, session_id: str) -> list[str]:
        """Every handle this session has assigned, in order."""
        return [
            e.payload["handle"]
            for e in self.sessions.events(session_id)
            if e.type == "delegation" and e.payload.get("mode") == "assign"
            and e.payload.get("handle")
        ]

    def unsettled_handles(self, session_id: str) -> list[str]:
        """Handles this session holds whose child has not reached a terminal state.

        ``WAITING_HUMAN`` counts as unsettled, which is the point: a child
        parked on an approval is still work this leader commissioned and has
        not got back, and it still occupies one of the parallel slots.
        """
        out: list[str] = []
        for handle in self.assigned_handles(session_id):
            child = self.sessions.get(handle)
            if child is None or child.state not in TERMINAL_STATES:
                out.append(handle)
        return out

    def settle_lost_handles(self, session_id: str) -> list[str]:
        """Fail any handle this session assigned that no live worker holds.

        A handle that hangs forever is worse than one that fails, because the
        leader waiting on it never gets a turn in which to notice (ADR-0093
        rule 5). After a restart the session store still shows the child as
        running while the thread that was running it is gone; this is what
        closes that gap, and `run` calls it whenever a session resumes.

        A child a live worker is still running is left alone, and so is a
        child of a *synchronous* delegation — that one is running on this
        thread, so it cannot be lost while anything is asking.
        """
        lost: list[str] = []
        for handle in self.unsettled_handles(session_id):
            with self._assign_lock:
                assignment = self._assignments.get(handle)
            if assignment is not None and not assignment.future.done():
                continue                       # a worker is still on it
            if assignment is not None:
                continue                       # settled by `check`/`gather`
            reason = ("assignment lost: no worker in this process holds this "
                      "handle (the runtime restarted, or the worker died)")
            self.sessions.log(handle, "assignment_lost", actor="runtime",
                              payload={"error": reason})
            self.sessions.set_state(handle, SessionState.FAILED)
            lost.append(handle)
        return lost

    def _run_assigned(self, agent_id: str, task: str, handle: str,
                      created_by: str) -> RunResult:
        """Run one assigned child on a worker thread.

        `run` already turns a failure into a FAILED session and a `RunResult`
        carrying the error. This exists for what `run` cannot catch — anything
        raised before or outside that handling — because a worker that dies
        without settling its session is exactly the hang rule 5 forbids.
        """
        try:
            result = self.run(agent_id, task, session_id=handle,
                              created_by=created_by)
        except BaseException as e:                      # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            self.sessions.log(handle, "error", actor=created_by,
                              payload={"error": error})
            self.sessions.set_state(handle, SessionState.FAILED)
            return RunResult(
                session_id=handle, session_url=self.sessions.url(handle),
                output="", state=SessionState.FAILED, error=error,
            )

        # A `gather` deadline may have passed while this was running. The
        # leader has already been told the handle failed, so letting `run`'s
        # COMPLETED stand would leave the store contradicting what the agent
        # was told, and a later reader unable to tell which was true. The
        # session stays failed and the work is kept as an event: the output
        # is real and may be worth reading, it was simply not delivered.
        with self._assign_lock:
            assignment = self._assignments.get(handle)
            abandoned = bool(assignment and assignment.abandoned)
        if abandoned:
            self.sessions.log(
                handle, "assignment_late_result", actor=created_by,
                payload={
                    "state": result.state.value,
                    "output": result.output,
                    "error": result.error,
                    "note": "the leader stopped waiting before this finished; "
                            "the work completed but was never delivered",
                },
            )
            self.sessions.set_state(handle, SessionState.FAILED)
        return result

    def _parent_turn_mark(self, session_id: str) -> int:
        """How many messages this session has exchanged so far.

        A cheap clock for one purpose: telling a leader that the conversation
        has moved on since it assigned something (ADR-0093 v1.1.0).
        """
        return sum(1 for e in self.sessions.events(session_id)
                   if e.type == "message")

    def _collect(self, assignment: _Assignment) -> dict[str, Any]:
        """Take the result of a settled handle, charging it to the parent.

        Charging happens exactly once. Without it, fan-out would be a way to
        spend past a ceiling by spending through somebody else: four children
        at a million tokens each cost the leader nothing, while the same work
        done serially would have stopped at the leader's own budget.
        """
        result: Optional[RunResult] = None
        error: Optional[str] = None
        if assignment.future.done():
            try:
                result = assignment.future.result()
            except BaseException as e:                  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"
        child = self.sessions.get(assignment.handle)
        out: dict[str, Any] = {
            "output": result.output if result else "",
            "error": error or (result.error if result else None),
            "tokens": child.token_usage if child else 0,
            "cost_usd": round(child.cost_usd, 6) if child else 0.0,
        }

        with self._assign_lock:
            first = not assignment.collected
            assignment.collected = True
        if not first:
            return out

        # Rule 1: authority was checked at assignment and is not re-checked
        # here. A mission grant is date-bounded, so it can lapse while the work
        # is in flight; stranding a lawfully commissioned result would punish
        # the leader for the calendar and teach everyone to avoid time-bounded
        # grants. The lapse is recorded instead, so the audit trail shows the
        # window the work was assigned under.
        if not self.org.can_delegate(assignment.by_agent_id,
                                     assignment.to_agent_id):
            out["authority_lapsed"] = True
            out["note"] = (
                f"{assignment.by_agent_id} may no longer delegate to "
                f"{assignment.to_agent_id}; this result was assigned while it "
                "could, and is delivered on that basis"
            )

        budget = self._budgets.get(assignment.parent_session_id)
        if budget is not None and out["tokens"]:
            budget.spend(int(out["tokens"]))
        self.sessions.log(
            assignment.parent_session_id, "delegation_collected",
            actor=assignment.by_agent_id,
            payload={
                "handle": assignment.handle,
                "to": assignment.to_agent_id,
                "state": child.state.value if child else "unknown",
                "tokens": out["tokens"],
                "cost_usd": out["cost_usd"],
                # Cost is recorded, not enforced: `TurnBudget` bounds tokens
                # and wall clock, and inventing a money ceiling it does not
                # have would be a control that reads as enforced and is not.
                "charged_to_budget": budget is not None,
                "authority_lapsed": bool(out.get("authority_lapsed")),
            },
        )
        return out

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

        # A handle nothing in this process holds is failed before the agent
        # can be told it is still running (ADR-0093 rule 5).
        self.settle_lost_handles(session.id)
        self.sessions.set_state(session.id, SessionState.RUNNING)
        # A decision that reads as the successor's own is a decision nobody
        # can audit, so every run under an inherited mandate says so on the
        # session itself (ADR-0094 rule 6).
        for standing in self.org.standing_in_as(agent.id):
            self.sessions.log(
                session.id, "acting_for", actor=agent.id,
                payload={
                    "for_agent": standing.failed_agent_id,
                    "decisions": standing.decisions,
                    "withheld": [w.model_dump() for w in standing.withheld],
                    "by_hierarchy": standing.by_hierarchy,
                    "ends_on": standing.ends_on,
                    "reason": standing.reason,
                },
            )
        self.sessions.log(session.id, "message", actor=created_by or "human",
                          payload={"role": "user", "content": prompt})
        self.preload_memories(agent, session.id, prompt)

        tools = self.harness.build(agent)
        tools.update(self._artifact_tools(agent, session.id))
        tools.update(self._memory_tools(agent, session.id))
        tools.update(self._subagent_tools(agent, session.id))
        tools.update(self._delegation_tools(agent, session.id))
        tools.update(self._workflow_tools(agent, session.id))
        tools.update(self._messaging_tools(agent, session.id))
        tools.update(self._escalation_tools(agent, session.id))
        for hook in self.tool_hooks:
            tools = hook(agent, session.id, tools)
        # The framework invokes these callables directly, never through
        # `HarnessBuilder.call`, so the policy checks are wrapped around them
        # here. Without this the mandate and approval gates bind only callers
        # that were already going through the front door (ADR-0067 rule 5).
        tools = self.harness.guarded(agent, tools)
        # Refusals that must come before everything else, approval included:
        # a call a separation forbids to this run is not one a person can
        # release (ADR-0118). They can only take a tool away, never add one.
        for guard in self.tool_guards:
            tools = {k: v for k, v in guard(agent, session.id, tools).items() if k in tools}

        adapter_cls = adapter_for(agent)
        adapter: RuntimeAdapter = adapter_cls(
            agent,
            self.harness.system_prompt(agent),
            tools,
            subagents=self._subagent_specs(agent),
        )

        # The adapter owns the budget for this run; delegation tools charge a
        # collected child's tokens against it (ADR-0093 rule 4).
        self._budgets[session.id] = adapter.budget

        result = RunResult(
            session_id=session.id,
            session_url=self.sessions.url(session.id),
            output="",
            state=SessionState.RUNNING,
        )
        # What arrives is screened before the agent sees it (ADR-0035).
        inbound = self._screen(agent, session.id, prompt, GuardrailKind.INPUT)
        if inbound.blocked:
            result.error = f"input refused: {inbound.reason()}"
            result.state = self.sessions.set_state(
                session.id, SessionState.FAILED).state
            self.sessions.log(session.id, "error", actor=agent.id,
                              payload={"error": result.error})
            self._budgets.pop(session.id, None)
            return result
        prompt = inbound.content if isinstance(inbound.content, str) else prompt

        try:
            out: TurnOutput = adapter.run(prompt)
            # And what leaves is screened before anyone else sees it.
            outbound = self._screen(agent, session.id, out.text,
                                    GuardrailKind.OUTPUT)
            if outbound.blocked:
                out.text = (
                    "This response was withheld at the boundary: "
                    f"{outbound.reason()}."
                )
            elif outbound.redacted:
                out.text = outbound.content
            out, contract_errors = self._enforce_contract(
                agent, adapter, session.id, prompt, out,
                screened=outbound.blocked,
            )
            result.output = out.text
            result.tool_calls = out.tool_calls
            self.sessions.record_usage(session.id, tokens=out.tokens, turns=1)
            self.sessions.log(session.id, "message", actor=agent.id,
                              payload={"role": "assistant", "content": out.text})
            result.state = self.sessions.set_state(
                session.id, SessionState.COMPLETED
            ).state
        except BudgetExceeded as e:
            # Spending a harness budget is a policy stop, not a crash. It is
            # recorded as its own event with the limit that bound, so an
            # operator reading the session can tell "this agent ran out of
            # room" from "this agent broke" — the same distinction the
            # evaluation gate draws between not-evaluated and failed.
            result.error = f"budget stop: {e}"
            self.sessions.log(session.id, "budget_exhausted", actor=agent.id,
                              payload={"limit": e.limit, "spent": e.spent,
                                       "allowed": e.allowed})
            self.sessions.set_state(session.id, SessionState.FAILED)
            result.state = SessionState.FAILED
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
        # Continuity (ADR-0094). One failed run is a bad turn, not an outage:
        # the threshold that decides is the one already declared on the
        # harness for exactly this judgement.
        if result.state is SessionState.FAILED:
            threshold = agent.harness.escalate_to_human_after_failures
            if threshold and self.consecutive_failures(agent.id) >= threshold:
                self.stand_down(agent.id)
        elif result.state is SessionState.COMPLETED:
            # It is back. A standing-in that outlives the outage is a
            # reorganisation nobody approved.
            if self.org.close_standing_in(agent.id):
                self.obs.raise_alert(
                    "standing_in_ended",
                    f"{agent.name} is running again; nobody is standing in "
                    "for it any more",
                    severity=Severity.INFO,
                    agent_id=agent.id,
                )

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
        self._budgets.pop(session.id, None)
        return result

    # -- guardrails and workspace (ADR-0035, ADR-0036) ---------------------

    def guardrail_engine(self, agent: Agent) -> GuardrailEngine:
        return GuardrailEngine(
            [Guardrail.model_validate(g) for g in agent.guardrails],
            classifier=self.classifier,
        )

    def context_for(self, agent: Agent) -> ResolvedContext:
        return ResolvedContext(
            agent_id=agent.id,
            policy=ContextPolicy.model_validate(agent.context_policy or {}),
            store=ArtifactStore.model_validate(agent.artifact_store)
            if agent.artifact_store else None,
            groups=tuple(agent.groups),
            readable_data_classes=tuple(
                (agent.memory or {}).get("readable_data_classes", [])
            ),
        )

    def _screen(self, agent: Agent, session_id: str, content: str,
                kind: GuardrailKind,
                context: Optional[dict[str, Any]] = None) -> GuardrailResult:
        """Run the boundary checks and record what they found."""
        result = self.guardrail_engine(agent).check(content, kind, context=context)
        if result.violations:
            self.sessions.log(
                session_id, "guardrail", actor=agent.id,
                payload={
                    "boundary": kind.value,
                    "allowed": result.allowed,
                    "redacted": result.redacted,
                    "violations": [
                        {"guardrail": v.guardrail, "check": v.check.value,
                         "action": v.action.value, "detail": v.detail}
                        for v in result.violations
                    ],
                },
            )
        if result.escalate_to:
            self.obs.raise_alert(
                "guardrail_escalation", result.reason(), severity=Severity.ERROR,
                agent_id=agent.id, session_id=session_id,
            )
        return result

    def check_output_contract(self, agent: Agent, output: Any) -> list[str]:
        """Check a result against the agent's declared shape, if it has one."""
        contract = agent.output_contract or {}
        schema = contract.get("schema") or contract.get("schema_") or {}
        if not schema:
            return []
        value = output
        if isinstance(output, str):
            import json

            try:
                value = json.loads(output)
            except (TypeError, ValueError):
                return [f"expected {contract.get('id', 'the declared shape')}, "
                        "got unparseable text"]
        return validate_shape(value, schema)

    # -- contract retry (ADR-0037, WS-024 M6) ------------------------------

    def _contract_policy(self, agent: Agent) -> tuple[str, int]:
        """The declared action and attempt budget for this agent's contract."""
        contract = agent.output_contract or {}
        action = str(contract.get("on_violation") or OutputViolationAction.RETRY.value)
        retries = contract.get("max_retries")
        retries = OutputContract.model_fields["max_retries"].default \
            if retries is None else int(retries)
        if self.max_contract_retries is not None:
            retries = min(retries, self.max_contract_retries)
        return action, max(retries, 0)

    def _enforce_contract(self, agent: Agent, adapter: RuntimeAdapter,
                          session_id: str, prompt: str, out: TurnOutput, *,
                          screened: bool = False) -> tuple[TurnOutput, list[str]]:
        """Check the contract and, where the policy says so, re-prompt.

        Every attempt is recorded, and an attempt budget that runs out is not
        an acceptance: the last violation is logged exactly as a single
        unretried one always has been, so a caller still sees it fail.
        """
        errors = self.check_output_contract(agent, out.text)
        if not errors:
            return out, errors
        action, budget = self._contract_policy(agent)
        # A withheld response is a guardrail decision, not a shape the agent
        # can be asked to fix, so it is never retried.
        if action != OutputViolationAction.RETRY.value or screened:
            budget = 0

        attempt = 0
        while True:
            last = attempt >= budget
            self.sessions.log(
                session_id, "output_contract", actor=agent.id,
                payload={"errors": errors[:5], "attempt": attempt + 1,
                         "max_attempts": budget + 1, "resolved": False,
                         "action": action, "final": last},
            )
            if last:
                return out, errors
            attempt += 1
            try:
                retried = adapter.run(self._contract_retry_prompt(
                    agent, prompt, out.text, errors))
            except Exception as e:
                self.sessions.log(
                    session_id, "output_contract", actor=agent.id,
                    payload={"errors": errors[:5], "attempt": attempt + 1,
                             "max_attempts": budget + 1, "resolved": False,
                             "error": f"{type(e).__name__}: {e}", "final": True},
                )
                return out, errors
            out = retried
            errors = self.check_output_contract(agent, out.text)
            if not errors:
                self.sessions.log(
                    session_id, "output_contract", actor=agent.id,
                    payload={"errors": [], "attempt": attempt + 1,
                             "max_attempts": budget + 1, "resolved": True,
                             "action": action, "final": True},
                )
                return out, errors

    def _contract_retry_prompt(self, agent: Agent, prompt: str, answer: str,
                               errors: list[str]) -> str:
        """Hand the agent its own answer and what is wrong with it."""
        contract = agent.output_contract or {}
        named = contract.get("id") or "the declared output contract"
        listed = "\n".join(f"- {e}" for e in errors[:10])
        return (
            f"{prompt}\n\n"
            f"Your previous answer did not satisfy {named}:\n{listed}\n\n"
            "Previous answer:\n"
            f"{answer[:4000]}\n\n"
            "Return only a corrected answer that matches the contract."
        )

    def _artifact_tools(self, agent: Agent, session_id: str) -> dict[str, Any]:
        resolved = self.context_for(agent)
        if resolved.store is None:
            return {}

        def artifact_write(content: str, path: str = "",
                           data_class: str = "") -> dict[str, Any]:
            """Write a file to your workspace and get a reference back."""
            try:
                artifact = self.workspace.write(
                    resolved, content, path=path, session_id=session_id,
                    data_class=data_class, source="agent",
                )
            except Exception as e:
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
            return {"ok": True, "id": artifact.id, "reference": artifact.reference()}

        def artifact_read(artifact_id: str, offset: int = 0,
                          limit: int = 8000) -> dict[str, Any]:
            """Read back an offloaded result, in whole or in part."""
            artifact = self.workspace.read(resolved, artifact_id, offset=offset,
                                           limit=limit)
            if artifact is None:
                return {"ok": False, "error": "no such artifact, or not yours"}
            return {"ok": True, "path": artifact.path, "content": artifact.content,
                    "size_bytes": artifact.size_bytes}

        def artifact_list() -> list[dict[str, Any]]:
            """List the files in your workspace for this session."""
            return [
                {"id": a.id, "path": a.path, "size_bytes": a.size_bytes,
                 "created_at": a.created_at}
                for a in self.workspace.list(resolved, session_id)
            ]

        return {"artifact_write": artifact_write, "artifact_read": artifact_read,
                "artifact_list": artifact_list}

    def compact_thread(self, agent: Agent, turns: list[Turn]):
        """Compact a thread with whatever summarizer the deployment supplied.

        Still refuses when the result would not be smaller (ADR-0036): that
        rule belongs to compaction, not to the summarizer, so a model-backed
        summarizer cannot talk it out of it.
        """
        return self.context.compact(self.context_for(agent), turns,
                                    summarizer=self.summarizer)

    def offload_if_large(self, agent: Agent, session_id: str, label: str,
                         content: str) -> str:
        """Replace an oversized tool result with a readable reference."""
        resolved = self.context_for(agent)
        if resolved.store is None:
            return content
        replaced, artifact = self.context.offload(
            resolved, content, label=label, session_id=session_id)
        if artifact is not None:
            self.sessions.log(session_id, "context_offload", actor=agent.id,
                              payload={"label": label, "artifact": artifact.id,
                                       "size_bytes": artifact.size_bytes})
        return replaced

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
                environments=tuple(sub.get("environments", [])),
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
            if self.delegation_depth(session_id) >= agent.harness.max_subagent_depth:
                return {"ok": False, "error": "max sub-agent depth reached"}
            child = self.run(
                to_agent_id,
                task,
                created_by=agent.id,
                parent_session_id=session_id,
            )
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
                    "mode": "delegate",
                },
            )
            return {
                "ok": child.error is None,
                "output": child.output,
                "session_url": child.session_url,
                "error": child.error,
            }

        def assign(to_agent_id: str, task: str,
                   priority: str = TaskPriority.UNKNOWN.value) -> dict[str, Any]:
            """Hand a task to another agent and get a handle back immediately.

            Use this to ask several agents at once, or when the answer is not
            needed before this turn ends. `delegate` is still the right tool
            when you want the answer now: it is simpler to read and most
            delegation genuinely wants an answer now.

            Who you may hand work to is decided here and does not change: the
            same check `delegate` makes, with the same refusal.

            `priority` is one of urgent, high, normal, low. It orders what
            `check`, `gather` and your outstanding list return, and it names
            the least important thing you are holding when you hit your
            parallel bound. It does **not** jump the queue, because there is
            no queue: at the bound this refuses rather than waiting, and an
            urgent task assigned over the bound is still refused.
            """
            try:
                rank = TaskPriority(priority)
            except ValueError:
                return {
                    "ok": False,
                    "error": (
                        f"unknown priority {priority!r}; use one of "
                        f"{[p.value for p in TaskPriority]}"
                    ),
                }
            if not self.org.can_delegate(agent.id, to_agent_id):
                return {
                    "ok": False,
                    "error": f"{agent.name} may not delegate to {to_agent_id}",
                }
            if self.delegation_depth(session_id) >= agent.harness.max_subagent_depth:
                return {"ok": False, "error": "max sub-agent depth reached"}

            # `max_parallel_subagents` is declared on every harness and until
            # now bound nothing, because serial delegation could not fan out.
            # It refuses rather than queueing: a queue nobody declared is a
            # bound nobody reviewed, with a latency nobody reviewed either.
            bound = agent.harness.max_parallel_subagents
            held = self._by_priority(self.unsettled_handles(session_id))
            if bound and len(held) >= bound:
                # Naming the least important thing being held makes the
                # refusal actionable rather than merely correct: the leader
                # can see what to collect or let go of (ADR-0097).
                lowest = held[-1]
                with self._assign_lock:
                    low = self._assignments.get(lowest)
                return {
                    "ok": False,
                    "error": (
                        f"{agent.name} already holds {len(held)} unsettled "
                        f"handles and max_parallel_subagents is {bound}; "
                        f"the least important is {lowest} "
                        f"({low.priority if low else 'unknown'}: "
                        f"{low.task[:60] if low else '?'}). Collect or "
                        "abandon one before assigning more — priority orders "
                        "what you see, it does not jump this bound"
                    ),
                    "outstanding": held,
                    "lowest_priority_handle": lowest,
                    "max_parallel_subagents": bound,
                }

            # The child session is created here, on the calling thread, so the
            # handle is a real, addressable session before this returns.
            child = self.sessions.create(
                to_agent_id,
                title=task[:80],
                created_by=agent.id,
                parent_session_id=session_id,
            )
            assignment = _Assignment(
                handle=child.id,
                parent_session_id=session_id,
                by_agent_id=agent.id,
                to_agent_id=to_agent_id,
                task=task,
                future=Future(),            # replaced by the submitted one below
                priority=rank.value,
                assigned_at=now_iso(),
                assigned_at_mark=self._parent_turn_mark(session_id),
            )
            with self._assign_lock:
                self._assignments[child.id] = assignment
            self.sessions.log(
                session_id,
                "delegation",
                actor=agent.id,
                payload={
                    "to": to_agent_id,
                    "task": task,
                    "child_session": child.id,
                    "child_session_url": self.sessions.url(child.id),
                    "state": child.state.value,
                    "mode": "assign",
                    "handle": child.id,
                    # On the audit event, so the trail says what the leader
                    # thought mattered at the time rather than what it says now.
                    "priority": rank.value,
                },
            )
            assignment.future = self._worker_pool().submit(
                self._run_assigned, to_agent_id, task, child.id, agent.id,
            )
            return {
                "ok": True,
                "handle": child.id,
                "to": to_agent_id,
                "state": child.state.value,
                "settled": False,
                "priority": rank.value,
                "session_url": self.sessions.url(child.id),
            }

        def _held(handle: str) -> Optional[_Assignment]:
            with self._assign_lock:
                assignment = self._assignments.get(handle)
            if assignment is None or assignment.parent_session_id != session_id:
                return None
            return assignment

        def check(handle: str) -> dict[str, Any]:
            """Ask what a handle is doing, without waiting for it.

            A handle parked on a human decision comes back as `waiting_human`.
            That is a state to report and get on with something else around,
            not one to wait on: the person may take hours.
            """
            assignment = _held(handle)
            if assignment is None:
                return {"ok": False, "handle": handle, "settled": True,
                        "state": SessionState.FAILED.value,
                        "error": f"{agent.name} does not hold handle {handle}"}
            child = self.sessions.get(handle)
            if child is None:
                return {"ok": False, "handle": handle, "settled": True,
                        "state": SessionState.FAILED.value,
                        "error": f"handle {handle} no longer exists"}
            # Terminal in the store is not yet collectable: `run` marks the
            # child COMPLETED a moment before the worker's future resolves
            # with the output. Reporting it settled in that gap delivered an
            # empty result and marked it collected, so it could never be
            # delivered at all. A handle `gather` gave up on is settled by
            # that decision, whatever its worker is still doing.
            settled = child.state in TERMINAL_STATES and (
                assignment.future.done() or assignment.abandoned)
            out: dict[str, Any] = {
                "ok": True,
                "handle": handle,
                "to": assignment.to_agent_id,
                "task": assignment.task,
                "priority": assignment.priority,
                "assigned_at": assignment.assigned_at,
                "state": child.state.value,
                "settled": settled,
                "waiting_human": child.state is SessionState.WAITING_HUMAN,
                "session_url": self.sessions.url(handle),
            }
            if child.state is SessionState.WAITING_HUMAN:
                out["note"] = (
                    "parked on a human decision; this is reported, not waited "
                    "on, and the handle still counts against your parallel bound"
                )
            if settled:
                out.update(self._collect(assignment))
                out["ok"] = out.get("error") is None
                # A handle collected several turns later returns into a
                # conversation that has moved on. Nothing here can make an
                # agent re-read what it asked for, but leaving it to notice
                # on its own is how a leader acts on an answer to a question
                # that is no longer the question. The task text comes back
                # with the result, and the fact that time passed is said out
                # loud rather than inferred from a timestamp.
                since = (self._parent_turn_mark(session_id)
                         - assignment.assigned_at_mark)
                out["messages_since_assigned"] = max(0, since)
                out["stale"] = since > 0
                if since > 0:
                    out["staleness"] = (
                        f"this was assigned {since} message(s) ago; re-read "
                        f"the task it answers — {assignment.task!r} — before "
                        "acting on it, because the conversation has moved on"
                    )
            return out

        def gather(handles: Any, timeout_s: float = 0.0) -> dict[str, Any]:
            """Wait for several handles and return what each one came back with.

            Every handle settles. One that cannot be resolved before the
            deadline comes back failed with the reason, because a handle that
            hangs forever leaves you with no turn in which to notice.
            """
            if isinstance(handles, str):
                handles = [handles]
            window = float(timeout_s) or float(
                agent.harness.wall_clock_budget_s or 0
            ) or 300.0
            deadline = time.monotonic() + window

            # Most important first, so a leader reading a long list reads the
            # thing that matters first rather than the thing assigned first.
            results: list[dict[str, Any]] = []
            for handle in self._by_priority(list(handles)):
                assignment = _held(handle)
                if assignment is None:
                    results.append({
                        "ok": False, "handle": handle, "settled": True,
                        "state": SessionState.FAILED.value,
                        "error": f"{agent.name} does not hold handle {handle}",
                    })
                    continue
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    assignment.future.result(timeout=remaining)
                except FutureTimeout:
                    # The worker may still be running. Its result is discarded
                    # and the handle fails, rather than being left open: the
                    # `assignment_abandoned` event records that this is a
                    # deadline, not a failure of the work itself.
                    with self._assign_lock:
                        assignment.abandoned = True
                        assignment.collected = True
                    reason = (
                        f"deadline passed after {round(window, 1)}s with the "
                        "work still running; the result is abandoned"
                    )
                    self.sessions.log(handle, "assignment_abandoned",
                                      actor=agent.id, payload={"error": reason})
                    self.sessions.set_state(handle, SessionState.FAILED)
                    results.append({
                        "ok": False, "handle": handle, "settled": True,
                        "to": assignment.to_agent_id,
                        "state": SessionState.FAILED.value, "error": reason,
                    })
                    continue
                except BaseException:                   # noqa: BLE001
                    pass                                # surfaced by _collect
                results.append(check(handle))
            return {
                "ok": all(r.get("ok") for r in results),
                "results": results,
                "outstanding": self._by_priority(
                    self.unsettled_handles(session_id)),
            }

        def spawn_subagent(name: str, task: str, instructions: str = "") -> dict[str, Any]:
            """Create an ephemeral sub-agent under this agent and run one task."""
            sub = Agent(
                name=f"{agent.name}/{name}",
                title=name,
                kind=AgentKind.SUBAGENT,
                org_unit_id=agent.org_unit_id,
                manager_agent_id=agent.id,
                system_id=agent.system_id,
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

        return {"delegate": delegate, "assign": assign, "check": check,
                "gather": gather, "spawn_subagent": spawn_subagent}

    def workflow_binding(self, workflow_id: str) -> Optional[WorkflowBinding]:
        exact = next(
            (w for w in self.workflow_bindings if w.workflow == workflow_id), None
        )
        return exact or next(
            (w for w in self.workflow_bindings if not w.workflow), None
        )

    def workflow_engine_for(self, agent: Agent, workflow_id: str) -> Any:
        """Resolve the engine for one workflow, bound to this agent's boundary."""
        binding = self.workflow_binding(workflow_id) or WorkflowBinding(
            workflow=workflow_id
        )
        return engine_for_binding(
            binding,
            boundary_for(
                agent,
                tenant_id=self.tenant_id,
                sandbox_runner=getattr(self.harness, "sandboxes", None),
            ),
            transport=self.workflow_transport,
            guardrails=self.guardrail_engine(agent),
        )

    def _workflow_tools(self, agent: Agent, session_id: str) -> dict[str, Any]:
        def run_workflow(workflow_id: str, inputs: Optional[dict] = None) -> dict[str, Any]:
            """Execute an encoded LangGraph workflow the agent is entitled to."""
            if workflow_id not in agent.workflow_ids:
                return {"ok": False, "error": f"{agent.name} may not run {workflow_id}"}
            ref = self.store.get(WORKFLOWS, workflow_id, WorkflowRef)
            if ref is None:
                return {"ok": False, "error": f"no workflow {workflow_id}"}
            engine = self.workflow_engine_for(agent, workflow_id)
            if isinstance(engine, ServiceEngine):
                return self._run_service_workflow(
                    agent, session_id, engine, ref, inputs or {}
                )
            res = engine.run(
                ref,
                inputs or {},
                # The engine is handed the *agent's* callables: it reaches
                # exactly what its caller reaches, and nothing else.
                tool_caller=lambda name, args: self.harness.call(
                    agent, name, **args
                ).value,
                agent_caller=lambda aid, args: self._delegation_tools(
                    agent, session_id
                )["delegate"](aid, args.get("task", "")),
                workflows=self._callable_workflows(agent, ref),
                workflow_caller=self.external_workflow_caller(agent, session_id),
            )
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

    def _callable_workflows(self, agent: Agent, ref: WorkflowRef) -> dict[str, WorkflowRef]:
        """The workflows a run may call: the agent's own, and the ones its
        graph's steps name — a step owned by another agent calls a workflow
        *that* agent is entitled to, and is checked against it (ADR-0110)."""
        wanted = set(agent.workflow_ids)
        wanted |= {n.get("workflow") for n in (ref.graph or {}).get("nodes", [])
                   if n.get("kind") == "workflow" and n.get("workflow")}
        out = {}
        for wid in sorted(wanted):
            found = self.store.get(WORKFLOWS, wid, WorkflowRef)
            if found is not None:
                out[wid] = found
        return out

    def external_workflow_caller(
        self, agent: Agent, session_id: str,
    ) -> Callable[[str, dict, dict], Any]:
        """How a step reaches a workflow whose body lives in an engine
        (ADR-0110): through the ADR-0056 invoker, under the step owner's
        boundary. An out-of-process engine is egress, so tenant, allowlist,
        data class, credential and approval are all checked before anything
        leaves, and the reply comes back as untrusted data."""

        def call(workflow_id: str, args: dict, step: dict) -> Any:
            owner_id = step.get("owner") or ""
            owner = self.org.agent(owner_id) if owner_id else None
            caller = owner or agent
            if owner_id and owner is None and owner_id != agent.id:
                # A team or a person owns it: the running agent is the hand
                # that makes the call, and is the boundary checked.
                caller = agent
            ref = self.store.get(WORKFLOWS, workflow_id, WorkflowRef)
            if ref is None:
                raise KeyError(f"no workflow {workflow_id}")
            engine = self.workflow_engine_for(caller, workflow_id)
            if not isinstance(engine, ServiceEngine):
                raise RuntimeError(
                    f"workflow '{workflow_id}' is built in an engine, and the "
                    f"binding runs it in process with '{engine.descriptor.name}', "
                    "which holds no body for it")
            classes = tuple((ref.interface or {}).get("receives_data_classes", []))
            result = self._run_service_workflow(
                caller, session_id, engine, ref, dict(args or {}),
                data_classes=classes)
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "refused")
            return result["state"].get("result")

        return call

    def _run_service_workflow(
        self, agent: Agent, session_id: str, engine: ServiceEngine,
        ref: WorkflowRef, inputs: dict[str, Any],
        data_classes: Optional[tuple[str, ...]] = None,
    ) -> dict[str, Any]:
        """Hand a workflow to another process — which is an egress event."""
        # What is sent is what the interface says the body receives; with no
        # interface, everything the agent may read is assumed to leave.
        classes = data_classes if data_classes is not None else tuple(
            (ref.interface or {}).get("receives_data_classes")
            or (agent.memory or {}).get("readable_data_classes", []))
        call = engine.invoke(ref, inputs, input_data_classes=classes)
        self.sessions.log(
            session_id, "workflow", actor=agent.id,
            payload={
                "workflow": ref.name,
                "engine": engine.descriptor.name,
                "invocation": engine.descriptor.mode.value,
                "checks": call.checks,
                "refusal": call.refusal,
                "outside_agent_sandbox": True,
            },
        )
        if call.refused:
            return {"ok": False, "state": {}, "path": [],
                    "error": f"{call.refusal}: {call.detail}"}
        return {"ok": True, "state": {"result": call.value}, "path": [ref.id],
                "engine": engine.descriptor.name}

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
