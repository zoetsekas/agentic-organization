#!/usr/bin/env python3
"""A larger design taken the whole distance, and through the phase gate.

    PYTHONPATH=src python3 examples/end_to_end_pharma.py

Where `end_to_end.py` shows the two new features on a small SOC, this runs the
big one: `helios.pharma.system.yaml`, a clinical-stage pharma company — four
levels of org tree, thirteen agents, three of them in more than one sandbox, a
sub-agent scoped to a subset of its parent's sandboxes, and a clinical-trial
readout modelled as a mission.

It goes further than the small example in one way that matters: it runs the
**phase gate** against a fabric's platform policy (`house.platform-policy.yaml`)
and against the binding (`helios.binding.yaml`), so you see the definition and
implementation phases decide whether this design is even allowed to compile —
including the check that a separation of duties survives the binding (ADR-0071).

Every stage is a real call into the code the CLI and runtime use.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from orgagents.compiler.base import register_builtin_targets
from orgagents.compiler.engine import compile_system
from orgagents.phases import review
from orgagents.platform_policy import load as load_platform_policy
from orgagents.spec.loader import load_binding, load_spec_text_with_migration
from orgagents.spec.validate import validate_spec

SPEC = ROOT / "examples" / "helios" / "helios.pharma.system.yaml"
BINDING = ROOT / "examples" / "helios" / "helios.binding.yaml"
POLICY = ROOT / "examples" / "house.platform-policy.yaml"


def banner(step: str, title: str) -> None:
    print(f"\n{'─' * 74}\n{step}  {title}\n{'─' * 74}")


def main() -> dict:
    register_builtin_targets()

    banner("1", "Load and migrate")
    spec, changes = load_spec_text_with_migration(SPEC.read_text())
    binding = load_binding(str(BINDING))
    policy = load_platform_policy(str(POLICY))
    print(f"  '{spec.metadata.name}' at spec_version {spec.metadata.spec_version}; "
          f"{len(spec.agents())} agents, {len(spec.environments)} sandbox classes")
    print("  migration:", "; ".join(changes) if changes else "already current")

    banner("2", "Validate")
    findings = validate_spec(spec)
    errors = [f for f in findings if f.severity == "error"]
    print(f"  {len(errors)} error(s), "
          f"{sum(1 for f in findings if f.severity == 'warning')} warning(s)")
    if errors:
        for e in errors:
            print(f"    ✗ {e}")
        raise SystemExit("the spec does not validate")

    banner("3", "The two agent features this design leans on")
    with tempfile.TemporaryDirectory() as tmp:
        ir = compile_system(spec, targets=["local"], out_dir=pathlib.Path(tmp),
                            binding=binding)[0].ir
        multi = [(a.id, [e.id for e in a.environments])
                 for a in ir.agents if len(a.environments) > 1]
        print(f"  {len(multi)} agents run in more than one sandbox (ADR-0082):")
        for aid, envs in multi:
            print(f"    · {aid}: {envs}")
        # A sub-agent may only use its parent's sandboxes.
        tm = ir.agent("trial_manager_agent")
        for sub in tm.subagents:
            print(f"  sub-agent {sub.tool_name} is scoped to {sub.environments} "
                  f"— a subset of its parent's {[e.id for e in tm.environments]}")
        # Instructions reach the prompt, before the org context (ADR-0083).
        authored = sum(1 for a in ir.agents
                       if "## Your instructions" in a.system_prompt())
        print(f"  {authored}/{len(ir.agents)} agents carry authored instructions "
              "in their composed prompt")

    banner("4", "Phase gate — is this design even allowed to compile?")
    for target in ("local", "terraform:gcp"):
        report = review(spec, binding=binding, target=target,
                        platform_policy=policy)
        print(f"\n  target '{target}' under platform policy '{policy.id}':")
        print(f"    {report.summary()}")
        for check in report.failures():
            print(f"    ✗ [{check.phase[:3]}] {check.title}: {check.detail}")
        print(f"    → ready to compile: "
              f"{'yes' if report.ready_to_compile else 'no'}")

    banner("5", "Compile to every target")
    with tempfile.TemporaryDirectory() as tmp:
        results = compile_system(
            spec, targets=["local", "maf", "terraform:gcp"],
            out_dir=pathlib.Path(tmp), binding=binding)
        for r in results:
            print(f"  {r.target}: {len(r.files)} file(s), {len(r.findings)} finding(s)")
        # The IR carries one job runner per sandbox (ADR-0082): the
        # two-sandbox bioinformatician is two runners, never one for the
        # wider. A single-workload target like terraform:gcp then deploys one
        # service sized for the widest sandbox, and says so — the isolation
        # still lives in the per-class sandbox images.
        bio_runners = [r.id for r in results[0].ir.resources
                       if r.kind == "job_runner" and r.owner == "bioinfo_agent"]
        print(f"    IR job runners for the two-sandbox bioinformatician: "
              f"{len(bio_runners)} {bio_runners}")

    banner("6", "Run four agents in the runtime")
    # The runtime is another consumer of the IR (ADR-0003). It runs on the
    # echo adapter here — no binding, no model keys — so the walk-through needs
    # nothing installed.
    with tempfile.TemporaryDirectory() as tmp:
        ir_run = compile_system(spec, targets=["local"],
                                out_dir=pathlib.Path(tmp))[0].ir
        from orgagents.platform import Platform
        from orgagents.runtime.loader import load_system

        platform = Platform(str(pathlib.Path(tmp) / "helios.db"),
                            configure_logs=False)
        load_system(platform, ir_run)
        ran = {}
        for aid in ("bioinfo_agent", "trial_manager_agent", "qa_agent",
                    "regulatory_agent"):
            result = platform.runtime.run(aid, "Carry out your next task.")
            ran[aid] = result.state.value
            print(f"  {aid}: {result.state.value}")

    banner("✓", "Designed, validated, gated, compiled to three targets, and run.")
    return {
        "agents": len(ir.agents),
        "multi_sandbox": [aid for aid, _ in multi],
        "ready_local": review(spec, binding=binding, target="local",
                              platform_policy=policy).ready_to_compile,
        "ran": ran,
    }


if __name__ == "__main__":
    main()
