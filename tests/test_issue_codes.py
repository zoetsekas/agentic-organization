"""The issue catalog covers every code the validator emits, and numbers stay put."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.metamodel import constraints
from orgagents.spec.issue_codes import catalog, display_number, lookup

SRC = Path(__file__).resolve().parents[1] / "src" / "orgagents"


def _emitted_codes() -> set[str]:
    """Every literal code passed to `err`, `warn` or `Finding` in the sources
    that produce findings."""
    codes: set[str] = set()
    for rel in ("spec/validate.py", "platform_policy.py", "designer/service.py"):
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None)
            if name in ("err", "warn") and node.args:
                code = node.args[0]
            elif name == "Finding":
                kw = {k.arg: k.value for k in node.keywords}
                code = kw.get("code") or (node.args[1] if len(node.args) > 1 else None)
            else:
                continue
            if isinstance(code, ast.Constant) and isinstance(code.value, str):
                codes.add(code.value)
    codes |= {"departed_owner", "departed_human", "human_unknown_to_directory"}
    return codes


def _constraint_codes() -> set[str]:
    text = Path(constraints.__file__).read_text(encoding="utf-8")
    names = re.findall(r'Violation\(\s*"([a-z_]+)"', text)
    return {f"model_{n}" for n in names}


def test_every_emitted_code_is_catalogued():
    missing = (_emitted_codes() | _constraint_codes()) - set(catalog())
    assert not missing, f"add these to issue_catalog.yaml: {sorted(missing)}"


def test_numbers_are_unique_and_sit_in_their_section():
    entries = list(catalog().values())
    numbers = [e["number"] for e in entries]
    assert len(numbers) == len(set(numbers))
    by_section: dict[str, set[int]] = {}
    for e in entries:
        by_section.setdefault(e["section"], set()).add(e["number"] // 100)
    assert all(len(blocks) == 1 for blocks in by_section.values()), by_section


def test_every_entry_explains_itself():
    for e in catalog().values():
        for key in ("title", "summary", "explanation", "why"):
            assert str(e.get(key, "")).strip(), (e["code"], key)
        assert e["fix"], e["code"]
        assert len(e["title"]) <= 60, e["code"]


def test_lookup_numbers_and_schema_fallback():
    assert display_number(7) == "OA-0007"
    assert lookup("duplicate_id")["id"] == "OA-0101"
    # pydantic's own error types share the schema entry
    assert lookup("extra_forbidden")["code"] == "schema_error"
    assert lookup(None) is None


def test_api_serves_codes_by_name_and_number(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGAGENTS_DB", str(tmp_path / "d.db"))
    client = TestClient(create_app())
    listed = client.get("/api/designer/issue-codes", headers={"X-User": "ana"})
    assert listed.status_code == 200
    assert len(listed.json()) == len(catalog())
    by_name = client.get("/api/designer/issue-codes/possible_escalation",
                         headers={"X-User": "ana"}).json()
    by_number = client.get(f"/api/designer/issue-codes/{by_name['id']}",
                           headers={"X-User": "ana"}).json()
    assert by_name == by_number
    assert client.get("/api/designer/issue-codes/model_nope",
                      headers={"X-User": "ana"}).status_code == 404
