"""The larger end-to-end example runs, gate and all.

`examples/end_to_end_pharma.py` takes the Helios pharma design — four levels
deep, thirteen agents, three of them multi-sandbox, a scoped sub-agent, a
mission — through load, validate, the phase gate against a fabric's platform
policy and binding, compilation to three targets, and a runtime run. It is the
complex counterpart to `end_to_end.py`, and it is run here so it cannot rot.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def outcome():
    spec = importlib.util.spec_from_file_location(
        "e2e_pharma", ROOT / "examples" / "end_to_end_pharma.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


def test_the_design_has_the_complexity_it_claims(outcome):
    assert outcome["agents"] == 13
    # Three agents need compute-or-network and PHI, so they run in two sandboxes.
    assert set(outcome["multi_sandbox"]) == {
        "bioinfo_agent", "trial_manager_agent", "biostat_agent"}


def test_it_passes_the_phase_gate_under_the_house_policy(outcome):
    """The strongest end-to-end claim: a production platform policy lets this
    design compile — it is not just valid, it is allowed."""
    assert outcome["ready_local"] is True


def test_the_agents_ran(outcome):
    assert set(outcome["ran"]) == {
        "bioinfo_agent", "trial_manager_agent", "qa_agent", "regulatory_agent"}
    assert all(state == "completed" for state in outcome["ran"].values())
