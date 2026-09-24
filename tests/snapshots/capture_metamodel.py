"""Capture the metamodel's derived outputs, to prove a refactor changes none
of them (ADR-0112 step 1). Captured once, before the profile split, from the
repo root:

    PYTHONPATH=src python tests/snapshots/capture_metamodel.py

`test_metamodel_profiles.py` compares against the committed JSON.
"""
import json
from pathlib import Path

from orgagents.metamodel import PROFILE, link_rules


def snapshot() -> dict:
    return {
        "link_rules": link_rules(),
        "stereotypes": [s.kind for s in PROFILE.stereotypes],
        "relationships": [[r.source, r.target, r.kind.value, r.stereotype,
                           r.field] for r in PROFILE.relationships],
    }


if __name__ == "__main__":
    Path(__file__).with_name("metamodel_link_rules.json").write_text(
        json.dumps(snapshot(), indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
