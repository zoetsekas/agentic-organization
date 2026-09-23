"""The transformation from the model to the physical representation
(ADR-0104): specified once, checked both ways, on every example and after
every scenario."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from orgagents.compiler.base import register_builtin_targets
from orgagents.compiler.engine import compile_system
from orgagents.compiler.ir import build_ir
from orgagents.metamodel import PROFILE, RelKind, Shape, specialisations
from orgagents.metamodel.scenarios import SCENARIOS, base, play
from orgagents.metamodel.transformation import (ELEMENTS, Mode, carried_by,
                                                describe, image_class, trace,
                                                trace_targets)
from orgagents.spec.loader import load_spec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*/*.system.yaml"))


def test_every_stereotype_has_a_transformation_rule():
    ruled = {r.kind for r in ELEMENTS}
    for st in PROFILE.stereotypes:
        if not st.abstract:
            assert st.kind in ruled, f"«{st.name}» has no rule"


def test_every_built_kind_names_a_real_ir_class():
    for r in ELEMENTS:
        if r.mode in (Mode.RESOLVED, Mode.DESIGN_ONLY):
            continue
        assert image_class(r.kind) is not None, r.kind


@pytest.mark.parametrize(
    "rel", [r for r in PROFILE.relationships
            if r.field and r.kind is not RelKind.GENERALIZATION
            and r.shape is not Shape.RECORD and r.stereotype != "owns"],
    ids=lambda r: f"{r.source}.{r.field}")
def test_every_relationship_is_carried_by_a_real_ir_field(rel):
    """A relationship the IR cannot carry is a relationship compilation
    loses — the defect ADR-0104 found twice. Either the image has the field,
    or the mapping says how it is resolved instead."""
    for kind in specialisations(rel.source):
        cls = image_class(kind)
        if cls is None:
            continue            # resolved or design-only: not an IR element
        field = carried_by(kind, rel.field)
        if field is None:
            continue
        head = field.split(".")[0]
        assert head in cls.model_fields, \
            f"{kind}.{rel.field}: {cls.__name__} has no '{head}'"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_every_example_traces_completely(path):
    spec = load_spec(path)
    t = trace(spec, build_ir(spec))
    assert t.complete, "\n".join(t.gaps)
    assert t.elements and t.links


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_every_agent_and_workflow_reaches_every_target(path):
    spec = load_spec(path)
    for target in register_builtin_targets().ids():
        result = compile_system(spec, targets=[target], write=False,
                                out_dir=tempfile.mkdtemp())[0]
        assert not trace_targets(result.ir, result.files, target)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_the_transformation_holds_after_every_scenario(scenario):
    """Whatever the model's operations leave, the IR carries."""
    spec = play(scenario).spec
    t = trace(spec, build_ir(spec))
    assert t.complete, "\n".join(t.gaps)


# -- what the trace found -----------------------------------------------------

def test_unit_links_reach_the_ir():
    ir = build_ir(base())
    assert [(l.source, l.target) for l in ir.unit_links] == [("risk", "ops")]


def test_a_subagents_output_contract_reaches_the_ir():
    spec = load_spec(ROOT / "examples" / "acme" / "acme.system.yaml")
    subs = [s for a in build_ir(spec).agents for s in a.subagents
            if s.output_contract is not None]
    assert subs, "sub-agents declare output contracts in acme"


def test_a_teams_role_grants_reach_its_members_capabilities():
    spec = load_spec(ROOT / "examples" / "acme" / "acme.system.yaml")
    cfo = next(a for a in build_ir(spec).agents if a.id == "cfo")
    assert "warehouse_query" in {c.id for c in cfo.capabilities}
    assert "warehouse_query" in spec.effective_capabilities("cfo")


def test_the_specification_is_current():
    assert (ROOT / "docs" / "metamodel" / "transformation.md").read_text() \
        == describe()
