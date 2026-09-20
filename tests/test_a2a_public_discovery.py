"""A public agent card is fetchable; the waiver stops exactly there.

ADR-0058 v1.1.0. The first version of the rule made a card fetch a full
endpoint call, which meant an endpoint with no `secret_ref` could not fetch
even a public card — so no public peer could be discovered at all. The waiver
covers one check on one body-less read, and these tests exist to keep it that
narrow: a waiver that quietly grew would be worse than the flaw it fixed.
"""
import pytest

from orgagents.runtime.endpoints import CallerBoundary, call_endpoint


class _Endpoint:
    def __init__(self, secret_ref=None, host="peer.example"):
        self.id = "peer"
        self.trust = "partner"
        self.send_data_classes = ["public"]
        self.secret_ref = secret_ref
        self.host = host


def _caller(allowlist=("peer.example",), network="egress_allowlist"):
    return CallerBoundary(
        agent_id="analyst", tenant_id="acme", network=network,
        egress_allowlist=list(allowlist), secret_refs=["analyst-secret"],
    )


def _transport(url, payload, headers=None):
    return {"name": "Peer", "skills": []}


def test_a_public_card_can_be_fetched_without_a_credential():
    result = call_endpoint(
        _Endpoint(secret_ref=None), "https://peer.example/.well-known/agent-card.json",
        None, caller=_caller(), discovery=True, transport=_transport,
    )
    assert result.ok, result.detail
    assert "credential_waived_for_discovery" in result.checks


def test_the_waiver_does_not_apply_to_a_call_with_a_body():
    # A message is not discovery, whatever the caller labels it.
    result = call_endpoint(
        _Endpoint(secret_ref=None), "https://peer.example/rpc",
        {"method": "SendMessage"}, caller=_caller(), discovery=True,
        transport=_transport,
    )
    assert not result.ok
    assert result.refusal == "no_credential"


def test_the_waiver_does_not_apply_without_the_discovery_flag():
    result = call_endpoint(
        _Endpoint(secret_ref=None), "https://peer.example/.well-known/agent-card.json",
        None, caller=_caller(), transport=_transport,
    )
    assert not result.ok
    assert result.refusal == "no_credential"


def test_discovery_is_still_subject_to_the_egress_allowlist():
    result = call_endpoint(
        _Endpoint(secret_ref=None), "https://elsewhere.example/.well-known/agent-card.json",
        None, caller=_caller(), discovery=True, transport=_transport,
    )
    assert not result.ok
    assert result.refusal != "no_credential"


def test_an_isolated_agent_still_cannot_discover_anything():
    result = call_endpoint(
        _Endpoint(secret_ref=None), "https://peer.example/.well-known/agent-card.json",
        None, caller=_caller(network="none"), discovery=True, transport=_transport,
    )
    assert not result.ok


def test_a_declared_credential_is_still_used_for_discovery():
    # The waiver is for endpoints that have no credential, not a way to skip
    # one that exists.
    result = call_endpoint(
        _Endpoint(secret_ref="peer-secret"),
        "https://peer.example/.well-known/agent-card.json", None,
        caller=_caller(), discovery=True, transport=_transport,
    )
    assert result.ok
    assert "credential" in result.checks
    assert "credential_waived_for_discovery" not in result.checks


def test_discovery_never_borrows_the_callers_credential():
    endpoint = _Endpoint(secret_ref="analyst-secret")  # the caller's own
    result = call_endpoint(
        endpoint, "https://peer.example/.well-known/agent-card.json", None,
        caller=_caller(), discovery=True, transport=_transport,
    )
    assert not result.ok
    assert result.refusal == "credential_inherited"
