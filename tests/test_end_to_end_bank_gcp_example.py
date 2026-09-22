"""The bank-to-GCP end-to-end example runs, gate and Gemini and all.

`examples/end_to_end_bank_gcp.py` takes the Atlas multinational bank through
load, validate, a feature-coverage assertion, the phase gate for the `adk` and
`terraform:gcp` targets under a platform policy, compilation to Google's stack
(ADK agents on Gemini, plus the governed terraform runtime), and a runtime run.
Run here so it cannot rot.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def outcome():
    spec = importlib.util.spec_from_file_location(
        "e2e_bank_gcp", ROOT / "examples" / "end_to_end_bank_gcp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


def test_every_feature_is_used(outcome):
    assert outcome["blocks_used"] == outcome["blocks_total"]


def test_it_is_ready_for_both_google_targets(outcome):
    assert outcome["ready"]["adk"] is True
    assert outcome["ready"]["terraform:gcp"] is True


def test_three_agents_are_multi_sandbox(outcome):
    assert set(outcome["multi_sandbox"]) == {
        "loan_officer_agent", "aml_agent", "platform_engineer_agent"}


def test_the_agents_ran(outcome):
    assert all(v == "completed" for v in outcome["ran"].values())
