"""The end-to-end example runs, every stage, on real code.

`examples/end_to_end.py` takes one design the whole distance — load, validate,
resolve to the IR, compile to two targets, and run in the runtime — and it is
the worked demonstration of the two capabilities added this phase: an agent in
more than one sandbox (ADR-0082) and an agent with its own instructions
(ADR-0083). A demonstration that is not run rots, so this runs it.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def outcome():
    spec = importlib.util.spec_from_file_location(
        "e2e_example", ROOT / "examples" / "end_to_end.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


def test_the_analyst_ends_up_in_two_sandboxes(outcome):
    assert outcome["sandboxes"] == ["analysis", "detonation"]


def test_each_sandbox_gets_its_own_runner(outcome):
    """One runner per sandbox — never one sized for the wider of the two."""
    assert len(outcome["runners"]) == 2
    assert outcome["runners"] == [
        "env-analyst_agent-analysis", "env-analyst_agent-detonation"]


def test_it_actually_ran(outcome):
    assert outcome["state"] == "completed"
