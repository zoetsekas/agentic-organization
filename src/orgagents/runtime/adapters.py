"""Runtime adapters.

The platform's agent definition is framework-neutral. An adapter takes the
assembled harness (system prompt, tools, sub-agent definitions, limits) and
runs one turn on a concrete framework: LangChain **deep agents**, the **OpenAI
Agents SDK**, native LangGraph, or a dependency-free echo runtime used by
tests and the designer's dry-run preview.

Adapters import their framework lazily, so a deployment only installs the one
it uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..models import Agent, Runtime


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
    ) -> None:
        self.agent = agent
        self.system_prompt = system_prompt
        self.tools = tools
        self.subagents = subagents or []

    def run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        raise NotImplementedError


class DeepAgentsAdapter(RuntimeAdapter):
    """LangChain deep agents: planning, virtual filesystem and sub-agents."""

    runtime = Runtime.DEEPAGENTS

    def _build(self) -> Any:
        from deepagents import create_deep_agent  # type: ignore
        from langchain_core.tools import StructuredTool  # type: ignore

        tools = [
            StructuredTool.from_function(func=fn, name=name)
            for name, fn in self.tools.items()
        ]
        return create_deep_agent(
            tools=tools,
            instructions=self.system_prompt,
            subagents=self.subagents,
            model=f"{self.agent.harness.model.provider}:{self.agent.harness.model.model}",
        )

    def run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        graph = self._build()
        messages = (history or []) + [{"role": "user", "content": prompt}]
        result = graph.invoke(
            {"messages": messages},
            config={"recursion_limit": self.agent.harness.max_turns},
        )
        msgs = result.get("messages", [])
        text = getattr(msgs[-1], "content", "") if msgs else ""
        return TurnOutput(text=text, raw=result)


class OpenAIAgentsAdapter(RuntimeAdapter):
    """OpenAI Agents SDK: `Agent` + `Runner`, with handoffs as delegation."""

    runtime = Runtime.OPENAI_AGENTS

    def _build(self) -> Any:
        from agents import Agent as SDKAgent  # type: ignore
        from agents import function_tool  # type: ignore

        tools = [function_tool(fn, name_override=name) for name, fn in self.tools.items()]
        handoffs = [
            SDKAgent(
                name=sa["name"],
                instructions=sa.get("prompt", ""),
                model=self.agent.harness.model.subagent_model
                or self.agent.harness.model.model,
            )
            for sa in self.subagents
        ]
        return SDKAgent(
            name=self.agent.name,
            instructions=self.system_prompt,
            model=self.agent.harness.model.model,
            tools=tools,
            handoffs=handoffs,
        )

    def run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        from agents import Runner  # type: ignore

        result = Runner.run_sync(
            self._build(), prompt, max_turns=self.agent.harness.max_turns
        )
        return TurnOutput(text=str(result.final_output), raw=result)


class LangGraphAdapter(RuntimeAdapter):
    """Native LangGraph ReAct loop, for agents that are mostly workflow shells."""

    runtime = Runtime.LANGGRAPH

    def run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        from langchain_core.tools import StructuredTool  # type: ignore
        from langgraph.prebuilt import create_react_agent  # type: ignore

        tools = [
            StructuredTool.from_function(func=fn, name=name)
            for name, fn in self.tools.items()
        ]
        graph = create_react_agent(
            f"{self.agent.harness.model.provider}:{self.agent.harness.model.model}",
            tools,
            prompt=self.system_prompt,
        )
        result = graph.invoke({"messages": (history or []) + [("user", prompt)]})
        msgs = result.get("messages", [])
        return TurnOutput(text=getattr(msgs[-1], "content", ""), raw=result)


class EchoAdapter(RuntimeAdapter):
    """Deterministic runtime with no model call.

    Used by tests and by the designer's dry-run, where the point is to verify
    wiring — prompt composition, tool availability, delegation legality — not
    model quality.
    """

    runtime = Runtime.ECHO

    def run(self, prompt: str, history: Optional[list[dict]] = None) -> TurnOutput:
        return TurnOutput(
            text=(
                f"[{self.agent.name}] acknowledged: {prompt}\n"
                f"tools available: {', '.join(sorted(self.tools))}"
            ),
            tokens=len(prompt.split()),
        )


_ADAPTERS: dict[Runtime, type[RuntimeAdapter]] = {
    Runtime.DEEPAGENTS: DeepAgentsAdapter,
    Runtime.OPENAI_AGENTS: OpenAIAgentsAdapter,
    Runtime.LANGGRAPH: LangGraphAdapter,
    Runtime.ECHO: EchoAdapter,
}


def adapter_for(agent: Agent) -> type[RuntimeAdapter]:
    return _ADAPTERS[agent.harness.runtime]
