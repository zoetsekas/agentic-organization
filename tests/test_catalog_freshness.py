"""Provenance, staleness and usage on catalog figures.

WS-026 M6 (figures refreshed from a source rather than typed) and WS-027 M5
(which designs reference which entry). ADR-0046 records what a stale figure is
allowed to do to a policy decision.
"""
import json
from pathlib import Path

import pytest

from orgagents.catalogs import (
    ApprovalStatus,
    CatalogEntry,
    CatalogError,
    CatalogKind,
    CatalogService,
    CatalogUsage,
    FigureMethod,
    FigureProvenance,
    FileFigureSource,
    HttpFigureSource,
    MappingFigureSource,
    STALE_MARKER,
    seed_catalog,
)
from orgagents.compiler import build_ir
from orgagents.compiler.ir import apply_model_approvals
from orgagents.compiler.registry import registry_report
from orgagents.spec import load_binding, load_spec
from orgagents.spec.model import ModelClass, ModelPolicy
from orgagents.store import Store

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme.system.yaml"
BINDING = ROOT / "examples" / "acme.binding.yaml"

LONG_AGO = "2019-01-01T00:00:00+00:00"
RECENT = "2026-09-01T00:00:00+00:00"


@pytest.fixture()
def catalog(tmp_path):
    service = CatalogService(Store(tmp_path / "catalog.db"))
    seed_catalog(service)
    return service


def _payload(**figures):
    return {
        "source": "acme-price-file",
        "observed_at": RECENT,
        "source_url": "https://prices.internal/models.json",
        "models": {"anthropic/claude-haiku-4-5-20251001": {
            "context_tokens": 200_000,
            "cost_per_million_input": 1.0,
            "cost_per_million_output": 5.0,
            "regions": ["us-east", "eu-west"],
            **figures,
        }},
    }


def _file_source(tmp_path, payload):
    path = tmp_path / "figures.json"
    path.write_text(json.dumps(payload))
    return FileFigureSource(path)


def _stale_the_cost(catalog, name, *, cost_in=None):
    """Age one entry's figures past its horizon, leaving the numbers alone."""
    entry = catalog.by_name(CatalogKind.MODEL, name)
    if cost_in is not None:
        entry.attributes = {**entry.attributes, "cost_per_million_input": cost_in,
                            "cost_per_million_output": cost_in}
    entry.provenance = FigureProvenance(
        method=FigureMethod.OPERATOR, source="an operator, once",
        confirmed_at=LONG_AGO, staleness_horizon_days=180)
    return catalog.publish(entry)


# -- provenance and staleness ---------------------------------------------


def test_seeded_figures_say_where_they_came_from(catalog):
    haiku = catalog.by_name(CatalogKind.MODEL, "claude-haiku-4.5")
    assert haiku.provenance.method is FigureMethod.OPERATOR
    assert haiku.provenance.confirmed_at
    # A placeholder holds no figure, so it must never read as a fresh one.
    placeholder = catalog.get("cat_model_openai_placeholder")
    assert placeholder.provenance.method is FigureMethod.PLACEHOLDER
    assert placeholder.figure_state == "placeholder"


def test_importing_records_provenance(catalog, tmp_path):
    source = _file_source(tmp_path, _payload(cost_per_million_input=1.25))
    report = catalog.refresh_figures(source)

    haiku = catalog.by_name(CatalogKind.MODEL, "claude-haiku-4.5")
    assert haiku.attributes["cost_per_million_input"] == 1.25
    assert haiku.provenance.method is FigureMethod.IMPORTED
    assert haiku.provenance.source == "acme-price-file"
    assert haiku.provenance.source_url.startswith("https://")
    assert haiku.provenance.confirmed_at == RECENT
    assert "cost_per_million_input" in haiku.provenance.fields
    assert report.updated["claude-haiku-4.5"] == ["cost_per_million_input"]
    assert haiku.figure_state == "fresh"


def test_a_source_with_no_figure_leaves_the_entry_alone(catalog, tmp_path):
    before = catalog.by_name(CatalogKind.MODEL, "claude-opus-5")
    report = catalog.refresh_figures(_file_source(tmp_path, _payload()))

    after = catalog.by_name(CatalogKind.MODEL, "claude-opus-5")
    assert after.attributes == before.attributes
    assert after.provenance == before.provenance
    # And it says so rather than quietly doing nothing.
    assert "claude-opus-5" in report.uncovered
    assert "no figure in source" in report.summary()


def test_a_source_may_not_set_editorial_fields(catalog, tmp_path):
    payload = _payload()
    payload["models"]["anthropic/claude-haiku-4-5-20251001"]["classes"] = ["frontier_reasoning"]
    catalog.refresh_figures(_file_source(tmp_path, payload))
    haiku = catalog.by_name(CatalogKind.MODEL, "claude-haiku-4.5")
    assert haiku.attributes["classes"] == ["fast_cheap", "balanced"]


def test_refresh_is_idempotent(catalog, tmp_path):
    source = _file_source(tmp_path, _payload(cost_per_million_input=1.25))
    catalog.refresh_figures(source)
    first = catalog.by_name(CatalogKind.MODEL, "claude-haiku-4.5")

    second_report = catalog.refresh_figures(source)
    second = catalog.by_name(CatalogKind.MODEL, "claude-haiku-4.5")

    assert second.attributes == first.attributes
    assert not second_report.changed_any
    assert second_report.reconfirmed == ["claude-haiku-4.5"]


def test_an_http_source_is_injected_not_imported(catalog):
    calls = []

    def fetch(url):
        calls.append(url)
        return _payload(context_tokens=250_000)

    source = HttpFigureSource("https://prices.internal/models.json", fetch)
    report = catalog.refresh_figures(source)

    # One fetch for the whole catalog, not one per entry.
    assert calls == ["https://prices.internal/models.json"]
    assert report.updated["claude-haiku-4.5"] == ["context_tokens"]
    assert catalog.by_name(
        CatalogKind.MODEL, "claude-haiku-4.5").attributes["context_tokens"] == 250_000


def test_a_figure_past_its_horizon_is_stale(catalog):
    entry = _stale_the_cost(catalog, "claude-sonnet-5")
    assert entry.figures_stale()
    assert entry.figure_state == "stale"
    assert entry.provenance.age_days() > 180

    fresh = catalog.by_name(CatalogKind.MODEL, "claude-opus-5")
    assert not fresh.figures_stale()


def test_never_confirmed_is_stale_not_fresh():
    assert FigureProvenance().is_stale()
    assert "never confirmed" in FigureProvenance().describe()


def test_staleness_shows_up_in_stats(catalog):
    _stale_the_cost(catalog, "claude-sonnet-5")
    stats = catalog.stats()
    assert stats["stale_figures"] == ["claude-sonnet-5"]
    assert "openai-model (complete before approving)" in stats["placeholder_figures"]


def test_refreshing_clears_staleness(catalog, tmp_path):
    _stale_the_cost(catalog, "claude-haiku-4.5")
    assert catalog.stats()["stale_figures"] == ["claude-haiku-4.5"]
    catalog.refresh_figures(_file_source(tmp_path, _payload()))
    assert catalog.stats()["stale_figures"] == []


# -- what a stale figure does to a decision (ADR-0046) ---------------------


def test_a_permitted_model_on_a_stale_figure_still_permits_but_says_so(catalog):
    _stale_the_cost(catalog, "claude-sonnet-5")
    sonnet = catalog.by_name(CatalogKind.MODEL, "claude-sonnet-5")
    decision = catalog.check_model(
        sonnet, ModelPolicy(classes=[ModelClass.BALANCED],
                            max_cost_per_million_tokens=10.0))
    assert decision.allowed
    assert decision.stale_figures
    assert STALE_MARKER in decision.reason


def test_a_refusal_on_a_stale_figure_still_refuses_and_names_it(catalog):
    _stale_the_cost(catalog, "claude-sonnet-5", cost_in=99.0)
    sonnet = catalog.by_name(CatalogKind.MODEL, "claude-sonnet-5")
    decision = catalog.check_model(
        sonnet, ModelPolicy(classes=[ModelClass.BALANCED],
                            max_cost_per_million_tokens=10.0))
    assert not decision.allowed
    assert "over the" in decision.reason and STALE_MARKER in decision.reason


def test_fallback_prefers_a_model_whose_price_we_still_believe(catalog):
    """Haiku is cheapest, but on a figure nobody has confirmed since 2019."""
    _stale_the_cost(catalog, "claude-haiku-4.5")
    policy = ModelPolicy(classes=[ModelClass.BALANCED])
    permitted = catalog.permitted_models(policy)
    assert [e.name for e in permitted][0] == "claude-sonnet-5"
    # The stale row is still permitted — it is last, not excluded.
    assert "claude-haiku-4.5" in [e.name for e in permitted]


def test_a_fallback_onto_a_stale_figure_is_visible_in_the_ir_and_registry(catalog):
    """When every candidate is stale, the fallback still happens — flagged."""
    for name in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4.5"):
        _stale_the_cost(catalog, name)

    spec = load_spec(EXAMPLE)
    binding = load_binding(BINDING).for_target("local")
    ir = apply_model_approvals(build_ir(spec, binding=binding), catalog)

    fell_back = [a for a in ir.agents
                 if a.model_approval and a.model_approval.fallback_applied]
    on_stale = [a for a in ir.agents
                if a.model_approval and STALE_MARKER in a.model_approval.approval_reason]
    assert on_stale, "a verdict on a stale figure must be recorded on the IR"

    registry = registry_report(ir)
    assert "stale catalog figure" in registry
    for agent in on_stale:
        assert f"`{agent.id}`" in registry.split("stale catalog figure")[1].split("\n")[0]
    if fell_back:
        assert set(a.id for a in fell_back) <= set(a.id for a in on_stale)


# -- usage tracking (WS-027 M5) -------------------------------------------


def test_a_compiled_design_records_which_entries_it_uses(catalog):
    spec = load_spec(EXAMPLE)
    binding = load_binding(BINDING).for_target("local")
    ir = apply_model_approvals(build_ir(spec, binding=binding), catalog)

    recorded = catalog.record_ir_usage(ir)
    assert recorded
    report = catalog.usage_report()
    assert report["references"] == len(recorded)
    used = catalog.by_name(CatalogKind.MODEL, "claude-sonnet-5")
    assert ir.name in catalog.usage.systems_using(used.id)
    # Nothing uses an environment template through the model path, so the
    # unused list is a real answer rather than an empty one.
    assert "relational-sql" in report["unused"]


def test_recording_usage_twice_does_not_duplicate_rows(catalog):
    spec = load_spec(EXAMPLE)
    binding = load_binding(BINDING).for_target("local")
    ir = apply_model_approvals(build_ir(spec, binding=binding), catalog)

    first = catalog.record_ir_usage(ir)
    second = catalog.record_ir_usage(ir)
    assert len(catalog.usage.all()) == len(first) == len(second)


def test_retiring_an_entry_in_use_is_refused_until_forced(catalog):
    entry = catalog.by_name(CatalogKind.MODEL, "claude-sonnet-5")
    catalog.record_usage(CatalogUsage(
        entry_id=entry.id, system="acme", subject="analyst", role="model"))

    with pytest.raises(CatalogError) as raised:
        catalog.retire(entry.id, reviewer="platform")
    assert "acme" in str(raised.value)

    retired = catalog.retire(entry.id, reviewer="platform",
                             superseded_by="claude-opus-5", force=True)
    assert retired.status is ApprovalStatus.RETIRED


def test_an_unused_entry_retires_without_ceremony(catalog):
    entry = catalog.by_name(CatalogKind.MODEL, "claude-haiku-4.5")
    assert catalog.retire(entry.id, reviewer="platform").status is ApprovalStatus.RETIRED


def test_a_named_model_on_a_stale_figure_is_still_marked(catalog):
    """An explicit allow list skips the constraints, not the provenance."""
    _stale_the_cost(catalog, "claude-opus-5")
    opus = catalog.by_name(CatalogKind.MODEL, "claude-opus-5")
    decision = catalog.check_model(opus, ModelPolicy(allow=["claude-opus-5"]))
    assert decision.allowed and decision.stale_figures
    assert STALE_MARKER in decision.reason
