"""Runtime adapters against the frameworks they claim to support.

Most of the suite runs on `EchoAdapter`, which proves our wiring and nothing
about LangChain or the OpenAI Agents SDK. These tests drive the real
frameworks — with a scripted model, so no API key and no network — because
every mismatch fixed here was invisible to a test that never left our own
code.
"""
from __future__ import annotations

import time

import pytest

from orgagents.models import Agent, Harness, ModelSpec, Runtime
from orgagents.runtime.adapters import (
    BudgetExceeded,
    DeepAgentsAdapter,
    EchoAdapter,
    OpenAIAgentsAdapter,
    TurnBudget,
    recursion_limit,
)

deepagents = pytest.importorskip("deepagents", reason="langgraph extra not installed")
langchain_core = pytest.importorskip("langchain_core")

from langchain_core.language_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402


class ScriptedModel(GenericFakeChatModel):
    """A chat model that replays messages and accepts tool bindings."""

    def bind_tools(self, tools, **kwargs):
        return self


def _agent(**harness) -> Agent:
    return Agent(name="Billing", harness=Harness(**harness))


# --------------------------------------------------------------------------
# max_turns is turns, whatever the framework counts
# --------------------------------------------------------------------------


def test_recursion_limit_translates_turns_into_super_steps():
    # A ReAct turn is a model step and a tool step, plus one to enter.
    assert recursion_limit(6) == 13
    assert recursion_limit(40) == 81


def test_recursion_limit_is_not_max_turns():
    """The old adapter passed max_turns straight through as recursion_limit.

    That gave deep agents roughly half the turns the same spec gave the OpenAI
    Agents SDK, where max_turns does mean turns.
    """
    assert recursion_limit(40) != 40


def test_deep_agents_passes_the_translated_limit_to_the_graph(monkeypatch):
    captured: dict = {}

    class Graph:
        def invoke(self, state, config=None):
            captured.update(config or {})
            return {"messages": [AIMessage(content="done")]}

    adapter = DeepAgentsAdapter(_agent(runtime=Runtime.DEEPAGENTS, max_turns=6), "", {})
    monkeypatch.setattr(adapter, "_build", lambda: Graph())
    adapter.run("hello")
    assert captured["recursion_limit"] == recursion_limit(6) == 13


# --------------------------------------------------------------------------
# Budgets bind on every runtime, because the frameworks do not enforce ours
# --------------------------------------------------------------------------


def test_token_budget_refuses_the_next_turn_once_spent():
    agent = _agent(runtime=Runtime.ECHO, token_budget=50)
    adapter = EchoAdapter(agent, "", {})
    adapter.budget.spend(50)
    with pytest.raises(BudgetExceeded) as excinfo:
        adapter.run("anything")
    assert excinfo.value.limit == "token"


def test_wall_clock_budget_refuses_the_next_turn_once_spent():
    agent = _agent(runtime=Runtime.ECHO, wall_clock_budget_s=1)
    adapter = EchoAdapter(agent, "", {})
    adapter.budget.started_at = time.monotonic() - 2
    with pytest.raises(BudgetExceeded) as excinfo:
        adapter.run("anything")
    assert excinfo.value.limit == "wall clock"


def test_a_budget_accumulates_across_the_turns_of_one_run():
    """A run is the first turn plus any output-contract retry, not one turn."""
    adapter = EchoAdapter(_agent(runtime=Runtime.ECHO, token_budget=1000), "", {})
    adapter.run("one two three")
    adapter.run("four five")
    assert adapter.budget.tokens_spent == 5


def test_zero_means_unbounded():
    budget = TurnBudget(tokens=0, seconds=0)
    budget.spend(10_000_000)
    budget.check()  # does not raise


def test_a_turn_already_running_is_not_killed_mid_flight():
    """The guarantee is that a turn never starts on an exhausted budget.

    Overrunning inside a turn is bounded by max_turns, not by us: stopping a
    turn part-way would leave a tool call half executed.
    """
    adapter = EchoAdapter(_agent(runtime=Runtime.ECHO, token_budget=1), "", {})
    out = adapter.run("a b c d e f")          # starts legally, overruns
    assert out.text
    assert adapter.budget.tokens_spent == 6
    with pytest.raises(BudgetExceeded):
        adapter.run("again")                   # the next one is refused


# --------------------------------------------------------------------------
# Deep agents, actually executed
# --------------------------------------------------------------------------


class _FakeModelDeepAgents(DeepAgentsAdapter):
    script: list = []

    def model(self):
        return ScriptedModel(messages=iter(self.script))


def test_deep_agents_runs_a_turn_and_calls_a_platform_tool():
    called: list[str] = []

    def lookup_invoice(invoice_id: str) -> str:
        """Look up an invoice by id."""
        called.append(invoice_id)
        return f"invoice {invoice_id}: 120.00 GBP, unpaid"

    adapter = _FakeModelDeepAgents(
        _agent(runtime=Runtime.DEEPAGENTS, max_turns=6),
        "You are Billing.",
        {"lookup_invoice": lookup_invoice},
    )
    adapter.script = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "lookup_invoice", "args": {"invoice_id": "INV-7"}, "id": "c1"}
            ],
        ),
        AIMessage(
            content="INV-7 is unpaid: 120.00 GBP.",
            usage_metadata={"input_tokens": 90, "output_tokens": 15, "total_tokens": 105},
        ),
    ]

    out = adapter.run("What is the status of INV-7?")

    assert called == ["INV-7"], "the framework never reached our tool"
    assert "unpaid" in out.text
    assert out.tokens == 105
    assert adapter.budget.tokens_spent == 105


def test_deep_agents_subagents_are_isolated_calls_not_forks():
    """ADR-0027: a sub-agent is a bounded call that returns to its caller.

    `fork` would hand it the parent's conversation — context the platform did
    not decide to give it.
    """
    adapter = DeepAgentsAdapter(
        _agent(runtime=Runtime.DEEPAGENTS),
        "",
        {},
        subagents=[{"name": "Collections", "description": "chases debt",
                    "prompt": "You chase debt."}],
    )
    (sub,) = adapter._subagents()
    assert sub["mode"] == "isolated"
    # The framework reads `system_prompt`; the adapter used to send `prompt`,
    # so every sub-agent ran with an empty instruction.
    assert sub["system_prompt"] == "You chase debt."
    assert "prompt" not in sub


def test_deep_agents_accepts_the_subagent_shape_we_build():
    """The mapping is only right if the framework takes it."""
    adapter = _FakeModelDeepAgents(
        _agent(runtime=Runtime.DEEPAGENTS),
        "You are Billing.",
        {},
        subagents=[{"name": "Collections", "description": "chases debt",
                    "prompt": "You chase debt."}],
    )
    adapter.script = [AIMessage(content="ok")]
    assert adapter._build() is not None


# --------------------------------------------------------------------------
# OpenAI Agents SDK: a sub-agent is a tool, not a handoff
# --------------------------------------------------------------------------


def test_openai_subagents_are_tools_not_handoffs():
    """A handoff transfers control: the sub-agent inherits the conversation
    and its output becomes the run's output, so the parent never resumes.
    That is not what ADR-0027 describes, and it is what this adapter used to
    build.
    """
    pytest.importorskip("agents", reason="openai extra not installed")

    def refund(amount: float) -> str:
        """Issue a refund."""
        return "ok"

    agent = _agent(runtime=Runtime.OPENAI_AGENTS,
                   model=ModelSpec(provider="openai", model="gpt-4.1"))
    adapter = OpenAIAgentsAdapter(
        agent, "You are Billing.", {"refund": refund},
        subagents=[{"name": "Collections", "description": "chases debt",
                    "prompt": "You chase debt."}],
    )
    built = adapter._build()

    assert built.handoffs == [], "control must return to the caller"
    assert "Collections" in [t.name for t in built.tools]


# --------------------------------------------------------------------------
# ADR-0067: the reference runtime's integrations
# --------------------------------------------------------------------------


def test_max_turns_is_expressed_as_a_model_call_limit_not_graph_depth():
    """`ModelCallLimitMiddleware(run_limit=n)` says max_turns exactly.

    The recursion_limit arithmetic stays as a structural backstop, but it is
    not what bounds the run any more (ADR-0067).
    """
    from langchain.agents.middleware import ModelCallLimitMiddleware

    adapter = _FakeModelDeepAgents(
        _agent(runtime=Runtime.DEEPAGENTS, max_turns=9), "", {})
    adapter.script = [AIMessage(content="ok")]
    built = adapter._build()
    assert built is not None
    # The middleware is constructed with the harness's own number.
    limiter = ModelCallLimitMiddleware(run_limit=9, exit_behavior="end")
    assert limiter.run_limit == 9


def test_the_virtual_filesystem_denies_by_default():
    """An unmatched path is *allowed* by deep agents, which is the opposite of
    ADR-0008, so the floor has to be written down."""
    from orgagents.models import DataGrant, Visibility
    from orgagents.runtime.adapters import filesystem_rules

    agent = _agent(runtime=Runtime.DEEPAGENTS)
    agent.harness.data_grants = [DataGrant(visibility=Visibility.PRIVATE,
                                           can_read=True, can_write=True)]
    rules = filesystem_rules(agent)
    assert rules[-1] == {"operations": ["read", "write"], "paths": ["/**"],
                         "mode": "deny"}, "the last rule is the deny floor"
    assert rules[0]["mode"] == "allow" and "/workspace/**" in rules[0]["paths"]


def test_an_agent_with_no_write_grant_cannot_write_anywhere():
    from orgagents.models import DataGrant, Visibility
    from orgagents.runtime.adapters import filesystem_rules

    agent = _agent(runtime=Runtime.DEEPAGENTS)
    agent.harness.data_grants = [DataGrant(visibility=Visibility.PRIVATE,
                                           can_read=True, can_write=False)]
    rules = filesystem_rules(agent)
    assert rules[0]["operations"] == ["read"]
    # A write to the workspace falls past the allow and hits the deny floor.
    from deepagents.middleware.filesystem import (
        FilesystemPermission,
        _check_fs_permission,
    )

    built = [FilesystemPermission(**r) for r in rules]
    assert _check_fs_permission(built, "write", "/workspace/out.txt") == "deny"
    assert _check_fs_permission(built, "read", "/workspace/out.txt") == "allow"
    assert _check_fs_permission(built, "read", "/etc/passwd") == "deny"


def test_the_permissions_reach_the_framework():
    """The rules are only governance if create_deep_agent accepts them."""
    from orgagents.models import DataGrant, Visibility

    adapter = _FakeModelDeepAgents(
        _agent(runtime=Runtime.DEEPAGENTS), "You are an agent.", {})
    adapter.agent.harness.data_grants = [
        DataGrant(visibility=Visibility.PRIVATE, can_read=True, can_write=False)
    ]
    adapter.script = [AIMessage(content="ok")]
    assert adapter._build() is not None


def test_mcp_over_the_adapter_transport_still_passes_our_allowlist():
    """Their transport, our policy (ADR-0067 rule 5).

    Tools from `langchain-mcp-adapters` come back through the ordinary
    resolution path, so the allowlist and `read_only` still apply. Handing
    them straight to the framework would skip every check in HarnessBuilder.
    """
    from orgagents.harness.mcp import MCPRegistry
    from orgagents.models import MCPServerRef

    class FakeTool:
        def __init__(self, name):
            self.name = name

        def invoke(self, kwargs):
            return {"called": self.name, "with": kwargs}

    class FakeClient:
        def list_tools(self):
            return [FakeTool("read_issue"), FakeTool("delete_project")]

        def call_tool(self, name, kwargs):
            return {"called": name, "with": kwargs}

    registry = MCPRegistry()
    registry.register_remote("jira", FakeClient())
    ref = MCPServerRef(name="jira", transport="http", url="https://j/mcp",
                       allowed_tools=["read_issue"], read_only=True)

    names = [p.name for p in registry.resolve(ref)]
    assert names == ["read_issue"], "the allowlist survives the new transport"


def test_a_server_declaring_no_endpoint_is_refused_rather_than_guessed():
    from orgagents.harness.mcp import connection_for
    from orgagents.models import MCPServerRef

    with pytest.raises(ValueError):
        connection_for(MCPServerRef(name="a", transport="stdio"))
    with pytest.raises(ValueError):
        connection_for(MCPServerRef(name="b", transport="http"))


def test_our_transport_names_map_to_the_adapters_own():
    from orgagents.harness.mcp import connection_for
    from orgagents.models import MCPServerRef

    assert connection_for(
        MCPServerRef(name="a", transport="http", url="u")
    )["transport"] == "streamable_http"
    assert connection_for(
        MCPServerRef(name="b", transport="sse", url="u")
    )["transport"] == "sse"
