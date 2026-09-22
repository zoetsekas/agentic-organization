#!/usr/bin/env python3
"""A multinational bank, taken to Google Cloud on Gemini.

    PYTHONPATH=src python3 examples/end_to_end_bank_gcp.py

The largest design (`atlas.bank.system.yaml`: a global universal bank — four
lines of business, the three lines of defence, five levels of org tree) taken
the whole distance and pointed at Google's stack:

  * the **adk** target emits a Google ADK package that runs the agents on
    Gemini via Vertex AI Agent Engine;
  * the **terraform:gcp** target places the *governed* runtime on Google Cloud.

It also serves as the feature-coverage check: this design populates every
top-level block the spec has, and step 2 asserts it.

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
from orgagents.spec.model import SystemSpec
from orgagents.spec.validate import validate_spec

SPEC = ROOT / "examples" / "atlas" / "atlas.bank.system.yaml"
BINDING = ROOT / "examples" / "atlas" / "atlas.binding.yaml"
POLICY = ROOT / "examples" / "house.platform-policy.yaml"


def banner(step: str, title: str) -> None:
    print(f"\n{'─' * 76}\n{step}  {title}\n{'─' * 76}")


def main() -> dict:
    register_builtin_targets()

    banner("1", "Load and migrate")
    spec, changes = load_spec_text_with_migration(SPEC.read_text())
    binding = load_binding(str(BINDING))
    policy = load_platform_policy(str(POLICY))
    print(f"  '{spec.metadata.name}' at spec_version {spec.metadata.spec_version}; "
          f"{len(spec.agents())} agents across {len(spec.environments)} sandbox "
          "classes")

    banner("2", "Feature coverage — every block the spec has")
    import yaml
    doc = yaml.safe_load(SPEC.read_text())
    blocks = list(SystemSpec.model_fields)
    used = [b for b in blocks if doc.get(b)]
    missing = [b for b in blocks if not doc.get(b)]
    print(f"  {len(used)}/{len(blocks)} top-level blocks populated")
    print(f"  missing: {missing or 'none — this design exercises every feature'}")
    assert not missing, f"coverage gap: {missing}"

    banner("3", "Validate")
    findings = validate_spec(spec)
    errors = [f for f in findings if f.severity == "error"]
    print(f"  {len(errors)} error(s), "
          f"{sum(1 for f in findings if f.severity == 'warning')} warning(s)")
    if errors:
        for e in errors:
            print(f"    ✗ {e}")
        raise SystemExit("the spec does not validate")

    banner("4", "The multinational shape")
    with tempfile.TemporaryDirectory() as tmp:
        ir = compile_system(spec, targets=["adk"], out_dir=pathlib.Path(tmp),
                            binding=binding)[0].ir
        lobs = [t for t in spec.organization.teams]
        print(f"  lines of business / functions: {[t.id for t in lobs]}")
        print("  three lines of defence: business units, then "
              "risk + financial_crime, then internal_audit")
        multi = [(a.id, [e.id for e in a.environments])
                 for a in ir.agents if len(a.environments) > 1]
        print(f"  {len(multi)} agents run in more than one sandbox (ADR-0082):")
        for aid, envs in multi:
            print(f"    · {aid}: {envs}")
        print(f"  information barriers as separations: "
              f"{[s.id for s in spec.separations]}")

    banner("5", "Phase gate — allowed to deploy to Google Cloud?")
    ready = {}
    for target in ("adk", "terraform:gcp"):
        report = review(spec, binding=binding, target=target,
                        platform_policy=policy)
        ready[target] = report.ready_to_compile
        print(f"  {target} under platform policy '{policy.id}': {report.summary()}")
        for check in report.failures():
            print(f"    ✗ [{check.phase[:3]}] {check.title}: {check.detail}")
        print(f"    → ready: {'yes' if report.ready_to_compile else 'no'}")

    banner("6", "Compile to Google's stack")
    with tempfile.TemporaryDirectory() as tmp:
        results = compile_system(
            spec, targets=["adk", "terraform:gcp"],
            out_dir=pathlib.Path(tmp), binding=binding)
        adk = next(r for r in results if r.target == "adk")
        gcp = next(r for r in results if r.target == "terraform:gcp")
        print(f"  adk (Vertex AI Agent Engine): {len(adk.files)} files")
        print(f"  terraform:gcp (governed runtime): {len(gcp.files)} files")

        # The GCP network isolation: placement → VPC/subnets/firewall (ADR-0084).
        net = next(f.content for f in gcp.files if f.path == "network.tf")
        subnets = net.count('resource "google_compute_subnetwork"')
        allows = net.count('"allow_')
        denies_egress = net.count("deny_egress_")
        print(f"  network.tf: 1 VPC, {subnets} per-placement subnets, "
              f"a default-deny, {allows} identity-scoped allow rules, "
              f"{denies_egress} egress-deny (offline sandboxes)")
        iam = next(f.content for f in gcp.files if f.path == "iam.tf")
        sas = iam.count('resource "google_service_account"')
        binds = iam.count('resource "google_project_iam_member"')
        print(f"  iam.tf: {sas} per-agent service accounts, {binds} IAM bindings")
        # The servers catalog (ADR-0085): the deployment's systems inventory.
        target = binding.targets[0]
        print(f"  systems inventory (servers catalog): {len(target.servers)} "
              f"backing systems for {len(target.capabilities)} capabilities")

        # The Gemini models came from the binding.
        trader = next(f.content for f in adk.files
                      if f.path.endswith("trader_agent.py"))
        model_line = next(ln.strip() for ln in trader.splitlines()
                          if ln.strip().startswith("model="))
        print(f"  ADK agents run on Gemini: trader has {model_line}")
        # The deploy entrypoint and the honesty report.
        deploy = next(f.content for f in adk.files
                      if f.path == "agent_engine.py")
        assert "agent_engines.create" in deploy
        print("  agent_engine.py deploys the root agent to Vertex AI Agent Engine")
        conformance = next(f.content for f in adk.files
                           if f.path == "CONFORMANCE.md")
        carried = "Sub-agent hierarchy" in conformance
        not_carried = all(k in conformance for k in
                          ("Mandates", "Separation of duties", "Roles"))
        print(f"  CONFORMANCE.md is honest: sub-agents carried={carried}, "
              f"authority-model reported as not carried={not_carried}")

    banner("7", "Run the governed runtime locally")
    # Governance lives in the harness, not in the ADK package — so to *run* the
    # design with its controls, use the platform runtime. Echo adapter, so no
    # Gemini keys are needed for the walk-through.
    with tempfile.TemporaryDirectory() as tmp:
        ir_run = compile_system(spec, targets=["adk"],
                                out_dir=pathlib.Path(tmp))[0].ir
        from orgagents.platform import Platform
        from orgagents.runtime.loader import load_system

        platform = Platform(str(pathlib.Path(tmp) / "atlas.db"),
                            configure_logs=False)
        load_system(platform, ir_run)
        ran = {}
        for aid in ("trader_agent", "research_analyst_agent", "aml_agent",
                    "loan_officer_agent"):
            result = platform.runtime.run(aid, "Carry out your next task.")
            ran[aid] = result.state.value
            print(f"  {aid}: {result.state.value}")

    banner("✓", "A global bank: every feature used, gated, compiled to Gemini/GCP, and run.")
    return {
        "blocks_used": len(used),
        "blocks_total": len(blocks),
        "multi_sandbox": [aid for aid, _ in multi],
        "ready": ready,
        "ran": ran,
    }


if __name__ == "__main__":
    main()
