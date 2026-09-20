"""Seed a worked example: a mid-size company's agentic org.

Run with ``python -m orgagents.seed`` or ``orgagents seed``. It builds the
org units, agents with human counterparts, harnesses with MCP mounts and
relational grants, sandboxes, skills, plugins, a demo warehouse, and publishes
everything to the catalog.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .models import (
    Agent,
    AgentKind,
    ChannelKind,
    DataGrant,
    Harness,
    HumanCounterpart,
    MCPServerRef,
    ModelSpec,
    OrgUnit,
    Plugin,
    RelationalGrant,
    Runtime,
    SandboxSpec,
    Skill,
    ToolBinding,
    Visibility,
)
from .platform import Platform
from .models import WorkflowRef
from .store import PLUGINS, SKILLS, WORKFLOWS

DEMO_WAREHOUSE = "demo_warehouse.db"


def build_demo_warehouse(path: str = DEMO_WAREHOUSE) -> str:
    """Create a small relational warehouse the finance agents can query."""
    p = Path(path)
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY, name TEXT, region TEXT, tax_id TEXT);
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY, customer_id INTEGER, amount_usd REAL,
            issued_on TEXT, status TEXT);
        CREATE TABLE IF NOT EXISTS headcount (
            id INTEGER PRIMARY KEY, department TEXT, employees INTEGER, quarter TEXT);
        """
    )
    if not conn.execute("SELECT 1 FROM customers LIMIT 1").fetchone():
        conn.executemany(
            "INSERT INTO customers(name,region,tax_id) VALUES(?,?,?)",
            [("Northwind", "EMEA", "GB123"), ("Contoso", "AMER", "US987"),
             ("Fabrikam", "APAC", "SG555")],
        )
        conn.executemany(
            "INSERT INTO invoices(customer_id,amount_usd,issued_on,status) VALUES(?,?,?,?)",
            [(1, 125000.0, "2026-01-15", "paid"), (1, 48000.0, "2026-04-02", "open"),
             (2, 310000.0, "2026-02-20", "paid"), (3, 76500.0, "2026-03-11", "overdue")],
        )
        conn.executemany(
            "INSERT INTO headcount(department,employees,quarter) VALUES(?,?,?)",
            [("Engineering", 142, "2026Q1"), ("Finance", 23, "2026Q1"),
             ("Sales", 67, "2026Q1")],
        )
        conn.commit()
    conn.close()
    return str(p)


def seed(db_path: str = "orgagents.db", base_url: str = "http://localhost:8000") -> Platform:
    platform = Platform(db_path, base_url=base_url)
    org, store = platform.org, platform.store
    warehouse = build_demo_warehouse()
    # The harness resolves DSNs from secret references; wire the demo one up.
    os.environ.setdefault("WAREHOUSE_DSN", str(Path(warehouse).resolve()))

    # -- org units ---------------------------------------------------------
    company = org.add_unit(OrgUnit(id="org_company", name="Acme Corp", kind="company",
                                   groups=["all-hands"]))
    tech = org.add_unit(OrgUnit(id="org_tech", name="Technology", kind="division",
                                parent_id=company.id, groups=["engineering"]))
    finance = org.add_unit(OrgUnit(id="org_finance", name="Finance", kind="division",
                                   parent_id=company.id, groups=["finance", "confidential"]))
    revenue = org.add_unit(OrgUnit(id="org_revenue", name="Revenue", kind="division",
                                   parent_id=company.id, groups=["revenue"]))
    platform_team = org.add_unit(OrgUnit(id="org_platform", name="Platform Engineering",
                                         kind="team", parent_id=tech.id,
                                         groups=["engineering"]))

    # -- shared building blocks -------------------------------------------
    public_rw = DataGrant(visibility=Visibility.PUBLIC, can_read=True, can_write=True)
    private_rw = DataGrant(visibility=Visibility.PRIVATE, can_read=True, can_write=True)

    def harness(
        prompt: str,
        *,
        groups: list[str] = (),
        runtime: Runtime = Runtime.ECHO,
        grants: list[RelationalGrant] = (),
        mcp: list[MCPServerRef] = (),
        model: str = "claude-opus-5",
    ) -> Harness:
        data_grants = [private_rw, public_rw]
        if groups:
            data_grants.append(
                DataGrant(
                    visibility=Visibility.PROTECTED,
                    groups=list(groups),
                    can_read=True,
                    can_write=True,
                )
            )
        return Harness(
            runtime=runtime,
            model=ModelSpec(model=model, subagent_model="claude-haiku-4-5-20251001"),
            system_prompt=prompt,
            data_grants=data_grants,
            relational_grants=list(grants),
            mcp_servers=list(mcp),
        )

    knowledge_mcp = MCPServerRef(name="knowledge", transport="http",
                                 url="https://mcp.corp.internal/knowledge",
                                 allowed_tools=["search", "fetch"], read_only=True)

    # -- skills and plugins ------------------------------------------------
    skills = [
        Skill(id="skl_board_reporting", name="board-reporting",
              description="Assemble a board-ready narrative from financial results.",
              instructions="Lead with the variance against plan, then drivers, then asks.",
              triggers=["board deck", "quarterly review"]),
        Skill(id="skl_sql_authoring", name="sql-authoring",
              description="Write safe, index-aware SQL against the warehouse.",
              instructions="Always filter by period, never SELECT *, cite the tables used.",
              triggers=["query", "report", "metric"]),
        Skill(id="skl_incident_response", name="incident-response",
              description="Run the incident bridge: triage, comms, postmortem.",
              instructions="Declare severity first; page the on-call human for SEV1/SEV2.",
              triggers=["incident", "outage", "sev"]),
    ]
    store.put_many(SKILLS, skills)

    plugins = [
        Plugin(id="plg_jira", name="jira", description="Issue tracking tools.",
               provides_tools=[
                   ToolBinding(name="jira_create_issue", source="plugin", ref="jira",
                               requires_approval=False),
                   ToolBinding(name="jira_transition", source="plugin", ref="jira"),
               ],
               requires_scopes=["jira:write"]),
        Plugin(id="plg_erp", name="erp-close", description="Period-close automation.",
               provides_tools=[ToolBinding(name="erp_post_journal", source="plugin",
                                           ref="erp", requires_approval=True)],
               provides_skills=["skl_board_reporting"], requires_scopes=["erp:post"]),
    ]
    store.put_many(PLUGINS, plugins)

    # -- agents ------------------------------------------------------------
    ceo = org.add_agent(Agent(
        id="agt_ceo", name="ceo-agent", title="Chief Executive Officer",
        kind=AgentKind.EXECUTIVE, org_unit_id=company.id,
        description="Sets priorities, routes company-wide requests, owns escalations.",
        human=HumanCounterpart(user_id="u_ceo", display_name="Dana Whitfield",
                               email="dana@acme.example", role_title="CEO",
                               approval_required_for=["erp_post_journal", "send_message"],
                               notify_channels=[ChannelKind.EMAIL, ChannelKind.SLACK]),
        harness=harness("You run Acme Corp's agent org. Delegate; do not execute.",
                        groups=["all-hands"], mcp=[knowledge_mcp]),
        workflow_ids=["wfl_delegate_review", "wfl_cross_team_request"],
        channels=[ChannelKind.DIRECT_TOOL, ChannelKind.SLACK, ChannelKind.EMAIL,
                  ChannelKind.INTERNAL_BUS],
        sandbox=SandboxSpec(template_id="sbx_minimal_reasoning"),
        tags=["executive", "routing"],
    ))

    cfo = org.add_agent(Agent(
        id="agt_cfo", name="cfo-agent", title="Chief Financial Officer",
        kind=AgentKind.EXECUTIVE, org_unit_id=finance.id, manager_agent_id=ceo.id,
        description="Owns financial reporting, budget and the close calendar.",
        human=HumanCounterpart(user_id="u_cfo", display_name="Priya Raman",
                               email="priya@acme.example", role_title="CFO",
                               approval_required_for=["erp_post_journal"]),
        harness=harness("You own Acme's financial reporting.",
                        groups=["finance", "confidential"],
                        grants=[RelationalGrant(
                            connection_name="warehouse", engine="sqlite",
                            dsn_secret_ref="WAREHOUSE_DSN",
                            tables=["customers", "invoices", "headcount"],
                            allowed_statements=["select"], row_limit=500,
                            masked_columns=["tax_id"])]),
        skill_ids=["skl_board_reporting"], plugin_ids=["plg_erp"],
        workflow_ids=["wfl_delegate_review", "wfl_data_request"],
        sandbox=SandboxSpec(template_id="sbx_document_processing"),
        channels=[ChannelKind.DIRECT_TOOL, ChannelKind.EMAIL, ChannelKind.INTERNAL_BUS],
        tags=["finance", "reporting"],
    ))

    org.add_agent(Agent(
        id="agt_fin_analyst", name="financial-analyst-agent", title="Financial Analyst",
        kind=AgentKind.INDIVIDUAL, org_unit_id=finance.id, manager_agent_id=cfo.id,
        description="Answers questions from the warehouse and drafts variance analysis.",
        human=HumanCounterpart(user_id="u_analyst", display_name="Tom Becker",
                               email="tom@acme.example", role_title="Senior Analyst"),
        harness=harness("You answer financial questions from the warehouse, with sources.",
                        groups=["finance"],
                        grants=[RelationalGrant(
                            connection_name="warehouse", engine="sqlite",
                            dsn_secret_ref="WAREHOUSE_DSN",
                            tables=["customers", "invoices", "headcount"],
                            allowed_statements=["select"], row_limit=1000,
                            masked_columns=["tax_id"])]),
        skill_ids=["skl_sql_authoring"], workflow_ids=["wfl_data_request"],
        sandbox=SandboxSpec(template_id="sbx_data_analysis"),
        tags=["finance", "analytics"],
    ))

    cto = org.add_agent(Agent(
        id="agt_cto", name="cto-agent", title="Chief Technology Officer",
        kind=AgentKind.EXECUTIVE, org_unit_id=tech.id, manager_agent_id=ceo.id,
        description="Owns engineering delivery and platform reliability.",
        human=HumanCounterpart(user_id="u_cto", display_name="Iris Nakamura",
                               email="iris@acme.example", role_title="CTO"),
        harness=harness("You run Acme engineering. Delegate to platform and app teams.",
                        groups=["engineering"], mcp=[knowledge_mcp]),
        workflow_ids=["wfl_delegate_review"],
        sandbox=SandboxSpec(template_id="sbx_minimal_reasoning"),
        tags=["engineering", "leadership"],
    ))

    org.add_agent(Agent(
        id="agt_platform_eng", name="platform-engineer-agent", title="Platform Engineer",
        kind=AgentKind.INDIVIDUAL, org_unit_id=platform_team.id, manager_agent_id=cto.id,
        description="Implements changes, opens pull requests, runs the test suite.",
        human=HumanCounterpart(user_id="u_eng", display_name="Samir Haddad",
                               email="samir@acme.example", role_title="Staff Engineer",
                               approval_required_for=["sandbox_exec"]),
        harness=harness("You implement and ship platform changes.", groups=["engineering"],
                        mcp=[MCPServerRef(name="github", transport="stdio",
                                          command="github-mcp-server",
                                          allowed_tools=["create_pull_request",
                                                         "get_file_contents"],
                                          read_only=False)]),
        plugin_ids=["plg_jira"], skill_ids=["skl_incident_response"],
        sandbox=SandboxSpec(template_id="sbx_software_engineering"),
        tags=["engineering", "delivery"],
    ))

    org.add_agent(Agent(
        id="agt_sre", name="sre-agent", title="Site Reliability Engineer",
        kind=AgentKind.SERVICE, org_unit_id=platform_team.id, manager_agent_id=cto.id,
        description="Shared service: any agent may call it for incident triage.",
        human=HumanCounterpart(user_id="u_sre", display_name="Lena Fischer",
                               email="lena@acme.example", role_title="SRE Lead"),
        harness=harness("You triage incidents and page humans for SEV1/SEV2.",
                        groups=["engineering"]),
        skill_ids=["skl_incident_response"],
        sandbox=SandboxSpec(template_id="sbx_integration_runner"),
        channels=[ChannelKind.DIRECT_TOOL, ChannelKind.SLACK, ChannelKind.INTERNAL_BUS],
        tags=["reliability", "shared-service"],
    ))

    cro = org.add_agent(Agent(
        id="agt_cro", name="cro-agent", title="Chief Revenue Officer",
        kind=AgentKind.EXECUTIVE, org_unit_id=revenue.id, manager_agent_id=ceo.id,
        description="Owns pipeline, forecasting and customer expansion.",
        human=HumanCounterpart(user_id="u_cro", display_name="Marco Oliveira",
                               email="marco@acme.example", role_title="CRO"),
        harness=harness("You own revenue. Coordinate with finance on forecasts.",
                        groups=["revenue"]),
        workflow_ids=["wfl_cross_team_request"],
        sandbox=SandboxSpec(template_id="sbx_minimal_reasoning"),
        tags=["revenue"],
    ))

    # Lateral link: revenue and finance talk without routing through the CEO.
    for a_id, peer_id in ((cro.id, cfo.id), (cfo.id, cro.id)):
        a = org.agent(a_id)
        if a and peer_id not in a.peer_agent_ids:
            a.peer_agent_ids.append(peer_id)
            org.add_agent(a)

    # -- catalog listings --------------------------------------------------
    for agent_id, tags in (
        ("agt_fin_analyst", ["finance", "sql", "reporting"]),
        ("agt_sre", ["reliability", "incident", "shared-service"]),
        ("agt_platform_eng", ["engineering", "github"]),
    ):
        platform.catalog.publish("agent", agent_id, owner="Acme Platform Team", tags=tags)
    for skill in skills:
        platform.catalog.publish("skill", skill.id, owner="Acme", tags=skill.triggers)
    for plugin in plugins:
        platform.catalog.publish("plugin", plugin.id, owner="Acme IT")
    for template in platform.sandboxes.templates():
        platform.catalog.publish("sandbox_template", template.id, owner="Acme Security")
    for wf in platform.store.list(WORKFLOWS, WorkflowRef):
        platform.catalog.publish("workflow", wf.id, owner="Acme Platform Team")

    platform.catalog.publish(
        "skill", "skl_board_reporting", owner="Acme Finance",
        visibility=Visibility.PROTECTED, groups=["finance"],
        tags=["board deck", "quarterly review"],
    )

    print(f"seeded {store.count('agents')} agents, "
          f"{store.count('catalog')} catalog entries, warehouse at {warehouse}")
    return platform


if __name__ == "__main__":  # pragma: no cover
    seed()
