"""Existing designs survive the move to PostgreSQL (ADR-0113): `orgagents db
migrate-from-sqlite` copies every workspace, design, revision and audit event
from the SQLite document store, reads the SQLite file only, and runs once."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from orgagents.designer import DesignerService, Member, Principal, SqlRepository, UserRole
from orgagents.designer.models import DesignerSettings
from orgagents.spec import exchange
from orgagents.spec.loader import load_spec
from orgagents.store import Store

ROOT = Path(__file__).resolve().parents[1]
ANA = Principal("ana", "Ana")


def _digest(path: Path) -> str:
    return "".join(hashlib.sha256(Path(str(path) + s).read_bytes()).hexdigest()
                   for s in ("", "-wal") if Path(str(path) + s).exists())


@pytest.fixture()
def sqlite_designer(tmp_path):
    """A SQLite designer store with what real ones hold: a model with
    history, a design in the pre-ADR-0101 layout, one carrying a key the
    model no longer reads, a draft, workspaces, members and audit."""
    db = tmp_path / "designer.db"
    svc = DesignerService(SqlRepository(Store(db)),
                          DesignerSettings(persistence="relational"))
    ws = svc.create_workspace(ANA, "Alpha")
    svc.add_member(ANA, ws.id, Member(user_id="bob", role=UserRole.EDITOR))
    ayc = exchange.canonical(load_spec(ROOT / "examples" / "ayc" /
                                       "ayc.system.yaml"))
    first = svc.create_system(ANA, workspace_id=ws.id, name="AYC", spec=ayc)
    changed = dict(ayc, metadata=dict(ayc["metadata"], description="v2"))
    svc.save_system(ANA, first.id, spec=changed, base_version=1,
                    message="second")
    old = {"metadata": {"name": "old"},
           "organization": {"id": "root", "name": "Old"},
           "capabilities": [{"id": "read_ledger"}]}
    svc.create_system(ANA, workspace_id=ws.id, name="Old layout", spec=old)
    legacy = exchange.canonical(load_spec(ROOT / "examples" / "northwind" /
                                          "northwind.finance.system.yaml"))
    legacy["organization"]["channels"][0]["purpose"] = "kept"
    svc.create_system(ANA, workspace_id=ws.id, name="Legacy key",
                      spec=legacy)
    svc.create_system(ANA, workspace_id=ws.id, name="Draft",
                      spec={"metadata": {"name": "d"},
                            "organization": {"members": "not a list"}})
    return db, svc, ws


def test_every_design_and_record_moves_and_reads_back_equal(
        sqlite_designer, postgres_url):
    from orgagents.designer.repository import PostgresRepository
    from orgagents.persistence.sqlite_import import migrate_from_sqlite
    db, svc, ws = sqlite_designer
    before = _digest(db)
    repo = PostgresRepository.from_url(postgres_url)
    try:
        report = migrate_from_sqlite(db, repo.engine, once=True)
        assert report.ok, report.summary()
        assert _digest(db) == before, "the SQLite file was written"
        by_name = {s.name: s for s in report.systems}
        assert by_name["AYC"].versions == [1, 2]
        assert by_name["AYC"].modelled == 2
        assert by_name["Old layout"].relaid == 1
        assert by_name["Legacy key"].drafts == 1
        assert by_name["Draft"].drafts == 1

        source = svc.repository
        for record in source.list_systems():
            moved = repo.get_system(record.id)
            assert moved is not None and moved.version == record.version
            assert (moved.name, moved.workspace_id, moved.created_by) == \
                (record.name, record.workspace_id, record.created_by)
            assert moved.layout == record.layout
            if record.name in ("Legacy key", "Draft"):
                assert moved.spec == record.spec      # kept whole
            else:
                assert load_spec_from(moved.spec) == load_spec_from(
                    record.spec)
            assert [r.version for r in repo.revisions(record.id)] == \
                [r.version for r in source.revisions(record.id)]
        assert [w.name for w in repo.list_workspaces()] == ["Alpha"]
        assert {m.user_id for m in repo.get_workspace(ws.id).members} >= \
            {"ana", "bob"}
        assert len(repo.audit_events()) == len(source.audit_events())

        again = migrate_from_sqlite(db, repo.engine, once=True)
        assert again.skipped
        twice = migrate_from_sqlite(db, repo.engine)
        assert all(s.skipped for s in twice.systems)
    finally:
        repo.engine.dispose()


def load_spec_from(data):
    from orgagents.spec.model import SystemSpec
    return SystemSpec.model_validate(data)


def test_the_cli_reports_what_it_moved(sqlite_designer, postgres_url,
                                       capsys):
    from orgagents.cli import main
    db, _, _ = sqlite_designer
    assert main(["db", "migrate-from-sqlite", str(db), "--once",
                 "--database-url", postgres_url]) == 0
    out = capsys.readouterr().out
    assert "AYC" in out and "read back equal" in out
    assert main(["db", "migrate-from-sqlite", str(db), "--once",
                 "--database-url", postgres_url]) == 0
    assert "imported before" in capsys.readouterr().out
