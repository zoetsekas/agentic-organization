"""The Terraform target translates placement into a VPC (ADR-0084).

The placement model (ADR-0069) resolves which agents share a sandbox and which
placements may reach which. The GCP target used to drop that to a label on a
Cloud Run service. This holds it to emitting real isolation: a VPC, a subnet
per placement, a default-deny firewall, and one identity-scoped allow rule per
declared cross-placement path.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from orgagents.compiler.base import register_builtin_targets
from orgagents.compiler.engine import compile_system
from orgagents.spec.loader import load_binding, load_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def atlas():
    register_builtin_targets()
    spec = load_spec(ROOT / "examples" / "atlas" / "atlas.bank.system.yaml")
    binding = load_binding(str(ROOT / "examples" / "atlas" / "atlas.binding.yaml"))
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(spec, targets=["terraform:gcp"],
                                out_dir=pathlib.Path(tmp), binding=binding)[0]
    files = {f.path: f.content for f in result.files}
    files["_ir"] = result.ir
    return files


def test_a_vpc_and_a_subnet_per_placement_are_emitted(atlas):
    net = atlas["network.tf"]
    assert 'resource "google_compute_network"' in net
    ir = atlas["_ir"]
    subnets = net.count('resource "google_compute_subnetwork"')
    assert subnets == len(ir.placements)


def test_there_is_a_default_deny_between_placements(atlas):
    net = atlas["network.tf"]
    assert 'resource "google_compute_firewall" "deny_cross_placement"' in net
    assert 'deny { protocol = "all" }' in net
    assert 'priority  = 65534' in net


def test_each_placement_rule_becomes_one_identity_scoped_allow(atlas):
    net = atlas["network.tf"]
    ir = atlas["_ir"]
    # One allow_<n> rule per placement rule whose endpoints both have agents.
    live = [r for r in ir.placement_rules
            if any(p.id == r.source and p.agents for p in ir.placements)
            and any(p.id == r.target and p.agents for p in ir.placements)]
    for i in range(len(live)):
        assert f'"allow_{i}_' in net
    # And the rules are scoped to service accounts, not IP ranges.
    assert "source_service_accounts" in net
    assert "target_service_accounts" in net


def test_an_allow_rule_names_the_reason_it_exists(atlas):
    """The audit question is *why* two teams may talk; the rule carries it."""
    net = atlas["network.tf"]
    assert "control_room_escalations" in net  # a shared channel
    assert "flow:" in net                       # a declared flow


def test_a_closed_sandbox_gets_an_egress_deny(atlas):
    """A `none`-posture sandbox — the clean room, the enclave, the deal room —
    cannot call home."""
    net = atlas["network.tf"]
    assert 'direction = "EGRESS"' in net
    assert "deny_egress_" in net
    # Atlas has three none-posture sandboxes with agents.
    assert net.count("deny_egress_") >= 3


def test_the_fqdn_allowlist_limit_is_stated_not_hidden(atlas):
    net = atlas["network.tf"]
    assert "NOTE:" in net and "FQDN" in net


def test_the_sandbox_job_attaches_to_its_subnet(atlas):
    agents = atlas["agents.tf"]
    assert "vpc_access" in agents
    assert "network_interfaces" in agents
    assert "google_compute_subnetwork" in agents


def test_a_provider_without_a_network_mapping_says_so(atlas):
    """AWS has no network profile yet; its network.tf must admit it rather than
    let a reader assume isolation."""
    register_builtin_targets()
    spec = load_spec(ROOT / "examples" / "atlas" / "atlas.bank.system.yaml")
    binding = load_binding(str(ROOT / "examples" / "atlas" / "atlas.binding.yaml"))
    # AWS is not bound here, but the network file is still emitted per target;
    # compile terraform:aws without a binding to read its network.tf.
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_system(spec, targets=["terraform:aws"],
                                out_dir=pathlib.Path(tmp))[0]
    net = next(f.content for f in result.files if f.path == "network.tf")
    assert "NOT generated" in net
