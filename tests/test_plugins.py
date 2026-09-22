"""The extension seam: anyone can add a target, runtime or cloud (ADR-0091).

Three things shipped as closed lists — the target registry, the `Runtime`
enum, the Terraform `PROFILES` dict — so every integration was a fork. They now
share one `Registry` with entry-point discovery and a `ProviderDescriptor`
saying what each implementation supports.

The descriptor is what keeps the core common and the edges pluggable: deep
agents has an interrupt gate and skills, the OpenAI Agents SDK has handoffs,
and a designer that hardcoded either would be wrong for the next one. So the
tests here care about two things — that a plugin can get in, and that what it
claims is checked.
"""
from __future__ import annotations

import dataclasses

import pytest

from orgagents.plugins import (
    FEATURES,
    PluginError,
    ProviderDescriptor,
    Registry,
    unknown_features,
)


def _descriptor(pid="x", **kw):
    kw.setdefault("title", "X")
    kw.setdefault("kind", "runtime")
    return ProviderDescriptor(id=pid, **kw)


# -- the descriptor is a contract, not a free-text tag ---------------------

def test_a_provider_cannot_claim_a_feature_the_platform_does_not_know():
    with pytest.raises(PluginError) as caught:
        _descriptor(supports=frozenset({"telepathy"}))
    assert "telepathy" in str(caught.value)


def test_the_feature_vocabulary_is_shared_and_documented():
    # Every feature carries a sentence; a bare word nobody can interpret is
    # not a contract.
    assert all(isinstance(v, str) and v for v in FEATURES.values())
    assert unknown_features(["tools", "subagents"]) == []


def test_a_descriptor_answers_what_it_supports():
    d = _descriptor(supports=frozenset({"handoffs", "tools"}))
    assert d.claims("handoffs")
    assert not d.claims("planning")
    assert d.as_dict()["supports"] == ["handoffs", "tools"]


# -- the registry ----------------------------------------------------------

def test_registering_a_plugin_never_silently_shadows_a_builtin():
    reg: Registry = Registry(name="target")
    reg.register("local", "builtin")
    with pytest.raises(PluginError) as caught:
        reg.register("local", "plugin")
    assert "already registered" in str(caught.value)
    # Overriding is possible, but only deliberately.
    reg.register("local", "plugin", replace=True)
    assert reg.get("local") == "plugin"


def test_loading_builtins_is_idempotent_and_order_independent():
    """The bug this replaces: an emptiness check meant registering your own
    implementation first silently cost you every built-in."""
    reg: Registry = Registry(name="target")
    reg.register("acme:onprem", "mine")          # a plugin arrives first
    calls = []

    def builtins():
        calls.append(1)
        reg.register("local", "builtin", replace=True)

    reg.load_builtins(builtins)
    reg.load_builtins(builtins)                  # again, for good measure
    assert calls == [1]                          # ran exactly once
    assert set(reg.ids()) == {"acme:onprem", "local"}   # nothing was lost


def test_a_missing_id_names_what_is_available():
    reg: Registry = Registry(name="runtime adapter")
    reg.register("echo", object())
    with pytest.raises(PluginError) as caught:
        reg.require("nope")
    assert "available: echo" in str(caught.value)


def test_a_broken_plugin_is_named_rather_than_crashing_the_platform():
    reg: Registry = Registry(name="target")
    reg.failures["acme:broken"] = "ImportError: no module named acme"
    with pytest.raises(PluginError) as caught:
        reg.require("whatever")
    assert "1 plugin(s) failed to load" in str(caught.value)


def test_supporting_answers_the_designers_question():
    reg: Registry = Registry(name="runtime")
    reg.register("deep", object(),
                 descriptor=_descriptor("deep", supports=frozenset({"planning"})))
    reg.register("openai", object(),
                 descriptor=_descriptor("openai", supports=frozenset({"handoffs"})))
    assert reg.supporting("planning") == ["deep"]
    assert reg.supporting("handoffs") == ["openai"]


# -- the three real registries are open ------------------------------------

def test_a_third_party_target_coexists_with_the_builtins():
    from orgagents.compiler.base import REGISTRY, register_builtin_targets

    class OnPrem:
        id = "test:onprem"

        def describe(self):
            return {"id": self.id, "title": "On-prem", "summary": "mine"}

        def generate(self, ir):
            return []

    register_builtin_targets()
    REGISTRY.register(OnPrem())
    try:
        ids = register_builtin_targets().ids()
        assert "test:onprem" in ids            # the plugin is there
        assert {"local", "terraform:gcp", "adk"} <= set(ids)   # so are builtins
    finally:
        REGISTRY.registry.items.pop("test:onprem", None)
        REGISTRY.registry.descriptors.pop("test:onprem", None)


def test_a_third_party_runtime_works_despite_the_closed_enum():
    """`Runtime` can only name what ships here, so the registry is keyed by
    the string it carries — which leaves room for somebody else's id."""
    from orgagents.runtime.adapters import (
        ADAPTERS,
        RuntimeAdapter,
        TurnOutput,
        adapter_for,
        register_builtin_adapters,
    )

    class AcmeAdapter(RuntimeAdapter):
        runtime = "acme_framework"

        def _run(self, prompt, history=None):
            return TurnOutput(text="acme")

    register_builtin_adapters()
    ADAPTERS.register("acme_framework", AcmeAdapter, third_party=True,
                      descriptor=_descriptor("acme_framework",
                                             supports=frozenset({"tools"})))
    try:
        harness = type("H", (), {"runtime": "acme_framework"})()
        agent = type("A", (), {"harness": harness})()
        assert adapter_for(agent) is AcmeAdapter
        assert ADAPTERS.descriptors["acme_framework"].third_party
    finally:
        ADAPTERS.items.pop("acme_framework", None)
        ADAPTERS.descriptors.pop("acme_framework", None)


def test_a_new_cloud_is_a_profile_plus_a_call():
    from orgagents.compiler.targets.terraform import (
        PROFILES,
        TerraformTarget,
        register_provider_profile,
    )

    profile = dataclasses.replace(PROFILES["gcp"], id="test_cloud",
                                  display="Test Cloud")
    register_provider_profile(profile)
    try:
        assert TerraformTarget("test_cloud").id == "terraform:test_cloud"
        with pytest.raises(ValueError):
            register_provider_profile(profile)      # no silent shadowing
    finally:
        PROFILES.pop("test_cloud", None)


# -- the builtins describe themselves honestly -----------------------------

def test_the_builtin_runtimes_differ_in_what_they_claim():
    """The whole point of the descriptor: these are not interchangeable."""
    from orgagents.runtime.adapters import register_builtin_adapters

    reg = register_builtin_adapters()
    assert reg.supporting("handoffs") == ["openai_agents_sdk"]
    assert reg.supporting("planning") == ["langchain_deepagents"]
    deep = reg.descriptors["langchain_deepagents"]
    openai = reg.descriptors["openai_agents_sdk"]
    assert deep.claims("interrupt_on") and not openai.claims("interrupt_on")
    # Both have the portable core.
    for core in ("instructions", "tools", "model"):
        assert deep.claims(core) and openai.claims(core)


def test_every_builtin_declares_only_known_features():
    from orgagents.compiler.base import register_builtin_targets
    from orgagents.runtime.adapters import register_builtin_adapters

    for reg in (register_builtin_adapters(), register_builtin_targets().registry):
        for descriptor in reg.descriptors.values():
            assert unknown_features(descriptor.supports) == [], descriptor.id
