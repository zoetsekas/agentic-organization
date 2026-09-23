"""IR diffing for change review (WS-005 M4).

Every fixture here is built by compiling `examples/acme/acme.system.yaml` and
mutating a copy of the spec, so the diff is exercised against IRs that came out
of the real resolution path — inheritance, narrowing, identities and all.
"""
import copy
from pathlib import Path

import pytest
import yaml

from orgagents.catalogs import CatalogService, seed_catalog
from orgagents.compiler import build_ir
from orgagents.compiler.diff import (
    ChangeKind,
    Direction,
    IncomparableIRError,
    Severity,
    diff_ir,
)
from orgagents.compiler.ir import TenantIR, apply_model_approvals
from orgagents.spec import load_binding, load_spec, load_spec_text
from orgagents.store import Store

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme" / "acme.system.yaml"
BINDING = ROOT / "examples" / "acme" / "acme.binding.yaml"


@pytest.fixture(scope="module")
def raw():
    return yaml.safe_load(EXAMPLE.read_text())


@pytest.fixture(scope="module")
def binding():
    return load_binding(BINDING).for_target("local")


@pytest.fixture(scope="module")
def base_ir(binding):
    return build_ir(load_spec(EXAMPLE), binding=binding)


def mutated(raw, change, binding, *, target="local"):
    """Compile a copy of the example spec with `change` applied to the document."""
    data = copy.deepcopy(raw)
    change(data)
    spec = load_spec_text(yaml.safe_dump(data))
    return build_ir(spec, target=target, binding=binding)


def _role(data, role_id):
    return next(r for r in data["organization"]["role_definitions"] if r["id"] == role_id)


def _environment(data, env_id):
    return next(e for e in data["organization"]["environments"] if e["id"] == env_id)


def _find(diff, subject, field, kind=None):
    return [
        c for c in diff.changes
        if c.subject == subject and c.field == field
        and (kind is None or c.kind is kind)
    ]


# -- nothing changed -------------------------------------------------------


def test_an_unchanged_ir_produces_an_empty_diff_that_says_so(base_ir, raw, binding):
    twin = mutated(raw, lambda d: None, binding)
    diff = diff_ir(base_ir, twin)
    assert diff.empty and diff.changes == []
    assert diff.security_findings == []
    assert "No differences" in diff.to_text()
    assert diff.to_dict()["summary"] == {
        "total": 0, "security_findings": 0, "worst_severity": None
    }


# -- widening vs narrowing -------------------------------------------------


def test_a_widened_permission_is_reported_and_outranks_a_cosmetic_change(
    base_ir, raw, binding
):
    def change(data):
        _role(data, "financial_analyst")["permissions"].append(
            {"action": "administer", "resource_kind": "data_class", "resource": "*"}
        )
        for member in data["organization"]["teams"][0]["members"]:
            if member["id"] == "analyst":
                member["description"] = "A freshly reworded description."

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    granted = _find(diff, "analyst", "permissions", ChangeKind.ADDED)
    assert granted and granted[0].direction is Direction.WIDENED
    assert granted[0].severity is Severity.CRITICAL   # administer over '*'
    assert granted[0].security_relevant
    assert "administer:data_class:*" in granted[0].summary

    cosmetic = _find(diff, "analyst", "description")
    assert cosmetic and cosmetic[0].severity is Severity.INFO
    assert not cosmetic[0].security_relevant
    # Ranked by consequence: the widening comes first in the report.
    assert diff.changes.index(granted[0]) < diff.changes.index(cosmetic[0])
    text = diff.to_text()
    assert text.index("administer:data_class:*") < text.index("description changed")


def test_a_narrowed_permission_is_a_different_event_and_not_a_finding(
    base_ir, raw, binding
):
    def change(data):
        role = _role(data, "financial_analyst")
        # The workflow grant is the role's own; the rest it shares with its
        # team, so dropping one of those would change nothing resolved.
        role["permissions"] = [
            p for p in role["permissions"] if p["resource_kind"] != "workflow"
        ]

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    revoked = _find(diff, "analyst", "permissions", ChangeKind.REMOVED)
    assert revoked, "a lost permission must still be reported"
    assert revoked[0].direction is Direction.NARROWED
    assert not revoked[0].security_relevant
    assert not diff.security_findings
    assert "no widening" in diff.to_text()
    assert _find(diff, "analyst", "permissions", ChangeKind.ADDED) == []


def test_dropping_a_condition_widens_a_permission_that_still_looks_the_same(
    base_ir, raw, binding
):
    def change(data):
        role = _role(data, "financial_analyst")
        grant = next(p for p in role["permissions"]
                     if p["resource_kind"] == "workflow")
        grant.setdefault("conditions", {})["during"] = "close_week"

    widened_ir = base_ir
    narrowed_ir = mutated(raw, change, binding)
    diff = diff_ir(narrowed_ir, widened_ir)   # condition present → absent
    modified = _find(diff, "analyst", "permissions", ChangeKind.MODIFIED)
    assert modified and modified[0].direction is Direction.WIDENED
    assert modified[0].severity is Severity.HIGH


# -- sandbox, egress and secrets -------------------------------------------


def test_an_added_egress_destination_is_a_security_finding(base_ir, raw, binding):
    def change(data):
        _environment(data, "analysis")["egress_allowlist"].append("pastebin.example")

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    egress = _find(diff, "analyst", "environment.egress_allowlist", ChangeKind.ADDED)
    assert egress and egress[0].direction is Direction.WIDENED
    assert egress[0].severity is Severity.HIGH and egress[0].security_relevant
    assert "pastebin.example" in egress[0].summary


def test_a_widened_network_posture_is_critical_when_it_reaches_open(
    base_ir, raw, binding
):
    def change(data):
        _environment(data, "isolated_review")["network"] = "open"

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    posture = _find(diff, "reconciler", "environment.network")
    assert posture and posture[0].direction is Direction.WIDENED
    assert posture[0].severity is Severity.CRITICAL

    # ...and the reverse comparison is a narrowing, not a finding.
    back = diff_ir(mutated(raw, change, binding), base_ir)
    assert back.security_findings == []
    assert _find(back, "reconciler", "environment.network")[0].direction \
        is Direction.NARROWED


def test_a_new_secret_reference_is_reported_against_the_identity_that_holds_it(
    base_ir, raw, binding
):
    def change(data):
        _environment(data, "documents").setdefault("secret_refs", []).append(
            "BILLING_API_KEY"
        )

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    secrets = [c for c in diff.security_findings
               if c.subject_kind == "identity" and c.field == "secret_refs"]
    assert secrets, "a credential landing on a workload identity must be reported"
    assert all(c.severity is Severity.CRITICAL for c in secrets)
    assert any("BILLING_API_KEY" in c.summary for c in secrets)


def test_a_removed_guardrail_is_critical_and_adding_one_is_not(base_ir, raw, binding):
    def change(data):
        data["organization"]["guardrails"] = [
            g for g in data["organization"]["guardrails"] if g["id"] != "no_credentials_out"
        ]

    stripped = mutated(raw, change, binding)
    diff = diff_ir(base_ir, stripped)
    removed = [c for c in diff.changes
               if c.field == "guardrails" and c.kind is ChangeKind.REMOVED]
    # A system-wide guardrail is reported once, not once per agent carrying it.
    assert len(removed) == 1 and removed[0].subject_kind == "system"
    assert removed[0].severity is Severity.CRITICAL
    assert removed[0].direction is Direction.WIDENED

    restored = diff_ir(stripped, base_ir)
    assert restored.security_findings == []
    assert all(c.direction is Direction.NARROWED for c in restored.changes
               if c.field == "guardrails")


# -- trust and outside parties ---------------------------------------------


def test_raising_an_endpoints_trust_class_is_critical(base_ir, raw, binding):
    def change(data):
        next(e for e in data["organization"]["endpoints"]
             if e["id"] == "market_research_desk")["trust"] = "internal"

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    trust = [c for c in diff.changes if c.field.endswith("].trust")]
    assert trust and trust[0].direction is Direction.WIDENED
    assert trust[0].severity is Severity.CRITICAL


def test_no_longer_treating_an_endpoints_output_as_data_is_critical(
    base_ir, raw, binding
):
    def change(data):
        next(e for e in data["organization"]["endpoints"]
             if e["id"] == "market_research_desk")["treat_output_as_data"] = False

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    flipped = [c for c in diff.changes
               if c.field.endswith("].treat_output_as_data")]
    assert flipped and flipped[0].severity is Severity.CRITICAL
    assert "prompt-injection" in flipped[0].rationale


# -- model governance ------------------------------------------------------


@pytest.fixture()
def catalog(tmp_path):
    service = CatalogService(Store(tmp_path / "catalog.db"))
    seed_catalog(service)
    return service


def test_an_agent_falling_off_the_approved_model_shelf_is_reported(
    raw, binding, catalog
):
    approved = apply_model_approvals(
        build_ir(load_spec(EXAMPLE), binding=binding), catalog)

    def change(data):
        # Nothing on the shelf is this cheap, so the policy can no longer be met.
        data["model_policy"]["max_cost_per_million_tokens"] = 0.0001

    unapproved = apply_model_approvals(mutated(raw, change, binding), catalog)
    diff = diff_ir(approved, unapproved)
    verdicts = [c for c in diff.changes if c.field == "model.approved"]
    assert verdicts, "a model that stopped being approved must be visible"
    assert all(c.severity is Severity.CRITICAL for c in verdicts)
    assert all(c.direction is Direction.WIDENED for c in verdicts)
    assert diff.security_findings


def test_moving_an_agent_onto_a_different_model_is_reported_without_alarm(
    raw, binding, catalog
):
    base = apply_model_approvals(
        build_ir(load_spec(EXAMPLE), binding=binding), catalog)
    other = copy.deepcopy(base)
    for agent in other.agents:
        agent.model = {**agent.model, "model": "claude-haiku-4-5-20251001"}
    other = apply_model_approvals(other, catalog)

    diff = diff_ir(base, other)
    swaps = [c for c in diff.changes if c.field == "model"]
    assert swaps and all(c.severity is Severity.MEDIUM for c in swaps)
    assert all(c.direction is Direction.NEUTRAL for c in swaps)


# -- the awkward cases -----------------------------------------------------


def test_a_removed_agent_is_reported_as_a_removal_not_as_a_hundred_edits(
    base_ir, raw, binding
):
    def change(data):
        finance = data["organization"]["teams"][0]
        finance["members"] = [m for m in finance["members"] if m["id"] != "reconciler"]

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    gone = _find(diff, "reconciler", "agent", ChangeKind.REMOVED)
    assert len(gone) == 1
    assert gone[0].direction is Direction.NARROWED
    assert not gone[0].security_relevant
    # Its permissions are not separately re-reported; the agent is simply gone.
    assert _find(diff, "reconciler", "permissions") == []
    # Its workload identity goes with it.
    assert [c for c in diff.changes
            if c.subject_kind == "identity" and c.kind is ChangeKind.REMOVED]


def test_an_agent_that_moved_team_is_a_move_with_its_inheritance_explained(
    base_ir, raw, binding
):
    def change(data):
        finance = data["organization"]["teams"][0]
        technology = data["organization"]["teams"][1]
        moving = next(m for m in finance["members"] if m["id"] == "analyst")
        finance["members"] = [m for m in finance["members"] if m["id"] != "analyst"]
        technology["members"].append(moving)

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    moved = _find(diff, "analyst", "team", ChangeKind.MOVED)
    assert len(moved) == 1
    assert moved[0].severity is Severity.HIGH
    assert "inherit" in moved[0].rationale
    # It is one agent moving, not one added and one removed.
    assert _find(diff, "analyst", "agent") == []


def test_an_id_reused_for_a_different_agent_is_called_out_as_a_replacement(
    base_ir, raw, binding
):
    def change(data):
        finance = data["organization"]["teams"][0]
        technology = data["organization"]["teams"][1]
        reused = next(m for m in finance["members"] if m["id"] == "analyst")
        finance["members"] = [m for m in finance["members"] if m["id"] != "analyst"]
        reused["name"] = "Capacity Planner"
        technology["members"].append(reused)

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    reuse = _find(diff, "analyst", "identity", ChangeKind.REUSED)
    assert len(reuse) == 1
    assert reuse[0].severity is Severity.HIGH
    assert "different agent" in reuse[0].summary
    # A reuse is reported instead of a move, not as well as one.
    assert _find(diff, "analyst", "team", ChangeKind.MOVED) == []


def test_two_targets_are_refused_rather_than_diffed(base_ir, raw, binding):
    other_target = mutated(raw, lambda d: None, None, target="terraform:gcp")
    with pytest.raises(IncomparableIRError) as excinfo:
        diff_ir(base_ir, other_target)
    assert "different targets" in str(excinfo.value)


def test_two_tenants_are_refused_because_isolation_is_absolute(base_ir):
    other_tenant = base_ir.model_copy(deep=True)
    other_tenant.tenant = TenantIR(
        id="t-blue", namespace_prefix="blue", isolation_domain="blue.example"
    )
    with pytest.raises(IncomparableIRError) as excinfo:
        diff_ir(base_ir, other_tenant)
    assert "different tenants" in str(excinfo.value)
    # Same tenant on both sides is comparable again.
    same = base_ir.model_copy(deep=True)
    same.tenant = other_tenant.tenant
    assert diff_ir(other_tenant, same).empty


def test_a_refusal_can_be_overridden_deliberately(base_ir, raw):
    other_target = mutated(raw, lambda d: None, None, target="terraform:gcp")
    diff = diff_ir(base_ir, other_target, force=True)
    assert isinstance(diff.changes, list)   # no refusal, for a caller who insists


# -- the two report forms --------------------------------------------------


def test_the_text_report_leads_with_security_and_the_json_form_carries_the_same(
    base_ir, raw, binding
):
    def change(data):
        _environment(data, "analysis")["egress_allowlist"].append("pastebin.example")
        for member in data["organization"]["teams"][0]["members"]:
            if member["id"] == "analyst":
                member["description"] = "Reworded."

    diff = diff_ir(base_ir, mutated(raw, change, binding))
    text = diff.to_text()
    assert text.index("SECURITY-RELEVANT") < text.index("OTHER CHANGES")
    assert "pastebin.example" in text
    assert "▲ widened" in text

    payload = diff.to_dict()
    assert payload["summary"]["total"] == len(diff.changes)
    assert payload["summary"]["security_findings"] == len(diff.security_findings)
    assert payload["summary"]["worst_severity"] == diff.worst().value
    first = payload["changes"][0]
    assert first["security_relevant"] is True
    assert first["path"].startswith("agent:analyst")
    assert "widened" == first["direction"]
    assert diff.to_json().startswith("{")
