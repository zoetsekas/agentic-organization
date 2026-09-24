"""WS-002 M4/M5: spec_version migration and JSON Schema export."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from orgagents.spec.loader import (
    SpecVersionError,
    dump_spec,
    load_spec_text,
    load_spec_text_with_migration,
)
from orgagents.spec.migrations import CURRENT, MIGRATIONS, migrate
from orgagents.spec.model import SPEC_VERSION
from orgagents.spec.schema import SCHEMA_DIALECT, system_spec_schema

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "acme" / "acme.system.yaml"


LEGACY_1_0 = """
metadata:
  name: legacy
  spec_version: "1.0.0"
organization:
  id: root
  name: Root
  leader: boss
  members:
    - id: boss
      name: Boss
      human: {name: Dana Whitfield, contact: dana@acme.example}
"""


# -- migration chain -------------------------------------------------------


def test_registry_is_an_unbroken_ordered_chain():
    assert MIGRATIONS[-1].to_version == CURRENT == SPEC_VERSION
    for earlier, later in zip(MIGRATIONS, MIGRATIONS[1:]):
        assert earlier.to_version == later.from_version


def test_old_document_migrates_and_loads():
    data = yaml.safe_load(LEGACY_1_0)
    upgraded, changes = migrate(data)

    assert upgraded["metadata"]["spec_version"] == CURRENT
    agent = upgraded["organization"]["members"][0]
    assert "human" not in agent
    assert agent["humans"] == [{"name": "Dana Whitfield",
                                "contact": "dana@acme.example"}]
    assert changes  # a migration that says nothing is not reviewable
    # The input document is left alone, so a caller can still diff it.
    assert data["organization"]["members"][0]["human"]

    spec = load_spec_text(LEGACY_1_0)
    boss = spec.agent("boss")
    assert boss.owner and boss.owner.name == "Dana Whitfield"


def test_change_list_names_the_hop_and_the_agent():
    _, changes = migrate(yaml.safe_load(LEGACY_1_0))
    text = "\n".join(changes)
    assert "1.0.0 → 1.1.0" in text
    assert "1.1.0 → 1.2.0" in text
    assert "boss" in text and "Dana Whitfield" in text
    # The 1.2.0 hop changes no data, and says so rather than staying silent.
    assert "no data change" in text


def test_loader_reports_whether_it_migrated():
    _, changes = load_spec_text_with_migration(LEGACY_1_0)
    assert changes

    _, none = load_spec_text_with_migration(EXAMPLE.read_text(encoding="utf-8"))
    assert none == []


def test_migration_is_idempotent():
    once, first_changes = migrate(yaml.safe_load(LEGACY_1_0))
    twice, second_changes = migrate(once)
    assert twice == once
    assert first_changes and second_changes == []


def test_newer_document_is_refused_never_downgraded():
    with pytest.raises(SpecVersionError):
        load_spec_text("metadata: {name: x, spec_version: '9.0.0'}")
    with pytest.raises(SpecVersionError):
        migrate({"metadata": {"name": "x", "spec_version": "1.9.0"}})
    with pytest.raises(SpecVersionError):
        migrate({"metadata": {"name": "x", "spec_version": "0.9.0"}})


def test_migrated_document_round_trips_through_dump():
    spec = load_spec_text(LEGACY_1_0)
    text = dump_spec(spec)
    assert yaml.safe_load(text)["metadata"]["spec_version"] == CURRENT
    assert load_spec_text(text).agent("boss").owner.name == "Dana Whitfield"


def test_example_still_loads_unchanged():
    spec, changes = load_spec_text_with_migration(EXAMPLE.read_text(encoding="utf-8"))
    assert changes == []
    assert spec.metadata.spec_version == CURRENT


# -- JSON Schema export ----------------------------------------------------


def test_schema_is_self_describing():
    schema = system_spec_schema()
    assert schema["$schema"] == SCHEMA_DIALECT
    assert schema["$id"].endswith(f"{SPEC_VERSION}.json")
    assert schema["title"] and schema["description"]
    assert schema["x-spec-version"] == SPEC_VERSION
    assert schema["type"] == "object"
    assert "metadata" in schema["properties"]
    assert schema["required"] == ["metadata"]
    # Editors resolve nested blocks through $defs; they must all be present.
    refs = json.dumps(schema)
    for name in ("Metadata", "Team", "AgentSpec", "Mission", "ModelPolicy"):
        assert name in schema["$defs"], name
        assert f"#/$defs/{name}" in refs


def test_schema_is_json_serializable():
    # A third-party editor gets bytes, not Python objects.
    text = json.dumps(system_spec_schema())
    assert json.loads(text)["$id"] == system_spec_schema()["$id"]


def test_schema_accepts_the_worked_example():
    schema = system_spec_schema()
    document = json.loads(json.dumps(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))))

    jsonschema = pytest.importorskip(
        "jsonschema", reason="validated structurally instead"
    )
    validator = jsonschema.validators.validator_for(schema)
    validator.check_schema(schema)
    errors = sorted(validator(schema).iter_errors(document), key=str)
    assert not errors, errors[:3]


def test_schema_covers_every_top_level_block_of_the_example():
    """Structural stand-in for validation when `jsonschema` is absent."""
    schema = system_spec_schema()
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    unknown = set(document) - set(schema["properties"])
    assert not unknown, unknown
