"""Sub-agents as tools, human pairing, tools and endpoints (ADR-0026/27/29/30)."""
from pathlib import Path

import pytest

from orgagents.compiler import build_ir
from orgagents.runtime.subagents import SubAgentError, SubAgentRunner, resolve
from orgagents.spec import load_binding, load_spec, validate_spec
from orgagents.spec.model import HumanRole, SubAgentKind, SubAgentSpec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme.system.yaml"
BINDING = ROOT / "examples" / "acme.binding.yaml"


@pytest.fixture(scope="module")
def spec():
    return load_spec(EXAMPLE)


@pytest.fixture(scope="module")
def ir(spec):
    return build_ir(spec, binding=load_binding(BINDING).for_target("local"))


# -- human pairing (ADR-0026) ---------------------------------------------


def test_an_agent_pairs_with_several_humans_in_named_roles(spec):
    sre = spec.agent("sre")
    assert len(sre.humans) == 3
    assert sre.owner.name == "Lena Fischer"
    assert {h.name for h in sre.humans_with(HumanRole.ESCALATION)} == {
        "Samir Haddad", "Iris Nakamura"
    }


def test_one_person_pairs_with_several_agents(spec):
    pairings = spec.humans()
    assert set(pairings["priya@acme.example"]) >= {"cfo", "analyst", "reconciler"}
    assert set(pairings["samir@acme.example"]) >= {"platform_lead", "platform_engineer"}


def test_approvers_are_resolved_per_action(spec):
    engineer = spec.agent("platform_engineer")
    approvers = engineer.approvers_for("code_change")
    assert [h.name for h in approvers] == ["Samir Haddad"]
    # The owner is not automatically an approver.
    assert engineer.owner.name == "Jo Adeyemi"
    assert HumanRole.APPROVER not in engineer.owner.roles


def test_exactly_one_owner_is_required(spec):
    broken = spec.model_copy(deep=True)
    analyst = broken.agent("analyst")
    analyst.humans[1].roles = [HumanRole.OWNER]
    assert "multiple_owners" in {f.code for f in validate_spec(broken)}

    ownerless = spec.model_copy(deep=True)
    for human in ownerless.agent("analyst").humans:
        human.roles = [HumanRole.REVIEWER]
    assert "agent_without_owner" in {f.code for f in validate_spec(ownerless)}


def test_gating_an_action_with_no_approver_is_an_error(spec):
    broken = spec.model_copy(deep=True)
    engineer = broken.agent("platform_engineer")
    engineer.humans = [h for h in engineer.humans if HumanRole.OWNER in h.roles]
    engineer.humans[0].approves = ["code_change"]
    codes = {f.code for f in validate_spec(broken)}
    assert "approvals_without_approver" in codes


def test_the_pre_1_1_single_human_field_still_loads():
    from orgagents.spec.loader import load_spec_text

    spec = load_spec_text(
        "metadata: {name: legacy, spec_version: '1.1.0'}\n"
        "organization:\n  id: t\n  leader: a\n  members:\n"
        "    - id: a\n      human: {name: Ada, contact: ada@x.example}\n"
    )
    assert spec.agent("a").owner.name == "Ada"


def test_ir_carries_every_pairing(ir):
    analyst = ir.agent("analyst")
    assert [h.name for h in analyst.humans] == ["Tom Becker", "Priya Raman"]
    assert analyst.owner.name == "Tom Becker"
    assert "Priya Raman" in analyst.system_prompt()
    assert "reviewer" in analyst.system_prompt()


# -- sub-agents as tools (ADR-0027) ---------------------------------------


def test_subagents_become_tools_not_org_members(ir, spec):
    analyst = ir.agent("analyst")
    assert [s.tool_name for s in analyst.subagents] == [
        "subagent_topic_research", "subagent_figure_check", "subagent_draft_critic"
    ]
    # They are not agents: no reporting line, no delegation edge, no session.
    assert "topic_research" not in {a.id for a in ir.agents}
    assert "topic_research" not in analyst.delegates_to


def test_subagent_cannot_widen_its_parents_access():
    with pytest.raises(SubAgentError, match="does not hold"):
        resolve(
            SubAgentSpec(id="greedy", capabilities=["code_change"]),
            parent_agent_id="analyst", parent_capabilities={"warehouse_query"},
        )
    with pytest.raises(SubAgentError, match="tools its parent does not hold"):
        resolve(
            SubAgentSpec(id="greedy", tools=["deploy"]),
            parent_agent_id="analyst", parent_capabilities=set(),
            parent_tools=set(),
        )


def test_subagent_cannot_change_the_isolation_boundary():
    with pytest.raises(SubAgentError, match="isolation boundary"):
        resolve(
            SubAgentSpec(id="escape", environment="build"),
            parent_agent_id="analyst", parent_capabilities=set(),
            parent_environment="isolated_review",
        )


def test_validator_rejects_a_widening_subagent(spec):
    broken = spec.model_copy(deep=True)
    broken.agent("analyst").subagents[0].capabilities.append("code_change")
    assert "subagent_widens_access" in {f.code for f in validate_spec(broken)}


def test_subagent_prompt_states_what_it_is_not():
    tool = resolve(
        SubAgentSpec(id="r", kind=SubAgentKind.RESEARCH, purpose="Find things",
                     returns="a cited list"),
        parent_agent_id="analyst", parent_capabilities=set(),
    )
    prompt = tool.system_prompt()
    assert "invoked as a tool" in prompt
    assert "no memory beyond this call" in prompt
    assert "subset of your caller's access" in prompt
    assert "a cited list" in prompt


def test_subagent_runner_records_each_call():
    tool = resolve(SubAgentSpec(id="r", returns="x"), parent_agent_id="a",
                   parent_capabilities=set())
    runner = SubAgentRunner([tool], invoke=lambda t, task, ctx: f"did {task}")
    result = runner.callables()["subagent_r"](task="the thing")
    assert result["result"] == "did the thing"
    assert runner.calls[0]["task"] == "the thing"


def test_subagents_run_inside_a_session(tmp_path, spec):
    from orgagents.platform import Platform
    from orgagents.runtime.loader import load_system

    ir = build_ir(spec, binding=load_binding(BINDING).for_target("local"))
    platform = Platform(str(tmp_path / "sub.db"), configure_logs=False)
    load_system(platform, ir)
    run = platform.runtime.run("analyst", "Explain the variance")
    tools = platform.runtime._subagent_tools(platform.org.agent("analyst"),
                                             run.session_id)
    assert tools["subagent_figure_check"](task="check the draft")["subagent"] == (
        "figure_check"
    )
    events = [e for e in platform.sessions.events(run.session_id)
              if e.type == "subagent"]
    assert events and events[0].payload["kind"] == "verify"


# -- tools and endpoints (ADR-0029, ADR-0030) -----------------------------


def test_tools_wrap_things_the_agent_already_holds(ir):
    analyst = ir.agent("analyst")
    lookup = next(t for t in analyst.tools if t.id == "warehouse_lookup")
    assert lookup.wraps_kind == "capability" and lookup.wraps == "warehouse_query"
    assert lookup.source == "agent"


def test_a_wrapper_inherits_approval_and_cannot_remove_it(ir, spec):
    ask = next(t for t in ir.agent("analyst").tools if t.id == "ask_market_research")
    assert ask.requires_approval
    assert "ask_market_research" in ir.agent("analyst").requires_approval_for


def test_plugins_bring_their_skills_and_tools(ir):
    cfo = ir.agent("cfo")
    assert "finance_pack" in cfo.plugins
    assert {s.id for s in cfo.skills} >= {"close_narrative"}
    assert any(t.source == "plugin" for t in cfo.tools)


def test_tool_referencing_an_unheld_capability_is_rejected(spec):
    broken = spec.model_copy(deep=True)
    broken.agent("sre").tools.append("warehouse_lookup")
    assert "tool_without_capability" in {f.code for f in validate_spec(broken)}


def test_external_endpoints_may_not_receive_non_public_data(spec):
    broken = spec.model_copy(deep=True)
    broken.endpoint("market_research_desk").send_data_classes.append("finance_internal")
    assert "endpoint_exfiltration" in {f.code for f in validate_spec(broken)}


def test_endpoint_output_must_be_treated_as_data(spec):
    broken = spec.model_copy(deep=True)
    broken.endpoint("market_research_desk").treat_output_as_data = False
    assert "endpoint_trusts_output" in {f.code for f in validate_spec(broken)}


def test_endpoint_secret_lands_on_the_calling_identity(ir):
    assert "RESEARCH_DESK_KEY" in ir.agent("analyst").identity.secret_refs
    assert "RESEARCH_DESK_KEY" not in ir.agent("sre").identity.secret_refs
    assert "instructions" in ir.agent("analyst").system_prompt()
