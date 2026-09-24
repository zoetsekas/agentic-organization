"""What an agent may rely on about its data (ADR-0099).

One test per line of that ADR's Verification section, plus the boundary the
ADR draws: the platform does not model a physical schema, because that belongs
to whoever owns the data and a copy of it is correct until their next
migration.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from orgagents.compiler.ir import build_ir
from orgagents.platform import Platform
from orgagents.runtime.loader import load_system
from orgagents.spec.loader import load_spec
from orgagents.spec.model import DataSemantics, StaleAction
from orgagents.spec.validate import errors, validate_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _spec(classes=None, agent_extra=None):
    from spec_fixtures import BASE
    from spec_fixtures import org as _org

    spec = {k: v for k, v in BASE.items()}
    spec["data_classes"] = classes or []
    org = spec["organization"]
    member = {**org["teams"][0]["members"][0], **(agent_extra or {})}
    spec["organization"] = {
        **org,
        "teams": [{**org["teams"][0],
                   "members": [member, *org["teams"][0]["members"][1:]]}],
    }
    return _org(spec)


def _codes(spec):
    return {f.code for f in errors(validate_spec(spec))}


def _all(spec):
    return {f.code for f in validate_spec(spec)}


# -- semantics and relations ------------------------------------------------


def test_a_class_says_what_it_is_separately_from_how_it_is_protected():
    spec = _spec([{"id": "person", "semantics": "subject"}])
    assert spec.data_classes[0].semantics is DataSemantics.SUBJECT
    assert not _codes(spec) & {"unknown_data_relation"}


def test_a_relation_to_a_class_that_does_not_exist_is_refused():
    spec = _spec([{"id": "orders",
                   "relations": [{"kind": "identifies", "target": "ghost"}]}])
    assert "unknown_data_relation" in _codes(spec)


def test_a_derived_class_may_not_drop_what_it_inherited():
    """A restriction a summary can drop is not a restriction."""
    spec = _spec([
        {"id": "payroll", "may_leave_region": False},
        {"id": "headcount_summary", "may_leave_region": True,
         "relations": [{"kind": "derived_from", "target": "payroll"}]},
    ])
    found = [f for f in validate_spec(spec)
             if f.code == "derived_class_drops_restriction"]
    assert found
    assert "may not leave its region" in found[0].message


def test_the_restriction_travels_through_a_chain():
    spec = _spec([
        {"id": "payroll", "may_leave_region": False},
        {"id": "mid", "may_leave_region": False,
         "relations": [{"kind": "derived_from", "target": "payroll"}]},
        {"id": "far", "may_leave_region": True,
         "relations": [{"kind": "derived_from", "target": "mid"}]},
    ])
    assert "derived_class_drops_restriction" in _codes(spec)


def test_a_plain_reference_carries_no_restriction():
    """`references` is the edge that means nothing follows it, and it has to
    stay that way or nobody will use it honestly."""
    spec = _spec([
        {"id": "payroll", "may_leave_region": False},
        {"id": "report", "may_leave_region": True,
         "relations": [{"kind": "references", "target": "payroll"}]},
    ])
    assert "derived_class_drops_restriction" not in _codes(spec)


def test_a_derivation_cycle_is_refused():
    spec = _spec([
        {"id": "a", "relations": [{"kind": "derived_from", "target": "b"}]},
        {"id": "b", "relations": [{"kind": "derived_from", "target": "a"}]},
    ])
    assert "data_derivation_cycle" in _codes(spec)


# -- the contract -----------------------------------------------------------


def test_a_dependency_on_a_class_that_does_not_exist_is_refused():
    spec = _spec([], {"data_dependencies": [{"data_class": "ghost"}]})
    assert "unknown_dependency_data_class" in _codes(spec)


def test_a_named_producer_must_declare_it_produces():
    """A contract with one party is not a contract."""
    spec = _spec([{"id": "ledger"}],
                 {"data_dependencies": [{"data_class": "ledger",
                                         "produced_by": "clerk"}]})
    found = [f for f in errors(validate_spec(spec))
             if f.code == "producer_does_not_produce"]
    assert found
    assert "not a contract" in found[0].message


def test_a_producer_that_is_not_an_agent_is_refused():
    spec = _spec([{"id": "ledger"}],
                 {"data_dependencies": [{"data_class": "ledger",
                                         "produced_by": "nobody"}]})
    assert "unknown_data_producer" in _codes(spec)


def test_data_from_outside_the_design_is_normal_and_said_once():
    """Most organizations' data comes from outside; a reader should know
    which half this is."""
    spec = _spec([{"id": "market_prices"}],
                 {"data_dependencies": [{"data_class": "market_prices",
                                         "fields": ["close"]}]})
    assert "externally_produced_data" not in _codes(spec), "not an error"
    assert "externally_produced_data" in _all(spec)


def test_a_dependency_with_no_fields_says_it_is_whole_class():
    """Pretending to a precision nobody supplied is worse than admitting the
    coarseness."""
    spec = _spec([{"id": "ledger"}],
                 {"data_dependencies": [{"data_class": "ledger"}]})
    assert "whole_class_dependency" not in _codes(spec)
    assert "whole_class_dependency" in _all(spec)


def test_refusing_is_the_default_when_data_is_stale():
    """A freshness window that carried on regardless would be decorative."""
    from orgagents.spec.model import DataDependency

    assert DataDependency(data_class="x").on_stale is StaleAction.REFUSE


# -- impact and staleness, against the real design --------------------------


@pytest.fixture(scope="module")
def running():
    ir = build_ir(load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml"))
    tmp = tempfile.TemporaryDirectory()
    platform = Platform(str(pathlib.Path(tmp.name) / "ayc.db"),
                        configure_logs=False)
    load_system(platform, ir)
    yield platform
    platform.close()  # Windows will not delete the open database file.
    tmp.cleanup()


def test_impact_names_the_dependents_and_what_each_relies_on(running):
    """The question an incident actually asks."""
    rows = running.runtime.data_impact("inventory_data")
    assert rows, "a declared dependency did not show up in impact"
    row = rows[0]
    assert row["agent_id"] == "ecommerce_agent"
    assert row["fields"] == ["sku", "on_hand", "location"]
    assert row["produced_by"] == "inventory_agent"
    assert row["whole_class"] is False


def test_a_class_nobody_declared_on_comes_back_empty(running):
    """Which means "nobody said", not "nothing is affected"."""
    assert running.runtime.data_impact("public_knowledge") == []


def test_fresh_data_passes_and_stale_data_takes_the_declared_action(running):
    fresh = running.runtime.stale_action("ecommerce_agent", "inventory_data", 60)
    assert fresh["ok"] and fresh["claimed"] and not fresh["stale"]

    stale = running.runtime.stale_action("ecommerce_agent", "inventory_data",
                                         5000)
    assert stale["ok"] is False
    assert stale["action"] == "refuse"
    assert "the design says refuse" in stale["note"]


def test_no_freshness_claim_is_not_the_same_as_a_claim_that_was_met(running):
    """Silence is not a promise, and the reply says so rather than reading
    like a pass."""
    reply = running.runtime.stale_action("cs_agent", "order_data", 99999)
    assert reply["ok"] and reply["claimed"] is False
    assert "declares no dependency" in reply["note"]


# -- the line this ADR draws ------------------------------------------------


def test_the_spec_models_no_physical_schema():
    """Physical shape belongs to whoever owns the data. A copy of it here
    would be correct until their next migration and silently wrong after.
    """
    from orgagents.spec import model

    source = pathlib.Path(model.__file__).read_text(encoding="utf-8")
    dependency = source[source.index("class DataDependency"):
                        source.index("class DataDependency") + 2000]
    for physical in ("column_type", "sql_type", "avro", "protobuf", "parquet",
                     "primary_key", "nullable"):
        assert physical not in dependency.lower(), (
            f"DataDependency grew {physical}; physical shape is the owning "
            "system's, and a copy of it drifts"
        )


# -- a contract that moves is a governance change ---------------------------


def _ayc_pair():
    ir = build_ir(load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml"))
    return ir, ir.model_copy(deep=True)


def _dep_changes(before, after):
    from orgagents.compiler.diff import diff_ir

    return [c for c in diff_ir(before, after).changes
            if "data_dependencies" in c.field]


def test_a_tightened_freshness_window_reads_as_narrowed():
    """The direction that can newly refuse work at run time."""
    before, after = _ayc_pair()
    for agent in after.agents:
        if agent.id == "ecommerce_agent":
            agent.data_dependencies[0].max_age_seconds = 120
    changes = _dep_changes(before, after)
    assert changes and changes[0].direction.value == "narrowed"
    assert "@900/refuse → " in changes[0].summary


def test_a_new_reliance_is_reported_as_one():
    from orgagents.spec.model import DataDependency

    before, after = _ayc_pair()
    for agent in after.agents:
        if agent.id == "cs_agent":
            agent.data_dependencies.append(
                DataDependency(data_class="order_data", fields=["id"]))
    changes = _dep_changes(before, after)
    assert changes and changes[0].kind.value == "added"
    assert "a change to this data is now a change to this agent" in \
        changes[0].rationale


def test_an_unchanged_contract_is_not_a_change():
    before, after = _ayc_pair()
    assert _dep_changes(before, after) == []
