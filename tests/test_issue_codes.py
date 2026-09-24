"""The issue catalog covers every code the validator emits, and numbers stay put."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.metamodel import constraints
from orgagents.spec.issue_codes import catalog, display_number, lookup
from orgagents.spec.validation import SECTIONS, declared_codes, rules

SRC = Path(__file__).resolve().parents[1] / "src" / "orgagents"


def _literal_codes(path: Path) -> set[str]:
    """Every literal code passed to `err`, `warn` (bare or as `ctx.err`) or
    `Finding` in one source file."""
    codes: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name in ("err", "warn") and node.args:
            code = node.args[0]
        elif name == "Finding":
            kw = {k.arg: k.value for k in node.keywords}
            code = kw.get("code") or (node.args[1] if len(node.args) > 1 else None)
        else:
            continue
        for branch in ((code.body, code.orelse) if isinstance(code, ast.IfExp)
                       else (code,)):
            if isinstance(branch, ast.Constant) and isinstance(branch.value, str):
                codes.add(branch.value)
    return codes


def _constraint_codes() -> set[str]:
    text = Path(constraints.__file__).read_text(encoding="utf-8")
    names = re.findall(r'Violation\(\s*"([a-z_]+)"', text)
    return {f"model_{n}" for n in names}


#: Rule modules that relay another module's findings, and where those are.
_RELAYED = {
    "orgagents.spec.validation.platform_policy":
        lambda: _literal_codes(SRC / "platform_policy.py"),
    "orgagents.spec.validation.metamodel_constraints": _constraint_codes,
}


def test_each_rule_module_declares_exactly_what_it_emits():
    """The registry's declarations are the source's literal codes, module by
    module: nothing emitted undeclared, nothing declared that cannot fire."""
    by_module: dict[str, set[str]] = {}
    for r in rules():
        by_module.setdefault(r.module, set()).update(r.codes)
    for module, declared in by_module.items():
        if module in _RELAYED:
            emitted = _RELAYED[module]()
        else:
            emitted = _literal_codes(Path(sys.modules[module].__file__))
        assert declared == emitted, (module, declared ^ emitted)


def test_registry_sections_run_in_catalog_order():
    order = [r.order for r in rules()]
    assert order == sorted(order)
    assert {r.section for r in rules()} <= set(SECTIONS)


def test_the_registry_and_the_catalog_agree():
    """Every declared code is catalogued, and every catalogued code is one a
    rule declares — except the loader's, which no rule emits."""
    loader = {c for c, e in catalog().items() if e["section"] == "loading the design"}
    loader_emitted = _literal_codes(SRC / "designer" / "service.py")
    assert not (loader_emitted - set(catalog())), loader_emitted
    assert declared_codes() == set(catalog()) - loader


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
