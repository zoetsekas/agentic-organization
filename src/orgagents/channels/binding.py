"""Binding a channel bridge, and refusing one that cannot carry the purpose.

Two refusals live here, and both are enforcement rather than advice
(ADR-0061 rules 2 and 3):

* a bridge that cannot mint or present a **non-human principal** does not bind
  at all — an agent speaking through a person's account makes every message in
  the channel a lie about who said it;
* a bridge that cannot deliver an **authenticated callback** may not bind
  `ChannelPurpose.APPROVE`, though it binds notify, report, ask and handoff
  happily. There is no fallback here on purpose: matching the word "approve"
  in chat text is forgeable by anyone who can type in the channel, so a bridge
  that cannot do better carries the purposes it can and not the one it cannot.

Bind time, not first call: by then somebody is already waiting for an answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..spec.model import ChannelPurpose
from .port import BridgeCapabilities, ChannelBridge, PrincipalKind


class BridgeRefused(RuntimeError):
    """Raised at bind time for a bridge that cannot satisfy rule 2, 3 or 6."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"channel bridge '{name or '<unnamed>'}' refused: {reason}")
        self.name = name
        self.reason = reason


@dataclass(frozen=True)
class BoundChannelBridge:
    """A bridge that passed the bind-time checks, and the evidence it did.

    Everything downstream takes one of these rather than a bare `ChannelBridge`,
    so an unchecked bridge cannot reach the runtime by being handed in a layer
    further down.
    """

    bridge: ChannelBridge
    capabilities: BridgeCapabilities
    tenant_id: str
    purposes: tuple[ChannelPurpose, ...] = field(default=())

    def carries(self, purpose: ChannelPurpose) -> bool:
        return purpose in self.purposes


def bind(
    bridge: ChannelBridge,
    *,
    tenant_id: str,
    purposes: Optional[Iterable[ChannelPurpose]] = None,
) -> BoundChannelBridge:
    """Check a bridge's declared identity, tenancy and purposes, or refuse it.

    `purposes` are the ones the binding asks this bridge to carry. Omitting
    them asks for everything the bridge claims it can do, which for a bridge
    without interactive callbacks is everything except approve.
    """
    capabilities = bridge.capabilities()
    name = capabilities.name

    # -- rule 2: an agent posts as itself or not at all --------------------
    if capabilities.principal_kind is PrincipalKind.HUMAN:
        raise BridgeRefused(
            name,
            "it can only post as a human principal, and an agent never speaks "
            "through a person's account (ADR-0061 rule 2)",
        )
    if capabilities.principal_kind is not PrincipalKind.SERVICE:
        raise BridgeRefused(
            name,
            "it declares no non-human principal, so every message would be "
            "anonymous or attributed to somebody who did not send it "
            "(ADR-0061 rule 2)",
        )
    if not capabilities.can_mint_bot_principal:
        raise BridgeRefused(
            name,
            "it cannot mint a non-human principal, so agents would have to "
            "share one hand-made account and the org chart's claim that each "
            "agent appears as itself would be false (ADR-0061 rule 2)",
        )
    if not capabilities.bot_principal_id:
        raise BridgeRefused(
            name,
            "it claims a service principal but names none, so there is "
            "nothing for the agent to be (ADR-0061 rule 2)",
        )

    # -- rule 6: one instance per tenant -----------------------------------
    if not tenant_id:
        raise BridgeRefused(name, "no tenant was named for this binding")
    if capabilities.tenant_id and capabilities.tenant_id != tenant_id:
        raise BridgeRefused(
            name,
            f"it serves tenant '{capabilities.tenant_id}', not '{tenant_id}'; "
            "a shared instance across tenants is a cross-tenant channel "
            "(ADR-0050, ADR-0061 rule 6)",
        )

    # -- rule 3: an approval is a click, not a word ------------------------
    supported = capabilities.supported_purposes()
    asked = tuple(purposes) if purposes is not None else supported
    if ChannelPurpose.APPROVE in asked and not capabilities.interactive_callbacks:
        raise BridgeRefused(
            name,
            "it cannot deliver an authenticated callback, so it may not carry "
            f"'{ChannelPurpose.APPROVE.value}'; it can still carry "
            f"{[p.value for p in supported]}. Degrading an approval to reading "
            "the word 'approve' out of chat text is not offered: a forged "
            "approval is worse than an unreachable one (ADR-0061 rule 3)",
        )
    unsupported = [p for p in asked if p not in supported]
    if unsupported:
        raise BridgeRefused(
            name,
            f"it does not support {[p.value for p in unsupported]}; it "
            f"supports {[p.value for p in supported]}",
        )

    return BoundChannelBridge(
        bridge=bridge,
        capabilities=capabilities,
        tenant_id=tenant_id,
        purposes=tuple(asked),
    )


__all__ = ["BoundChannelBridge", "BridgeRefused", "bind"]
