"""Typed exchange (ADR-0113 §4–6): YAML and JSON in which every element names
its UML type, loaded strictly where a type is present and by position where
it is not."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from orgagents.cli import main
from orgagents.spec import exchange
from orgagents.spec.binding import Binding
from orgagents.spec.issue_codes import catalog
from orgagents.spec.loader import dump_spec, load_binding, load_spec, load_spec_text
from orgagents.spec.model import SystemSpec

ROOT = Path(__file__).resolve().parents[1]
SPECS = sorted((ROOT / "examples").glob("*/*.system.yaml"))
BINDINGS = sorted((ROOT / "examples").glob("*/*.binding.yaml"))
SCHEMA = ROOT / "docs" / "api" / "typed-exchange.schema.json"


def _load(path: Path):
    return load_binding(path) if path.name.endswith(".binding.yaml") \
        else load_spec(path)


@pytest.mark.parametrize("path", SPECS + BINDINGS, ids=lambda p: p.name)
@pytest.mark.parametrize("fmt", exchange.FORMATS)
def test_every_example_round_trips_typed(path, fmt):
    obj = _load(path)
    typed = exchange.dump(obj, fmt)
    again = exchange.load(typed)
    assert type(again) is type(obj)
    assert exchange.dump(again, fmt) == typed
    # Exporting twice writes the same file.
    assert exchange.dump(obj, fmt) == typed


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.name)
def test_untyped_export_is_the_spec_format_as_before(path):
    spec = load_spec(path)
    assert exchange.dump(spec, "yaml", typed=False) == dump_spec(spec)
    # ... and the typed one is that document with types added, nothing else.
    typed = yaml.safe_load(exchange.dump(spec))
    assert exchange.strip_types(typed, SystemSpec) == \
        yaml.safe_load(dump_spec(spec))


def _objects(node, cls, where=""):
    """(where, node, class) for every object at a declared position."""
    if not isinstance(node, dict):
        return
    yield where, node, cls
    for key, value, sub in exchange._children(node, cls):
        yield from _objects(value, sub, f"{where}.{key}")


@pytest.mark.parametrize("path", SPECS + BINDINGS, ids=lambda p: p.name)
def test_every_element_and_datatype_value_names_its_uml_type(path):
    obj = _load(path)
    doc = yaml.safe_load(exchange.dump(obj))
    cls = type(obj)
    assert doc["type"] == exchange.root_type(cls)
    assert doc["profiles"] == exchange.profile_versions(cls)
    seen = 0
    for where, node, sub in _objects(doc, cls):
        if where:
            assert node.get("type") == exchange.uml_type_of(sub), where
            seen += 1
    assert seen > 5


def test_types_are_profile_qualified_uml_names():
    doc = yaml.safe_load(exchange.dump(load_spec(ROOT / "examples" / "ayc" /
                                                 "ayc.system.yaml")))
    org = doc["organization"]
    assert org["type"] == "Organisation::Organization"
    assert org["members"][0]["type"] == "Organisation::Agent"
    assert org["members"][0]["mandate"]["type"] == "Authority::Mandate"
    assert org["teams"][0]["type"] == "Organisation::Team"
    assert org["data_classes"][0]["type"] == "Data::DataClass"
    assert doc["metadata"]["type"] == "Core::Metadata"
    budgets = doc.get("budgets") or [{"type": "Assurance::Budget"}]
    assert budgets[0]["type"] == "Assurance::Budget"
    # Enumeration literals and primitives carry no type.
    assert isinstance(org["data_classes"][0].get("scope", "x"), str)


def test_an_untyped_file_loads_as_before():
    text = (ROOT / "examples" / "ayc" / "ayc.system.yaml").read_text(
        encoding="utf-8")
    assert "type: Organisation::" not in text
    assert load_spec_text(text) == load_spec(ROOT / "examples" / "ayc" /
                                             "ayc.system.yaml")


def _typed_ayc() -> dict:
    return yaml.safe_load(exchange.dump(load_spec(
        ROOT / "examples" / "ayc" / "ayc.system.yaml")))


def _issues(doc) -> list[exchange.TypeIssue]:
    with pytest.raises(exchange.TypedDocumentError) as e:
        load_spec_text(yaml.safe_dump(doc))
    return e.value.issues


def test_a_type_that_contradicts_its_position_is_refused_with_its_code():
    doc = _typed_ayc()
    doc["organization"]["teams"][0]["members"][0]["type"] = \
        "Organisation::Team"
    issues = _issues(doc)
    assert [i.code for i in issues] == ["uml_type_mismatch"]
    assert issues[0].number == "OA-3002"
    assert issues[0].where == "organization.teams[0].members[0]"
    assert "Organisation::Agent" in issues[0].message


def test_an_unknown_type_is_refused_with_its_code():
    doc = _typed_ayc()
    doc["organization"]["members"][0]["type"] = "Organisation::Agnet"
    issues = _issues(doc)
    assert [(i.code, i.number) for i in issues] == \
        [("unknown_uml_type", "OA-3003")]


def test_the_root_must_be_a_model():
    doc = _typed_ayc()
    doc["type"] = "Deployment::Binding"
    assert [i.code for i in _issues(doc)] == ["uml_type_mismatch"]


def test_another_major_profile_version_is_refused_and_a_minor_one_loads():
    doc = _typed_ayc()
    doc["profiles"]["Organisation"] = "1.4.2"
    load_spec_text(yaml.safe_dump(doc))
    doc["profiles"]["Organisation"] = "2.0.0"
    doc["profiles"]["Astrology"] = "1.0.0"
    issues = _issues(doc)
    assert {(i.code, i.number) for i in issues} == \
        {("unsupported_profile_version", "OA-3004")}
    assert {i.where for i in issues} == {"profiles.Organisation",
                                         "profiles.Astrology"}


def test_every_wrong_type_is_reported_not_only_the_first():
    doc = _typed_ayc()
    doc["organization"]["members"][0]["type"] = "Organisation::Team"
    doc["organization"]["data_classes"][0]["type"] = "Access::Capability"
    assert len(_issues(doc)) == 2


def test_a_typed_binding_is_checked_too(tmp_path):
    doc = yaml.safe_load(exchange.dump(load_binding(
        ROOT / "examples" / "ayc" / "ayc.binding.yaml")))
    doc["targets"][0]["servers"][0]["type"] = "Deployment::Target"
    path = tmp_path / "x.binding.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(exchange.TypedDocumentError) as e:
        load_binding(path)
    assert e.value.issues[0].where == "targets[0].servers[0]"


def test_the_new_codes_are_catalogued_in_the_loading_section():
    for code, number in (("uml_type_mismatch", 3002),
                         ("unknown_uml_type", 3003),
                         ("unsupported_profile_version", 3004)):
        entry = catalog()[code]
        assert entry["number"] == number
        assert entry["section"] == "loading the design"


def test_the_committed_json_schema_is_the_generated_one():
    assert SCHEMA.read_text(encoding="utf-8") == exchange.schema_text(), \
        "regenerate: orgagents export --schema -o " \
        "docs/api/typed-exchange.schema.json"


@pytest.mark.parametrize("path", [SPECS[0], BINDINGS[0]],
                         ids=lambda p: p.name)
def test_typed_documents_validate_against_the_schema(path):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    doc = json.loads(exchange.dump(_load(path), "json"))
    jsonschema.validate(doc, schema)
    bad = json.loads(exchange.dump(_load(path), "json"))
    if "organization" in bad:
        bad["organization"]["type"] = "Organisation::Team"
    else:
        bad["targets"][0]["type"] = "Deployment::Server"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_cli_export_and_import(tmp_path, capsys):
    src = ROOT / "examples" / "northwind" / "northwind.finance.system.yaml"
    out = tmp_path / "nw.json"
    assert main(["export", str(src), "--format", "json", "-o", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["type"] == "Core::Model"
    back = tmp_path / "nw.system.yaml"
    assert main(["import", str(out), "--untyped", "-o", str(back)]) == 0
    assert back.read_text(encoding="utf-8") == dump_spec(load_spec(src))
    doc["organization"]["type"] = "Organisation::Team"
    out.write_text(json.dumps(doc), encoding="utf-8")
    assert main(["import", str(out)]) == 1
    assert "OA-3002" in capsys.readouterr().err


def test_cli_exports_a_binding_typed(tmp_path):
    out = tmp_path / "b.yaml"
    src = ROOT / "examples" / "ayc" / "ayc.local.binding.yaml"
    assert main(["export", str(src), "-o", str(out)]) == 0
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert doc["type"] == "Deployment::Binding"
    assert doc["targets"][0]["type"] == "Deployment::Target"
    assert Binding.model_validate(exchange.strip_types(doc, Binding)) == \
        load_binding(src)
