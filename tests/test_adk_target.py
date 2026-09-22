"""The Google ADK / Vertex AI Agent Engine target.

The second platform target (after MAF). Where MAF emits declarative YAML, ADK's
deployable unit is Python, so this emits an ADK package. The interesting half
is the same as MAF's: what does *not* come across, and does the target say so
(ADR-0073)? ADK carries a little more — sub_agents, callbacks, an output
schema — and the conformance report has to be honest about both the more and
the still-missing.
"""
from __future__ import annotations

import ast
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
        result = compile_system(spec, targets=["adk"], out_dir=pathlib.Path(tmp))[0]
    return {f.path: f.content for f in result.files}


def test_the_target_is_registered():
    register_builtin_targets()
    from orgagents.compiler.base import REGISTRY
    assert "adk" in REGISTRY.targets


def test_every_agent_becomes_a_python_llm_agent(emitted):
    modules = [p for p in emitted if p.startswith("agents/") and p.endswith(".py")
               and not p.endswith("__init__.py")
               and not p.endswith("tools.py")
               and not p.endswith("_backends.py")]
    assert modules
    for path in modules:
        assert "LlmAgent(" in emitted[path]


def test_every_emitted_python_file_parses(emitted):
    """A target that emits Python that does not parse is not a target."""
    for path, content in emitted.items():
        if path.endswith(".py"):
            ast.parse(content)  # raises on a syntax error


def test_the_root_agent_is_the_org_leader(emitted):
    init = emitted["agents/__init__.py"]
    # Northwind's org leader is the chief executive, id `ceo`.
    assert "root_agent = ceo" in init


def test_delegation_becomes_sub_agents(emitted):
    """Our authorized-delegation edges become ADK sub_agents — the shape, with
    the rule reported as not enforced."""
    init = emitted["agents/__init__.py"]
    # The controller delegates to accounts payable and receivable.
    assert "controller.sub_agents = [" in init
    assert "payables" in init and "receivables" in init


def test_a_gemini_model_id_is_chosen(emitted):
    """A non-Gemini binding still yields a runnable Gemini id, flagged."""
    module = next(v for k, v in emitted.items() if k.endswith("controller.py"))
    assert "gemini" in module


def test_generate_content_config_carries_the_sampling_ceiling(emitted):
    module = next(v for k, v in emitted.items() if k.endswith("controller.py"))
    assert "generate_content_config" in module
    assert "max_output_tokens" in module


def test_tools_are_stubs_that_refuse_until_bound(emitted):
    # Northwind binds no servers, so its tools are stubs — now typed scaffolds
    # in one shared module, imported and wrapped as FunctionTools per agent.
    module = next(v for k, v in emitted.items() if k.endswith("controller.py"))
    assert "FunctionTool(" in module
    assert "from .tools import" in module
    stubs = emitted["agents/tools.py"]
    assert "NotImplementedError" in stubs
    assert "TODO: implement" in stubs


def test_the_mandate_is_reported_as_not_carried(emitted):
    """The controller has a mandate; ADK cannot hold it, so it must be named as
    a gap rather than dropped in silence (ADR-0073)."""
    module = next(v for k, v in emitted.items() if k.endswith("controller.py"))
    assert "NOT" in module and "mandate" in module


def test_the_deploy_script_targets_agent_engine(emitted):
    deploy = emitted["agent_engine.py"]
    assert "agent_engines.create" in deploy
    assert "--project" in deploy and "--location" in deploy
    ast.parse(deploy)


def test_conformance_names_what_adk_cannot_enforce(emitted):
    report = emitted["CONFORMANCE.md"]
    for missing in ("Roles and permissions", "Mandates", "Separation of duties",
                    "Autonomy postures", "Data classes"):
        assert missing in report
    # And it is honest that ADK carries more shape than MAF.
    assert "Sub-agent hierarchy" in report


def test_requirements_name_adk_and_agent_engine(emitted):
    req = emitted["requirements.txt"]
    assert "google-adk" in req
    assert "agent_engines" in req or "agent-engines" in req or "aiplatform" in req
