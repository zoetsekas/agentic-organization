"""Compiler: phase boundary, IR resolution, targets, output ownership."""
import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from orgagents.compiler import build_ir, compile_system
from orgagents.compiler.base import register_builtin_targets
from orgagents.compiler.engine import CompileError
from orgagents.spec import load_binding, load_spec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme.system.yaml"
BINDING = ROOT / "examples" / "acme.binding.yaml"
TARGETS_DIR = ROOT / "src" / "orgagents" / "compiler" / "targets"


@pytest.fixture(scope="module")
def spec():
    return load_spec(EXAMPLE)


@pytest.fixture(scope="module")
def binding():
    return load_binding(BINDING)


# -- phase boundary (ADR-0005) --------------------------------------------


def test_no_target_imports_the_spec_package():
    """Targets consume the IR only, so they cannot re-derive a permission."""
    for path in TARGETS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            module = (
                node.module
                if isinstance(node, ast.ImportFrom)
                else ",".join(a.name for a in node.names)
                if isinstance(node, ast.Import)
                else None
            )
            if module and "spec" in module.split("."):
                pytest.fail(f"{path.name} imports {module}; targets must use the IR")


# -- IR resolution ---------------------------------------------------------


def test_reporting_chain_is_resolved_through_the_tree(spec):
    ir = build_ir(spec)
    chain = {a.id: a.reports_to for a in ir.agents}
    assert chain["platform_engineer"] == "platform_lead"
    assert chain["platform_lead"] == "cto"
    assert chain["cto"] == "ceo"
    assert chain["ceo"] is None


def test_leaders_delegate_down_and_members_do_not(spec):
    ir = build_ir(spec)
    assert set(ir.agent("cfo").delegates_to) >= {"analyst", "reconciler"}
    assert "cfo" not in ir.agent("analyst").delegates_to
    # Shared services are reachable from anywhere (ADR-0006).
    assert "sre" in ir.agent("analyst").delegates_to


def test_team_permissions_inherit_downwards(spec):
    ir = build_ir(spec)
    keys = {p.key() for p in ir.agent("analyst").permissions}
    assert "read:data_class:finance_internal" in keys      # from the finance team
    assert "query:capability:warehouse_query" in keys      # from the agent role
    assert "read:data_class:engineering_internal" not in keys


def test_environment_narrowing_is_resolved_once(spec):
    ir = build_ir(spec)
    analyst = ir.agent("analyst")
    assert analyst.environment.timeout_seconds == 600      # narrowed from 900
    assert ir.agent("reconciler").environment.egress_allowlist == []


def test_system_prompt_carries_responsibility_provenance(spec):
    prompt = build_ir(spec).agent("analyst").system_prompt()
    assert "role: financial_analyst" in prompt
    assert "cfo" in prompt and "Tom Becker" in prompt


def test_neutral_resource_set_is_produced(spec):
    ir = build_ir(spec)
    kinds = {r.kind for r in ir.resources}
    assert {"compute_service", "identity", "policy_binding", "job_runner",
            "network_boundary", "state_store", "secret"} <= kinds


# -- targets ---------------------------------------------------------------


def test_registry_exposes_infrastructure_targets_and_a_platform_target():
    """Two kinds, and the difference is the point.

    `local` and `terraform:*` are **infrastructure** targets: they deploy this
    platform's runtime, which picks an adapter. `maf` is a **platform**
    target: it emits another vendor's agent definitions, which is what the
    Terraform analogy promises and what nothing here did until it existed.
    """
    assert register_builtin_targets().ids() == [
        "local", "maf", "terraform:aws", "terraform:azure", "terraform:gcp"
    ]


def test_local_target_output(tmp_path, spec, binding):
    result = compile_system(spec, targets=["local"], out_dir=tmp_path,
                            binding=binding)[0]
    compose = yaml.safe_load((result.out_dir / "docker-compose.yaml").read_text())
    services = compose["services"]
    assert "agent-analyst" in services and "designer" in services
    # An isolated environment gets a gateway-less network (ADR-0009/0011).
    assert "isolated" in services["agent-reconciler"]["networks"]
    assert compose["networks"]["isolated"]["internal"] is True
    assert "egress" in services["agent-analyst"]["networks"]
    assert (result.out_dir / "Makefile").exists()
    assert (result.out_dir / "agents" / "analyst.json").exists()


def test_generated_output_contains_no_secret_values(tmp_path, spec, binding):
    results = compile_system(
        spec, targets=["local", "terraform:gcp"], out_dir=tmp_path, binding=binding
    )
    credential = re.compile(
        r"(postgres(ql)?://\S+:\S+@|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)"
    )
    for result in results:
        for gf in result.files:
            assert not credential.search(gf.content), f"credential in {gf.path}"
    env = (results[0].out_dir / ".env.example").read_text()
    assert "WAREHOUSE_DSN=" in env
    assert all(line.endswith("=") for line in env.splitlines()
               if "=" in line and not line.startswith("#"))


def test_terraform_target_emits_one_identity_per_agent(tmp_path, spec, binding):
    result = compile_system(spec, targets=["terraform:gcp"], out_dir=tmp_path,
                            binding=binding)[0]
    iam = (result.out_dir / "iam.tf").read_text()
    accounts = re.findall(r'resource "google_service_account" "(\w+)"', iam)
    assert sorted(accounts) == sorted(a.id for a in result.ir.agents)


def test_terraform_identifiers_are_valid_and_blocks_balance(tmp_path, spec, binding):
    """`terraform validate` needs the binary; these are the checks we can make."""
    for target in ("terraform:gcp", "terraform:aws", "terraform:azure"):
        result = compile_system(spec, targets=[target], out_dir=tmp_path / target[-3:],
                                binding=binding)[0]
        for gf in result.files:
            if not gf.path.endswith(".tf") and not gf.path.endswith(".tf.example"):
                continue
            for _type, name in re.findall(r'resource "([^"]+)" "([^"]+)"', gf.content):
                assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name), name
            assert gf.content.count("{") == gf.content.count("}"), gf.path


def test_mapping_report_accounts_for_every_permission(tmp_path, spec, binding):
    """ADR-0012: fidelity loss is reported, never hidden."""
    for target in ("terraform:gcp", "terraform:aws", "terraform:azure"):
        result = compile_system(spec, targets=[target], out_dir=tmp_path / target[-3:],
                                binding=binding)[0]
        report = (result.out_dir / "MAPPING.md").read_text()
        for agent in result.ir.agents:
            for perm in agent.permissions:
                assert f"`{perm.key()}`" in report, f"{perm.key()} missing from {target}"


def test_binding_selects_the_runtime_adapter(tmp_path, spec, binding):
    ir = compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding)[0].ir
    assert ir.agent("ceo").runtime_adapter == "echo"
    tf_ir = compile_system(spec, targets=["terraform:gcp"], out_dir=tmp_path,
                           binding=binding)[0].ir
    assert tf_ir.agent("ceo").runtime_adapter == "langchain_deepagents"


# -- output ownership (ADR-0014) ------------------------------------------


def test_regeneration_is_idempotent(tmp_path, spec, binding):
    first = compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding)[0]
    before = json.loads((first.out_dir / "manifest.json").read_text())
    compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding)
    after = json.loads((first.out_dir / "manifest.json").read_text())
    assert before == after


def test_modified_generated_file_blocks_regeneration(tmp_path, spec, binding):
    result = compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding)[0]
    makefile = result.out_dir / "Makefile"
    makefile.write_text(makefile.read_text() + "\n# hand edit\n")
    with pytest.raises(CompileError, match="modified since it was generated"):
        compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding)
    compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding, force=True)
    assert "# hand edit" not in makefile.read_text()


def test_overlays_survive_regeneration(tmp_path, spec, binding):
    result = compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding)[0]
    overlay = result.out_dir / "overlays" / "extra.tf"
    overlay.parent.mkdir(exist_ok=True)
    overlay.write_text("# mine\n")
    compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding)
    assert overlay.read_text() == "# mine\n"


def test_generated_files_carry_a_provenance_header(tmp_path, spec, binding):
    result = compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding)[0]
    compose = (result.out_dir / "docker-compose.yaml").read_text()
    assert "Generated by orgagents" in compose and "overlays/" in compose


def test_invalid_spec_never_reaches_a_target(tmp_path, spec):
    broken = spec.model_copy(deep=True)
    broken.organization.leader = "nobody"
    with pytest.raises(CompileError, match="validation failed"):
        compile_system(broken, targets=["local"], out_dir=tmp_path)


# -- runtime is a target consumer (ADR-0003) ------------------------------


def test_compiled_system_loads_and_runs_in_the_runtime(tmp_path, spec, binding):
    from orgagents.platform import Platform
    from orgagents.runtime.loader import load_system

    ir = compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding)[0].ir
    platform = Platform(str(tmp_path / "runtime.db"), configure_logs=False)
    loaded = load_system(platform, ir)
    assert len(loaded["agents"]) == len(ir.agents)

    chain = [a.id for a in platform.org.chain_of_command("platform_engineer")]
    assert chain == ["platform_engineer", "platform_lead", "cto", "ceo"]

    assert platform.org.can_delegate("cfo", "analyst")
    assert not platform.org.can_delegate("analyst", "platform_engineer")

    result = platform.runtime.run("analyst", "Summarize open invoices.")
    assert result.state.value == "completed"
    assert result.session_url.endswith(result.session_id)

    # The sandbox came from the spec's environment class, already narrowed.
    agent = platform.org.agent("analyst")
    sandbox = platform.sandboxes.resolve(agent.sandbox)
    assert sandbox.timeout_s == 600
    assert platform.sandboxes.resolve(
        platform.org.agent("reconciler").sandbox
    ).network == "none"


def test_capability_constraints_reach_the_harness(tmp_path, spec, binding):
    from orgagents.platform import Platform
    from orgagents.runtime.loader import load_system

    ir = compile_system(spec, targets=["local"], out_dir=tmp_path, binding=binding)[0].ir
    platform = Platform(str(tmp_path / "harness.db"), configure_logs=False)
    load_system(platform, ir)
    grant = platform.org.agent("analyst").harness.relational_grants[0]
    assert grant.allowed_statements == ["select"]
    assert grant.row_limit == 1000
    assert "tax_id" in grant.masked_columns
    # An approval-gated capability arrives as an interrupt, not a free tool.
    assert "pii_reconciliation" in platform.org.agent("reconciler").harness.interrupt_on


# -- triggers, channels, registry (ADR-0020, 0021, 0022) ------------------


def test_triggers_resolve_with_fire_times(spec, binding):
    ir = build_ir(spec, binding=binding.for_target("local"))
    flash = next(t for t in ir.triggers if t.id == "weekday_flash_report")
    assert flash.cron == "0 7 * * 1-5" and flash.timezone == "Europe/London"
    assert len(flash.next_runs) == 3
    # A trigger may not outlive the system's durability budget.
    assert all(t.max_runtime_seconds <= spec.resilience.max_run_seconds
               for t in ir.triggers)


def test_channels_merge_contract_and_binding(spec, binding):
    ir = build_ir(spec, binding=binding.for_target("local"))
    approvals = ir.channel("finance_approvals")
    assert approvals.provider == "msteams"           # from the binding
    assert approvals.response_sla_minutes == 120     # from the spec
    assert len(approvals.escalation) == 2
    assert "customer_pii" in approvals.forbid_data_classes


def test_agents_carry_channels_budget_and_flows(spec, binding):
    ir = build_ir(spec, binding=binding.for_target("local"))
    analyst = ir.agent("analyst")
    assert analyst.approval_channel == "finance_approvals"
    assert analyst.consults == ["sre"]                 # declared flow, not hierarchy
    assert analyst.budget_usd == 1200 and analyst.on_budget_breach == "warn"
    # The tightest budget wins: the reconciler has its own daily halt budget.
    assert ir.agent("reconciler").on_budget_breach == "halt"


def test_declared_flows_widen_delegation_only_when_they_delegate(spec, binding):
    ir = build_ir(spec, binding=binding.for_target("local"))
    # consult/notify/escalate flows must not become delegation edges
    assert "sre" in ir.agent("analyst").consults
    assert "cfo" not in ir.agent("reconciler").delegates_to


def test_local_target_emits_scheduler_and_channel_bridges(tmp_path, spec, binding):
    result = compile_system(spec, targets=["local"], out_dir=tmp_path,
                            binding=binding)[0]
    compose = yaml.safe_load((result.out_dir / "docker-compose.yaml").read_text())
    services = compose["services"]
    assert "scheduler" in services
    assert "channel-finance_approvals" in services
    bridge = services["channel-finance_approvals"]
    assert bridge["labels"]["org.agentic.provider"] == "msteams"
    # The bridge holds the workspace credential; the agents do not.
    assert "TEAMS_BOT_ID" in bridge["environment"]
    assert not any(
        "TEAMS_BOT_ID" in services[f"agent-{a.id}"]["environment"]
        for a in result.ir.agents
    )
    assert (result.out_dir / "triggers.json").exists()


def test_registry_report_inventories_the_fleet(tmp_path, spec, binding):
    result = compile_system(spec, targets=["local"], out_dir=tmp_path,
                            binding=binding)[0]
    registry = (result.out_dir / "REGISTRY.md").read_text()
    for agent in result.ir.agents:
        assert f"`{agent.id}`" in registry
        if agent.human:
            assert agent.human.name in registry
    assert "weekday_flash_report" in registry
    assert "Promotion gates" in registry and "production" in registry
    assert "Agents without a human owner:** none" in registry


def test_terraform_targets_emit_triggers_and_channels(tmp_path, spec, binding):
    for target in ("terraform:gcp", "terraform:aws", "terraform:azure"):
        result = compile_system(spec, targets=[target], out_dir=tmp_path / target[-3:],
                                binding=binding)[0]
        triggers = (result.out_dir / "triggers.tf").read_text()
        channels = (result.out_dir / "channels.tf").read_text()
        assert "trigger-weekday_flash_report" in triggers
        # A scheduled run uses the agent's identity, not the scheduler's.
        assert "service_account" in triggers
        assert "channel-finance_approvals" in channels
        assert (result.out_dir / "REGISTRY.md").exists()


def test_scheduler_holds_no_credentials_of_its_own(tmp_path, spec, binding):
    result = compile_system(spec, targets=["local"], out_dir=tmp_path,
                            binding=binding)[0]
    compose = yaml.safe_load((result.out_dir / "docker-compose.yaml").read_text())
    env = compose["services"]["scheduler"]["environment"]
    assert not any(key.endswith("_DSN") or key.endswith("_TOKEN") for key in env)


def test_knowledge_sources_reach_the_ir_and_identity(spec, binding):
    ir = build_ir(spec, binding=binding.for_target("local"))
    handbook = next(k for k in ir.knowledge if k.id == "finance_handbook")
    assert handbook.provider == "wiki" and handbook.index == "finance-handbook-v3"
    # Its secret is attached to the identity of the agent that reads it.
    assert "WIKI_TOKEN" in ir.agent("analyst").identity.secret_refs
    assert "WIKI_TOKEN" not in ir.agent("sre").identity.secret_refs
