import pytest

from orgagents.messaging import ChannelKind, DeliveryError
from orgagents.models import SessionState


def test_run_creates_addressable_session(platform):
    r = platform.runtime.run("agt_fin_analyst", "Summarize open invoices.")
    assert r.state is SessionState.COMPLETED
    assert r.session_url.endswith(r.session_id)
    events = {e.type for e in platform.sessions.events(r.session_id)}
    assert {"session_created", "message", "state_changed"} <= events


def test_delegation_creates_child_session(platform):
    parent = platform.runtime.run("agt_cfo", "Kick off the quarterly close.")
    tools = platform.runtime._delegation_tools(
        platform.org.agent("agt_cfo"), parent.session_id
    )
    out = tools["delegate"]("agt_fin_analyst", "Pull invoice aging.")
    assert out["ok"]
    children = platform.sessions.children(parent.session_id)
    assert len(children) == 1 and children[0].agent_id == "agt_fin_analyst"
    trace = platform.sessions.trace(parent.session_id)
    assert trace["children"][0]["session"]["agent_id"] == "agt_fin_analyst"


def test_delegation_respects_hierarchy(platform):
    session = platform.sessions.create("agt_fin_analyst")
    tools = platform.runtime._delegation_tools(
        platform.org.agent("agt_fin_analyst"), session.id
    )
    assert not tools["delegate"]("agt_platform_eng", "Deploy this.")["ok"]


def test_subagent_spawn(platform):
    session = platform.sessions.create("agt_cto")
    tools = platform.runtime._delegation_tools(platform.org.agent("agt_cto"), session.id)
    out = tools["spawn_subagent"]("dep-auditor", "Audit dependencies.")
    assert out["ok"]
    assert any(a.name == "cto-agent/dep-auditor" for a in platform.org.agents())


def test_direct_message_requires_delegation_rights(platform):
    analyst = platform.org.agent("agt_fin_analyst")
    with pytest.raises(DeliveryError):
        platform.bus.send(analyst, to_agent_id="agt_platform_eng",
                          channel=ChannelKind.DIRECT_TOOL, body="hi")


def test_enterprise_channel_delivery(platform):
    sre = platform.org.agent("agt_sre")
    msg = platform.bus.send(sre, channel=ChannelKind.SLACK,
                            channel_address="#incidents", subject="SEV2", body="db latency")
    assert msg.payload["delivery"]["status"] == "recorded"
    assert platform.bus.channel_history("#incidents")[0].subject == "SEV2"


def test_ask_human_pauses_session(platform):
    r = platform.runtime.run("agt_cfo", "Post the accrual journal.")
    tools = platform.runtime._escalation_tools(
        platform.org.agent("agt_cfo"), r.session_id
    )
    out = tools["ask_human"]("Approve the $2M accrual?")
    assert out["status"] == "waiting_human"
    assert platform.sessions.get(r.session_id).state is SessionState.WAITING_HUMAN
    resumed = platform.runtime.resume(r.session_id, "Approved.")
    assert resumed.state is SessionState.COMPLETED


def test_catalog_search_and_install(platform):
    entries = platform.catalog.search("sql")
    assert entries and all("sql" in (e.name + e.summary + " ".join(e.tags)).lower()
                           for e in entries)
    skill_entry = platform.catalog.entry_for("skill", "skl_sql_authoring")
    platform.catalog.install(skill_entry.id, "agt_platform_eng")
    assert "skl_sql_authoring" in platform.org.agent("agt_platform_eng").skill_ids


def test_protected_listing_hidden_from_other_groups(platform):
    finance_view = platform.catalog.search("board", viewer_groups=["finance"])
    other_view = platform.catalog.search("board", viewer_groups=["engineering"])
    assert any(e.name == "board-reporting" for e in finance_view)
    assert not any(e.name == "board-reporting" for e in other_view)


def test_protected_install_blocked_for_outsiders(platform):
    entry = platform.catalog.entry_for("skill", "skl_board_reporting")
    with pytest.raises(PermissionError):
        platform.catalog.install(entry.id, "agt_platform_eng")


def test_plugin_install_adds_tools(platform):
    entry = platform.catalog.entry_for("plugin", "plg_jira")
    platform.catalog.install(entry.id, "agt_fin_analyst")
    agent = platform.org.agent("agt_fin_analyst")
    assert any(t.name == "jira_create_issue" for t in agent.harness.tools)


def test_metrics_and_alerts(platform):
    platform.runtime.run("agt_sre", "Check error budget.")
    m = platform.obs.metrics()
    assert m["agents"] >= 7 and m["sessions"]["total"] >= 1
    platform.obs.raise_alert("test_alert", "synthetic")
    assert any(a.title == "test_alert" for a in platform.obs.alerts())
