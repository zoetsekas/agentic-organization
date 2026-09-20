import pytest

from orgagents.harness.relational import RelationalMCP, SQLPolicyError, statement_class
from orgagents.models import RelationalGrant, SandboxSpec


def test_statement_classification():
    assert statement_class("SELECT 1") == "select"
    assert statement_class("WITH x AS (SELECT 1) SELECT * FROM x") == "select"
    assert statement_class("DROP TABLE t") == "ddl"


def test_grant_blocks_writes_and_masks_columns(platform):
    analyst = platform.org.agent("agt_fin_analyst")
    ok = platform.harness.call(
        analyst, "db_warehouse__query", sql="SELECT name, tax_id FROM customers"
    )
    assert ok.ok and all(r["tax_id"] == "***" for r in ok.value["rows"])

    denied = platform.harness.call(
        analyst, "db_warehouse__query", sql="DELETE FROM customers"
    )
    assert not denied.ok and "not permitted" in denied.error


def test_grant_blocks_tables_outside_scope(tmp_path):
    grant = RelationalGrant(connection_name="c", engine="sqlite", tables=["invoices"])
    db = RelationalMCP(grant, str(tmp_path / "w.db"))
    with pytest.raises(SQLPolicyError):
        db.check("SELECT * FROM salaries")
    with pytest.raises(SQLPolicyError):
        db.check("SELECT 1; DROP TABLE invoices")


def test_sandbox_templates_and_overrides(platform):
    names = {t.name for t in platform.sandboxes.templates()}
    assert {"minimal-reasoning", "data-analysis", "software-engineering",
            "regulated-data-clean-room"} <= names

    spec = SandboxSpec(template_id="sbx_regulated_data", timeout_s=60,
                       egress_extra=["evil.example"])
    resolved = platform.sandboxes.resolve(spec)
    assert resolved.timeout_s == 60           # overrides may only narrow
    assert resolved.egress_allowlist == []    # no egress on a zero-network template

    container = platform.sandboxes.container_spec(spec)
    assert container["network_policy"]["mode"] == "none"
    assert container["security"]["run_as_non_root"] is True


def test_sandbox_execution(platform):
    eng = platform.org.agent("agt_platform_eng")
    eng.human.approval_required_for = []   # approval gate tested separately
    platform.org.add_agent(eng)
    r = platform.harness.call(eng, "sandbox_exec", command="python3 -c \"print(7*6)\"")
    assert r.ok and r.value["stdout"].strip() == "42"


def test_approval_gate_blocks_tool(platform):
    eng = platform.org.agent("agt_platform_eng")
    r = platform.harness.call(eng, "sandbox_exec", command="echo hi")
    assert not r.ok and r.requires_approval


def test_system_prompt_carries_org_context(platform):
    prompt = platform.harness.system_prompt(platform.org.agent("agt_fin_analyst"))
    assert "cfo-agent" in prompt and "Tom Becker" in prompt and "sql-authoring" in prompt
