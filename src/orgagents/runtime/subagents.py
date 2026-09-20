"""Sub-agents that behave like tools (ADR-0027).

A sub-agent is *not* an org member. It has no reporting line, no human
counterpart of its own, no session URL anyone browses to, and no memory beyond
the call. It is a task-scoped worker the parent invokes like any other tool:
call it with arguments, get a result, it is gone.

The security property that makes this safe is **narrow-only inheritance**: a
sub-agent runs under its parent's identity with a subset of the parent's
capabilities. It can never reach something the parent could not, which is why
a sub-agent needs no permission model of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..spec.model import SubAgentKind

# Instructions that ship with each recognized sub-agent shape, so a spec author
# gets useful behaviour from `kind: research` without writing a prompt.
KIND_INSTRUCTIONS: dict[SubAgentKind, str] = {
    SubAgentKind.RESEARCH: (
        "Gather what is known about the topic from the sources you are given. "
        "Separate findings from inference, cite every source, and say plainly "
        "what you could not establish."
    ),
    SubAgentKind.REVIEW: (
        "Review the work against the stated criteria. Report concrete problems "
        "with their location and severity. Do not rewrite the work; do not "
        "invent criteria that were not given to you."
    ),
    SubAgentKind.SUMMARIZE: (
        "Summarize faithfully. Preserve figures, dates and named entities "
        "exactly. Do not introduce conclusions the source does not support."
    ),
    SubAgentKind.EXTRACT: (
        "Extract exactly the requested fields. Return them in the requested "
        "shape. Where a field is absent, say so rather than guessing."
    ),
    SubAgentKind.CRITIQUE: (
        "Argue the strongest case against the work. Name the assumption that, "
        "if wrong, matters most. Be specific and brief."
    ),
    SubAgentKind.PLAN: (
        "Break the task into ordered steps with their dependencies. Flag the "
        "steps that need a decision from a human."
    ),
    SubAgentKind.VERIFY: (
        "Check each claim against the evidence provided. Mark every claim "
        "supported, contradicted or unverifiable, and show what you checked."
    ),
    SubAgentKind.CUSTOM: "",
}


class SubAgentError(RuntimeError):
    """Raised when a sub-agent definition violates narrow-only inheritance."""


@dataclass
class SubAgentTool:
    """One sub-agent, resolved and ready to be called as a tool."""

    id: str
    name: str
    kind: SubAgentKind
    purpose: str
    instructions: str
    parent_agent_id: str
    capabilities: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    knowledge: tuple[str, ...] = ()
    environment: Optional[str] = None
    returns: str = ""
    max_turns: int = 8
    max_runtime_seconds: int = 300
    parallel_safe: bool = True

    @property
    def tool_name(self) -> str:
        return f"subagent_{self.id}"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.tool_name,
            "description": (
                f"{self.purpose or self.kind.value} "
                f"(sub-agent of {self.parent_agent_id}; returns {self.returns or 'a result'})"
            ),
            "kind": self.kind.value,
            "capabilities": list(self.capabilities),
            "max_runtime_seconds": self.max_runtime_seconds,
        }

    def system_prompt(self) -> str:
        """A sub-agent is told what it is, and what it is not."""
        base = KIND_INSTRUCTIONS.get(self.kind, "")
        parts = [
            f"You are '{self.name or self.id}', a {self.kind.value} sub-agent invoked "
            f"as a tool by {self.parent_agent_id}.",
        ]
        if self.purpose:
            parts.append(f"Purpose: {self.purpose}")
        if base:
            parts.append(base)
        if self.instructions:
            parts.append(self.instructions)
        parts += [
            "",
            "Constraints:",
            f"- Return {self.returns or 'your result'} and nothing else; your caller "
            "decides what happens next.",
            "- You hold a subset of your caller's access. If something is out of "
            "reach, say so; never work around it.",
            "- You have no memory beyond this call and no authority to act on the "
            "organization's behalf.",
            f"- Finish within {self.max_turns} turns.",
        ]
        return "\n".join(parts)


def resolve(
    subagent,
    *,
    parent_agent_id: str,
    parent_capabilities: set[str],
    parent_tools: Optional[set[str]] = None,
    parent_knowledge: Optional[set[str]] = None,
    parent_environment: Optional[str] = None,
) -> SubAgentTool:
    """Resolve one sub-agent, enforcing narrow-only inheritance."""
    extra = set(subagent.capabilities) - parent_capabilities
    if extra:
        raise SubAgentError(
            f"sub-agent '{subagent.id}' requests capabilities its parent "
            f"'{parent_agent_id}' does not hold: {sorted(extra)}"
        )
    if parent_tools is not None:
        extra_tools = set(subagent.tools) - parent_tools
        if extra_tools:
            raise SubAgentError(
                f"sub-agent '{subagent.id}' requests tools its parent does not "
                f"hold: {sorted(extra_tools)}"
            )
    if parent_knowledge is not None:
        extra_knowledge = set(subagent.knowledge) - parent_knowledge
        if extra_knowledge:
            raise SubAgentError(
                f"sub-agent '{subagent.id}' requests knowledge its parent does not "
                f"hold: {sorted(extra_knowledge)}"
            )
    if subagent.environment and parent_environment and (
        subagent.environment != parent_environment
    ):
        raise SubAgentError(
            f"sub-agent '{subagent.id}' requests environment "
            f"'{subagent.environment}' but its parent runs in "
            f"'{parent_environment}'; a sub-agent may not change the isolation "
            "boundary"
        )
    return SubAgentTool(
        id=subagent.id,
        name=subagent.name or subagent.id,
        kind=subagent.kind,
        purpose=subagent.purpose,
        instructions=subagent.instructions,
        parent_agent_id=parent_agent_id,
        # Default to the parent's capabilities only when none are named; naming
        # fewer is the encouraged path.
        capabilities=tuple(subagent.capabilities),
        tools=tuple(subagent.tools),
        knowledge=tuple(subagent.knowledge),
        environment=subagent.environment or parent_environment,
        returns=subagent.returns,
        max_turns=subagent.max_turns,
        max_runtime_seconds=subagent.max_runtime_seconds,
        parallel_safe=subagent.parallel_safe,
    )


@dataclass
class SubAgentRunner:
    """Turns resolved sub-agents into callables the harness can expose."""

    tools: list[SubAgentTool]
    invoke: Callable[[SubAgentTool, str, dict[str, Any]], Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def callables(self) -> dict[str, Callable[..., Any]]:
        out: dict[str, Callable[..., Any]] = {}
        for tool in self.tools:
            out[tool.tool_name] = self._make(tool)
        return out

    def _make(self, tool: SubAgentTool) -> Callable[..., Any]:
        def call(task: str, **context: Any) -> dict[str, Any]:
            record = {"subagent": tool.id, "task": task, "context": context}
            self.calls.append(record)
            result = self.invoke(tool, task, context)
            record["result"] = result
            return {
                "subagent": tool.id,
                "kind": tool.kind.value,
                "returns": tool.returns,
                "result": result,
            }

        call.__name__ = tool.tool_name
        call.__doc__ = tool.describe()["description"]
        return call
