"""The starter design and the gate scaffold (ADR-0090).

The phase gate knows exactly what a design is missing. Until `spec new` and
`phase --scaffold` existed, it named the gap and left the author at a blank
page, and the fastest real path to a first spec was "copy the nearest shipped
example".

Two properties matter here, and the second is the one that rots:

1. the starter design is **valid from the first save** — it validates and
   compiles, so an author sees something real before doing governance work;
2. the scaffold's YAML shapes are **actually correct** — a template that
   teaches a shape the model rejects is worse than no template. The filled
   fixture below is built from the scaffold's own blocks and must close the
   definition gate; if the spec model moves, this test fails and says the
   scaffold is stale.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

from orgagents.compiler.engine import compile_system
from orgagents.phases import review
from orgagents.scaffold import (
    GATE_BLOCKS,
    binding_template,
    catalog_blocks,
    scaffold_for,
    starter_spec,
)
from orgagents.spec.loader import load_spec_text
from orgagents.spec.validate import validate_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]


# -- the starter design ----------------------------------------------------

def test_the_starter_design_is_valid_from_the_first_save():
    spec = load_spec_text(starter_spec("acme"))
    errors = [f for f in validate_spec(spec) if f.severity == "error"]
    assert not errors, [str(e) for e in errors]


def test_the_starter_design_compiles(tmp_path):
    spec = load_spec_text(starter_spec("acme"))
    result = compile_system(spec, targets=["local"], out_dir=tmp_path)[0]
    assert result.files


def test_the_starter_design_does_not_yet_pass_the_gate():
    """The point of the starter is to be honest: it runs, and it is not yet
    governed. A template that claimed to pass the gate would be the lie the
    gate exists to catch."""
    spec = load_spec_text(starter_spec("acme"))
    assert review(spec, target="local").failures()


def test_the_starter_names_an_owner_when_given_one():
    assert "owner: Platform Team" in starter_spec("acme", owner="Platform Team")


def test_the_starter_carries_a_block_for_each_gate_check():
    text = starter_spec("acme")
    for block in GATE_BLOCKS:
        if block.check_id == "production_gate":
            continue                       # folded into the lifecycle block
        assert block.title in text, block.check_id


# -- the scaffold ----------------------------------------------------------

def test_the_scaffold_answers_the_checks_that_actually_fail():
    spec = load_spec_text(starter_spec("acme", owner="Platform Team"))
    report = review(spec, target="local")
    text = scaffold_for(report.failures(), name="acme", target="local")
    # An owner was given, so that check passes and must not be scaffolded.
    assert "The system has a named owner" not in text
    # These do fail, so they must be.
    assert "Data is classified" in text
    assert "Spend is bounded" in text


def test_the_scaffold_offers_a_binding_when_the_target_has_none():
    spec = load_spec_text(starter_spec("acme"))
    report = review(spec, target="local")
    text = scaffold_for(report.failures(), name="acme", target="local")
    assert "binding.yaml" in text
    assert "target: local" in text


def test_the_scaffold_declares_nothing_on_your_behalf():
    """Every emitted block is commented. A scaffold that silently declared a
    budget or a data class would be inventing governance nobody agreed."""
    spec = load_spec_text(starter_spec("acme"))
    report = review(spec, target="local")
    text = scaffold_for(report.failures(), name="acme", target="local")
    for line in text.splitlines():
        assert not line.strip() or line.lstrip().startswith("#"), line


# -- the shapes the scaffold teaches must be real --------------------------

#: A design written using exactly the blocks the scaffold emits. If the spec
#: model changes shape, this stops passing the gate and the scaffold is stale.
FILLED = """
metadata:
  name: acme
  spec_version: "1.4.0"
  version: "0.1.0"
  owner: Platform Team
data_classes:
  - id: public_knowledge
    description: Published material, safe to quote.
    scope: public
environments:
  - id: reasoning
    description: No code execution; delegation and tool calls only.
    network: none
roles:
  - id: team_member
    title: Team member
    responsibilities:
      - Do the work this team owns, and say so when you cannot.
    permissions:
      - {action: read, resource_kind: data_class, resource: public_knowledge}
people:
  - id: p_owner
    name: Dana
    contact: dana@acme.example
    position: Head of Platform
    unit: acme
channels:
  - id: ops_desk
    channel_class: team_chat
    description: Where alerts and approvals land for humans.
    purposes: [ask, notify]
    human_facing: true
memory:
  session:
    retention_minutes: 180
  long_term:
    enabled: true
    retention_days: 365
    promotion_allowed: true
    promotion_requires_approval: true
  namespaces:
    - id: lessons_learned
      description: What worked, de-identified.
budgets:
  - id: company_monthly
    scope_kind: system
    scope: acme
    period: monthly
    limit_usd: 1000
    on_breach: throttle
lifecycle:
  stage: development
  owner: Platform Team
  review_cadence_days: 90
  gates:
    - to_stage: production
      requires: [evaluations_passed, owner_assigned]
      min_pass_rate: 1.0
organization:
  id: acme
  name: acme
  leader: lead
  mandate:
    decisions: []
  members:
    - id: lead
      name: lead
      description: Leads this organization.
      mandate:
        decisions: []
      roles: [team_member]
      humans: [{person: p_owner, roles: [owner]}]
      environments: [{environment: reasoning}]
"""


def test_filling_in_the_scaffold_closes_the_definition_gate():
    """The whole promise: work the scaffold and the gate goes green."""
    spec = load_spec_text(FILLED)
    assert not [f for f in validate_spec(spec) if f.severity == "error"]
    report = review(spec)
    assert report.definition_complete, [
        c.title for c in report.failures("definition")]


# -- the approved catalog feeds the scaffold -------------------------------

class _Entry:
    """A stand-in for a CatalogEntry; the scaffold reads it duck-typed."""

    def __init__(self, kind, name, summary, status="approved", **attributes):
        self.kind, self.name, self.summary = kind, name, summary
        self.status, self.attributes = status, attributes


CATALOG = [
    _Entry("environment_template", "reasoning",
           "No code execution; delegation and tool calls only.",
           tier="minimal", network="none", toolchains=["none"]),
    _Entry("environment_template", "build",
           "Developer environment.", status="restricted",
           tier="large", network="allowlist"),
    _Entry("permission_set", "finance-read",
           "Read finance data and query the warehouse.",
           permissions=[{"action": "read", "resource_kind": "data_class",
                         "resource": "finance_internal"}]),
    _Entry("guardrail", "no-credentials-out",
           "Block credential-shaped content leaving an agent.",
           checks=["secrets"], on_violation="block",
           applies_to=["output"]),
    _Entry("model", "claude-sonnet-5", "The everyday choice.",
           provider="anthropic"),
    _Entry("model", "half-finished", "Not reviewed yet.", status="proposed",
           provider="somebody"),
    _Entry("mcp_server", "relational-sql", "Policy-enforcing SQL access.",
           transport="stdio", command="orgagents-mcp-sql", engine="postgres"),
]


def test_the_scaffold_offers_the_approved_shelf():
    blocks = catalog_blocks(CATALOG)
    envs = blocks["environments_declared"]
    assert "id: reasoning" in envs
    assert "network: none" in envs
    assert "finance_read" in blocks["agents_have_roles"]


def test_only_approved_entries_are_ever_offered():
    """Scaffolding a restricted or proposed entry would route the author
    around the approval the status records."""
    blocks = catalog_blocks(CATALOG)
    assert "build" not in blocks["environments_declared"]
    binding = binding_template("acme", "local", catalog=CATALOG)
    assert "claude-sonnet-5" in binding
    assert "half-finished" not in binding


def test_a_guardrail_is_offered_as_recommended_not_required():
    text = starter_spec("acme", catalog=CATALOG)
    assert "Guardrails (recommended)" in text
    assert "no_credentials_out" in text
    # Still a valid design: everything offered is commented.
    spec = load_spec_text(text)
    assert not [f for f in validate_spec(spec) if f.severity == "error"]


def test_an_empty_catalog_falls_back_to_the_generic_template():
    assert catalog_blocks([]) == {}
    assert catalog_blocks(None) == {}
    # And the starter still carries a generic environments block.
    assert "environments:" in starter_spec("acme")


def test_the_binding_names_approved_servers():
    binding = binding_template("acme", "local", catalog=CATALOG)
    assert "relational_sql" in binding
    assert "engine: postgres" in binding


# -- the CLI journey -------------------------------------------------------

def _cli(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    env = {"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, "-m", "orgagents.cli", *args],
                          cwd=cwd, env=env, capture_output=True, text=True)


def test_spec_new_writes_a_design_the_next_command_can_read(tmp_path):
    out = _cli("spec", "new", "acme", "--owner", "Platform Team", cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    written = tmp_path / "acme.system.yaml"
    assert written.is_file()
    check = _cli("spec", "validate", "acme.system.yaml", cwd=tmp_path)
    assert check.returncode == 0, check.stderr
    assert "0 errors" in check.stdout


def test_spec_new_refuses_to_clobber_without_force(tmp_path):
    assert _cli("spec", "new", "acme", cwd=tmp_path).returncode == 0
    again = _cli("spec", "new", "acme", cwd=tmp_path)
    assert again.returncode == 1
    assert "exists" in again.stdout


def test_phase_scaffold_writes_the_gaps(tmp_path):
    _cli("spec", "new", "acme", cwd=tmp_path)
    out = _cli("phase", "acme.system.yaml", "--target", "local",
               "--scaffold", "-o", "gaps.yaml", cwd=tmp_path)
    # `phase` still exits non-zero because the gate is not met — scaffolding
    # the answer is not the same as having answered it, and CI depends on
    # that exit code.
    assert out.returncode == 1, out.stdout
    assert "scaffold written to gaps.yaml" in out.stdout
    gaps = (tmp_path / "gaps.yaml").read_text()
    assert "Data is classified" in gaps
    assert "budgets:" in gaps
