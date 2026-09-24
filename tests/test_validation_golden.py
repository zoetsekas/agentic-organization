"""The validator's findings, pinned: same findings, same order.

Captured from the single-function validator before it became a rule registry
(`orgagents.spec.validation`), and held here so that the registry, or any
later move of a rule between modules, cannot change what a design is told or
the order it is told it in (the Issues tab and several tests depend on the
order).

Every shipped example is a case, with and without a production-strict
platform policy, plus a few designs broken on purpose so that error paths are
pinned as well as the clean ones.

`mission_past_its_end_date` depends on today's date, so it is left out of the
comparison. Regenerate after a deliberate change with
`ORGAGENTS_REGEN_GOLDEN=1 pytest tests/test_validation_golden.py`.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
from typing import Any, Callable

import pytest
import yaml

from orgagents.directory import StaticDirectory
from orgagents.platform_policy import PlatformPolicy
from orgagents.spec.loader import load_spec, load_spec_text
from orgagents.spec.validate import validate_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "validation_findings.json"
SYSTEMS = sorted(ROOT.glob("examples/*/*.system.yaml"))
ACME = ROOT / "examples" / "acme" / "acme.system.yaml"
DATE_DEPENDENT = {"mission_past_its_end_date"}


def _strict() -> PlatformPolicy:
    return PlatformPolicy(
        id="house", version="1.0.0", treat_as="production",
        require_declared=["separations", "guardrails", "decisions", "evaluations"],
        forbid_autonomy_over=["release_payment"],
    )


def _acme(mutate: Callable[[dict[str, Any]], None]) -> Any:
    doc = yaml.safe_load(ACME.read_text(encoding="utf-8"))
    mutate(doc["organization"])
    return load_spec_text(yaml.safe_dump(doc, sort_keys=False))


def _broken_structure(org: dict[str, Any]) -> None:
    org["teams"][0]["leader"] = ""
    org["capabilities"].append(copy.deepcopy(org["capabilities"][0]))
    org["members"].append(copy.deepcopy(org["teams"][1]["members"][0]))
    org["members"][0]["peers"] = ["ghost"]
    org["members"][0]["successor"] = org["members"][0]["id"]


def _broken_references(org: dict[str, Any]) -> None:
    org["triggers"][0]["agent"] = "ghost"
    org["triggers"][0]["deliver_to"] = ["nowhere"]
    org["triggers"][0]["cadence"] = {"expression": "not a cron", "timezone": "Mars/Base"}
    org["missions"][0]["members"].append("ghost")
    org["missions"][0]["ends_on"] = "2026-01-01"
    org["channels"][0]["members"] = ["ghost"]
    org["interaction_flows"] = [{"source": "ceo", "target": "ceo"},
                                {"source": "ghost", "target": "ceo"}]
    org["guardrails"] = []


def _broken_workflow(org: dict[str, Any]) -> None:
    graph = org["workflows"][0]["graph"]
    graph["nodes"].append({"id": "orphan", "kind": "tool", "tool": "x"})
    graph["nodes"].append({"id": "fork", "kind": "fork"})
    graph["nodes"].append({"id": "ask", "kind": "human", "person": "nobody"})
    graph["edges"].append({"from": "ghost", "to": "draft"})
    graph["edges"].append({"from": "draft", "to": "nowhere"})
    graph["edges"].append({"from": "execute", "to": "fork"})
    org["workflows"].append({"id": "empty", "graph": {"nodes": []}})


def _departed_owner(spec: Any) -> Any:
    contacts = {h.contact for a in spec.agents() for h in a.humans if h.contact}
    people = {c: {"active": True} for c in contacts}
    owner = next(h.contact for a in spec.agents() for h in a.humans
                 if h.contact and h.is_owner)
    people[owner] = {"active": False}
    people.pop(sorted(contacts - {owner})[0], None)
    return StaticDirectory(people, name="ldap")


def _cases() -> dict[str, Callable[[], list[Any]]]:
    cases: dict[str, Callable[[], list[Any]]] = {}
    for path in SYSTEMS:
        cases[path.stem] = lambda p=path: validate_spec(load_spec(p))
        cases[f"{path.stem}+strict"] = (
            lambda p=path: validate_spec(load_spec(p), platform_policy=_strict()))
    for name, mutate in (("broken_structure", _broken_structure),
                         ("broken_references", _broken_references),
                         ("broken_workflow", _broken_workflow)):
        cases[f"acme+{name}"] = lambda m=mutate: validate_spec(_acme(m))
        cases[f"acme+{name}+strict"] = (
            lambda m=mutate: validate_spec(_acme(m), platform_policy=_strict()))

    def departed() -> list[Any]:
        spec = load_spec(ACME)
        return validate_spec(spec, directory=_departed_owner(spec))

    cases["acme+departed_owner"] = departed
    return cases


CASES = _cases()


def _render(findings: list[Any]) -> list[list[str]]:
    return [[f.severity, f.code, f.where, f.message]
            for f in findings if f.code not in DATE_DEPENDENT]


def _regenerate() -> None:
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    data = {name: _render(run()) for name, run in CASES.items()}
    GOLDEN.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                      encoding="utf-8")


if os.environ.get("ORGAGENTS_REGEN_GOLDEN"):
    _regenerate()


def test_every_case_has_a_golden():
    assert set(json.loads(GOLDEN.read_text(encoding="utf-8"))) == set(CASES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_findings_match_the_golden(name):
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))[name]
    assert _render(CASES[name]()) == expected


def test_the_broken_cases_break():
    """The crafted cases are only worth pinning if they reach error paths."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name in ("acme+broken_structure", "acme+broken_references",
                 "acme+broken_workflow", "acme+departed_owner"):
        assert any(sev == "error" or code.startswith("departed")
                   for sev, code, _, _ in golden[name]), name
