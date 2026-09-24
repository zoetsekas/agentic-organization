"""The model in PostgreSQL (ADR-0113): every example round-trips through the
tables to identical typed YAML and JSON, the committed migrations reproduce
the generated DDL, and revisions are snapshots.

Runs against a real PostgreSQL started in Docker (`tests/pg.py`); skipped,
with the reason, where there is none.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orgagents.spec import exchange
from orgagents.spec.binding import Binding
from orgagents.spec.loader import load_binding, load_spec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
SPECS = sorted(EXAMPLES.glob("*/*.system.yaml"))
BINDINGS = sorted(EXAMPLES.glob("*/*.binding.yaml"))


def _binding_for(spec_path: Path):
    """The binding an example ships beside its spec, if any."""
    for b in BINDINGS:
        if b.parent == spec_path.parent and ".local." not in b.name:
            return b
    return None


@pytest.fixture()
def db(postgres_url):
    from orgagents.persistence.store import engine
    eng = engine(postgres_url)
    yield eng
    eng.dispose()


def _store(db, model_id, spec, binding=None, version=1):
    from orgagents.persistence.store import write_revision
    with db.begin() as conn:
        return write_revision(conn, model_id=model_id, version=version,
                              spec=spec, binding=binding)


def _read(db, revision_id):
    from orgagents.persistence.store import read_revisions
    with db.connect() as conn:
        return read_revisions(conn, [revision_id])[revision_id]


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.parent.name)
def test_every_example_round_trips_through_the_tables(db, path):
    spec = load_spec(path)
    binding_path = _binding_for(path)
    binding = load_binding(binding_path) if binding_path else None
    written = _store(db, path.stem, exchange.canonical(spec),
                     exchange.canonical(binding) if binding else None)
    assert written["spec_state"] == "model", written["spec_reason"]
    back = _read(db, written["revision_id"])
    from orgagents.spec.model import SystemSpec
    again = SystemSpec.model_validate(back.spec)
    for fmt in exchange.FORMATS:
        assert exchange.dump(again, fmt) == exchange.dump(spec, fmt)
    if binding is not None:
        assert written["binding_state"] == "binding"
        b = Binding.model_validate(back.binding)
        for fmt in exchange.FORMATS:
            assert exchange.dump(b, fmt) == exchange.dump(binding, fmt)


@pytest.mark.parametrize("path", BINDINGS, ids=lambda p: p.name)
def test_every_example_binding_round_trips_on_its_own(db, path):
    binding = load_binding(path)
    written = _store(db, path.stem, None, exchange.canonical(binding))
    assert written["binding_state"] == "binding"
    back = _read(db, written["revision_id"])
    assert exchange.dump(Binding.model_validate(back.binding)) == \
        exchange.dump(binding)


def test_a_typed_file_goes_in_and_comes_out_typed_and_identical(db):
    path = EXAMPLES / "ayc" / "ayc.system.yaml"
    typed = exchange.dump(load_spec(path), "json")
    loaded = exchange.load(typed)
    written = _store(db, "ayc", exchange.canonical(loaded))
    from orgagents.spec.model import SystemSpec
    back = SystemSpec.model_validate(_read(db, written["revision_id"]).spec)
    assert exchange.dump(back, "json") == typed


def test_a_draft_the_model_refuses_is_kept_whole(db):
    draft = {"metadata": {"name": "half"}, "organization": {"teams": "x"}}
    written = _store(db, "half", draft)
    assert written["spec_state"] == "draft"
    assert _read(db, written["revision_id"]).spec == draft


def test_keys_the_model_would_drop_keep_the_revision_a_draft(db):
    spec = exchange.canonical(load_spec(EXAMPLES / "sentinel" /
                                        "sentinel.secops.system.yaml"))
    spec["organization"]["not_a_field"] = {"kept": True}
    written = _store(db, "sentinel", spec)
    assert written["spec_state"] == "draft"
    assert "not_a_field" in written["spec_reason"]
    assert _read(db, written["revision_id"]).spec == spec


def test_one_targets_binding_round_trips_as_one_target(db):
    binding = load_binding(EXAMPLES / "ayc" / "ayc.local.binding.yaml")
    target = exchange.canonical(binding.targets[0])
    written = _store(db, "ayc", None, target)
    assert written["binding_state"] == "target"
    assert _read(db, written["revision_id"]).binding == target


def test_revisions_are_snapshots_and_pruning_one_removes_its_rows(db):
    from sqlalchemy import text
    spec = exchange.canonical(load_spec(EXAMPLES / "ayc" / "ayc.system.yaml"))
    first = _store(db, "ayc", spec, version=1)
    changed = exchange.canonical(load_spec(EXAMPLES / "ayc" /
                                           "ayc.system.yaml"))
    changed["organization"]["members"][0]["description"] = "changed"
    second = _store(db, "ayc", changed, version=2)
    a, b = _read(db, first["revision_id"]), _read(db, second["revision_id"])
    assert a.spec == spec and b.spec == changed
    with db.begin() as conn:
        count = conn.execute(text(
            "SELECT count(*) FROM organisation.agent WHERE revision_id = :r"),
            {"r": first["revision_id"]}).scalar()
        assert count > 0
        conn.execute(text("DELETE FROM core.revision WHERE revision_id = :r"),
                     {"r": first["revision_id"]})
        for q in ("organisation.agent", "organisation.worker", "core.element",
                  "authority.mandate__decisions"):
            left = conn.execute(text(
                f"SELECT count(*) FROM {q} WHERE revision_id = :r"),
                {"r": first["revision_id"]}).scalar()
            assert left == 0, q
    assert _read(db, second["revision_id"]).spec == changed


def test_the_database_holds_its_own_constraints(db):
    """A CHECK against an enumeration's literals, and a key from a part to
    its owner, refuse a row our mapper would never write."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    spec = exchange.canonical(load_spec(EXAMPLES / "ayc" / "ayc.system.yaml"))
    rev = _store(db, "ayc", spec)["revision_id"]
    with pytest.raises(IntegrityError):
        with db.begin() as conn:
            conn.execute(text(
                "UPDATE access.capability SET action = 'teleport' "
                "WHERE revision_id = :r"), {"r": rev})
    with pytest.raises(IntegrityError):
        with db.begin() as conn:
            conn.execute(text(
                "UPDATE data.data_dependency SET owner_row = -1 "
                "WHERE revision_id = :r"), {"r": rev})


def test_references_resolve_to_rows_and_scoped_ids_to_the_nearest(db):
    """A capability binding's server is the one in its own target, though
    three targets each declare a server of that id."""
    from sqlalchemy import text
    spec = exchange.canonical(load_spec(EXAMPLES / "ayc" / "ayc.system.yaml"))
    binding = exchange.canonical(load_binding(EXAMPLES / "ayc" /
                                              "ayc.binding.yaml"))
    rev = _store(db, "ayc", spec, binding)["revision_id"]
    with db.connect() as conn:
        wrong = conn.execute(text(
            "SELECT count(*) FROM deployment.capability_binding cb "
            "JOIN deployment.server s ON s.row_id = cb.server__row "
            "WHERE cb.revision_id = :r AND s.owner_row <> cb.owner_row"),
            {"r": rev}).scalar()
        dangling = conn.execute(text(
            "SELECT count(*) FROM deployment.capability_binding "
            "WHERE revision_id = :r AND server IS NOT NULL "
            "AND server__row IS NULL"), {"r": rev}).scalar()
    assert wrong == 0 and dangling == 0
