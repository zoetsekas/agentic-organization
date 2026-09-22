"""The two review surfaces are reachable from the command line.

Both were built as libraries by agents that could not touch `cli.py`. A
capability nobody can invoke is not delivered, so these tests exist to keep the
wiring — not the libraries, which have their own tests — honest.
"""
import json

import pytest
import yaml

from orgagents.cli import main

SPEC = "examples/acme/acme.system.yaml"
BINDING = "examples/acme/acme.binding.yaml"


@pytest.fixture
def widened(tmp_path) -> str:
    """The same system with one extra permission on a role."""
    doc = yaml.safe_load(open(SPEC).read())
    for role in doc["roles"]:
        if "analyst" in role["id"]:
            role.setdefault("permissions", []).append(
                {"action": "write", "resource_kind": "workflow", "resource": "*"}
            )
            break
    path = tmp_path / "after.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return str(path)


# -- spec diff -------------------------------------------------------------


def test_an_identical_pair_reports_no_difference(capsys):
    assert main(["spec", "diff", SPEC, SPEC, "--binding", BINDING]) == 0
    assert "No differences" in capsys.readouterr().out


def test_a_widened_permission_is_reported_and_leads_the_report(capsys, widened):
    assert main(["spec", "diff", SPEC, widened, "--binding", BINDING]) == 0
    out = capsys.readouterr().out
    assert "SECURITY-RELEVANT" in out
    assert "write:workflow:*" in out
    # The finding must come before the routine changes, or nobody reads it.
    assert out.index("SECURITY-RELEVANT") < out.index("change(s)")


def test_fail_on_turns_a_finding_into_a_non_zero_exit(capsys, widened):
    assert main(["spec", "diff", SPEC, widened, "--binding", BINDING,
                 "--fail-on", "high"]) == 1
    # ...and an identical pair still passes the same gate.
    assert main(["spec", "diff", SPEC, SPEC, "--binding", BINDING,
                 "--fail-on", "high"]) == 0


def test_json_output_is_machine_readable(capsys, widened):
    assert main(["spec", "diff", SPEC, widened, "--binding", BINDING,
                 "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["security_findings"] >= 1


def test_diff_without_a_second_spec_is_refused(capsys):
    assert main(["spec", "diff", SPEC]) == 2
    assert "two specs" in capsys.readouterr().out


# -- evaluate / gate -------------------------------------------------------


def test_the_gate_reports_without_running_anything(tmp_path, capsys):
    # `gate` answers "what does the evidence say today". A command that
    # silently produced evidence in order to report on it would defeat it.
    code = main(["gate", SPEC, "--db", str(tmp_path / "g.db")])
    out = capsys.readouterr().out
    assert code == 1
    assert "not_evaluated" in out
    assert "no evaluation run has judged this agent" in out


def test_evaluate_runs_the_cases_and_exits_non_zero_while_a_gate_is_unmet(
    tmp_path, capsys
):
    code = main(["evaluate", SPEC, "--db", str(tmp_path / "e.db")])
    out = capsys.readouterr().out
    assert code == 1
    # A checkable case actually ran, and an unverifiable one is named as such
    # rather than quietly counted as a pass.
    assert "refuses_pii_outside_clean_room" in out
    assert "unsupported" in out


def test_an_agent_whose_cases_are_all_unverifiable_is_not_reported_as_failed(
    tmp_path, capsys
):
    main(["evaluate", SPEC, "--db", str(tmp_path / "e.db")])
    out = capsys.readouterr().out
    # The gate block prints "<state> <agent> <reason>"; the case lines above
    # it print an outcome first, so match on the agent in second position of a
    # line that starts with a gate state.
    states = {"passed", "failed", "not_evaluated", "stale"}
    ceo = [line for line in out.splitlines()
           if line.split()[:2][:1] and line.split()[0] in states
           and line.split()[1:2] == ["ceo"]]
    assert ceo, out
    # "We cannot check this" is not "the agent got it wrong".
    assert ceo[0].startswith("not_evaluated")
