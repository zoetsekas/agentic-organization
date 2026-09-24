"""Short-lived missions, model policy and the platform catalog.

ADR-0039 (missions), ADR-0040 (approved models), ADR-0041 (the catalog).
"""
from pathlib import Path

import pytest

from orgagents.catalogs import (
    ApprovalStatus,
    CatalogEntry,
    CatalogKind,
    CatalogService,
    Entitlement,
    seed_catalog,
)
from orgagents.compiler import build_ir, compile_system
from orgagents.compiler.engine import CompileError
from orgagents.compiler.ir import apply_model_approvals
from orgagents.phases import review
from orgagents.spec import load_binding, load_spec, validate_spec
from orgagents.spec.model import MissionStatus, ModelClass, ModelPolicy
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


# -- missions (ADR-0039) ---------------------------------------------------


def test_missions_draw_from_the_standing_organization(spec):
    mission = spec.mission("q4_forecast_rebuild")
    agent_ids = {a.id for a in spec.agents()}
    assert set(mission.members) <= agent_ids
    # Members keep their home team; a mission is not a reorganization.
    assert spec.team_of("analyst").id == "finance"
    assert spec.team_of("cro").id == "revenue"


def test_a_mission_crosses_the_org_chart(spec):
    """The point of a task force: people from different standing teams."""
    mission = spec.mission("q4_forecast_rebuild")
    homes = {spec.team_of(m).id for m in mission.members}
    assert len(homes) > 1


def test_every_mission_has_a_leader_who_is_a_member(spec):
    for mission in spec.missions:
        assert mission.leader
        assert mission.leader in mission.members


def test_a_mission_without_a_leader_is_refused(spec):
    broken = spec.model_copy(deep=True)
    broken.mission("q4_forecast_rebuild").leader = ""
    assert "mission_without_leader" in {f.code for f in validate_spec(broken)}


def test_a_leader_from_outside_the_mission_is_refused(spec):
    broken = spec.model_copy(deep=True)
    broken.mission("q4_forecast_rebuild").leader = "sre"
    assert "mission_leader_not_member" in {f.code for f in validate_spec(broken)}


def test_a_mission_must_end(spec):
    """A mission that never ends is a reorganization."""
    broken = spec.model_copy(deep=True)
    broken.mission("q4_forecast_rebuild").ends_on = None
    assert "mission_without_end" in {f.code for f in validate_spec(broken)}


def test_a_mission_needs_an_objective(spec):
    broken = spec.model_copy(deep=True)
    broken.mission("checkout_latency_swat").objective = ""
    assert "mission_without_objective" in {f.code for f in validate_spec(broken)}


def test_members_must_exist_in_the_organization(spec):
    broken = spec.model_copy(deep=True)
    broken.mission("q4_forecast_rebuild").members.append("nobody")
    assert "mission_member_not_in_org" in {f.code for f in validate_spec(broken)}


def test_a_long_mission_is_flagged_as_a_standing_team(spec):
    broken = spec.model_copy(deep=True)
    broken.mission("q4_forecast_rebuild").ends_on = "2027-09-15"
    assert "mission_too_long" in {f.code for f in validate_spec(broken)}


def test_dates_must_make_sense(spec):
    broken = spec.model_copy(deep=True)
    mission = broken.mission("q4_forecast_rebuild")
    mission.starts_on, mission.ends_on = "2026-10-31", "2026-09-15"
    assert "mission_ends_before_it_starts" in {f.code for f in validate_spec(broken)}


def test_a_mission_never_grants_access_a_member_lacks(spec):
    """Mission roles are intersected with what a member already holds."""
    broken = spec.model_copy(deep=True)
    mission = broken.mission("q4_forecast_rebuild")
    mission.roles = ["platform_engineer"]        # the analyst holds nothing of it
    findings = {f.code for f in validate_spec(broken)}
    assert "mission_would_widen_access" in findings
    ir = build_ir(broken)
    resolved = next(m for m in ir.missions if m.id == "q4_forecast_rebuild")
    assert resolved.granted_permissions["analyst"] == []


def test_mission_membership_opens_lateral_work_for_its_duration(spec):
    ir = build_ir(spec)
    analyst = ir.agent("analyst")
    # The analyst and the CRO are on the same mission, in different teams.
    assert "cro" in analyst.mission_delegates_to
    assert "cro" in analyst.delegates_to
    # Someone on neither mission is unaffected.
    assert "platform_engineer" not in analyst.delegates_to


def test_mission_membership_reaches_the_prompt(spec):
    prompt = build_ir(spec).agent("cro").system_prompt()
    assert "Missions you are on" in prompt
    assert "Q4 forecast rebuild" in prompt
    assert "until 2026-10-31" in prompt


def test_the_ir_records_the_window_and_status(spec):
    ir = build_ir(spec)
    mission = next(m for m in ir.missions if m.id == "checkout_latency_swat")
    assert mission.status == MissionStatus.ACTIVE.value
    assert mission.duration_days == 28
    assert mission.sponsor.name == "Iris Nakamura"


def test_completed_missions_stop_conferring_anything(spec):
    finished = spec.model_copy(deep=True)
    finished.mission("q4_forecast_rebuild").status = MissionStatus.COMPLETED
    ir = build_ir(finished)
    assert "cro" not in ir.agent("analyst").mission_delegates_to


def test_the_registry_lists_missions(spec, binding, tmp_path, catalog):
    result = compile_system(spec, targets=["local"], out_dir=tmp_path,
                            binding=binding, catalog=catalog)[0]
    registry = (result.out_dir / "REGISTRY.md").read_text(encoding="utf-8")
    assert "Missions (short-lived teams)" in registry
    assert "q4_forecast_rebuild" in registry and "2026-10-31" in registry
    assert "Missions with no end date:** none" in registry


# -- model policy (ADR-0040) ----------------------------------------------


def test_every_agent_is_limited_to_approved_models(spec, binding, catalog):
    ir = apply_model_approvals(
        build_ir(spec, binding=binding.for_target("local")), catalog)
    for agent in ir.agents:
        assert agent.model_approval.approved, (
            agent.id, agent.model_approval.approval_reason)
        assert agent.model_approval.catalog_entry


def test_a_model_outside_the_policy_stops_the_build(spec, binding, catalog, tmp_path):
    over = binding.model_copy(deep=True)
    # The analyst's policy is balanced/long_context under a 20/M ceiling.
    over.for_target("local").agent_overrides["analyst"] = {
        "model": {"model": "claude-opus-5"}}
    with pytest.raises(CompileError, match="not permitted"):
        compile_system(spec, targets=["local"], out_dir=tmp_path, binding=over,
                       catalog=catalog)


def test_an_uncatalogued_model_is_refused(catalog):
    decision = catalog.resolve_model(ModelPolicy(classes=[ModelClass.BALANCED]),
                                     provider="acme", model_id="homegrown-1")
    assert not decision.allowed and "not in the catalog" in decision.reason


def test_policy_constraints_are_each_enforced(catalog):
    opus = catalog.by_name(CatalogKind.MODEL, "claude-opus-5")
    cheap = ModelPolicy(classes=[ModelClass.FRONTIER_REASONING],
                        max_cost_per_million_tokens=5)
    assert "over the" in catalog.check_model(opus, cheap).reason

    big = ModelPolicy(classes=[ModelClass.BALANCED], min_context_tokens=1_000_000)
    sonnet = catalog.by_name(CatalogKind.MODEL, "claude-sonnet-5")
    assert "below the required" in catalog.check_model(sonnet, big).reason

    elsewhere = ModelPolicy(classes=[ModelClass.BALANCED], require_regions=["ap-south"])
    assert "outside the required" in catalog.check_model(sonnet, elsewhere).reason


def test_an_explicit_allow_list_is_the_whole_answer(catalog):
    policy = ModelPolicy(classes=[ModelClass.FAST_CHEAP], allow=["claude-opus-5"])
    opus = catalog.by_name(CatalogKind.MODEL, "claude-opus-5")
    haiku = catalog.by_name(CatalogKind.MODEL, "claude-haiku-4.5")
    assert catalog.check_model(opus, policy).allowed          # named, class ignored
    assert not catalog.check_model(haiku, policy).allowed     # not named


def test_denying_a_model_beats_everything(catalog):
    policy = ModelPolicy(classes=[ModelClass.BALANCED], deny=["claude-sonnet-5"])
    sonnet = catalog.by_name(CatalogKind.MODEL, "claude-sonnet-5")
    assert not catalog.check_model(sonnet, policy).allowed


def test_a_refusal_offers_alternatives(catalog):
    decision = catalog.resolve_model(
        ModelPolicy(classes=[ModelClass.BALANCED], max_cost_per_million_tokens=20),
        provider="anthropic", model_id="claude-opus-5")
    assert not decision.allowed
    assert "claude-haiku-4.5" in decision.alternatives


def test_an_empty_policy_is_a_spec_error(spec):
    broken = spec.model_copy(deep=True)
    broken.model_policy.classes = []
    assert "model_policy_empty" in {f.code for f in validate_spec(broken)}


def test_the_phase_gate_checks_models(spec, binding, catalog):
    report = review(spec, binding=binding, target="local", catalog=catalog)
    assert report.ready_to_compile, [str(c) for c in report.failures()]
    over = binding.model_copy(deep=True)
    over.for_target("local").agent_overrides["analyst"] = {
        "model": {"model": "claude-opus-5"}}
    bad = review(spec, binding=over, target="local", catalog=catalog)
    assert "models_are_approved" in {c.id for c in bad.failures("implementation")}


# -- the catalog (ADR-0041) ------------------------------------------------


def test_the_catalog_carries_every_kind_of_building_block(catalog):
    kinds = {e.kind for e in catalog.list()}
    assert {CatalogKind.MODEL, CatalogKind.MCP_SERVER,
            CatalogKind.ENVIRONMENT_TEMPLATE, CatalogKind.PERMISSION_SET,
            CatalogKind.GUARDRAIL} <= kinds


def test_only_approved_entries_are_selectable(catalog):
    proposed = catalog.by_name(CatalogKind.MODEL,
                               "openai-model (complete before approving)")
    assert not proposed.selectable
    assert catalog.by_name(CatalogKind.MODEL, "claude-sonnet-5").selectable


def test_restricted_entries_need_an_entitlement(catalog):
    repo = catalog.by_name(CatalogKind.MCP_SERVER, "source-repository")
    assert repo.available_to(groups=["engineering"])
    assert not repo.available_to(groups=["finance"])


def test_entitlements_can_be_scoped_to_an_environment(catalog):
    entry = catalog.by_name(CatalogKind.MODEL, "claude-opus-5")
    catalog.entitle(entry.id, Entitlement(environments=["production"]))
    refreshed = catalog.get(entry.id)
    assert refreshed.available_to(environment="production")
    assert not refreshed.available_to(environment="development")


def test_retiring_an_entry_removes_it_from_selection(catalog):
    sonnet = catalog.by_name(CatalogKind.MODEL, "claude-sonnet-5")
    opus = catalog.by_name(CatalogKind.MODEL, "claude-opus-5")
    catalog.retire(sonnet.id, reviewer="security", superseded_by=opus.id)
    retired = catalog.get(sonnet.id)
    assert not retired.selectable
    decision = catalog.check_model(retired, ModelPolicy(classes=[ModelClass.BALANCED]))
    assert not decision.allowed and "retired" in decision.reason
    assert opus.id in decision.reason


def test_review_records_who_decided(catalog):
    entry = catalog.by_name(CatalogKind.MODEL,
                            "self-hosted (complete before approving)")
    reviewed = catalog.review(entry.id, ApprovalStatus.REJECTED, reviewer="ana",
                              note="no on-premises capacity this year")
    assert reviewed.reviewed_by == "ana" and reviewed.reviewed_at
    assert "capacity" in reviewed.review_note


def test_search_filters_by_kind_status_and_text(catalog):
    assert catalog.search("sql", kind=CatalogKind.MCP_SERVER)
    approved = catalog.search(status=ApprovalStatus.APPROVED)
    assert approved and all(e.status is ApprovalStatus.APPROVED for e in approved)
    assert catalog.search("nothing-matches-this") == []


def test_stats_surface_what_needs_attention(catalog):
    stats = catalog.stats()
    assert stats["total"] >= 18
    assert stats["unreviewed"], "placeholder entries should be flagged"


def test_publishing_a_new_entry_and_installing_it(catalog):
    entry = catalog.publish(CatalogEntry(
        kind=CatalogKind.TOOL, name="invoice-lookup",
        summary="Look up one invoice by number.", owner="Finance Systems",
        status=ApprovalStatus.APPROVED, tags=["tool", "finance"]))
    assert catalog.get(entry.id).name == "invoice-lookup"
    assert catalog.record_install(entry.id).installs == 1


def test_seeding_is_idempotent(catalog):
    before = len(catalog.list())
    assert seed_catalog(catalog) == 0
    assert len(catalog.list()) == before


def test_a_mission_never_inverts_the_hierarchy(spec):
    """Mission membership must not let someone task their own leader."""
    ir = build_ir(spec)
    analyst, cfo = ir.agent("analyst"), ir.agent("cfo")
    # Both are on the Q4 mission, and the CFO leads it and manages the analyst.
    assert "cro" in analyst.mission_delegates_to          # a genuine peer
    assert "cfo" not in analyst.mission_delegates_to      # its leader
    assert "cfo" not in analyst.delegates_to
    # The mission leader may task the people on the mission. That is the job.
    assert {"analyst", "cro"} <= set(cfo.mission_delegates_to)


def test_a_mission_leader_from_another_team_can_task_its_members(spec):
    ir = build_ir(spec)
    lead = ir.agent("platform_lead")
    assert {"platform_engineer", "sre"} <= set(lead.mission_delegates_to)
    # And a member cannot task that leader back.
    assert "platform_lead" not in ir.agent("sre").mission_delegates_to


# -- runtime expiry (ADR-0039 v1.1.0, WS-025 M4) ---------------------------


def test_the_ir_carries_the_window_each_grant_is_good_for(spec):
    analyst = build_ir(spec).agent("analyst")
    grant = next(g for g in analyst.mission_grants
                 if g.mission == "q4_forecast_rebuild")
    assert grant.peers == ["cro"]
    assert grant.ends_on == "2026-10-31"
    # Standing reach is recorded separately, so the runtime can tell the two
    # apart once the mission is over.
    assert "cro" not in analyst.standing_delegates_to


def _loaded(tmp_path, spec, binding, name="runtime.db"):
    from orgagents.platform import Platform
    from orgagents.runtime.loader import load_system

    ir = build_ir(spec, target="local", binding=binding.for_target("local"))
    platform = Platform(str(tmp_path / name), configure_logs=False)
    load_system(platform, ir)
    return platform


def test_a_live_mission_confers_lateral_reach_at_runtime(tmp_path, spec, binding):
    platform = _loaded(tmp_path, spec, binding, "live.db")
    from datetime import date

    assert platform.org.can_delegate("analyst", "cro", on=date(2026, 10, 1))
    assert platform.org.mission_peers("analyst", on=date(2026, 10, 1)) == ["cro"]


def test_a_mission_past_its_end_date_confers_nothing(tmp_path, spec, binding):
    from datetime import date

    platform = _loaded(tmp_path, spec, binding, "expired.db")
    # The day after the mission ends, the same call is refused — no recompile,
    # no sweep, nothing else changed.
    assert not platform.org.can_delegate("analyst", "cro", on=date(2026, 11, 1))
    assert platform.org.mission_peers("analyst", on=date(2026, 11, 1)) == []
    # Standing reporting lines are untouched by expiry.
    assert platform.org.can_delegate("cfo", "analyst", on=date(2026, 11, 1))


def test_a_mission_confers_nothing_before_it_starts(tmp_path, spec, binding):
    from datetime import date

    platform = _loaded(tmp_path, spec, binding, "early.db")
    assert not platform.org.can_delegate("analyst", "cro", on=date(2026, 9, 1))


def test_sweeping_closes_missions_that_are_over(spec):
    from datetime import date

    from orgagents.missions import sweep

    copy = spec.model_copy(deep=True)
    assert sweep(copy, date(2026, 10, 15)) == ["checkout_latency_swat"]
    assert copy.mission("checkout_latency_swat").status is MissionStatus.COMPLETED
    # Idempotent: a second sweep on the same day finds nothing left.
    assert sweep(copy, date(2026, 10, 15)) == []


def test_an_overdue_mission_is_reported_by_the_validator(spec):
    overdue = spec.model_copy(deep=True)
    overdue.mission("q4_forecast_rebuild").ends_on = "2020-01-31"
    overdue.mission("q4_forecast_rebuild").starts_on = "2020-01-01"
    findings = {f.code for f in validate_spec(overdue)}
    assert "mission_past_its_end_date" in findings
