"""JSON Schema export for the System Spec, for editors we do not own.

WS-002 M5: the spec is meant to be authored in more places than our own UI —
an IDE, a form builder, a review tool. Those consume JSON Schema, not pydantic
models, so the schema is generated from the model rather than hand-written:
a hand-written copy drifts, and a drifted schema is worse than none.

Pydantic emits draft 2020-12. The tidy-up here is what a third-party editor
needs on top of that: a stable `$id` it can key on, a declared `$schema`, a
title and description, and the spec version the schema describes.
"""
from __future__ import annotations

import json
from typing import Any

from .model import SPEC_VERSION, SystemSpec

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID = f"https://orgagents.dev/schema/system-spec/{SPEC_VERSION}.json"


def system_spec_schema() -> dict[str, Any]:
    """The System Spec as a self-describing JSON Schema document."""
    schema = SystemSpec.model_json_schema(mode="validation")
    schema["$schema"] = SCHEMA_DIALECT
    schema["$id"] = SCHEMA_ID
    schema["title"] = "OrgAgents System Spec"
    schema["description"] = (
        "The implementation-neutral definition of an agentic system (ADR-0004). "
        f"Describes spec_version {SPEC_VERSION}; older documents are brought "
        "forward by `orgagents spec migrate`."
    )
    schema["x-spec-version"] = SPEC_VERSION
    # Editors offer far better completion when they know the version field is
    # constrained, and a spec claiming another version is not this schema's.
    metadata = schema.get("$defs", {}).get("Metadata", {})
    version_field = metadata.get("properties", {}).get("spec_version")
    if isinstance(version_field, dict):
        version_field.setdefault("default", SPEC_VERSION)
        version_field["examples"] = [SPEC_VERSION]
    return schema


def system_spec_schema_json(indent: int = 2) -> str:
    return json.dumps(system_spec_schema(), indent=indent, sort_keys=False) + "\n"
