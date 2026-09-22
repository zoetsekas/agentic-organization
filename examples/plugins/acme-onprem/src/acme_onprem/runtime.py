"""A third-party runtime adapter (ADR-0091).

`Runtime` is a closed enum, so it can only ever name the frameworks that ship
with orgagents. That does not close the seam: the adapter registry is keyed by
the **string** a `Runtime` member carries, which leaves room for an id the enum
will never hold. Acme's in-house agent framework registers as
`acme_framework` and dispatches like any built-in.

The interesting part is the descriptor. Acme's framework transfers control
between agents (a handoff) and never returns, which is a genuinely different
mechanism from a sub-agent that returns a result to its caller — so it claims
`handoffs` and not `subagents`. It has no planning tool and no interrupt gate,
so it claims neither.

That honesty is the whole point of the descriptor: the designer reads it to
decide which fields to offer, and a reader believes what it says. Claiming
`interrupt_on` here would put a human-in-the-loop field in front of somebody
whose runtime cannot pause.
"""
from __future__ import annotations

from typing import Any, Optional

from orgagents.plugins import ProviderDescriptor
from orgagents.runtime.adapters import RuntimeAdapter, TurnOutput

#: The id this adapter registers under. It is a plain string precisely because
#: the `Runtime` enum could never be made to hold it.
RUNTIME_ID = "acme_framework"


class AcmeRuntimeAdapter(RuntimeAdapter):
    """Run one turn on Acme's in-house agent framework."""

    runtime = RUNTIME_ID

    @staticmethod
    def descriptor() -> ProviderDescriptor:
        """What this framework really carries.

        Every word comes from `orgagents.plugins.FEATURES`; one the platform
        does not know is refused at construction, so a descriptor cannot drift
        into marketing.
        """
        return ProviderDescriptor(
            id=RUNTIME_ID,
            title="Acme in-house framework",
            kind="runtime",
            summary="Acme's own agent loop: handoffs and streaming, no "
                    "planning tool and no interrupt gate.",
            supports=frozenset({
                "instructions", "tools", "model",
                "handoffs",        # transfers control; does not return
                "streaming",
                "structured_output",
            }),
        )

    def _run(self, prompt: str,
             history: Optional[list[dict]] = None) -> TurnOutput:
        """One turn against the framework.

        The framework is imported lazily, exactly as the built-in adapters do
        theirs, so installing this plugin does not force the dependency on
        anybody who never selects it. The base class owns the budget and calls
        this; guardrails are enforced there rather than here, because a limit
        expressed once in the spec must mean the same thing on every runtime
        (ADR-0067).
        """
        from acme_agents import Session  # type: ignore  # noqa: F401

        session = Session(
            system_prompt=self.system_prompt,
            model=self.agent.harness.model.model,
            tools=list(self.tools),
            # A handoff target is another agent that takes over the
            # conversation. This is not a sub-agent call and the descriptor
            # says so.
            handoffs=[sa["name"] for sa in self.subagents],
        )
        reply = session.send(prompt, history=history or [])
        return TurnOutput(text=reply.text, tokens=reply.usage.total_tokens,
                          raw=reply)


def build() -> type[RuntimeAdapter]:
    """Entry-point factory.

    An entry point may resolve to the implementation itself or to a
    zero-argument factory returning one; this is the factory form.
    """
    return AcmeRuntimeAdapter


def register(registry: Any = None) -> None:
    """Register without installing, for a host that wires plugins by hand."""
    from orgagents.runtime.adapters import ADAPTERS, register_builtin_adapters

    register_builtin_adapters()
    target = registry if registry is not None else ADAPTERS
    target.register(RUNTIME_ID, AcmeRuntimeAdapter, third_party=True,
                    descriptor=AcmeRuntimeAdapter.descriptor(), replace=True)
