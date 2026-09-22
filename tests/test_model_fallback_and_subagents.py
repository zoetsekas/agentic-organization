"""Sub-agent model governance and fallback (ADR-0040 M4/M5).

An agent's model has been governed since M3; its sub-agents' was not, which is
the obvious way around the policy. These tests cover the closed hole, and the
fallback that resolves rather than refuses where the policy allows it.
"""
from pathlib import Path

import pytest

from orgagents.catalogs import CatalogService, seed_catalog
from orgagents.compiler import build_ir, compile_system
from orgagents.compiler.engine import CompileError
from orgagents.compiler.ir import apply_model_approvals
from orgagents.spec import load_binding, load_spec
from orgagents.store import Store

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme" / "acme.system.yaml"
BINDING = ROOT / "examples" / "acme" / "acme.binding.yaml"


@pytest.fixture(scope="module")
def spec():
    return load_spec(EXAMPLE)


@pytest.fixture(scope="module")
def binding():
    return load_binding(BINDING)


@pytest.fixture()
def catalog(tmp_path):
    service = CatalogService(Store(tmp_path / "catalog.db"))
    seed_catalog(service)
    return service


def _local(binding):
    return binding.for_target("local")


# -- sub-agent enforcement (M4) -------------------------------------------


def test_the_example_subagent_model_is_approved(spec, binding, catalog):
    ir = apply_model_approvals(build_ir(spec, binding=_local(binding)), catalog)
    analyst = ir.agent("analyst")
    assert analyst.subagents, "the analyst is the agent with sub-agents"
    assert analyst.model_approval.subagent_approved
    assert analyst.model_approval.subagent_catalog_entry


def test_a_subagent_on_an_unapproved_model_stops_the_build(
    spec, binding, catalog, tmp_path
):
    over = binding.model_copy(deep=True)
    _local(over).agent_overrides["analyst"] = {
        "model": {"subagent_model": "homegrown-1"}}
    with pytest.raises(CompileError, match="sub-agents"):
        compile_system(spec, targets=["local"], out_dir=tmp_path, binding=over,
                       catalog=catalog)


def test_subagent_classes_narrow_what_the_parent_may_use(spec, binding, catalog):
    """The analyst may run on `balanced`; its sub-agents may not."""
    over = binding.model_copy(deep=True)
    # claude-sonnet-5 is balanced, which the agent's own policy permits, but
    # the analyst narrows sub-agents to fast_cheap.
    _local(over).agent_overrides["analyst"] = {
        "model": {"subagent_model": "claude-sonnet-5"}}
    ir = apply_model_approvals(build_ir(spec, binding=_local(over)), catalog)
    approval = ir.agent("analyst").model_approval
    assert approval.approved, approval.approval_reason
    assert not approval.subagent_approved
    assert approval.subagent_classes == ["fast_cheap"]
    # The refusal says what would work instead.
    assert approval.subagent_alternatives


def test_an_agent_without_subagents_is_not_judged_on_one(spec, binding, catalog):
    ir = apply_model_approvals(build_ir(spec, binding=_local(binding)), catalog)
    plain = next(a for a in ir.agents if not a.subagents)
    assert plain.model_approval.subagent_approved
    assert plain.model_approval.subagent_approval_reason == "no sub-agents"


# -- fallback (M5) ---------------------------------------------------------


def _allow_fallback(spec, agent_id: str):
    relaxed = spec.model_copy(deep=True)
    relaxed.agent(agent_id).model_policy.allow_fallback = True
    return relaxed


def test_fallback_resolves_rather_than_refusing_and_is_recorded(
    spec, binding, catalog
):
    over = binding.model_copy(deep=True)
    # Opus is frontier and over the analyst's 20/M ceiling.
    _local(over).agent_overrides["analyst"] = {"model": {"model": "claude-opus-5"}}
    ir = apply_model_approvals(
        build_ir(_allow_fallback(spec, "analyst"), binding=_local(over)), catalog)
    approval = ir.agent("analyst").model_approval
    assert approval.approved
    assert approval.fallback_applied
    assert approval.requested_model == "claude-opus-5"
    assert approval.model != "claude-opus-5"
    assert "fell back" in approval.fallback_reason
    # The swap reaches the binding the targets render, not only the record.
    assert ir.agent("analyst").model["model"] == approval.model


def test_fallback_is_visible_in_the_registry(spec, binding, catalog, tmp_path):
    over = binding.model_copy(deep=True)
    _local(over).agent_overrides["analyst"] = {"model": {"model": "claude-opus-5"}}
    result = compile_system(_allow_fallback(spec, "analyst"), targets=["local"],
                            out_dir=tmp_path, binding=over, catalog=catalog)[0]
    registry = (result.out_dir / "REGISTRY.md").read_text()
    assert "Agents moved to a fallback model:** `analyst`" in registry
    assert "fell back to" in registry


def test_a_subagent_model_may_also_fall_back(spec, binding, catalog):
    over = binding.model_copy(deep=True)
    _local(over).agent_overrides["analyst"] = {
        "model": {"subagent_model": "claude-sonnet-5"}}
    ir = apply_model_approvals(
        build_ir(_allow_fallback(spec, "analyst"), binding=_local(over)), catalog)
    approval = ir.agent("analyst").model_approval
    assert approval.subagent_approved and approval.subagent_fallback_applied
    assert approval.subagent_model != "claude-sonnet-5"


def test_no_fallback_when_the_policy_does_not_allow_it(spec, binding, catalog):
    assert spec.agent("analyst").model_policy.allow_fallback is False
    over = binding.model_copy(deep=True)
    _local(over).agent_overrides["analyst"] = {"model": {"model": "claude-opus-5"}}
    ir = apply_model_approvals(build_ir(spec, binding=_local(over)), catalog)
    approval = ir.agent("analyst").model_approval
    assert not approval.approved and not approval.fallback_applied
    assert approval.model == "claude-opus-5"
