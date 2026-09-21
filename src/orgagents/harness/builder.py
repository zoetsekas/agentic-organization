"""Assembles an agent's runnable toolset from its harness definition.

The builder is the single place where a declarative `Harness` becomes
callables: MCP tools, relational grants, data-plane access, sandbox execution,
delegation to reports, workflow invocation and messaging. Policy checks
(approval gates, delegation legality, SQL grants) live here, not in the model
loop, so the same rules apply whichever runtime executes the agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..data.planes import AccessDenied, DataPlanes
from ..models import Agent, Skill, ToolBinding, Visibility
from ..org import OrgChart
from ..store import SKILLS, Store
from .mcp import MCPRegistry
from .relational import RelationalMCP
from .sandbox import SandboxRunner


@dataclass
class ToolCallResult:
    name: str
    ok: bool
    value: Any = None
    error: str = ""
    requires_approval: bool = False
    #: Set when the call was outside the agent's mandate: the decision class
    #: it would have taken, and the agent it belongs to (ADR-0065).
    decision: Optional[str] = None
    escalate_to: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


class HarnessBuilder:
    """Turns a `Harness` into a name -> callable map for one agent."""

    def __init__(
        self,
        store: Store,
        *,
        registry: Optional[MCPRegistry] = None,
        sandboxes: Optional[SandboxRunner] = None,
        dsn_resolver: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.store = store
        self.org = OrgChart(store)
        self.planes = DataPlanes(store)
        self.registry = registry or MCPRegistry()
        self.sandboxes = sandboxes or SandboxRunner(store)
        # Maps a secret reference to an actual DSN. Defaults to env lookup.
        self.dsn_resolver = dsn_resolver or self._default_dsn_resolver

    @staticmethod
    def _default_dsn_resolver(secret_ref: str) -> str:
        import os

        return os.environ.get(secret_ref, ":memory:")

    # -- assembly ----------------------------------------------------------

    def build(self, agent: Agent) -> dict[str, Callable[..., Any]]:
        tools: dict[str, Callable[..., Any]] = {}
        tools.update(self._mcp_tools(agent))
        tools.update(self._relational_tools(agent))
        tools.update(self._data_tools(agent))
        tools.update(self._sandbox_tools(agent))
        tools.update(self._org_tools(agent))
        return tools

    def bindings(self, agent: Agent) -> list[ToolBinding]:
        """Describe the assembled toolset for the UI and for the model prompt."""
        out = list(agent.harness.tools)
        for name in self.build(agent):
            if not any(b.name == name for b in out):
                out.append(ToolBinding(name=name, source="builtin"))
        return out

    # -- tool families -----------------------------------------------------

    def _mcp_tools(self, agent: Agent) -> dict[str, Callable[..., Any]]:
        tools: dict[str, Callable[..., Any]] = {}
        for ref in agent.harness.mcp_servers:
            try:
                for proxy in self.registry.resolve(ref):
                    tools[proxy.qualified_name] = proxy
            except KeyError:
                # An unmounted server is reported, not fatal: the designer may
                # be previewing a harness whose server is not running yet.
                tools[f"{ref.name}__unavailable"] = (
                    lambda _n=ref.name, **_: ToolCallResult(
                        _n, False, error=f"MCP server '{_n}' not mounted"
                    )
                )
        return tools

    def _relational_tools(self, agent: Agent) -> dict[str, Callable[..., Any]]:
        tools: dict[str, Callable[..., Any]] = {}
        for grant in agent.harness.relational_grants:
            dsn = self.dsn_resolver(grant.dsn_secret_ref or grant.connection_name)
            server = RelationalMCP(grant, dsn).as_mcp_server()
            self.registry.register(server)
            for name, fn in server.tools.items():
                tools[f"{server.name}__{name}"] = fn
        return tools

    def _data_tools(self, agent: Agent) -> dict[str, Callable[..., Any]]:
        def memory_write(
            namespace: str,
            key: str,
            value: Any,
            visibility: str = "private",
            groups: Optional[list[str]] = None,
        ) -> dict[str, Any]:
            """Write a record to a data plane the agent may write to."""
            rec = self.planes.write(
                agent,
                namespace,
                key,
                value,
                visibility=Visibility(visibility),
                groups=groups or [],
            )
            return rec.model_dump()

        def memory_read(namespace: str, key: str) -> Optional[dict[str, Any]]:
            """Read one record, honoring the agent's grants."""
            rec = self.planes.read(agent, namespace, key)
            return rec.model_dump() if rec else None

        def memory_query(namespace_glob: str = "*", tag: Optional[str] = None) -> list[dict]:
            """List every record visible to the agent."""
            return [r.model_dump() for r in self.planes.query(
                agent, namespace_glob=namespace_glob, tag=tag
            )]

        return {
            "memory_write": memory_write,
            "memory_read": memory_read,
            "memory_query": memory_query,
        }

    def _sandbox_tools(self, agent: Agent) -> dict[str, Callable[..., Any]]:
        if agent.sandbox is None:
            return {}

        def sandbox_exec(command: str, files: Optional[dict[str, str]] = None) -> dict:
            """Run a command inside the agent's sandbox template."""
            return self.sandboxes.run(agent.sandbox, command, files=files)

        def sandbox_info() -> dict:
            """Describe the resolved sandbox environment."""
            return self.sandboxes.resolve(agent.sandbox).model_dump()

        return {"sandbox_exec": sandbox_exec, "sandbox_info": sandbox_info}

    def _org_tools(self, agent: Agent) -> dict[str, Callable[..., Any]]:
        def list_reports() -> list[dict[str, str]]:
            """List the agents this agent may delegate to."""
            out = [
                {"id": r.id, "name": r.name, "title": r.title, "relation": "report"}
                for r in self.org.reports(agent.id)
            ]
            for pid in agent.peer_agent_ids:
                p = self.org.agent(pid)
                if p:
                    out.append(
                        {"id": p.id, "name": p.name, "title": p.title, "relation": "peer"}
                    )
            return out

        def whoami() -> dict[str, Any]:
            """Describe this agent's place in the organization."""
            chain = self.org.chain_of_command(agent.id)
            return {
                "id": agent.id,
                "name": agent.name,
                "title": agent.title,
                "kind": agent.kind.value,
                "human": agent.human.model_dump() if agent.human else None,
                "groups": agent.groups,
                "chain_of_command": [a.name for a in chain],
                "reports": [r.name for r in self.org.reports(agent.id)],
            }

        return {"list_reports": list_reports, "whoami": whoami}

    # -- skills ------------------------------------------------------------

    def skills(self, agent: Agent) -> list[Skill]:
        out = []
        for sid in agent.skill_ids:
            s = self.store.get(SKILLS, sid, Skill)
            if s:
                out.append(s)
        return out

    def system_prompt(self, agent: Agent) -> str:
        """Compose the agent's operating instructions from org + harness + skills."""
        chain = " -> ".join(a.name for a in reversed(self.org.chain_of_command(agent.id)))
        reports = ", ".join(r.name for r in self.org.reports(agent.id)) or "none"
        human = agent.human
        parts = [
            agent.harness.system_prompt.strip()
            or f"You are {agent.name}, the {agent.title or agent.kind.value} agent.",
            "",
            "## Organizational context",
            f"- Reporting line: {chain or agent.name}",
            f"- Direct reports you may delegate to: {reports}",
            f"- Groups (protected data access): {', '.join(agent.groups) or 'none'}",
        ]
        if human:
            parts += [
                f"- Human counterpart: {human.display_name} ({human.role_title or 'owner'}), "
                f"{human.email}",
                f"- Always request approval before: "
                f"{', '.join(human.approval_required_for) or 'nothing'}",
            ]
        skills = self.skills(agent)
        if skills:
            parts += ["", "## Skills available"]
            parts += [f"- {s.name}: {s.description}" for s in skills]
        parts += [
            "",
            "## Operating rules",
            "- Prefer delegating to a direct report when the task fits their remit.",
            "- Use encoded workflows for multi-step processes that must be auditable.",
            "- Never write to a data plane you were not granted; ask instead.",
            f"- Escalate to {human.display_name if human else 'your manager'} after "
            f"{agent.harness.escalate_to_human_after_failures} consecutive failures.",
        ]
        return "\n".join(parts)

    # -- policy helpers ----------------------------------------------------

    def requires_approval(self, agent: Agent, tool_name: str) -> bool:
        if tool_name in agent.harness.interrupt_on:
            return True
        if agent.human and tool_name in agent.human.approval_required_for:
            return True
        return any(
            b.name == tool_name and b.requires_approval for b in agent.harness.tools
        )

    def decision_class(self, agent: Agent, tool_name: str) -> Optional[str]:
        """The decision class this tool constitutes, if it constitutes one."""
        for binding in agent.harness.tools:
            if binding.name == tool_name:
                return binding.decision
        return None

    def condition_failure(
        self, agent: Agent, decision: str, arguments: dict[str, Any]
    ) -> Optional[str]:
        """Check a mandate's conditions against the call, or say why not.

        Conditions were carried into the IR and read by nothing, so a mandate
        of "approve spend under 250k" bounded nothing at all (ADR-0071). They
        are now evaluated, with a small deliberate grammar:

        * ``max_<field>`` — the call's ``<field>`` must be present and at most
          this;
        * ``min_<field>`` — present and at least this;
        * ``<field>_in`` — present and one of these.

        Two rules matter more than the grammar. A condition naming a field the
        call does not supply is a **refusal**, because otherwise omitting the
        amount removes the ceiling. And a key this grammar cannot parse is
        also a refusal: silently ignoring a condition nobody can evaluate is
        exactly how these became decorative.
        """
        for condition in agent.mandate_conditions:
            for key, limit in condition.items():
                if key.startswith("max_") or key.startswith("min_"):
                    field = key[4:]
                    if field not in arguments:
                        return (
                            f"'{decision}' is bounded by {key}={limit}, and the "
                            f"call supplies no '{field}' to check it against"
                        )
                    value = arguments[field]
                    if key.startswith("max_") and value > limit:
                        return f"{field}={value} exceeds {key}={limit}"
                    if key.startswith("min_") and value < limit:
                        return f"{field}={value} is below {key}={limit}"
                elif key.endswith("_in"):
                    field = key[:-3]
                    if field not in arguments:
                        return (
                            f"'{decision}' is bounded by {key}, and the call "
                            f"supplies no '{field}' to check it against"
                        )
                    if arguments[field] not in limit:
                        return f"{field}={arguments[field]!r} is not one of {limit}"
                else:
                    return (
                        f"'{decision}' carries condition '{key}', which this "
                        "platform cannot evaluate; a bound nobody can check is "
                        "not a bound"
                    )
        return None

    def refusal(
        self,
        agent: Agent,
        tool_name: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> Optional[ToolCallResult]:
        """Why this call may not proceed, or `None` if it may.

        One implementation, used by both `call` and the wrappers handed to a
        runtime framework — because a policy that holds on one path and not
        the other is not a policy (ADR-0067 rule 5).

        The order is mandate, then approval (ADR-0065). Permission is already
        settled: a tool the agent was never granted is simply absent from the
        assembled toolset, and that refusal stops there rather than escalating,
        because sending a human an action the agent could never perform spends
        their attention on nothing.
        """
        decision = self.decision_class(agent, tool_name)
        if decision and decision not in agent.mandate:
            holder = self.org.mandate_holder(agent.id, decision)
            if holder is None:
                # Naming who does hold it is not routing to them: an agent
                # outside this line is reached through the process that owns
                # the decision, never by escalating past a control (ADR-0070).
                elsewhere = [
                    a.name for a in self.org.mandate_holders(decision)
                    if a.id != agent.id
                ]
                where = (
                    f"; it is held by {', '.join(elsewhere)}, who are not in "
                    "this escalation line"
                    if elsewhere
                    else "; no agent in the organization holds it"
                )
                return ToolCallResult(
                    tool_name,
                    False,
                    error=(
                        f"'{decision}' is outside every mandate above this "
                        f"agent{where}"
                    ),
                    decision=decision,
                )
            return ToolCallResult(
                tool_name,
                False,
                error=(
                    f"'{decision}' is above this agent's mandate; it belongs "
                    f"to '{holder.name}'"
                ),
                decision=decision,
                escalate_to=holder.id,
                meta={"holder": holder.id,
                      "approver": holder.human.email if holder.human else None},
            )

        if decision:
            failed = self.condition_failure(agent, decision, arguments or {})
            if failed:
                return ToolCallResult(
                    tool_name,
                    False,
                    error=f"outside the bounds on '{decision}': {failed}",
                    decision=decision,
                )

        if self.requires_approval(agent, tool_name):
            return ToolCallResult(
                tool_name,
                False,
                error="human approval required",
                requires_approval=True,
                meta={"approver": agent.human.email if agent.human else None},
            )
        return None

    def guarded(
        self, agent: Agent, tools: dict[str, Callable[..., Any]]
    ) -> dict[str, Callable[..., Any]]:
        """Wrap an assembled toolset so a framework cannot route around policy.

        The runtime hands these callables to deep agents or the OpenAI SDK,
        which invoke them directly — never through `call`. Without this
        wrapper every mandate and approval check is enforced only against
        callers that were already being careful, which is the wrong half.
        """

        def wrap(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
            def guarded_tool(**kwargs: Any) -> Any:
                refused = self.refusal(agent, name, kwargs)
                if refused is not None:
                    return {
                        "ok": False,
                        "error": refused.error,
                        "requires_approval": refused.requires_approval,
                        "decision": refused.decision,
                        "escalate_to": refused.escalate_to,
                    }
                return fn(**kwargs)

            guarded_tool.__name__ = getattr(fn, "__name__", name)
            guarded_tool.__doc__ = fn.__doc__
            return guarded_tool

        return {name: wrap(name, fn) for name, fn in tools.items()}

    def call(self, agent: Agent, tool_name: str, **kwargs: Any) -> ToolCallResult:
        """Invoke a tool with policy enforcement and structured errors."""
        tools = self.build(agent)
        fn = tools.get(tool_name)
        if fn is None:
            return ToolCallResult(tool_name, False, error=f"no such tool '{tool_name}'")
        refused = self.refusal(agent, tool_name, kwargs)
        if refused is not None:
            return refused
        try:
            return ToolCallResult(tool_name, True, value=fn(**kwargs))
        except AccessDenied as e:
            return ToolCallResult(tool_name, False, error=f"access denied: {e}")
        except Exception as e:  # surfaced to the trace, never swallowed
            return ToolCallResult(tool_name, False, error=f"{type(e).__name__}: {e}")
