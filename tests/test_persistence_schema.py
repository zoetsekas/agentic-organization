"""The relational schema is generated from the UML profiles (ADR-0113 §1):
every stereotype has a table, every relationship a key, a link table or a
part table, every enumeration a reader table; the committed migrations are
current and reproduce the generated DDL; and the designer's optimistic
versions hold against PostgreSQL."""
from __future__ import annotations

import threading

import pytest

from orgagents.metamodel import PROFILES, RelKind, Shape
from orgagents.metamodel.completeness import _owner_class, _walk_field, py_class
from orgagents.persistence import migrations, relational
from orgagents.persistence.relational import (Flat, Links, Parts, Ref, ident,
                                              plan, structure)


def _all_fields(fields):
    for f in fields:
        yield f
        if isinstance(f, Flat):
            yield from _all_fields(f.children)


def _field_map(cls, name):
    """The mapping of one field of one class, wherever the class sits: its
    own table, or flattened in a host's."""
    p = plan()
    for t in p.classes.values():
        if t.cls is cls:
            for f in t.fields:
                if f.attr == name:
                    return f
    for t in p.classes.values():
        for f in _all_fields(t.fields):
            if isinstance(f, Flat) and f.cls is cls:
                for c in f.children:
                    if c.attr == name:
                        return c
    return None


def test_every_stereotype_with_a_model_has_a_table_in_its_profiles_schema():
    p = plan()
    for prof in PROFILES:
        for st in prof.stereotypes:
            if not st.model:
                continue
            t = p.by_kind.get(st.kind)
            assert t is not None, st.kind
            assert t.schema == prof.name.lower()
            assert t.uml_type == f"{prof.name}::{st.name}"


def test_every_relationship_has_a_key_a_link_table_or_a_part_table():
    for prof in PROFILES:
        for r in prof.relationships:
            if not r.field or r.kind is RelKind.GENERALIZATION:
                continue
            if r.shape is Shape.RECORD:
                ac = py_class(r.association_class)
                for end in ("source", "target"):
                    assert isinstance(_field_map(ac, end), Ref), (r, end)
                continue
            cls, step = _walk_field(_owner_class(r.owner_kind()),
                                    r.field)[-1]
            f = _field_map(cls, step)
            if r.shape is Shape.PART:
                assert isinstance(f, Parts), r
            elif r.shape is Shape.REF:
                assert isinstance(f, Ref), r
            elif r.shape is Shape.REFS:
                assert isinstance(f, Links), r
            elif r.shape is Shape.REF_OBJECTS:
                assert isinstance(f, Parts), r
                key = _field_map(py_class(r.association_class), r.key)
                assert isinstance(key, Ref), r


def test_every_reference_has_a_foreign_key_and_every_part_its_owners():
    s = structure()
    for t in plan().classes.values():
        fks = s["tables"][t.q]["foreign_keys"]
        cols = {c for fk in fks.values() for c in fk["columns"]}
        for f in _all_fields(t.fields):
            if isinstance(f, Ref):
                assert f.row_col in cols, (t.q, f.col)
        if t.owned and len({o.q for o in t.owners}) == 1:
            assert "owner_row" in cols, t.q
        assert "revision_id" in cols, t.q
    for link in plan().links.values():
        cols = {c for fk in s["tables"][link.q]["foreign_keys"].values()
                for c in fk["columns"]}
        assert {"target_row", "revision_id"} <= cols, link.q


def test_every_enumeration_has_a_reader_table_and_a_check():
    s = structure()
    for prof in PROFILES:
        for e in prof.enumerations:
            q = f"{prof.name.lower()}.{ident('enum_' + relational.snake(e.name))}"
            assert [r[0] for r in s["tables"][q]["rows"]] == list(e.literals)
    capability = s["tables"]["access.capability"]
    assert any('"action" IN' in c for c in capability["checks"].values())


def test_identifiers_fit_postgresql():
    s = structure()
    for q, t in s["tables"].items():
        for name in [*q.split("."), *t["columns"], *t["foreign_keys"],
                     *t["checks"], *t["indexes"]]:
            assert len(name.encode()) <= 63, name


def test_the_ddl_is_deterministic():
    assert relational.ddl() == relational.ddl(structure())


def test_the_committed_migrations_are_current():
    assert migrations.committed(), "no migrations committed"
    assert migrations.pending_statements() == [], (
        "the profiles changed: run `orgagents db generate-migration <name>` "
        "and commit the new file")


# -- against PostgreSQL ----------------------------------------------------------

def _catalogue(url):
    """What a database holds, in a form two databases can be compared by."""
    from sqlalchemy import text

    from orgagents.persistence.store import engine
    eng = engine(url)
    schemas = tuple(structure()["schemas"])
    with eng.connect() as conn:
        cols = conn.execute(text(
            "SELECT table_schema, table_name, column_name, data_type, "
            "udt_name, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = ANY(:s) "
            "ORDER BY 1, 2, 3"), {"s": list(schemas)}).all()
        cons = conn.execute(text(
            "SELECT n.nspname, c.conname, pg_get_constraintdef(c.oid), "
            "c.condeferrable, c.condeferred FROM pg_constraint c "
            "JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = ANY(:s) ORDER BY 1, 2"),
            {"s": list(schemas)}).all()
        idx = conn.execute(text(
            "SELECT schemaname, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = ANY(:s) ORDER BY 1, 2"),
            {"s": list(schemas)}).all()
        rows = {}
        for q, t in structure()["tables"].items():
            if t.get("rows"):
                schema, name = q.split(".")
                rows[q] = conn.execute(text(
                    f'SELECT * FROM "{schema}"."{name}" ORDER BY 1')).all()
    eng.dispose()
    return cols, cons, idx, rows


def test_the_committed_migrations_reproduce_the_generated_ddl(
        postgres_url, empty_postgres_url):
    """`postgres_url` was built by applying every committed migration;
    `empty_postgres_url` gets the DDL the profiles generate today."""
    import psycopg
    with psycopg.connect(empty_postgres_url, autocommit=True) as c:
        c.execute(relational.ddl())
    assert _catalogue(postgres_url) == _catalogue(empty_postgres_url)


def test_a_generated_migration_takes_the_old_schema_to_the_new(
        empty_postgres_url, postgres_server):
    """Drop a table, a column and an enumeration literal from today's
    structure to make an 'old' one; its DDL plus `diff(old, new)` must be
    today's database."""
    import copy

    import psycopg

    from pg import drop_database, fresh_database
    new = structure()
    old = copy.deepcopy(new)
    del old["tables"]["organisation.agent__peers"]
    del old["tables"]["access.capability"]["columns"]["secret_ref"]
    old["tables"]["core.enum_effect"]["rows"] = [["allow", 0]]
    old["tables"]["access.capability"]["checks"] = {}
    with psycopg.connect(empty_postgres_url, autocommit=True) as c:
        c.execute(relational.ddl(old))
        c.execute("\n".join(relational.diff(old, new)))
    reference = fresh_database(postgres_server)
    try:
        with psycopg.connect(reference, autocommit=True) as c:
            c.execute(relational.ddl(new))
        assert _catalogue(empty_postgres_url) == _catalogue(reference)
    finally:
        drop_database(postgres_server, reference)


def test_migrating_twice_applies_nothing_and_an_edited_migration_is_refused(
        postgres_url, monkeypatch, tmp_path):
    from orgagents.persistence.store import engine, migrate
    eng = engine(postgres_url)
    assert migrate(eng) == []
    edited = tmp_path / migrations.committed()[0].name
    edited.write_text("-- changed\n", encoding="utf-8")
    monkeypatch.setattr(migrations, "committed", lambda: [edited])
    with pytest.raises(RuntimeError, match="forward-only"):
        migrate(eng)
    eng.dispose()


# -- optimistic versions (ADR-0033) against PostgreSQL ---------------------------

@pytest.fixture()
def repo(postgres_url):
    from orgagents.designer.repository import PostgresRepository
    r = PostgresRepository.from_url(postgres_url)
    yield r
    r.engine.dispose()


def _record(name="ayc"):
    from pathlib import Path

    from orgagents.designer.models import SystemRecord
    from orgagents.spec import exchange
    from orgagents.spec.loader import load_spec
    spec = load_spec(Path(__file__).resolve().parents[1] / "examples" /
                     "ayc" / "ayc.system.yaml")
    return SystemRecord(name=name, spec=exchange.canonical(spec))


def test_two_writers_from_one_version_one_wins_one_is_stale(repo):
    from orgagents.designer.repository import VersionConflict
    first = repo.save_system(_record(), expected_version=None, author="ana")
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def write(who: str) -> None:
        mine = repo.get_system(first.id)
        mine.description = who
        barrier.wait()
        try:
            repo.save_system(mine, expected_version=1, author=who)
            outcomes.append("saved")
        except VersionConflict as e:
            assert e.expected == 1 and e.actual == 2
            outcomes.append("stale")

    threads = [threading.Thread(target=write, args=(w,)) for w in
               ("ana", "bob")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["saved", "stale"]
    assert repo.get_system(first.id).version == 2
    assert [r.version for r in repo.revisions(first.id)] == [2, 1]


def test_a_new_design_cannot_be_created_twice(repo):
    from orgagents.designer.repository import VersionConflict
    rec = _record()
    repo.save_system(rec.model_copy(), expected_version=None, author="ana")
    with pytest.raises(VersionConflict):
        repo.save_system(rec.model_copy(), expected_version=None,
                         author="bob")


def test_each_revision_is_a_snapshot_read_back_from_its_rows(repo):
    from sqlalchemy import text
    saved = repo.save_system(_record(), expected_version=None, author="ana")
    changed = repo.get_system(saved.id)
    changed.spec["organization"]["members"][0]["description"] = "v2"
    repo.save_system(changed, expected_version=1, author="ana")
    v1, v2 = repo.revision(saved.id, 1), repo.revision(saved.id, 2)
    assert v1.spec["organization"]["members"][0]["description"] != "v2"
    assert v2.spec["organization"]["members"][0]["description"] == "v2"
    with repo.engine.connect() as conn:
        per_rev = conn.execute(text(
            "SELECT r.version, count(*) FROM organisation.agent a "
            "JOIN core.revision r USING (revision_id) "
            "WHERE r.model_id = :m GROUP BY r.version ORDER BY 1"),
            {"m": saved.id}).all()
    assert len(per_rev) == 2 and per_rev[0][1] == per_rev[1][1] > 0


def test_old_revisions_are_pruned_with_their_rows(repo, monkeypatch):
    from sqlalchemy import text
    monkeypatch.setattr(type(repo), "max_revisions", 2)
    saved = repo.save_system(_record(), expected_version=None, author="ana")
    for v in (1, 2, 3):
        rec = repo.get_system(saved.id)
        rec.description = f"v{v + 1}"
        repo.save_system(rec, expected_version=v, author="ana")
    assert [r.version for r in repo.revisions(saved.id)] == [4, 3]
    with repo.engine.connect() as conn:
        versions = conn.execute(text(
            "SELECT DISTINCT model_version FROM organisation.agent "
            "WHERE model_id = :m ORDER BY 1"), {"m": saved.id}).scalars().all()
    assert versions == [3, 4]


def test_deleting_a_design_removes_every_row(repo):
    from sqlalchemy import text
    saved = repo.save_system(_record(), expected_version=None, author="ana")
    assert repo.delete_system(saved.id)
    with repo.engine.connect() as conn:
        left = conn.execute(text(
            "SELECT count(*) FROM core.element WHERE model_id = :m"),
            {"m": saved.id}).scalar()
    assert left == 0 and repo.get_system(saved.id) is None
