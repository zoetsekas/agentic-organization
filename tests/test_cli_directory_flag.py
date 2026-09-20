"""`spec validate --directory` actually runs the reconciliation.

The gap this closes was not that reconciliation was wrong — it was that
nothing called it, so a departed owner went unreported however good the
check was (WS-016 M4, ADR-0044).
"""
import json

from orgagents.cli import main

EXAMPLE = "examples/acme.system.yaml"


def _directory(tmp_path, contact: str, status: str) -> str:
    path = tmp_path / "people.json"
    path.write_text(json.dumps(
        {"people": [{"contact": contact, "status": status,
                     "display_name": "Priya Raman"}]}
    ))
    return str(path)


def test_without_the_flag_nothing_is_reconciled(capsys):
    assert main(["spec", "validate", EXAMPLE]) == 0
    assert "departed" not in capsys.readouterr().out


def test_a_departed_owner_is_reported_with_the_flag(tmp_path, capsys):
    path = _directory(tmp_path, "priya@acme.example", "departed")
    assert main(["spec", "validate", EXAMPLE, "--directory", path]) == 0
    out = capsys.readouterr().out
    assert "departed_owner" in out
    assert "priya@acme.example" in out


def test_an_active_person_produces_no_finding(tmp_path, capsys):
    path = _directory(tmp_path, "priya@acme.example", "active")
    assert main(["spec", "validate", EXAMPLE, "--directory", path]) == 0
    out = capsys.readouterr().out
    assert "departed_owner" not in out
    # Everyone else is simply unknown to a directory holding one person; that
    # is a warning, never an error, and never a departure.
    assert "human_unknown_to_directory" in out
