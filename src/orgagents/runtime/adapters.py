"""Runtime adapters.

The platform's agent definition is framework-neutral. An adapter takes the
assembled harness (system prompt, tools, sub-agent definitions, limits) and
runs one turn on a concrete framework: LangChain **deep agents**, the **OpenAI
Agents SDK**, native LangGraph, or a dependency-free echo runtime used by
tests and the designer's dry-run preview.

Adapters import their framework lazily, so a deployment only installs the one
it uses.

Guardrails are enforced *here*, in `RuntimeAdapter.run`, not in the framework.
Each framework bounds a run differently — or not at all — so a harness limit
expressed once in the spec would otherwise mean something different on each
runtime, or nothing. Subclasses implement `_run`; the base class owns the
budget.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..models import Agent, Runtime
from ..plugins import ProviderDescriptor, Registry
from .stub_model import chat_model_for


class BudgetExceeded(RuntimeError):
    """A harness budget was spent. Carries which one, so the reason survives."""

    def __init__(self, limit: str, spent: float, allowed: float) -> None:
        super().__init__(
            f"{limit} budget exhausted: {spent:.0f} of {allowed:.0f} used"
        )
        self.limit = limit
        self.spent = spent
        self.allowed = allowed


@dataclass
class TurnBudget:
    """Token and wall-clock ceilings for one agent run.

    A run is several turns — the first one, plus any output-contract retry —
    so the budget is held by the adapter for the whole run rather than reset
    per turn.

    It binds *between* turns, not inside one. Neither framework lets us stop a
    turn part-way without killing the thread running it, and a half-executed
    tool call is worse than an overrun, so the honest guarantee is: a turn
    never *starts* on an exhausted budget, and the overrun is bounded by one
    turn. `max_turns` is what bounds that turn from the inside.
    """

    tokens: int = 0            # 0 means unbounded
    seconds: float = 0.0       # 0 means unbounded
    tokens_spent: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @classmethod
    def from_harness(cls, agent: Agent) -> "TurnBudget":
        return cls(
            tokens=agent.harness.token_budget,
            seconds=float(agent.harness.wall_clock_budget_s),
        )

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def check(self) -> None:
        """Refuse to start another turn on an exhausted budget."""
        if self.tokens and self.tokens_spent >= self.tokens:
            raise BudgetExceeded("token", self.tokens_spent, self.tokens)
        if self.seconds and self.elapsed >= self.seconds:
            raise BudgetExceeded("wall clock", self.elapsed, self.seconds)

    def spend(self, tokens: int) -> None:
        self.tokens_spent += max(0, tokens)


#: Where an agent's material belongs (ADR-0036). Everything else on the
#: framework's virtual filesystem is outside what the platform governs.
WORKSPACE = "/workspace"


def filesystem_rules(agent: Agent) -> list[dict[str, Any]]:
    """Permission rules for a framework-provided virtual filesystem.

    deep agents' file tools are real tools an agent can reach, and until this
    existed they bypassed the artifact store and the data planes entirely — an
    agent that may not write anywhere could still write everywhere, inside the
    framework.

    Two properties matter. `_check_fs_permission` takes the **first matching
    rule**, so specific allows come before the floor. And an unmatched path is
    **allowed** by default, which is the opposite of ADR-0008 — so the deny
    floor is written down explicitly rather than assumed.
    """
    writable = any(g.can_write for g in agent.harness.data_grants)
    operations = ["read", "write"] if writable else ["read"]
    return [
        {"operations": operations, "paths": [f"{WORKSPACE}/**"], "mode": "allow"},
        # An agent with no write grant reaches this rule for a write, which is
        # the intended refusal rather than an oversight.
        {"operations": ["read", "write"], "paths": ["/**"], "mode": "deny"},
    ]


def recursion_limit(max_turns: int) -> int:
    """Translate harness turns into LangGraph super-steps.

    `max_turns` counts agent turns — one model call and the tool calls it
    asked for. LangGraph's `recursion_limit` counts graph super-steps, and a
    ReAct turn is two of them (the model node, then the tool node), plus one
    step to enter the graph.

    This is a **backstop**, not the turn limit. `ModelCallLimitMiddleware`
    expresses `max_turns` exactly in the framework's own terms and is what
    actually bounds the run (ADR-0067); `recursion_limit` remains as the graph
    depth beyond which something has gone wrong structurally.
    """
    return 2 * max(1, max_turns) + 1


def _langchain_tokens(result: Any) -> int:
    """Sum token usage across the AI messages LangChain returned."""
    total = 0
    for message in (result or {}).get("messages", []) or []:
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, dict):
            total += int(usage.get("total_tokens") or 0)
    return total


def _open_schema(fn: Callable[..., Any], StructuredTool: Any) -> dict[str, Any]:
    """The JSON schema a framework should show for one of our tools.

    Every tool the runtime hands a framework is a `guarded` wrapper taking
    ``**kwargs``, and an MCP proxy is ``**kwargs`` too. Inferred from that
    signature, LangChain builds a schema with no fields and **drops every
    argument** on the way in — a model's call arrives empty and the mandate
    condition that needed `amount` refuses it. So the schema comes from what
    the tool really takes: an MCP server's own `inputSchema`, else the wrapped
    function's signature; and it stays open to extra fields either way, so an
    argument is never silently lost (ADR-0109).
    """
    inner = fn
    while hasattr(inner, "guarded_fn"):
        inner = inner.guarded_fn
    schema = getattr(inner, "input_schema", None)
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
        try:
            probe = StructuredTool.from_function(func=inner, name="probe")
            derived = probe.args_schema.model_json_schema()
            props = derived.get("properties", {})
            if set(props) != {"kwargs"}:
                schema = {"type": "object", "properties": props,
                          "required": derived.get("required", [])}
        except Exception:                                 # noqa: BLE001
            pass
    return {**schema, "additionalProperties": True}


def langchain_tools(tools: dict[str, Callable[..., Any]], StructuredTool: Any) -> list[Any]:
    """Our toolset as LangChain tools that pass their arguments through."""
    out = []
    for name, fn in tools.items():
        inner = fn
        while hasattr(inner, "guarded_fn"):
            inner = inner.guarded_fn
        description = (getattr(inner, "description", "") or fn.__doc__
                       or inner.__doc__ or name).strip()
        out.append(StructuredTool(name=name, description=description, func=fn,
                                  args_schema=_open_schema(fn, StructuredTool)))
    return out


def langchain_tool_calls(result: Any) -> list[dict[str, Any]]:
    """Each tool call a LangChain run made, with what came back.

    Paired by call id, so a caller — the chat window, a scenario, an auditor —
    sees the refusal a mandate produced next to the call that earned it.
    """
    messages = (result or {}).get("messages", []) or []
    outcomes = {getattr(m, "tool_call_id", None): m for m in messages
                if getattr(m, "type", "") == "tool"}
    calls = []
    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            answer = outcomes.get(call.get("id"))
            content = getattr(answer, "content", None)
            try:
                parsed = json.loads(content) if isinstance(content, str) else content
            except ValueError:
                parsed = content
            ok = (answer is not None
                  and getattr(answer, "status", "success") != "error"
                  and not (isinstance(parsed, dict) and parsed.get("ok") is False))
            calls.append({"tool": call.get("name"), "arguments": call.get("args"),
                          "ok": ok, "result": parsed})
    return calls


def _openai_tokens(result: Any) -> int:
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    return int(getattr(usage, "total_tokens", 0) or 0)


@dataclass
class TurnOutput:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    raw: Any = None


class RuntimeAdapter:
    """Base class: build a framework agent, then run turns against it."""

    runtime: Runtime

    def __init__(
        self,
        agent: Agent,
        system_prompt: str,
        tools: dict[str, Callable[..., Any]],
        *,
        subagents: Optional[list[dict[str, Any]]] = None,
        budget: Optional[TurnBudget] = None,
    ) -> None:
        self.agent = agent
        self.system_prompt = system_prompt
        self.tools = tools
        self.subagents = subagents or []
        self.budget = budget if budget is not None else TurnBudget.from_harness(agent)

    def run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        """Run one turn against the framework, within the harness budget."""
        self.budget.check()
        out = self._run(prompt, history)
        self.budget.spend(out.tokens)
        return out

    def _run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        raise NotImplementedError


class DeepAgentsAdapter(RuntimeAdapter):
    """LangChain deep agents: planning, virtual filesystem and sub-agents."""

    runtime = Runtime.DEEPAGENTS

    def _subagents(self) -> list[dict[str, Any]]:
        """Map platform sub-agent descriptions onto the framework's shape.

        `mode="isolated"` is the one that matches ADR-0027: the sub-agent gets
        its own context and returns a result to its caller. `fork` would hand
        it the parent's conversation, which is context the platform did not
        decide to give it.
        """
        return [
            {
                "name": sa["name"],
                "description": sa.get("description", ""),
                "system_prompt": sa.get("prompt", ""),
                "mode": "isolated",
                **({"model": sa["model"]} if sa.get("model") else {}),
            }
            for sa in self.subagents
        ]

    def _build(self) -> Any:
        from deepagents import create_deep_agent  # type: ignore
        from langchain_core.tools import StructuredTool  # type: ignore

        tools = langchain_tools(self.tools, StructuredTool)
        from deepagents import FilesystemPermission  # type: ignore
        from langchain.agents.middleware import ModelCallLimitMiddleware  # type: ignore

        return create_deep_agent(
            model=self.model(),
            tools=tools,
            system_prompt=self.system_prompt,
            subagents=self._subagents(),
            # The harness already says which actions stop for a human; the
            # framework can hold the interrupt rather than us re-inventing it.
            interrupt_on={name: True for name in self.agent.harness.interrupt_on},
            # The virtual filesystem is governed like anything else the agent
            # can reach (ADR-0067).
            permissions=[
                FilesystemPermission(**rule)
                for rule in filesystem_rules(self.agent)
            ],
            middleware=[
                # `max_turns` means turns, and this says so in the framework's
                # own terms. `end` stops the run cleanly rather than raising:
                # an agent that used its turns did not fail.
                ModelCallLimitMiddleware(
                    run_limit=self.agent.harness.max_turns, exit_behavior="end"
                )
            ],
        )

    def model(self) -> Any:
        spec = self.agent.harness.model
        return chat_model_for(spec.provider, spec.model, self.agent.name)

    def _run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        graph = self._build()
        messages = (history or []) + [{"role": "user", "content": prompt}]
        result = graph.invoke(
            {"messages": messages},
            config={"recursion_limit": recursion_limit(self.agent.harness.max_turns)},
        )
        msgs = result.get("messages", [])
        text = getattr(msgs[-1], "content", "") if msgs else ""
        return TurnOutput(text=text, tool_calls=langchain_tool_calls(result),
                          tokens=_langchain_tokens(result), raw=result)


class OpenAIAgentsAdapter(RuntimeAdapter):
    """OpenAI Agents SDK: `Agent` + `Runner`, sub-agents exposed as tools."""

    runtime = Runtime.OPENAI_AGENTS

    def _build(self) -> Any:
        from agents import Agent as SDKAgent  # type: ignore
        from agents import function_tool  # type: ignore

        tools = [function_tool(fn, name_override=name) for name, fn in self.tools.items()]
        # A sub-agent is a bounded call that returns to its caller (ADR-0027),
        # which is `as_tool`. `handoffs` — what this adapter used to use —
        # *transfers control*: the sub-agent inherits the conversation and its
        # output becomes the run's output, so the parent never resumes. Same
        # word, different mechanism.
        for sa in self.subagents:
            child = SDKAgent(
                name=sa["name"],
                instructions=sa.get("prompt", ""),
                model=self.agent.harness.model.subagent_model
                or self.agent.harness.model.model,
            )
            tools.append(
                child.as_tool(
                    tool_name=sa["name"],
                    tool_description=sa.get("description", ""),
                    max_turns=self.agent.harness.max_turns,
                )
            )
        return SDKAgent(
            name=self.agent.name,
            instructions=self.system_prompt,
            model=self.agent.harness.model.model,
            tools=tools,
        )

    def _run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        from agents import Runner  # type: ignore

        # `max_turns` here does mean agent turns, so it passes through.
        result = Runner.run_sync(
            self._build(), prompt, max_turns=self.agent.harness.max_turns
        )
        return TurnOutput(
            text=str(result.final_output),
            tokens=_openai_tokens(result),
            raw=result,
        )


class LangGraphAdapter(RuntimeAdapter):
    """Native LangGraph ReAct loop, for agents that are mostly workflow shells."""

    runtime = Runtime.LANGGRAPH

    def model(self) -> Any:
        spec = self.agent.harness.model
        return chat_model_for(spec.provider, spec.model, self.agent.name)

    def _run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        from langchain_core.tools import StructuredTool  # type: ignore
        from langgraph.prebuilt import create_react_agent  # type: ignore

        tools = langchain_tools(self.tools, StructuredTool)
        graph = create_react_agent(self.model(), tools, prompt=self.system_prompt)
        result = graph.invoke(
            {"messages": (history or []) + [("user", prompt)]},
            config={"recursion_limit": recursion_limit(self.agent.harness.max_turns)},
        )
        msgs = result.get("messages", [])
        return TurnOutput(
            text=getattr(msgs[-1], "content", ""),
            tool_calls=langchain_tool_calls(result),
            tokens=_langchain_tokens(result),
            raw=result,
        )


class EchoAdapter(RuntimeAdapter):
    """Deterministic runtime with no model call.

    Used by tests and by the designer's dry-run, where the point is to verify
    wiring — prompt composition, tool availability, delegation legality — not
    model quality.
    """

    runtime = Runtime.ECHO

    def _run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        return TurnOutput(
            text=(
                f"[{self.agent.name}] acknowledged: {prompt}\n"
                f"tools available: {', '.join(sorted(self.tools))}"
            ),
            tokens=len(prompt.split()),
        )


# --------------------------------------------------------------------------
# The adapter registry (ADR-0091)
#
# `Runtime` is a closed enum, so it can only ever name the frameworks that
# ship here. The registry is keyed by the *string* those members carry, which
# means a third-party adapter gets an id of its own — "acme_framework" — and a
# built-in one keeps working unchanged, because a str-enum member and its value
# normalise to the same key.
# --------------------------------------------------------------------------

#: Entry-point group a third-party runtime adapter registers under.
ADAPTER_GROUP = "orgagents.runtime_adapters"

ADAPTERS: Registry = Registry(name="runtime adapter",
                              entry_point_group=ADAPTER_GROUP)


def runtime_key(runtime: Any) -> str:
    """The registry key for a `Runtime` member or a plain string id."""
    return str(getattr(runtime, "value", runtime))


#: What each built-in runtime can actually carry, in the shared vocabulary.
#: These are read by the designer to decide which fields to offer, so they are
#: claims about behaviour rather than aspiration (ADR-0073).
_BUILTIN_RUNTIMES = (
    (Runtime.DEEPAGENTS, DeepAgentsAdapter, "LangChain deep agents",
     {"instructions", "tools", "model", "subagents", "interrupt_on", "skills",
      "long_term_memory", "filesystem_permissions", "structured_output",
      "planning", "middleware", "streaming", "durable_execution"}),
    (Runtime.OPENAI_AGENTS, OpenAIAgentsAdapter, "OpenAI Agents SDK",
     {"instructions", "tools", "model", "subagents", "handoffs",
      "structured_output", "streaming"}),
    (Runtime.LANGGRAPH, LangGraphAdapter, "Native LangGraph ReAct loop",
     {"instructions", "tools", "model", "structured_output", "streaming",
      "durable_execution"}),
    (Runtime.ECHO, EchoAdapter, "Deterministic echo (tests and demos)",
     {"instructions", "tools"}),
)


def _register_builtin_adapters() -> None:
    for runtime, adapter, title, supports in _BUILTIN_RUNTIMES:
        ADAPTERS.register(
            runtime_key(runtime), adapter, replace=True,
            descriptor=ProviderDescriptor(
                id=runtime_key(runtime), title=title, kind="runtime",
                summary=(adapter.__doc__ or "").strip().split("\n")[0],
                supports=frozenset(supports)))


def register_builtin_adapters() -> Registry:
    """Register the shipped adapters, then any installed by a plugin."""
    ADAPTERS.load_builtins(_register_builtin_adapters)
    ADAPTERS.discover()
    return ADAPTERS


def adapter_for(agent: Agent) -> type[RuntimeAdapter]:
    """The adapter class for an agent's runtime.

    Fails naming what *is* available: an integrator who mistypes a runtime id,
    or whose plugin failed to install, learns which ids exist rather than
    getting a bare KeyError from a dict they cannot see.
    """
    register_builtin_adapters()
    return ADAPTERS.require(runtime_key(agent.harness.runtime))
