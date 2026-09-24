"""A stub chat model: canned replies, and the tool calls it is told to make.

A workstation can run everything an organization needs except the model: that
takes a key and a network. This replaces the model and **only** the model
(ADR-0109). It is a LangChain chat model, so it runs inside the real deep
agents (or LangGraph) loop the binding selected — the framework, the tool
node, `HarnessBuilder.guarded`, the mandate and approval checks, and the MCP
transport to the backing systems are all the ones a real model would drive.

What it decides is decided by the prompt, not by judgement:

* each line of the user's message of the form ``call <tool> {json}`` becomes
  one tool call, in order, in a single model turn;
* when the tool results come back, it answers once, in canned words, saying
  what it called and what came back — including a refusal, verbatim;
* a message with no ``call`` lines gets a canned reply naming the agent and
  the tools it holds, which is what a person poking at an agent wants to see.

Nothing here is random, so the same prompt gives the same transcript. The
provider id is ``stub``; `chat_model_for` is the one place a binding's
``provider: stub`` becomes this class.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

#: ``call fishbowl__purchase_ordering {"sku": "AYC-CH-001", "quantity": 12}``
CALL_LINE = re.compile(
    r"^\s*call\s+(?P<tool>[A-Za-z0-9_.\-]+)\s*(?P<args>\{.*\})?\s*$"
)

STUB_PROVIDER = "stub"


def parse_calls(text: str) -> list[dict[str, Any]]:
    """The tool calls a prompt asks for, in order. Bad JSON is kept, marked."""
    calls: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        match = CALL_LINE.match(line)
        if not match:
            continue
        raw = match.group("args") or "{}"
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as exc:
            args = {"_unparseable": raw, "_error": str(exc)}
        calls.append({"name": match.group("tool"), "args": args})
    return calls


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def _verdict(content: str) -> tuple[bool, str]:
    """Whether a tool result reads as a refusal, and the reason if so."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict) and data.get("ok") is False:
        return False, str(data.get("error") or "refused")
    if content.startswith("Error"):
        return False, content
    return True, ""


def _build_class() -> type:
    """Define the model class against LangChain, imported lazily.

    The core package installs without LangChain (adapters import their
    framework lazily), so the class is built on first use rather than at
    import time.
    """
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class StubChatModel(BaseChatModel):
        """Deterministic chat model; see the module docstring."""

        agent_name: str = "agent"
        tool_names: list[str] = []

        @property
        def _llm_type(self) -> str:
            return "orgagents-stub"

        def bind_tools(self, tools: Any, **_: Any) -> "StubChatModel":
            names = []
            for t in tools or []:
                name = getattr(t, "name", None)
                if name is None and isinstance(t, dict):
                    name = t.get("name") or t.get("function", {}).get("name")
                if name:
                    names.append(name)
            return self.model_copy(update={"tool_names": names})

        def _reply(self, messages: list[Any]) -> AIMessage:
            # Results of the calls made in this run's last tool turn.
            results: list[ToolMessage] = []
            for m in reversed(messages):
                if isinstance(m, ToolMessage):
                    results.append(m)
                    continue
                break
            if results:
                results.reverse()
                lines = [f"[{self.agent_name}] (stub model) "
                         f"{len(results)} tool call(s):"]
                for r in results:
                    ok, why = _verdict(_text(r.content))
                    lines.append(f"- {r.name}: " + ("ok" if ok else f"refused — {why}"))
                return AIMessage(content="\n".join(lines))

            human = next((m for m in reversed(messages)
                          if isinstance(m, HumanMessage)), None)
            calls = parse_calls(_text(human.content) if human else "")
            if calls:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": c["name"], "args": c["args"],
                         "id": f"stub_call_{i}", "type": "tool_call"}
                        for i, c in enumerate(calls)
                    ],
                )
            held = ", ".join(sorted(self.tool_names)) or "none"
            return AIMessage(content=(
                f"[{self.agent_name}] (stub model) Hello. I am a stub: I make "
                "the tool calls a message spells out as "
                "`call <tool> {json}`, one per line, and report what came "
                f"back. Tools I hold: {held}."
            ))

        def _generate(self, messages: list[Any], stop: Optional[list[str]] = None,
                      run_manager: Any = None, **kwargs: Any) -> ChatResult:
            message = self._reply(messages)
            words = sum(len(_text(m.content).split()) for m in messages)
            message.usage_metadata = {
                "input_tokens": words,
                "output_tokens": len(_text(message.content).split()),
                "total_tokens": words + len(_text(message.content).split()),
            }
            return ChatResult(generations=[ChatGeneration(message=message)])

    return StubChatModel


_CLASS: Optional[type] = None


def stub_chat_model(agent_name: str = "agent") -> Any:
    """A `StubChatModel` instance, building the class on first use."""
    global _CLASS
    if _CLASS is None:
        _CLASS = _build_class()
    return _CLASS(agent_name=agent_name)


def chat_model_for(provider: str, model: str, agent_name: str = "agent") -> Any:
    """The model argument a LangChain-based adapter hands its framework.

    ``provider:model`` strings are resolved by LangChain itself; ``stub`` is
    ours and is resolved here, so it never reaches a provider lookup that
    would go looking for a key.
    """
    if provider == STUB_PROVIDER:
        return stub_chat_model(agent_name)
    return f"{provider}:{model}"
