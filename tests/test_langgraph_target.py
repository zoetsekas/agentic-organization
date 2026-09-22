"""The LangGraph Platform / LangSmith target (deepagents runtime).

The third platform target (after MAF and ADK). Its deployable unit is a
LangGraph graph served by LangGraph Platform, with tracing in LangSmith — and
the graph is built by the same `langchain_deepagents` runtime this platform
runs under `terraform:gcp`. So the point of this target is the *mix*: one
runtime (deepagents), two destinations (your own cloud, or the hosted LangChain
platform). deepagents carries more of a design than ADK — real `subagents` and
a genuine `interrupt_on` human gate — so the conformance report has to be
honest about both the more and the still-missing (ADR-0073).
"""
from __future__ import annotations

import ast
import json
import pathlib
import tempfile

import pytest

from orgagents.compiler.base import register_builtin_targets
from orgagents.compiler.engine import compile_system
from orgagents.spec.loader import load_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def emitted() -> dict[str, str]:
    register_builtin_targets()
    spec = load_spec(ROOT / "examples" / "northwind" / "northwind.finance.system.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(spec, targets=["langgraph"], out_dir=pathlib.Path(tmp))[0]
    return {f.path: f.content for f in result.files}


def test_the_target_is_registered():
    register_builtin_targets()
    from orgagents.compiler.base import REGISTRY
    assert "langgraph" in REGISTRY.targets


def test_every_agent_becomes_a_deepagents_graph(emitted):
    modules = [p for p in emitted if p.startswith("graphs/") and p.endswith(".py")
               and not p.endswith("__init__.py")
               and not p.endswith("tools.py")
               and not p.endswith("_backends.py")]
    assert modules
    for path in modules:
        assert "create_deep_agent(" in emitted[path]


def test_every_emitted_python_file_parses(emitted):
    """A target that emits Python that does not parse is not a target."""
    for path, content in emitted.items():
        if path.endswith(".py"):
            ast.parse(content)  # raises on a syntax error


def test_the_manifest_is_valid_and_names_the_org_root(emitted):
    manifest = json.loads(emitted["langgraph.json"])
    assert manifest["dependencies"] == ["."]
    assert manifest["env"] == ".env"
    # The org entry point plus one graph per agent.
    assert manifest["graphs"]["org"].endswith("__init__.py:root")
    assert "controller" in manifest["graphs"]


def test_the_root_is_the_org_leader(emitted):
    init = emitted["graphs/__init__.py"]
    # Northwind's org leader is the chief executive, id `ceo`.
    assert "root = ceo" in init


def test_delegation_becomes_deepagents_subagents(emitted):
    """Our authorized-delegation edges become deepagents `subagents` — the
    shape, with the rule reported as not enforced."""
    module = next(v for k, v in emitted.items() if k.endswith("controller.py"))
    assert "SUBAGENTS = [" in module
    assert "subagents=SUBAGENTS" in module
    assert "payables" in module and "receivables" in module


def test_a_gated_tool_becomes_an_interrupt(emitted):
    """A tool the design marks for approval becomes a real human-in-the-loop
    gate — more than MAF or ADK carry."""
    joined = "\n".join(v for k, v in emitted.items() if k.endswith(".py"))
    assert "interrupt_on={" in joined


def test_tools_are_stubs_that_refuse_until_bound(emitted):
    # Northwind binds no servers, so its tools are stubs — now typed scaffolds
    # in one shared module, imported and wrapped as StructuredTools per agent.
    module = next(v for k, v in emitted.items() if k.endswith("controller.py"))
    assert "StructuredTool.from_function(" in module
    assert "from .tools import" in module
    stubs = emitted["graphs/tools.py"]
    assert "NotImplementedError" in stubs
    assert "TODO: implement" in stubs


def test_the_mandate_is_reported_as_not_carried(emitted):
    """The controller has a mandate; deepagents cannot hold it, so it must be
    named as a gap rather than dropped in silence (ADR-0073)."""
    module = next(v for k, v in emitted.items() if k.endswith("controller.py"))
    assert "NOT" in module and "mandate" in module


def test_the_env_template_wires_langsmith(emitted):
    env = emitted[".env.example"]
    assert "LANGSMITH_API_KEY" in env
    assert "LANGSMITH_TRACING" in env


def test_conformance_names_what_deepagents_cannot_enforce(emitted):
    report = emitted["CONFORMANCE.md"]
    for missing in ("Roles and permissions", "Mandates", "Separation of duties",
                    "Autonomy postures", "Data classes"):
        assert missing in report
    # And it is honest that deepagents carries more than ADK/MAF.
    assert "Human-in-the-loop gate" in report
    assert "Sub-agent hierarchy" in report


def test_requirements_name_deepagents_and_langgraph(emitted):
    req = emitted["requirements.txt"]
    assert "deepagents" in req
    assert "langgraph" in req


@pytest.fixture(scope="module")
def ayc() -> dict[str, str]:
    register_builtin_targets()
    spec = load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml")
    from orgagents.spec.loader import load_binding
    binding = load_binding(ROOT / "examples" / "ayc" / "ayc.binding.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(spec, targets=["langgraph"],
                                out_dir=pathlib.Path(tmp), binding=binding)[0]
    return {f.path: f.content for f in result.files}


def test_skills_lower_into_the_skills_parameter(ayc):
    # The ecommerce agent holds the listing_copy skill.
    mod = ayc["graphs/ecommerce_agent.py"]
    assert 'skills=["listing_copy"]' in mod


def test_long_term_memory_lowers_into_the_memory_parameter(ayc):
    joined = "\n".join(v for k, v in ayc.items() if k.endswith(".py"))
    assert 'memory=["merchandising_lessons"]' in joined


def test_filesystem_permissions_are_emitted_with_a_deny_floor(ayc):
    mod = ayc["graphs/ecommerce_agent.py"]
    assert "from deepagents import create_deep_agent, FilesystemPermission" in mod
    assert "FilesystemPermission(operations=" in mod
    assert 'mode="deny"' in mod          # the explicit floor (ADR-0008)


def test_planning_agent_gets_the_todo_middleware(ayc):
    mod = ayc["graphs/ecommerce_agent.py"]
    assert "from langchain.agents.middleware import" in mod
    assert "TodoListMiddleware()" in mod
    assert "middleware=[" in mod


def test_the_approver_the_interrupt_routes_to_is_named(ayc):
    mod = ayc["graphs/ecommerce_agent.py"]
    assert "interrupt_on=" in mod
    # deepagents pauses; the approver is our model, so it is documented.
    assert "the approver the design routes to is Lea" in mod


def test_an_output_contract_lowers_into_response_format():
    register_builtin_targets()
    spec = load_spec(ROOT / "examples" / "acme" / "acme.system.yaml")
    from orgagents.spec.loader import load_binding
    binding = load_binding(ROOT / "examples" / "acme" / "acme.binding.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(spec, targets=["langgraph"],
                                out_dir=pathlib.Path(tmp), binding=binding)[0]
    emitted = {f.path: f.content for f in result.files}
    assert "response_format={" in emitted["graphs/analyst.py"]


def test_conformance_now_claims_skills_memory_and_planning(emitted):
    report = emitted["CONFORMANCE.md"]
    for row in ("Skills", "Long-term memory", "Structured output",
                "Planning / task plan"):
        assert row in report


def test_it_is_the_same_runtime_as_the_gcp_target(emitted):
    """The whole point: deepagents on LangGraph Platform is the same runtime as
    deepagents on GCP. The README says so, and the graph is a deepagents graph."""
    readme = emitted["README.md"]
    assert "terraform:gcp" in readme
    assert "langchain_deepagents" in readme
