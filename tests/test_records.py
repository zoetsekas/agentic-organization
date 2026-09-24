"""The governance rules from ADR-0001, checked against the real record set."""
from pathlib import Path

import pytest

from orgagents import records

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def rs() -> records.RecordSet:
    return records.load(ROOT)


def test_real_record_set_is_valid(rs):
    assert records.validate(rs) == []


def test_both_record_kinds_are_present(rs):
    assert len(rs.of_kind("adr")) >= 15
    assert len(rs.of_kind("ws")) >= 8


def test_every_accepted_decision_has_a_workstream(rs):
    for adr in rs.of_kind("adr"):
        if adr.status == "Accepted":
            assert adr.refs("workstreams"), f"{adr.id} is accepted with no workstream"


def test_supersession_is_symmetric_and_status_consistent(rs):
    superseded = [r for r in rs.of_kind("adr") if r.status == "Superseded"]
    assert superseded, "the set should demonstrate at least one supersession"
    for record in superseded:
        replacements = record.refs("superseded_by")
        assert replacements
        for replacement in replacements:
            assert record.id in rs.get(replacement).refs("supersedes")


def test_trade_offs_are_stated_on_every_record(rs):
    for record in rs.records.values():
        body = record.body.lower()
        start = body.index("## disadvantages")
        section = body[start + len("## disadvantages"):]
        section = section.split("\n## ")[0].strip()
        assert len(section) > 40, f"{record.id} has a token disadvantages section"


def test_changelog_matches_version(rs):
    for record in rs.records.values():
        assert record.changelog()[0][0] == record.version


def test_validator_catches_a_broken_record(tmp_path):
    decisions = tmp_path / records.DECISIONS_DIR
    decisions.mkdir(parents=True)
    (decisions / "ADR-0001-broken.md").write_text(
        "---\nid: ADR-0001\ntitle: Broken\nstatus: Invented\nversion: one\n"
        "date: 2026-01-01\ndeciders: [x]\nrelated: [ADR-9999]\n---\n\n## Context\nnope\n"
    , encoding="utf-8")
    errors = records.validate(records.load(tmp_path))
    codes = " ".join(errors)
    assert "status 'Invented'" in codes
    assert "not semver" in codes
    assert "unknown record 'ADR-9999'" in codes
    assert "missing required section" in codes
    assert "changelog has no entries" in codes


def test_scaffold_allocates_the_next_id(tmp_path, rs):
    for folder in (records.DECISIONS_DIR, records.WORKSTREAMS_DIR):
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / "_template.md").write_text(
            (ROOT / folder / "_template.md").read_text(encoding="utf-8")
        , encoding="utf-8")
    path = records.scaffold(rs, "adr", "A brand new decision", tmp_path)
    expected = records.next_id(rs, "adr")
    assert expected in path
    assert expected in Path(path).read_text(encoding="utf-8")


def test_indexes_are_regenerable_and_current(rs):
    generated = records.render_decision_index(rs)
    on_disk = (ROOT / records.DECISIONS_DIR / "index.md").read_text(encoding="utf-8")
    assert generated.strip() == on_disk.strip(), "run `orgagents records index`"


def test_graph_edges_resolve(rs):
    graph = records.graph(rs)
    ids = {n["id"] for n in graph["nodes"]}
    assert all(e["to"] in ids and e["from"] in ids for e in graph["edges"])


def test_no_test_imports_the_suite_as_a_package():
    """`from tests.x import y` passes locally and fails in CI.

    `python -m pytest` puts the working directory on `sys.path`; the bare
    `pytest` CI runs does not. Five tests imported a shared fixture that way,
    passed every local run, and failed every CI run — which is the worst shape
    a test failure can have, because the signal arrives where nobody is
    looking at it. Shared fixtures live in `tests/spec_fixtures.py` and are
    imported as a plain module, which works under both.
    """
    import ast

    offenders = []
    for path in sorted((ROOT / "tests").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(
                    ".")[0] == "tests":
                offenders.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.Import) and any(
                    a.name.split(".")[0] == "tests" for a in node.names):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        f"these import the suite as a package and will fail under a bare "
        f"pytest: {offenders}"
    )
