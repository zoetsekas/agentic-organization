"""A servers catalog in the binding (ADR-0085).

An enterprise reaches a handful of systems through dozens of capabilities.
Declaring the connection on every capability repeats it and hides the sharing.
A `servers:` catalog names each system once; a capability references it. The
resolution has to be transparent, so the phase gate's barrier-survival check
(ADR-0071) still sees two capabilities on one catalog server as colliding.
"""
from __future__ import annotations

import pytest

from orgagents.spec.binding import TargetBinding


def test_a_capability_inherits_its_servers_connection():
    t = TargetBinding.model_validate({
        "target": "local",
        "servers": [{"id": "erp", "kind": "database", "engine": "postgres",
                     "dsn_secret_ref": "ERP_DSN"}],
        "capabilities": [{"capability": "ledger_query", "server": "erp"}],
    })
    cb = t.capability_binding("ledger_query")
    assert cb.server_name == "erp"
    assert cb.engine == "postgres"
    assert cb.dsn_secret_ref == "ERP_DSN"


def test_an_inline_field_overrides_the_catalog():
    t = TargetBinding.model_validate({
        "target": "local",
        "servers": [{"id": "erp", "engine": "postgres",
                     "dsn_secret_ref": "SHARED_DSN"}],
        "capabilities": [{"capability": "c", "server": "erp",
                          "dsn_secret_ref": "OWN_DSN"}],
    })
    assert t.capability_binding("c").dsn_secret_ref == "OWN_DSN"


def test_a_dangling_server_reference_is_refused():
    with pytest.raises(ValueError, match="does not declare"):
        TargetBinding.model_validate({
            "target": "x",
            "capabilities": [{"capability": "c", "server": "nope"}],
        })


def test_a_capability_with_no_connection_at_all_is_refused():
    with pytest.raises(ValueError, match="neither"):
        TargetBinding.model_validate({
            "target": "x",
            "capabilities": [{"capability": "c"}],
        })


def test_the_inline_form_still_works_unchanged():
    t = TargetBinding.model_validate({
        "target": "x",
        "capabilities": [{"capability": "c", "server_name": "s",
                          "transport": "http", "url": "http://x/mcp"}],
    })
    cb = t.capability_binding("c")
    assert cb.server_name == "s" and cb.url == "http://x/mcp"


def test_two_capabilities_on_one_catalog_server_collide():
    """The barrier-survival guarantee: sharing a catalog server is as visible
    as sharing an inline server_name."""
    t = TargetBinding.model_validate({
        "target": "x",
        "servers": [{"id": "one_box", "transport": "http", "url": "http://x/mcp"}],
        "capabilities": [
            {"capability": "approve_label", "server": "one_box"},
            {"capability": "submit_filing", "server": "one_box"},
        ],
    })
    a = t.capability_binding("approve_label")
    b = t.capability_binding("submit_filing")
    assert (a.server_name, a.dsn_secret_ref or "") == \
           (b.server_name, b.dsn_secret_ref or "")


def test_the_atlas_binding_uses_the_catalog():
    import pathlib

    from orgagents.spec.loader import load_binding

    root = pathlib.Path(__file__).resolve().parents[1]
    b = load_binding(str(root / "examples" / "atlas" / "atlas.binding.yaml"))
    target = b.targets[0]
    assert len(target.servers) >= 10
    # Every capability binds by reference, not inline.
    assert all(cb.server for cb in target.capabilities)
    # The two sides of alert_and_report sit on different servers, so the
    # separation survives the binding.
    aml = target.capability_binding("aml_triage")
    sar = target.capability_binding("sar_filing")
    assert aml.server_name != sar.server_name
