"""Retry on output-contract violation (ADR-0037, WS-024 M6).

A violation used to be recorded and returned. Now it is re-prompted, bounded
by the contract's own attempt budget, with every attempt in the session log —
and a contract that still fails after the last attempt fails exactly as loudly
as it always did.
"""
from pathlib import Path

import pytest

from orgagents.compiler import build_ir
from orgagents.platform import Platform
from orgagents.runtime.adapters import RuntimeAdapter, TurnOutput
from orgagents.runtime.loader import load_system
from orgagents.spec import load_binding, load_spec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme.system.yaml"
GOOD = '{"findings": [{"text": "revenue up", "source": "invoices"}]}'


class ScriptedAdapter(RuntimeAdapter):
    """Returns canned answers in order, repeating the last one.

    The scripted answers stand in for a model that gets the shape wrong and
    then, told what was wrong, gets it right.
    """

    replies: list[str] = []
    prompts: list[str] = []

    def run(self, prompt, history=None) -> TurnOutput:
        type(self).prompts.append(prompt)
        index = min(len(type(self).prompts) - 1, len(type(self).replies) - 1)
        return TurnOutput(text=type(self).replies[index], tokens=1)


def scripted(*replies) -> type[ScriptedAdapter]:
    return type("Scripted", (ScriptedAdapter,),
                {"replies": list(replies), "prompts": []})


@pytest.fixture()
def system(tmp_path):
    ir = build_ir(load_spec(EXAMPLE),
                  binding=load_binding(ROOT / "examples" / "acme.binding.yaml")
                  .for_target("local"))
    platform = Platform(str(tmp_path / "retry.db"), configure_logs=False)
    load_system(platform, ir)
    return platform


def analyst(system):
    agent = system.org.agent("analyst")
    assert agent.output_contract["id"] == "cited_findings"
    return agent


def contract_events(system, session_id):
    return [e for e in system.runtime.sessions.events(session_id)
            if e.type == "output_contract"]


def run_with(system, adapter_cls, prompt="summarize Q3"):
    """Drive a full run against a scripted adapter."""
    import orgagents.runtime.engine as engine_module

    original = engine_module.adapter_for
    engine_module.adapter_for = lambda agent: adapter_cls
    try:
        return system.runtime.run("analyst", prompt)
    finally:
        engine_module.adapter_for = original


# -- the retry itself ------------------------------------------------------


def test_a_violation_is_re_prompted_and_the_corrected_answer_is_returned(system):
    adapter = scripted("just some prose", GOOD)
    result = run_with(system, adapter)

    assert result.output == GOOD
    assert len(adapter.prompts) == 2
    events = contract_events(system, result.session_id)
    assert [e.payload["attempt"] for e in events] == [1, 2]
    assert events[-1].payload["resolved"] is True


def test_the_retry_prompt_carries_the_violation_and_the_previous_answer(system):
    adapter = scripted("just some prose", GOOD)
    run_with(system, adapter)

    retry = adapter.prompts[1]
    assert "cited_findings" in retry
    assert "unparseable" in retry
    assert "just some prose" in retry
    assert "summarize Q3" in retry


def test_a_conforming_first_answer_is_never_retried(system):
    adapter = scripted(GOOD)
    result = run_with(system, adapter)
    assert len(adapter.prompts) == 1
    assert contract_events(system, result.session_id) == []


def test_retries_are_bounded_and_every_attempt_is_recorded(system):
    """Three attempts for max_retries=2, not an unbounded argument."""
    adapter = scripted("prose", "still prose", "prose again", "and again")
    result = run_with(system, adapter)

    agent = analyst(system)
    budget = agent.output_contract["max_retries"]
    assert len(adapter.prompts) == budget + 1
    events = contract_events(system, result.session_id)
    assert [e.payload["attempt"] for e in events] == list(range(1, budget + 2))
    assert all(e.payload["max_attempts"] == budget + 1 for e in events)


def test_the_budget_is_configurable_at_the_runtime(system):
    system.runtime.max_contract_retries = 0
    adapter = scripted("prose", GOOD)
    result = run_with(system, adapter)
    assert len(adapter.prompts) == 1
    assert result.output == "prose"
    assert contract_events(system, result.session_id)[-1].payload["final"] is True


def test_a_contract_that_never_conforms_still_fails_loudly(system):
    """Exhausting the budget must not look like success."""
    adapter = scripted("prose", "prose", "prose")
    result = run_with(system, adapter)

    final = contract_events(system, result.session_id)[-1].payload
    assert final["resolved"] is False and final["final"] is True
    assert final["errors"]
    assert system.runtime.check_output_contract(analyst(system), result.output)


def test_a_failing_retry_call_ends_the_attempts_and_is_recorded(system):
    class Exploding(ScriptedAdapter):
        replies = ["prose"]
        prompts: list[str] = []

        def run(self, prompt, history=None):
            if type(self).prompts:
                type(self).prompts.append(prompt)
                raise RuntimeError("adapter blew up on retry")
            return super().run(prompt, history)

    result = run_with(system, Exploding)
    final = contract_events(system, result.session_id)[-1].payload
    assert "adapter blew up" in final["error"]
    assert final["resolved"] is False
    assert result.output == "prose"  # the original answer, not a fabricated one


def test_a_withheld_response_is_not_retried(system):
    """A guardrail refusal is not a shape the agent can be asked to fix."""
    adapter = scripted("my key is AKIAIOSFODNN7EXAMPLE", GOOD)
    result = run_with(system, adapter)

    assert len(adapter.prompts) == 1
    assert "withheld at the boundary" in result.output
    assert contract_events(system, result.session_id)[-1].payload["final"] is True


def test_an_agent_without_a_contract_is_left_alone(system):
    import orgagents.runtime.engine as engine_module

    adapter = scripted("prose is fine here")
    original = engine_module.adapter_for
    engine_module.adapter_for = lambda agent: adapter
    try:
        result = system.runtime.run("cfo", "how did the quarter go?")
    finally:
        engine_module.adapter_for = original
    assert result.output == "prose is fine here"
    assert len(adapter.prompts) == 1


def test_a_flag_policy_records_without_re_prompting(system):
    agent = analyst(system)
    agent.output_contract = {**agent.output_contract, "on_violation": "flag"}
    system.store.put("agents", agent)

    adapter = scripted("prose", GOOD)
    result = run_with(system, adapter)
    assert len(adapter.prompts) == 1
    events = contract_events(system, result.session_id)
    assert events[-1].payload["action"] == "flag"
    assert events[-1].payload["resolved"] is False
