"""The AYC operations walk-through runs, and keeps demonstrating what it says.

The script is documentation that executes. This holds it to its claims, so it
cannot quietly stop showing them.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "ayc" / "end_to_end_operations.py"


@pytest.fixture(scope="module")
def result():
    spec = importlib.util.spec_from_file_location("ayc_ops", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


def test_a_leader_held_four_handles_at_once(result):
    """Serial delegation could hold one."""
    assert result["fanned_out"] == 4


def test_the_collection_came_back_most_important_first(result):
    assert result["priority_order"] == ["urgent", "high", "normal", "low"]


def test_the_bound_refusal_named_what_to_drop_and_refused_anyway(result):
    error = result["bound_refusal"]
    assert "max_parallel_subagents is 2" in error
    assert "the least important is" in error
    assert "it does not jump this bound" in error


def test_a_failed_leader_was_covered_by_its_declared_successor(result):
    assert result["standing_in"] == "cs_agent"
    assert result["conferred"] == ["publish_product"]


def test_the_separation_bound_case_falls_back_to_the_manager(result):
    """Standing in by hierarchy confers nothing, which is why it is safe."""
    assert result["cs_stand_in_by_hierarchy"] is True


def test_a_leader_saw_its_subtree_and_not_beyond(result):
    assert result["team_rows"] >= 3


def test_a_converging_loop_converged(result):
    assert result["cycle_error"] is None
    assert 3 < result["cycle_steps"] < 20


def test_a_loop_that_cannot_converge_says_which_bound_it_hit(result):
    assert "max_steps is 25" in result["runaway_error"]
