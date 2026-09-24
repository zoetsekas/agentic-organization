#!/usr/bin/env python3
"""One design, taken the whole distance: spec → validate → IR → compile → run.

    PYTHONPATH=src python3 examples/end_to_end.py

This is the worked end-to-end example. It uses `sentinel.secops.system.yaml`
because that design exercises the two capabilities the other examples predate:

  * an agent in **more than one sandbox** (ADR-0082) — the SOC analyst triages
    in `analysis` and detonates in `detonation`, and you can see below that it
    resolves to two placements and two job runners, never one enclosing both;

  * an agent that carries its own **instructions** (ADR-0083) — printed as they
    land in the composed system prompt, after the author's description and
    before the organization's own account of who the agent answers to.

Every stage is a real call into the same code the CLI and the runtime use, not
a narration of one. If a stage would fail, this script fails at it. A test
(`tests/test_end_to_end_example.py`) runs the whole thing so it cannot rot.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from orgagents.compiler.base import register_builtin_targets
from orgagents.compiler.engine import compile_system
from orgagents.spec.loader import load_spec_text_with_migration
from orgagents.spec.validate import validate_spec

SPEC = ROOT / "examples" / "sentinel" / "sentinel.secops.system.yaml"


def banner(step: str, title: str) -> None:
    print(f"\n{'─' * 72}\n{step}  {title}\n{'─' * 72}")


def main() -> dict:
    register_builtin_targets()

    # 1. Load, migrating an older document forward and saying what that cost.
    banner("1", "Load the spec (migrating if the document is older)")
    spec, changes = load_spec_text_with_migration(SPEC.read_text())
    print(f"  loaded '{spec.metadata.name}' at spec_version "
          f"{spec.metadata.spec_version}")
    print("  migration:", "; ".join(changes) if changes else "already current")

    # 2. Validate. Errors would stop a compile; warnings are advisory.
    banner("2", "Validate")
    findings = validate_spec(spec)
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    print(f"  {len(errors)} error(s), {len(warnings)} warning(s)")
    for w in warnings:
        print(f"    · {w.code}: {w.message}")
    if errors:
        for e in errors:
            print(f"    ✗ {e}")
        raise SystemExit("the spec does not validate; nothing downstream runs")

    # 3. Resolve to the IR — permissions, placements and prompts, resolved once.
    banner("3", "Resolve to the IR, and read the two new features off it")
    # The IR is what a target consumes; compiling to a target hands it back.
    with tempfile.TemporaryDirectory() as tmp:
        results = compile_system(spec, targets=["local", "maf"],
                                 out_dir=pathlib.Path(tmp))
        ir = results[0].ir

        analyst = ir.agent("analyst_agent")
        print(f"  the SOC analyst runs in {len(analyst.environments)} sandboxes: "
              f"{[e.id for e in analyst.environments]}")
        print(f"    → placements: {analyst.placements}")
        runners = [r.id for r in ir.resources
                   if r.kind == "job_runner" and r.owner == "analyst_agent"]
        print(f"    → one job runner per sandbox: {runners}")
        assert len(analyst.environments) == 2 and len(runners) == 2, \
            "the multi-sandbox feature did not survive to the IR"

        prompt = analyst.system_prompt()
        assert "## Your instructions" in prompt, \
            "the agent's authored instructions did not reach the prompt"
        block = prompt.split("## Your instructions", 1)[1].split("##", 1)[0]
        print("\n  the analyst's authored instructions, as compiled:")
        for line in block.strip().splitlines():
            print(f"    │ {line}")

        # 4. Compile — the artifacts a platform would deploy.
        banner("4", "Compile to targets")
        for result in results:
            print(f"  {result.target}: {len(result.files)} file(s), "
                  f"{len(result.findings)} finding(s)")
        maf = next(r for r in results if r.target == "maf")
        analyst_yaml = next(
            f for f in maf.files
            if f.path.endswith("analyst_agent.agent.yaml")).content
        assert "## Your instructions" in analyst_yaml
        print("    the instructions are carried into the Microsoft Agent "
              "Framework agent file as well.")

    # 5. Run — the runtime is just another consumer of the IR (ADR-0003).
    banner("5", "Load into the runtime and run the analyst")
    from orgagents.platform import Platform
    from orgagents.runtime.loader import load_system

    with tempfile.TemporaryDirectory() as tmp:
        platform = Platform(str(pathlib.Path(tmp) / "e2e.db"),
                            configure_logs=False)
        load_system(platform, ir)
        # Both sandboxes came across as narrowed templates.
        sandboxes = [t for t in platform.sandboxes.templates()
                     if "analyst_agent" in t.id]
        print(f"  the analyst's sandboxes materialized as "
              f"{len(sandboxes)} templates: {[t.id for t in sandboxes]}")
        result = platform.runtime.run(
            "analyst_agent",
            "EDR and the SIEM both flag host WKS-4419 beaconing. Triage it.")
        print(f"  run state: {result.state.value}")
        print(f"  session:   {result.session_id}")

    banner("✓", "End to end: designed, validated, compiled to two targets, and run.")
    return {
        "sandboxes": [e.id for e in analyst.environments],
        "runners": runners,
        "state": result.state.value,
    }


if __name__ == "__main__":
    main()
