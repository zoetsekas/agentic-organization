"""The questions ADR-0113 says the relational schema must answer (Q1–Q5),
run as SQL against PostgreSQL and checked against the same answer computed
from the spec in Python. The SQL is the ADR's own, word for word."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from orgagents.persistence import queries
from orgagents.spec import exchange
from orgagents.spec.loader import load_binding, load_spec

ROOT = Path(__file__).resolve().parents[1]
ADR = next((ROOT / "docs" / "decisions").glob("ADR-0113-*.md")).read_text(
    encoding="utf-8")
AYC = ROOT / "examples" / "ayc"


def _norm(sql: str) -> str:
    return " ".join(sql.replace(";", " ").split())


def test_the_sql_is_the_adrs_word_for_word():
    block = ADR.split("```sql", 1)[1].split("```", 1)[0]
    parts = re.split(r"^-- (Q\d):.*$", block, flags=re.M)
    in_adr = {parts[i].lower(): _norm(parts[i + 1])
              for i in range(1, len(parts), 2)}
    assert in_adr == {k: _norm(v) for k, v in queries.QUERIES.items()}


# -- against the database ------------------------------------------------------

@pytest.fixture()
def stored(postgres_url):
    """AYC (spec and binding) and Northwind, each saved as a design."""
    from orgagents.persistence.store import engine, write_revision
    eng = engine(postgres_url)
    designs = {
        "ayc": (load_spec(AYC / "ayc.system.yaml"),
                load_binding(AYC / "ayc.binding.yaml")),
        "northwind": (load_spec(ROOT / "examples" / "northwind" /
                                "northwind.finance.system.yaml"),
                      load_binding(ROOT / "examples" / "northwind" /
                                   "northwind.binding.yaml")),
    }
    revs = {}
    with eng.begin() as conn:
        for model_id, (spec, binding) in designs.items():
            # An older revision first, so "the head" is a real choice.
            write_revision(conn, model_id=model_id, version=1,
                           spec=exchange.canonical(spec))
            revs[model_id] = write_revision(
                conn, model_id=model_id, version=2,
                spec=exchange.canonical(spec),
                binding=exchange.canonical(binding))["revision_id"]
    yield eng, designs, revs
    eng.dispose()


def _run(eng, name, **kw):
    with eng.connect() as conn:
        return queries.run(conn, name, **kw)


def _mandated(spec):
    """(uml type, id, decisions) for everything that can hold a mandate."""
    def teams(t):
        yield t
        for sub in t.teams:
            yield from teams(sub)
    org = spec.organization
    for t in teams(org):
        kind = "Organisation::Organization" if t is org \
            else "Organisation::Team"
        yield kind, t.id, t.mandate
    for a in org.all_agents():
        yield "Organisation::Agent", a.id, a.mandate
    for p in org.people:
        yield "Organisation::Person", p.id, p.mandate
    for m in org.missions:
        yield "Organisation::Mission", m.id, m.mandate


def test_q1_agents_that_rely_on_a_data_class(stored):
    eng, designs, revs = stored
    spec = designs["ayc"][0]
    for dc in spec.organization.data_classes:
        expected = sorted({a.id for a in spec.organization.all_agents()
                           if any(d.data_class == dc.id
                                  for d in a.data_dependencies)})
        got = [r[0] for r in _run(eng, "q1", rev=revs["ayc"], x=dc.id)]
        assert got == expected, dc.id
    # By design rather than revision: the head of `ayc`.
    assert _run(eng, "q1", model="ayc", x="inventory_data")


def test_q2_who_may_decide(stored):
    eng, designs, revs = stored
    spec = designs["ayc"][0]
    asked = 0
    for d in spec.organization.decisions:
        expected = sorted((kind, i) for kind, i, m in _mandated(spec)
                          if m is not None and d.id in m.decisions)
        got = _run(eng, "q2", rev=revs["ayc"], y=d.id)
        assert got == expected, d.id
        asked += bool(expected)
    assert asked >= 5


def test_q3_capabilities_on_a_server(stored):
    eng, designs, revs = stored
    binding = designs["ayc"][1]
    servers = {s.id for t in binding.targets for s in t.servers}
    assert servers
    for z in servers:
        expected = sorted((cb.capability, t.target) for t in binding.targets
                          for cb in t.capabilities if cb.server == z)
        assert _run(eng, "q3", rev=revs["ayc"], z=z) == expected, z


def test_q4_every_element_of_a_type_across_designs(stored):
    eng, designs, _ = stored
    for uml_type, collect in (
            ("Organisation::Agent",
             lambda s: [(a.id, a.name) for a in s.organization.all_agents()]),
            ("Data::DataClass",
             lambda s: [(d.id, None) for d in s.organization.data_classes]),
            ("Deployment::Server", None)):
        got = _run(eng, "q4", t=uml_type)
        if collect is None:
            expected = sorted((m, s.id, None) for m, (_, b) in designs.items()
                              for t in b.targets for s in t.servers)
        else:
            expected = sorted((m, i, n or None) for m, (s, _) in
                              designs.items() for i, n in collect(s))
        assert got == expected, uml_type
    # Only heads: two revisions of each design are stored.
    assert len({r for r in _run(eng, "q4", t="Core::System")}) == 2


def test_q5_agents_holding_a_capability_directly_or_through_a_role(stored):
    eng, designs, revs = stored
    spec = designs["ayc"][0]
    roles = {r.id: set(r.capabilities)
             for r in spec.organization.role_definitions}
    for c in spec.organization.capabilities:
        expected = sorted({
            a.id for a in spec.organization.all_agents()
            if c.id in a.capabilities
            or any(c.id in roles.get(ra.role, ()) for ra in a.roles)})
        got = [r[0] for r in _run(eng, "q5", rev=revs["ayc"], c=c.id)]
        assert got == expected, c.id


def test_cli_runs_a_query(stored, postgres_url, capsys):
    from orgagents.cli import main
    assert main(["db", "query", "q1", "model=ayc", "x=inventory_data",
                 "--database-url", postgres_url]) == 0
    assert capsys.readouterr().out.strip()
