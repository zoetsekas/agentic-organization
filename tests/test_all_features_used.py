"""The Atlas bank example exercises every top-level spec block.

The request behind this example was to verify that all of the platform's
features are used. The cleanest way to hold that is a test: enumerate every
top-level field of `SystemSpec` and assert the multinational example populates
each. When a new block is added to the spec, this fails until the coverage
example is extended — which is the point.
"""
from __future__ import annotations

import pathlib

import yaml

from orgagents.spec.model import SystemSpec

ROOT = pathlib.Path(__file__).resolve().parents[1]
ATLAS = ROOT / "examples" / "atlas" / "atlas.bank.system.yaml"


def test_every_top_level_block_is_populated():
    doc = yaml.safe_load(ATLAS.read_text())
    blocks = list(SystemSpec.model_fields)
    missing = [b for b in blocks if not doc.get(b)]
    assert not missing, (
        f"the coverage example does not use: {missing}. Either add it to "
        "examples/atlas/atlas.bank.system.yaml or record why it does not belong.")


def test_the_coverage_example_is_the_widest_one():
    """A guard against the coverage example quietly shrinking below the others.

    If some other example ever populates a block Atlas does not, Atlas has
    stopped being the coverage example and this says so.
    """
    blocks = set(SystemSpec.model_fields)
    atlas = {b for b in blocks
             if yaml.safe_load(ATLAS.read_text()).get(b)}
    for other in ROOT.glob("examples/*/*.system.yaml"):
        if other.name == "atlas.bank.system.yaml":
            continue
        doc = yaml.safe_load(other.read_text())
        theirs = {b for b in blocks if doc.get(b)}
        assert theirs <= atlas, (
            f"{other.name} uses blocks Atlas does not: {sorted(theirs - atlas)}")
