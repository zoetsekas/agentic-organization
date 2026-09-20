from orgagents.models import Agent, AgentKind, OrgUnit


def test_seeded_hierarchy(platform):
    ceo = platform.org.agent("agt_ceo")
    assert {a.id for a in platform.org.reports(ceo.id)} >= {"agt_cfo", "agt_cto", "agt_cro"}
    chain = platform.org.chain_of_command("agt_fin_analyst")
    assert [a.id for a in chain] == ["agt_fin_analyst", "agt_cfo", "agt_ceo"]


def test_delegation_rules(platform):
    org = platform.org
    assert org.can_delegate("agt_cfo", "agt_fin_analyst")          # direct report
    assert org.can_delegate("agt_ceo", "agt_fin_analyst")          # skip level
    assert org.can_delegate("agt_cro", "agt_cfo")                  # registered peer
    assert org.can_delegate("agt_fin_analyst", "agt_sre")          # shared service
    assert not org.can_delegate("agt_fin_analyst", "agt_cfo")      # cannot delegate up
    assert not org.can_delegate("agt_fin_analyst", "agt_platform_eng")


def test_groups_inherit_from_org_unit(blank):
    unit = blank.org.add_unit(OrgUnit(name="Legal", groups=["legal"]))
    agent = blank.org.add_agent(
        Agent(name="counsel", kind=AgentKind.INDIVIDUAL, org_unit_id=unit.id)
    )
    assert "legal" in agent.groups


def test_tree_shape(platform):
    tree = platform.org.to_tree()
    assert len(tree) == 1 and tree[0]["name"] == "ceo-agent"
    assert any(c["name"] == "cfo-agent" for c in tree[0]["children"])
