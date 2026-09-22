"""Undo, and the one place it has to refuse.

WS-032 named the hard part before this was built: locks mean one editor at a
time on a *node*, not on a design, so an undo stack that crosses somebody
else's merged change would restore a state that was never true for anybody.
Better to have no undo past that point and say so.

Writing the browser check for this found the other half. Coalescing keyed on
elapsed time and a shared default reason merged two *unrelated* actions that
happened within the window, so undoing a rename also removed the component
dropped a moment before it. Coalescing is opt-in now.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def canvas_js() -> str:
    return (ROOT / "web" / "canvas.js").read_text()


def body_of(source: str, name: str) -> str:
    """The text of one function, up to the next top-level closing brace."""
    at = source.index(f"function {name}(")
    return source[at:].split("\n}", 1)[0]


def test_a_merge_throws_the_undo_stack_away(canvas_js):
    """The refusal WS-032 asked for."""
    outcome = body_of(canvas_js, "handleSaveOutcome")
    assert "resetHistory()" in outcome, (
        "a merge must clear the stack: undoing past it would silently delete "
        "the other editor's change")


def test_reopening_a_design_throws_the_undo_stack_away(canvas_js):
    """A fresh read describes a document the old history is not about."""
    assert canvas_js.count("resetHistory()") >= 3   # init, merge, reopen


def test_coalescing_is_opt_in(canvas_js):
    """The defect the loose browser check hid.

    Merging two actions because they were close together is worse than not
    coalescing at all, so a caller has to ask.
    """
    push = body_of(canvas_js, "pushHistory")
    assert "coalesce = false" in push, "coalescing must default to off"
    assert "history.coalescing" in push, (
        "a run has to be continuous: the previous push must itself have been "
        "part of one")


def test_only_the_form_opts_in_and_it_names_the_field(canvas_js):
    """Two fields edited in quick succession stay two steps."""
    opted_in = re.findall(r"markDirty\(([^;]*?), true\)", canvas_js)
    assert len(opted_in) == 1, f"unexpected coalescing callers: {opted_in}"
    assert "field.name" in opted_in[0], (
        "the reason must name the field, or typing in one box then another "
        "would collapse into one step")


def test_undo_restores_the_model_and_the_picture_together(canvas_js):
    """They are one document; restoring half of it is a corrupt state."""
    apply_fn = body_of(canvas_js, "applyHistory")
    assert "canvas.record.spec" in apply_fn
    assert "canvas.record.layout" in apply_fn


def test_an_undo_never_continues_a_coalescing_run(canvas_js):
    for name in ("undo", "redo"):
        assert "history.coalescing = false" in body_of(canvas_js, name), name


def test_the_stack_is_bounded(canvas_js):
    """An unbounded stack of whole-document snapshots is a memory leak."""
    assert "UNDO_LIMIT" in canvas_js
    push = body_of(canvas_js, "pushHistory")
    assert "shift()" in push


def test_scrolling_is_still_not_an_edit(canvas_js):
    """It must not take an undo step any more than it takes a revision."""
    remember = body_of(canvas_js, "rememberViewport")
    assert "markDirty" not in remember
    assert "pushHistory" not in remember
