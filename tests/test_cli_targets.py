"""`orgagents targets` lists every target, including ones whose `describe()`
gives what they emit rather than a summary (it used to stop with
KeyError: 'summary' after the first)."""
from orgagents.cli import main
from orgagents.compiler import register_builtin_targets


def test_targets_lists_every_registered_target(capsys):
    assert main(["targets"]) == 0
    out = capsys.readouterr().out
    ids = [d["id"] for d in register_builtin_targets().describe_all()]
    assert len(ids) > 1
    listed = [line.split()[0] for line in out.splitlines()
              if line and not line.startswith(" ")]
    assert listed == ids
