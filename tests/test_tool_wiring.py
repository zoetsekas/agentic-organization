"""Wiring bound capabilities into real clients instead of stubs.

The platform targets (adk, langgraph) used to emit every tool as
`raise NotImplementedError`, because a tool's callable is the host's. But the
binding's servers catalog (ADR-0085) already says what kind of system backs a
capability and how it is reached, which is enough to emit a working client. So
a capability behind a declared MCP or database server is wired for real, and
only a capability with no server bound stays a stub.

AYC binds every capability to Shopify (MCP) or Fishbowl (database), so its
generated packages must contain no stubs at all. That is the assertion that
keeps the wiring honest.
"""
from __future__ import annotations

import ast
import pathlib
import tempfile

import pytest

from orgagents.compiler.base import register_builtin_targets
from orgagents.compiler.engine import compile_system
from orgagents.compiler.targets._wiring import (
    BACKENDS_SHIM,
    emit_wired_def,
    resolve_backend,
    shim_imports,
)
from orgagents.spec.loader import load_binding, load_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ayc_ir():
    from orgagents.compiler.ir import build_ir
    spec = load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml")
    binding = load_binding(ROOT / "examples" / "ayc" / "ayc.binding.yaml")
    return build_ir(spec, target="langgraph", binding=binding.for_target("langgraph"))


def _emit(target: str) -> dict[str, str]:
    register_builtin_targets()
    spec = load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml")
    binding = load_binding(ROOT / "examples" / "ayc" / "ayc.binding.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(spec, targets=[target], out_dir=pathlib.Path(tmp),
                                binding=binding)[0]
    return {f.path: f.content for f in result.files}


# -- the shim itself -------------------------------------------------------

def test_the_backends_shim_parses_and_defines_both_clients():
    tree = ast.parse(BACKENDS_SHIM)
    defs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert {"mcp_call", "bounded_sql"} <= defs


def test_a_credential_is_an_env_name_never_a_value():
    # The shim reads os.environ; it never embeds a token.
    assert "os.environ" in BACKENDS_SHIM
    assert "Bearer" in BACKENDS_SHIM  # header built from the env var, not a literal


# -- resolution ------------------------------------------------------------

def test_an_mcp_capability_resolves_to_an_mcp_backend(ayc_ir):
    plan = resolve_backend(ayc_ir, "product_publishing")
    assert plan and plan["kind"] == "mcp"
    assert plan["url"].startswith("https://")
    # The remote tool is named for the capability, not the server mount.
    assert plan["tool_name"] == "product_publishing"


def test_a_database_capability_resolves_to_a_sql_backend(ayc_ir):
    plan = resolve_backend(ayc_ir, "stock_check")
    assert plan and plan["kind"] == "database"
    assert plan["dsn_env"] == "FISHBOWL_DSN"


def test_an_unbound_capability_resolves_to_nothing(ayc_ir):
    assert resolve_backend(ayc_ir, "no_such_capability") is None


def test_emit_wired_def_is_runnable_python():
    mcp = emit_wired_def({"name": "x", "description": "d", "backend": {
        "kind": "mcp", "server_id": "s", "url": "https://h/mcp",
        "transport": "streamable_http", "tool_name": "x", "secret_env": None}})
    ast.parse("\n".join(mcp))
    sql = emit_wired_def({"name": "y", "description": "d",
                          "allowed_operations": ["select"], "max_rows": 100,
                          "backend": {"kind": "database", "dsn_env": "DSN"}})
    ast.parse("\n".join(sql))


def test_shim_imports_picks_the_needed_clients():
    assert shim_imports({"mcp"}) == ["mcp_call"]
    assert shim_imports({"database"}) == ["bounded_sql"]
    assert shim_imports({"mcp", "database"}) == ["bounded_sql", "mcp_call"]


# -- the whole-package assertion, per target -------------------------------

@pytest.mark.parametrize("target,shim_path,pkg", [
    ("langgraph", "graphs/_backends.py", "graphs/"),
    ("adk", "agents/_backends.py", "agents/"),
])
def test_ayc_has_no_stubs_because_every_capability_is_bound(target, shim_path, pkg):
    emitted = _emit(target)
    # The shim is emitted, and no agent module raises NotImplementedError.
    assert shim_path in emitted
    agent_modules = [p for p in emitted
                     if p.startswith(pkg) and p.endswith(".py")
                     and not p.endswith("_backends.py")
                     and not p.endswith("__init__.py")]
    assert agent_modules
    for path in agent_modules:
        assert "NotImplementedError" not in emitted[path], path


@pytest.mark.parametrize("target", ["langgraph", "adk"])
def test_every_generated_python_file_still_parses(target):
    for path, content in _emit(target).items():
        if path.endswith(".py"):
            ast.parse(content)


@pytest.mark.parametrize("target", ["langgraph", "adk"])
def test_a_bound_capability_calls_the_declared_backend(target):
    emitted = _emit(target)
    joined = "\n".join(v for k, v in emitted.items() if k.endswith(".py"))
    assert "mcp_call(" in joined       # Shopify / accounting / cms / deploy
    assert "bounded_sql(" in joined    # Fishbowl
    # The bound is enforced: SELECT only for the stock check.
    assert '["select"]' in joined


@pytest.mark.parametrize("target", ["langgraph", "adk"])
def test_requirements_name_the_wired_clients(target):
    req = _emit(target)["requirements.txt"]
    assert "langchain-mcp-adapters" in req
    assert "sqlalchemy" in req
