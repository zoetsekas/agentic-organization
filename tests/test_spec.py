"""System Spec: neutrality, structure, least privilege (ADR-0004, 0006, 0008, 0009)."""
import enum
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from orgagents.spec import load_spec, validate_spec
from orgagents.spec import model as spec_model
from orgagents.spec.loader import SpecVersionError, load_spec_text
from orgagents.spec.model import NetworkPosture, SystemSpec

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "acme.system.yaml"

# Names that would make the spec an implementation decision (ADR-0004).
FORBIDDEN = {
    "aws", "gcp", "google", "azure", "amazon", "microsoft", "terraform",
    "kubernetes", "docker", "compose", "postgres", "mysql", "sqlite", "redis",
    # "teams" is deliberately absent: it is a legitimate domain word here
    # (Team, Team.teams) as well as a product name.
    "kafka", "slack", "msteams", "langchain", "langgraph", "deepagents", "openai",
    "anthropic", "claude", "gpt", "bedrock", "vertex", "lambda", "s3",
}


def _schema_vocabulary(model: type[BaseModel], seen=None) -> set[str]:
    """Every field name, Literal value and enum member reachable from a model."""
    seen = seen if seen is not None else set()
    words: set[str] = set()
    if model in seen:
        return words
    seen.add(model)
    for name, field in model.model_fields.items():
        words.add(name)
        stack = [field.annotation]
        while stack:
            annotation = stack.pop()
            origin = typing.get_origin(annotation)
            if origin is typing.Literal:
                words |= {str(a).lower() for a in typing.get_args(annotation)}
                continue
            if origin is not None:
                stack.extend(typing.get_args(annotation))
                continue
            if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
                words |= {str(m.value).lower() for m in annotation}
            elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
                words |= _schema_vocabulary(annotation, seen)
    return words


def test_spec_schema_names_no_vendor_or_framework():
    """ADR-0004: neutrality is a failing test, not a convention."""
    vocabulary = _schema_vocabulary(SystemSpec)
    leaked = {w for w in vocabulary if w.lower() in FORBIDDEN}
    assert not leaked, f"implementation names leaked into the spec schema: {leaked}"


def test_binding_is_where_vendors_are_allowed():
    from orgagents.spec.binding import Binding

    assert "provider" in _schema_vocabulary(Binding)


def test_example_spec_has_no_errors():
    findings = validate_spec(load_spec(EXAMPLE))
    assert [str(f) for f in findings if f.severity == "error"] == []


def test_example_spec_is_four_levels_deep():
    spec = load_spec(EXAMPLE)
    depths = {t.id: len(t.id) for t in spec.teams()}
    assert {"acme", "finance", "technology", "platform", "revenue"} <= set(depths)
    platform = next(t for t in spec.teams() if t.id == "platform")
    assert platform.leader == "platform_lead"
    assert "platform_lead" in {m.id for m in platform.members}


def test_unsupported_spec_version_is_refused():
    with pytest.raises(SpecVersionError):
        load_spec_text("metadata: {name: x, spec_version: '9.0.0'}")


def _mutate(text: str, old: str, new: str) -> SystemSpec:
    return load_spec_text(EXAMPLE.read_text().replace(old, new, 1))


def test_leader_must_be_a_member():
    spec = _mutate(EXAMPLE.read_text(), "  leader: cfo", "  leader: ceo")
    codes = {f.code for f in validate_spec(spec)}
    assert "leader_not_member" in codes


def test_child_leader_may_not_be_listed_in_the_parent():
    """ADR-0006 v1.1.0: parent participation is implicit, not a second membership."""
    spec = load_spec(EXAMPLE)
    technology = next(t for t in spec.teams() if t.id == "technology")
    platform = next(t for t in spec.teams() if t.id == "platform")
    lead = next(m for m in platform.members if m.id == "platform_lead")
    technology.members.append(lead)
    codes = {f.code for f in validate_spec(spec)}
    assert "child_leader_double_listed" in codes
    assert "multiple_home_teams" in codes


def test_environment_override_may_not_widen():
    spec = load_spec(EXAMPLE)
    agent = spec.agent("reconciler")
    agent.environments[0].network = NetworkPosture.OPEN
    agent.environments[0].egress_allowlist = ["exfiltration.example"]
    errors = [f for f in validate_spec(spec) if f.severity == "error"]
    assert any(f.code == "environment_widened" for f in errors)


def test_narrowing_is_applied_and_egress_dropped_on_isolated_class():
    spec = load_spec(EXAMPLE)
    isolated = spec.environment("isolated_review")
    narrowed = isolated.narrow(spec.agent("reconciler").environments[0])
    assert narrowed.network is NetworkPosture.NONE
    assert narrowed.egress_allowlist == []


def test_wildcard_grants_are_fatal_in_production():
    text = EXAMPLE.read_text().replace(
        "  environment: development", "  environment: production"
    )
    findings = validate_spec(load_spec_text(text))
    wildcards = [f for f in findings if f.code == "wildcard_resource"]
    assert wildcards and all(f.severity == "error" for f in wildcards)


def test_unknown_references_are_errors():
    spec = load_spec(EXAMPLE)
    spec.agent("analyst").capabilities.append("nonexistent_capability")
    assert any(f.code == "unknown_capability" for f in validate_spec(spec))


def test_protected_data_class_requires_groups():
    spec = load_spec(EXAMPLE)
    spec.data_class("finance_internal").groups = []
    assert any(f.code == "protected_without_groups" for f in validate_spec(spec))


def test_data_placement_is_enforced():
    spec = load_spec(EXAMPLE)
    spec.environment("build").mounts.append("customer_pii")
    assert any(f.code == "placement_violation" for f in validate_spec(spec))
