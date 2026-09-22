"""Regenerating stubs must never clobber an engineer's implementation.

We generate typed tool stubs for the engineer to fill in (ADR-0087). The moment
they start implementing, a re-compile must not overwrite that work. The stub
module is engineer-owned and merged additively (ADR-0089): a re-compile leaves
every existing function exactly as it is and only appends a stub for a tool the
design newly requires.
"""
from __future__ import annotations

import pathlib

import pytest

from orgagents.compiler.base import register_builtin_targets
from orgagents.compiler.engine import _merge_additive, compile_system
from orgagents.spec.loader import load_binding, load_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMPL = '    return {"on_hand": 42}  # engineer implementation'


# -- the merge primitive ---------------------------------------------------

def test_an_implemented_function_is_left_untouched():
    existing = ('"""x"""\n\n\ndef stock_lookup(sku):\n'
                '    """look up."""\n' + IMPL + "\n")
    generated = ('"""x"""\n\n\ndef stock_lookup(sku):\n'
                 '    """look up."""\n    raise NotImplementedError("TODO")\n')
    merged, added = _merge_additive(existing, generated)
    assert added == []
    assert merged == existing            # byte-for-byte unchanged
    assert "engineer implementation" in merged


def test_a_new_tool_is_appended_without_touching_the_old():
    existing = ('"""x"""\n\n\ndef stock_lookup(sku):\n' + IMPL + "\n")
    generated = (existing
                 + '\n\ndef new_report(month):\n'
                 '    raise NotImplementedError("TODO: implement new_report")\n')
    merged, added = _merge_additive(existing, generated)
    assert added == ["new_report"]
    assert "engineer implementation" in merged        # old kept
    assert merged.count("def stock_lookup(") == 1      # not duplicated
    assert "def new_report(" in merged                 # new appended
    assert "TODO: implement new_report" in merged


def test_an_unparseable_file_is_never_corrupted():
    existing = "def half_written(:\n    this is not valid python"
    generated = 'def anything():\n    raise NotImplementedError("TODO")\n'
    merged, added = _merge_additive(existing, generated)
    assert added == []
    assert merged == existing            # left exactly as found


# -- end to end through compile_system -------------------------------------

@pytest.fixture()
def ayc():
    return (load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml"),
            load_binding(ROOT / "examples" / "ayc" / "ayc.binding.yaml"))


def _compile(spec, binding, out):
    register_builtin_targets()
    return compile_system(spec, targets=["langgraph"], out_dir=out,
                          binding=binding)[0]


def test_recompile_preserves_an_implementation(tmp_path, ayc):
    spec, binding = ayc
    out = tmp_path / "gen"
    _compile(spec, binding, out)
    tools = out / "langgraph" / "graphs" / "tools.py"
    original = tools.read_text()
    assert "def stock_lookup(" in original

    # The engineer implements the stub.
    tools.write_text(original.replace(
        'raise NotImplementedError("TODO: implement stock_lookup")',
        'return {"on_hand": 7}  # engineer implementation'))

    # A second compile into the same directory must not clobber it.
    result = _compile(spec, binding, out)
    after = tools.read_text()
    assert "engineer implementation" in after
    assert "TODO: implement stock_lookup" not in after
    assert tools.as_posix().endswith("tools.py")
    # tools.py had no new tools, so it was preserved, not rewritten.
    assert "graphs/tools.py" in result.skipped


def test_backends_and_agent_modules_stay_do_not_edit(tmp_path, ayc):
    """Only tools.py is engineer-owned; the real clients keep the manifest
    guard, so a hand-edit there is caught rather than merged."""
    spec, binding = ayc
    out = tmp_path / "gen"
    _compile(spec, binding, out)
    backends = out / "langgraph" / "graphs" / "_backends.py"
    assert "engineer-owned" not in backends.read_text()
    assert "Do not edit" in backends.read_text()
