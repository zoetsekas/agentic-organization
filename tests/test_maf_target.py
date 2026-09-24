"""A platform target: emitting somebody else's agent definitions.

`local` and `terraform` are infrastructure targets — they deploy this
platform's runtime, which then picks an adapter. This is the first target that
produces files another vendor's runtime loads, which is what the Terraform
analogy actually promises and what nothing here had done.

The interesting half is not what comes across. It is what does not, and
whether the target says so: a target that silently dropped a mandate would be
generating a lie, and a control that reads as enforced and is not is worse
than no control (ADR-0073).
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest
import yaml

from orgagents.compiler.base import register_builtin_targets
from orgagents.compiler.engine import compile_system
from orgagents.spec.loader import load_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def emitted() -> dict[str, str]:
    register_builtin_targets()
    spec = load_spec(ROOT / "examples" / "northwind" / "northwind.finance.system.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(
            spec, targets=["maf"], out_dir=pathlib.Path(tmp)
        )[0]
    return {f.path: f.content for f in result.files}


def test_every_agent_becomes_a_loadable_prompt_file(emitted):
    """The portable core: instructions, model, tools."""
    agents = [p for p in emitted if p.startswith("agents/")]
    assert len(agents) == 12
    for path in agents:
        body = emitted[path]
        # Strip our generated header comments before parsing.
        doc = yaml.safe_load(
            "\n".join(l for l in body.splitlines() if not l.startswith("# Generated"))
        )
        assert doc["kind"] == "Prompt", path
        assert doc["name"], path
        assert doc["instructions"], path


def test_the_model_is_named_in_the_targets_vocabulary_not_ours(emitted):
    doc = yaml.safe_load(emitted["agents/cfo.agent.yaml"].split("\n\n", 1)[1])
    assert doc["model"]["provider"] == "Anthropic"


def test_an_agents_authority_does_not_come_across_and_the_file_says_so(emitted):
    """The CFO's mandate is the design's whole point and MAF has nowhere for it."""
    body = emitted["agents/cfo.agent.yaml"]
    assert "Carried by the design and NOT by this file" in body
    assert "mandate: may decide" in body
    assert "approve_invoice" in body
    assert "placement 'finance--analysis'" in body


def test_a_gated_tool_is_flagged_because_the_format_cannot_declare_it(emitted):
    """Verified against the format, not assumed.

    `approvalMode` is a field on MAF's declarative `mcp` tool and not on its
    `function` tool, so a function tool that requires approval cannot be
    declared. Emitting it silently ungated would be the worst outcome.
    """
    gated = [p for p, body in emitted.items()
             if p.startswith("agents/") and "NOT EXPRESSIBLE" in body]
    assert gated, "Northwind gates tools; at least one agent should say so"
    body = emitted[gated[0]]
    assert "approvalMode" in body
    assert "requires approval" in body


def test_the_conformance_report_names_every_construct_that_does_not_travel(emitted):
    report = emitted["CONFORMANCE.md"]
    for construct in ("Roles and permissions", "Mandates", "Separation of duties",
                      "Autonomy postures", "People as principals"):
        assert construct in report, construct
    assert "**no model**" in report
    # And who enforces it instead, which is the point of the report.
    assert "our harness" in report
    assert "ADR-0067 rule 5" in report


def test_the_report_admits_the_tool_surface_is_incomplete(emitted):
    """The most important finding this target produced.

    An agent's real toolset is assembled by our harness at run time from
    mounted MCP servers and built-in families, and is never lowered into the
    IR. So no target can emit it — the one genuinely portable thing in the
    agent ecosystem is the part this platform does not compile.
    """
    report = emitted["CONFORMANCE.md"]
    assert "The rest of the tool surface" in report
    assert "not in the IR" in report


def test_capabilities_are_emitted_as_tools_because_most_designs_have_no_others(
    emitted,
):
    """Northwind declares twelve agents and no `tools:` block at all."""
    body = emitted["agents/controller.agent.yaml"]
    assert "tools:" in body
    assert "invoice_approval" in body


def test_the_report_counts_this_design_rather_than_speaking_generally(emitted):
    report = emitted["CONFORMANCE.md"]
    assert "**12** agents" in report
    assert "agents with a mandate" in report


def test_the_report_does_not_blame_the_target(emitted):
    """MAF is a runtime SDK and these are not runtime SDK concerns."""
    report = emitted["CONFORMANCE.md"]
    assert "not a defect in MAF" in report


def test_the_readme_sends_a_reader_to_the_conformance_report_first(emitted):
    assert "CONFORMANCE.md" in emitted["README.md"]


def test_the_target_does_not_import_the_spec_package():
    """The rule every target follows: permissions resolve once, in the IR."""
    source = (ROOT / "src" / "orgagents" / "compiler" / "targets" / "maf.py").read_text(
        encoding="utf-8")
    assert "orgagents.spec" not in source
    assert "from ...spec" not in source


# --------------------------------------------------------------------------
# A regression this target found
# --------------------------------------------------------------------------


def test_a_paired_person_reaches_the_system_prompt_with_a_name(emitted):
    """ADR-0079 moved pairings to a reference and broke this.

    A pairing that names a declared person carries no name or contact of its
    own, and the system prompt read them inline — so "the people you answer
    to" listed a blank name and a blank mailbox. That is worse than omitting
    the section: it tells an agent it answers to nobody. The IR resolves the
    reference now, which is where references are supposed to resolve.
    """
    body = emitted["agents/cfo.agent.yaml"]
    assert "Marcus Oyelaran" in body
    assert "marcus.oyelaran@northwind.example" in body
    assert "-  (owner) — owner, " not in body, "the blank pairing is back"
