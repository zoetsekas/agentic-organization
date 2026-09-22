"""Bring your own implementation: templates, and overlays that work (ADR-0092).

Two ways to customise what the implementation phase produces, neither of which
requires forking this package or writing a `Target` class:

* the **template target** renders a directory of `*.tmpl` files against the IR,
  so a Helm chart or a Nomad job is a template rather than a Python plugin;
* **overlays** let an operator extend a generated stack — and until now that was
  a promise on every generated file with no mechanism behind it.

The overlay mechanism differs per target and that is the point: Compose needs an
explicit `-f` chain, Terraform auto-loads root `*.tf` and ignores
subdirectories. A single "put customizations in overlays/" line was wrong for
one of them, which is worse than saying nothing.
"""
from __future__ import annotations

import pathlib

import pytest

from orgagents.compiler.engine import compile_system
from orgagents.compiler.targets.template import TemplateTarget, agent_context
from orgagents.spec.loader import load_binding, load_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ayc():
    return (load_spec(ROOT / "examples" / "ayc" / "ayc.system.yaml"),
            load_binding(ROOT / "examples" / "ayc" / "ayc.binding.yaml"))


def _templates(tmp_path: pathlib.Path) -> pathlib.Path:
    """A template directory a user might actually write."""
    d = tmp_path / "templates"
    (d / "jobs").mkdir(parents=True)
    (d / "README.md.tmpl").write_text(
        "# $system_name\n$agent_count agents\n")
    (d / "jobs" / "{agent}.nomad.tmpl").write_text(
        'job "$agent_id" {\n  # $agent_description\n'
        '  # team: $team\n  # approval: $requires_approval_for\n}\n')
    return d


def _compile(spec, binding, tmp_path, templates):
    """Register the template target, compile, then take it back out.

    The registry is global, so a target left behind would follow the suite into
    other files — which is exactly how this leaked the first time.
    """
    from orgagents.compiler.base import REGISTRY, register_builtin_targets

    register_builtin_targets()
    REGISTRY.register(TemplateTarget(str(templates)), replace=True)
    try:
        return compile_system(spec, targets=["template"],
                              out_dir=tmp_path / "out", binding=binding)[0]
    finally:
        REGISTRY.registry.items.pop("template", None)
        REGISTRY.registry.descriptors.pop("template", None)


# -- the template target ---------------------------------------------------

def test_a_template_renders_once_per_agent(tmp_path, ayc):
    """Iteration lives in the filename, not in a template language."""
    spec, binding = ayc
    result = _compile(spec, binding, tmp_path, _templates(tmp_path))
    emitted = {f.path for f in result.files}
    assert "jobs/ecommerce_agent.nomad" in emitted
    assert "jobs/ceo_agent.nomad" in emitted
    # One job per agent, and the .tmpl suffix is stripped.
    jobs = [p for p in emitted if p.startswith("jobs/")]
    assert len(jobs) == len(spec_agents(result))
    assert not any(p.endswith(".tmpl") for p in emitted)


def spec_agents(result):
    return result.ir.agents


def test_a_system_template_renders_once(tmp_path, ayc):
    spec, binding = ayc
    result = _compile(spec, binding, tmp_path, _templates(tmp_path))
    readme = next(f for f in result.files if f.path == "README.md")
    assert "# ayc" in readme.content
    assert f"{len(result.ir.agents)} agents" in readme.content


def test_the_design_reaches_the_template(tmp_path, ayc):
    spec, binding = ayc
    result = _compile(spec, binding, tmp_path, _templates(tmp_path))
    job = next(f for f in result.files
               if f.path == "jobs/ecommerce_agent.nomad").content
    assert 'job "ecommerce_agent"' in job
    assert "Shopify" in job                       # the description
    assert "E-Commerce" in job                    # the team path
    assert "product_publishing" in job            # what needs approval


def test_an_unknown_placeholder_is_left_alone_and_reported(tmp_path, ayc):
    """A `$VAR` is far more often a shell variable than a mistake, so the
    compile must not die on one — but a real typo still has to be findable."""
    spec, binding = ayc
    templates = _templates(tmp_path)
    (templates / "typo.txt.tmpl").write_text("$agnet_name and $HOME\n")
    result = _compile(spec, binding, tmp_path, templates)
    rendered = next(f for f in result.files if f.path == "typo.txt").content
    assert rendered == "$agnet_name and $HOME\n"   # untouched, not crashed
    report = next(f for f in result.files if f.path == "CONFORMANCE.md").content
    assert "agnet_name" in report                  # and findable


def test_it_ships_the_ir_so_a_template_is_never_the_ceiling(tmp_path, ayc):
    spec, binding = ayc
    result = _compile(spec, binding, tmp_path, _templates(tmp_path))
    assert "system.ir.json" in {f.path for f in result.files}


def test_the_conformance_report_refuses_to_claim_anything(tmp_path, ayc):
    """This target renders somebody else's templates, so it cannot promise
    what they carry — and must say so rather than imply otherwise."""
    spec, binding = ayc
    result = _compile(spec, binding, tmp_path, _templates(tmp_path))
    report = next(f for f in result.files if f.path == "CONFORMANCE.md").content
    for owed in ("Roles, permissions, mandates", "Separation of duties",
                 "Approvals"):
        assert owed in report
    assert "harness" in report


def test_a_missing_template_directory_fails_clearly(tmp_path, ayc):
    spec, binding = ayc
    with pytest.raises(ValueError, match="template directory not found"):
        _compile(spec, binding, tmp_path, tmp_path / "nope")


def test_it_claims_only_the_core_it_actually_exposes():
    """It cannot know what a user's templates carry, so claiming features on
    their behalf would be a guess."""
    descriptor = TemplateTarget("x").descriptor()
    assert descriptor.claims("instructions")
    assert not descriptor.claims("interrupt_on")


def test_agent_context_is_all_strings(tmp_path, ayc):
    """`string.Template` substitutes into text; a non-string would blow up at
    render time rather than here."""
    spec, binding = ayc
    result = _compile(spec, binding, tmp_path, _templates(tmp_path))
    context = agent_context(result.ir, result.ir.agents[0])
    assert all(isinstance(v, str) for v in context.values())


# -- overlays, made real ---------------------------------------------------

def test_compose_overlays_are_actually_merged(tmp_path, ayc):
    """The Makefile builds the -f chain, so `make up` includes them. Without
    this the advice on every generated file was inert."""
    spec, _ = ayc
    result = compile_system(spec, targets=["local"], out_dir=tmp_path)[0]
    makefile = next(f for f in result.files if f.path == "Makefile").content
    assert "COMPOSE_OVERLAYS" in makefile
    assert "wildcard overlays/*.yaml" in makefile
    assert "$(COMPOSE) up" in makefile            # up uses the chain
    assert "docker compose up" not in makefile    # and not the bare command


def test_each_target_explains_its_own_overlay_idiom(tmp_path, ayc):
    """Compose merges a subdirectory; Terraform does not descend into one. One
    sentence could not be true for both."""
    spec, binding = ayc
    local = compile_system(spec, targets=["local"], out_dir=tmp_path / "l")[0]
    tf = compile_system(spec, targets=["terraform:gcp"],
                        out_dir=tmp_path / "t", binding=binding)[0]

    local_readme = next(f for f in local.files
                        if f.path == "overlays/README.md").content
    assert "merged over the generated stack" in local_readme

    tf_readme = next(f for f in tf.files
                     if f.path == "overlays/README.md").content
    # Terraform's honest answer is "not in here".
    assert "not in here" in tf_readme
    assert "root module" in tf_readme


def test_an_overlay_cannot_edit_away_a_control(tmp_path, ayc):
    """Both readmes say it, because an operator who believed otherwise would
    think a compose file could widen an agent's authority."""
    spec, binding = ayc
    for target, out in (("local", "l"), ("terraform:gcp", "t")):
        result = compile_system(spec, targets=[target],
                                out_dir=tmp_path / out, binding=binding)[0]
        readme = next(f for f in result.files
                      if f.path == "overlays/README.md").content
        assert "never the design" in readme
