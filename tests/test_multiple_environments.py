"""An agent may run in more than one sandbox (ADR-0082).

The blast radius of analysing a ledger and of instructing a bank are not the
same, so an agent that does both has two sandboxes, not one that takes the
wider of the two. Which one a given call belongs in is *derived*, not
declared: a capability names its data classes, a data class names the
environments it is allowed in, and the intersection is the answer. So the
checks below are about whether that intersection is ever empty.
"""
from __future__ import annotations

import copy

import pytest

from orgagents.compiler.ir import build_ir
from orgagents.placements import resolve
from orgagents.spec.loader import load_spec_text
from orgagents.spec.migrations import migrate
from orgagents.spec.model import SystemSpec
from orgagents.spec.validate import validate_spec

BASE = {
    "metadata": {"name": "t", "spec_version": "1.3.0", "version": "0.1.0"},
    "data_classes": [
        {"id": "ledger", "scope": "protected", "groups": ["fin"],
         "allowed_environments": ["analysis"]},
        {"id": "payments", "scope": "protected", "groups": ["fin"],
         "allowed_environments": ["payments_isolated"]},
    ],
    "environments": [
        {"id": "analysis", "network": "allowlist",
         "egress_allowlist": ["ledger.internal"]},
        {"id": "payments_isolated", "network": "allowlist",
         "egress_allowlist": ["bank.internal"]},
    ],
    "capabilities": [
        {"id": "read_ledger", "action": "query", "resource_class": "ledger_db",
         "data_classes": ["ledger"]},
        {"id": "release_payment", "action": "write", "resource_class": "bank",
         "data_classes": ["payments"], "autonomy": "supervised",
         "constraints": {"requires_approval": True}},
    ],
    "roles": [
        {"id": "treasurer", "title": "Treasurer",
         "responsibilities": ["Analyse the ledger and release payments."],
         "capabilities": ["read_ledger", "release_payment"],
         "permissions": [
             {"action": "query", "resource_kind": "capability", "resource": "read_ledger"},
             {"action": "write", "resource_kind": "capability", "resource": "release_payment"},
         ]},
    ],
    "organization": {
        "id": "root", "name": "Root", "leader": "treasurer",
        "members": [
            {"id": "treasurer", "name": "Treasurer",
             "roles": [{"role": "treasurer"}],
             "mandate": {"decisions": []},
             "environments": [
                 {"environment": "analysis"},
                 {"environment": "payments_isolated"},
             ]},
        ],
        "teams": [],
    },
}


def _spec(mutate=None) -> SystemSpec:
    d = copy.deepcopy(BASE)
    if mutate:
        mutate(d)
    return SystemSpec.model_validate(d)


def _errors(spec) -> set[str]:
    return {f.code for f in validate_spec(spec) if f.severity == "error"}


def test_an_agent_with_two_sandboxes_that_each_do_work_is_valid():
    """Ledger analysis lands in `analysis`, payment release in
    `payments_isolated`. Both are used, so both are legitimate."""
    assert not _errors(_spec())


def test_a_capability_with_no_admitting_sandbox_is_refused():
    """Drop the payments sandbox: releasing a payment now has nowhere to run,
    and giving the agent the analysis sandbox does not fix it because the data
    class does not admit that one."""
    def drop_payments(d):
        d["organization"]["members"][0]["environments"] = [
            {"environment": "analysis"}]
    assert "capability_without_a_sandbox" in _errors(_spec(drop_payments))


def test_a_sandbox_no_capability_uses_is_a_warning():
    def add_idle(d):
        d["environments"].append({"id": "idle", "network": "none"})
        d["organization"]["members"][0]["environments"].append(
            {"environment": "idle"})
    warnings = {f.code for f in validate_spec(_spec(add_idle))
                if f.severity == "warning"}
    assert "sandbox_without_work" in warnings


def test_the_same_sandbox_declared_twice_is_refused():
    def dup(d):
        d["organization"]["members"][0]["environments"].append(
            {"environment": "analysis"})
    assert "duplicate_environment" in _errors(_spec(dup))


def test_an_agent_in_two_sandboxes_is_in_two_placements():
    placed = resolve(_spec())
    assert placed.home["treasurer"] == ["root--analysis", "root--payments_isolated"]
    assert len(placed.for_agent("treasurer")) == 2


def test_the_ir_carries_every_sandbox_and_a_resource_per_sandbox():
    ir = build_ir(_spec())
    treasurer = ir.agent("treasurer")
    assert {e.id for e in treasurer.environments} == {"analysis", "payments_isolated"}
    assert sorted(treasurer.placements) == [
        "root--analysis", "root--payments_isolated"]
    runners = [r for r in ir.resources
               if r.kind == "job_runner" and r.owner == "treasurer"]
    assert len(runners) == 2      # one per sandbox, never one for the wider


def test_a_subagent_may_use_a_parents_sandbox_but_not_a_new_one():
    def widen(d):
        d["organization"]["members"][0]["subagents"] = [
            {"id": "helper", "environments": ["build"]}]
        d["environments"].append({"id": "build", "network": "open"})
    assert "subagent_changes_environment" in _errors(_spec(widen))

    def scoped(d):
        d["organization"]["members"][0]["subagents"] = [
            {"id": "helper", "environments": ["payments_isolated"]}]
    assert "subagent_changes_environment" not in _errors(_spec(scoped))


def test_the_pre_1_3_environment_key_migrates_to_a_list():
    doc = copy.deepcopy(BASE)
    doc["metadata"]["spec_version"] = "1.2.0"
    agent = doc["organization"]["members"][0]
    agent.pop("environments")
    agent["environment"] = {"environment": "analysis"}
    out, changes = migrate(doc)
    assert out["organization"]["members"][0]["environments"] == [
        {"environment": "analysis"}]
    assert any("environments" in c for c in changes)


def test_reading_a_pre_1_3_document_raw_is_refused_not_silently_dropped():
    """Validating the raw dict without the migration would drop the sandbox."""
    with pytest.raises(ValueError, match="environments"):
        SystemSpec.model_validate({
            "metadata": {"name": "t"},
            "organization": {"id": "r", "leader": "a", "members": [
                {"id": "a", "name": "A", "environment": {"environment": "x"}}],
             "teams": []},
        })
