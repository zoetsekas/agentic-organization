"""The extension seam: registries anyone can add to without forking.

A design is vendor-neutral by construction (ADR-0002), but the *implementation*
phase is where vendors live — a deployment target, an agent framework, a cloud
provider. Those shipped as closed lists: a hardcoded target registry, a closed
`Runtime` enum, a module-level `PROFILES` dict. Adding one meant editing this
package, which makes every integration a fork and every fork a merge conflict.

This module is the one mechanism they now share:

* a **`Registry`** that holds implementations by id, refuses a silent
  overwrite, and discovers third-party entries from Python **entry points**, so
  `pip install orgagents-target-onprem` is the whole installation step;
* a **`ProviderDescriptor`** saying what an implementation actually supports,
  drawn from one shared `FEATURES` vocabulary.

The descriptor is what keeps the core common and the edges pluggable. Deep
agents has sub-agents, an interrupt gate and skills; the OpenAI Agents SDK has
handoffs and neither of the others. A designer that hardcoded either vendor's
field list would be wrong for the next one, so it asks the descriptor instead
and renders what that provider claims — the same reason a target publishes a
conformance report rather than pretending (ADR-0073).

Registration is explicit and additive. A plugin that silently replaced a
built-in would change what a design compiles to without anybody deciding that,
so `register` refuses unless asked to `replace`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as _dc_replace
from typing import Any, Callable, Generic, Iterable, Optional, TypeVar

T = TypeVar("T")


class PluginError(RuntimeError):
    """A plugin could not be registered, found or loaded."""


# --------------------------------------------------------------------------
# What an implementation can claim to support
# --------------------------------------------------------------------------

#: The shared vocabulary. A provider claims a subset; the designer and the
#: conformance reports read it rather than hardcoding a vendor's field list.
#: Anything not named here is not a thing this platform knows how to show, so
#: adding a word is a deliberate act — it is the contract, not a free-text tag.
FEATURES: dict[str, str] = {
    # The portable core. Every agent runtime has these.
    "instructions": "A system prompt authored per agent",
    "tools": "Named tools the agent may call",
    "model": "A model chosen per agent",
    # Delegation — the two mechanisms differ and are not interchangeable.
    "subagents": "Spawned child agents that return to their caller",
    "handoffs": "Transfer of control to another agent that does not return",
    # Governance the framework can hold itself.
    "interrupt_on": "A human-in-the-loop gate that pauses before a tool",
    "filesystem_permissions": "Governed virtual-filesystem access",
    "structured_output": "A schema the reply must satisfy",
    # Longer-lived context.
    "skills": "Packaged, on-demand behaviours",
    "long_term_memory": "Memory that survives a session",
    "planning": "An explicit task plan the agent maintains",
    # Operational.
    "middleware": "Interceptors around the model call",
    "streaming": "Incremental output as the run proceeds",
    "durable_execution": "Checkpointed runs that resume after a failure",
}


def unknown_features(names: Iterable[str]) -> list[str]:
    """Feature words this platform does not know. Empty means all valid."""
    return sorted(n for n in names if n not in FEATURES)


@dataclass(frozen=True)
class ProviderDescriptor:
    """What one implementation is and what it supports.

    `supports` is the honest list: a provider that claims `interrupt_on` must
    actually pause for a human, because the designer will offer the field and a
    reader will believe it.
    """

    id: str
    title: str
    kind: str                       # target | runtime | provider_profile
    summary: str = ""
    supports: frozenset[str] = field(default_factory=frozenset)
    #: Set by the registry: False for anything that shipped with the platform.
    third_party: bool = False

    def __post_init__(self) -> None:
        bad = unknown_features(self.supports)
        if bad:
            raise PluginError(
                f"provider '{self.id}' claims unknown feature(s) {bad}; "
                f"known features are {sorted(FEATURES)}")

    def claims(self, feature: str) -> bool:
        return feature in self.supports

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "summary": self.summary,
            "supports": sorted(self.supports),
            "third_party": self.third_party,
        }


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------


@dataclass
class Registry(Generic[T]):
    """Implementations by id, open to plugins.

    `name` is what a person reads in an error; `entry_point_group` is where
    third-party entries are discovered. Discovery is separate from built-in
    registration and both are idempotent, so the order they happen in — and
    how often — cannot change the result. (An earlier registry used "is the
    registry empty?" as its did-I-initialise check, which meant registering one
    plugin first silently cost you every built-in.)
    """

    name: str
    entry_point_group: str = ""
    items: dict[str, T] = field(default_factory=dict)
    descriptors: dict[str, ProviderDescriptor] = field(default_factory=dict)
    #: Plugins that declared themselves but could not be loaded.
    failures: dict[str, str] = field(default_factory=dict)
    _builtins_loaded: bool = False
    _discovered: bool = False

    # -- registration ------------------------------------------------------

    def register(self, key: str, item: T, *,
                 descriptor: Optional[ProviderDescriptor] = None,
                 replace: bool = False, third_party: bool = False) -> T:
        """Add an implementation. Refuses to shadow an existing id silently."""
        if not key:
            raise PluginError(f"a {self.name} needs a non-empty id")
        if key in self.items and not replace:
            raise PluginError(
                f"{self.name} '{key}' is already registered; pass "
                f"replace=True to override it deliberately")
        self.items[key] = item
        if descriptor is not None:
            if third_party and not descriptor.third_party:
                descriptor = _dc_replace(descriptor, third_party=True)
            self.descriptors[key] = descriptor
        return item

    def load_builtins(self, loader: Callable[[], None]) -> None:
        """Run `loader` once. Idempotent regardless of what else is present."""
        if self._builtins_loaded:
            return
        self._builtins_loaded = True
        loader()

    def discover(self) -> list[str]:
        """Register third-party implementations from entry points, once.

        A plugin that fails to import must not take the platform down with it:
        the id is skipped and named, because a broken integration is the
        plugin author's problem and a silent one is everybody's.
        """
        if self._discovered or not self.entry_point_group:
            return []
        self._discovered = True
        loaded: list[str] = []
        try:
            from importlib.metadata import entry_points
        except ImportError:                       # pragma: no cover
            return []
        try:
            found = entry_points(group=self.entry_point_group)
        except TypeError:                         # pragma: no cover - py<3.10
            found = entry_points().get(self.entry_point_group, [])
        for entry in found:
            try:
                factory = entry.load()
                item = factory() if callable(factory) else factory
                descriptor = getattr(item, "descriptor", None)
                if callable(descriptor):
                    descriptor = descriptor()
                self.register(entry.name, item, descriptor=descriptor,
                              replace=False, third_party=True)
                loaded.append(entry.name)
            except Exception as exc:              # noqa: BLE001 - see docstring
                self.failures[entry.name] = str(exc)
        return loaded

    # -- lookup ------------------------------------------------------------

    def get(self, key: str) -> Optional[T]:
        return self.items.get(key)

    def require(self, key: str) -> T:
        """Fetch, or fail naming what *is* available — a bare KeyError tells
        an integrator nothing about which id they should have used."""
        item = self.items.get(key)
        if item is None:
            known = ", ".join(self.ids()) or "none registered"
            extra = ""
            if self.failures:
                extra = (f" ({len(self.failures)} plugin(s) failed to load: "
                         f"{', '.join(sorted(self.failures))})")
            raise PluginError(
                f"unknown {self.name} '{key}'; available: {known}{extra}")
        return item

    def ids(self) -> list[str]:
        return sorted(self.items)

    def describe_all(self) -> list[dict[str, Any]]:
        return [self.descriptors[k].as_dict()
                for k in self.ids() if k in self.descriptors]

    def supporting(self, feature: str) -> list[str]:
        """Ids whose descriptor claims a feature — what a designer asks to
        decide whether to offer a field."""
        return [k for k in self.ids()
                if k in self.descriptors and self.descriptors[k].claims(feature)]
