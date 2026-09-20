"""Calling something outside our boundary, under the endpoint rules (ADR-0030).

`AgentEndpoint` already declares *whether* a call outside the organization is
allowed and on what terms; `spec.validate` checks those terms at design time.
This module is the call-time twin: one place where an outbound call is
actually gated, so that everything which leaves the agent's boundary — an
external agent, a service workflow engine (ADR-0056) — passes the same checks
instead of each growing its own.

The order of the checks is the point. Tenant, egress and classification are
decided *before* the transport is touched: a refusal that happens after the
bytes are on the wire is not a refusal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from ..guardrails import GuardrailEngine, GuardrailResult
from ..models import Agent
from ..spec.model import EndpointTrust, GuardrailKind

# What a transport is: it is handed the endpoint URL, the payload and the
# *name* of the credential to use. It never receives a secret value, because
# nothing in this process resolves one (ADR-0015).
Transport = Callable[[str, Any, Optional[str]], Any]


@dataclass(frozen=True)
class CallerBoundary:
    """The boundary the caller sits inside, frozen for the duration of a call.

    Frozen on purpose: the thing being invoked must not be able to widen the
    boundary it is being invoked from.
    """

    agent_id: str
    tenant_id: Optional[str] = None
    network: str = "none"
    egress_allowlist: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()
    permissions: frozenset[str] = frozenset()
    data_classes: tuple[str, ...] = ()

    @property
    def can_egress(self) -> bool:
        return self.network != "none"


@dataclass
class EndpointCallResult:
    ok: bool
    value: Any = None
    refusal: Optional[str] = None          # machine-readable reason code
    detail: str = ""
    guardrail: Optional[GuardrailResult] = None
    checks: list[str] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return not self.ok


def boundary_for(
    agent: Agent,
    *,
    tenant_id: Optional[str] = None,
    sandbox_runner: Optional[Any] = None,
    permissions: Optional[Sequence[str]] = None,
) -> CallerBoundary:
    """Read an agent's boundary off the agent, never off the thing calling."""
    network, allowlist, own_secrets = "none", (), ()
    if agent.sandbox is not None and sandbox_runner is not None:
        try:
            template = sandbox_runner.resolve(agent.sandbox)
        except KeyError:
            template = None
        if template is not None:
            network = template.network
            allowlist = tuple(template.egress_allowlist)
            own_secrets = tuple(template.secret_refs)
    perms = tuple(permissions or [t.name for t in agent.harness.tools])
    return CallerBoundary(
        agent_id=agent.id,
        tenant_id=tenant_id,
        network=network,
        egress_allowlist=allowlist,
        # The agent's own credentials, listed so that nothing downstream can
        # quietly reuse one of them as if it were its own.
        secret_refs=own_secrets,
        permissions=frozenset(perms),
        data_classes=tuple(
            (agent.memory or {}).get("readable_data_classes", [])
        ),
    )


def host_of(url: str) -> str:
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split("@")[-1].split(":", 1)[0]


def host_allowed(host: str, allowlist: Sequence[str]) -> bool:
    for pattern in allowlist:
        if pattern == host:
            return True
        if pattern.startswith("*.") and host.endswith(pattern[1:]):
            return True
    return False


def call_endpoint(
    endpoint: Any,
    url: str,
    payload: Any,
    *,
    caller: CallerBoundary,
    discovery: bool = False,
    transport: Optional[Transport] = None,
    guardrails: Optional[GuardrailEngine] = None,
    payload_data_classes: Sequence[str] = (),
    endpoint_tenant: Optional[str] = None,
    approval_granted: bool = False,
) -> EndpointCallResult:
    """Send `payload` to `endpoint`, or refuse and say which rule refused it."""
    checks: list[str] = []

    # 1. Tenant scoping (ADR-0050). A shared instance is a cross-tenant channel.
    checks.append("tenant")
    if endpoint_tenant is not None and endpoint_tenant != caller.tenant_id:
        return EndpointCallResult(
            False, refusal="cross_tenant", checks=checks,
            detail=f"endpoint belongs to tenant '{endpoint_tenant}', caller to "
                   f"'{caller.tenant_id}'",
        )

    # 2. Egress. A `network: none` sandbox reaches nothing; that is the
    #    environment class doing its job, not an obstacle to route around.
    checks.append("egress")
    host = host_of(url)
    if not caller.can_egress:
        return EndpointCallResult(
            False, refusal="egress_blocked", checks=checks,
            detail=f"agent '{caller.agent_id}' has network: none and cannot "
                   f"reach '{host}'",
        )
    if caller.network == "egress_allowlist" and not host_allowed(
        host, caller.egress_allowlist
    ):
        return EndpointCallResult(
            False, refusal="egress_not_allowlisted", checks=checks,
            detail=f"'{host}' is not on the egress allowlist of "
                   f"'{caller.agent_id}'",
        )

    # 3. Classification, before anything leaves.
    checks.append("data_classification")
    sendable = set(getattr(endpoint, "send_data_classes", []) or [])
    refused = [dc for dc in payload_data_classes if dc not in sendable]
    if refused:
        return EndpointCallResult(
            False, refusal="data_class_refused", checks=checks,
            detail=f"endpoint '{getattr(endpoint, 'id', url)}' may not be sent: "
                   f"{', '.join(sorted(refused))}",
        )

    # 4. Credentials. The callee authenticates as itself.
    #
    # Discovery is the one exception, and it is narrow (ADR-0058 v1.1.0): a
    # public agent card is meant to be fetched unauthenticated, so requiring a
    # credential made every public peer undiscoverable. The waiver applies only
    # to a body-less read, it waives *only* this check — tenant, allowlist and
    # the guardrail still run — and the card remains untrusted data.
    checks.append("credential")
    secret_ref = getattr(endpoint, "secret_ref", None)
    if not secret_ref and discovery and payload is None:
        checks[-1] = "credential_waived_for_discovery"
    elif not secret_ref:
        return EndpointCallResult(
            False, refusal="no_credential", checks=checks,
            detail="the endpoint declares no secret_ref of its own",
        )
    if secret_ref and secret_ref in caller.secret_refs:
        return EndpointCallResult(
            False, refusal="credential_inherited", checks=checks,
            detail=f"'{secret_ref}' is the calling agent's credential",
        )

    # 5. Approval, where the trust class demands one.
    checks.append("approval")
    if getattr(endpoint, "requires_approval", False) and not approval_granted:
        return EndpointCallResult(
            False, refusal="approval_required", checks=checks,
            detail=f"endpoint '{getattr(endpoint, 'id', url)}' requires approval",
        )

    # 6. The call itself.
    checks.append("transport")
    if transport is None:
        return EndpointCallResult(
            False, refusal="no_transport", checks=checks,
            detail="no transport is configured for outbound endpoint calls",
        )
    try:
        response = transport(url, payload, secret_ref)
    except Exception as e:  # a failure out there is data too, never a crash here
        return EndpointCallResult(
            False, refusal="transport_error", checks=checks,
            detail=f"{type(e).__name__}: {e}",
        )

    # 7. The answer is untrusted input and crosses the tool-output boundary
    #    (ADR-0035) before it re-enters context.
    checks.append("guardrail:tool_output")
    trust = getattr(endpoint, "trust", EndpointTrust.EXTERNAL)
    if guardrails is not None and (
        getattr(endpoint, "treat_output_as_data", True)
        or trust is not EndpointTrust.INTERNAL
    ):
        verdict = guardrails.check(
            response if isinstance(response, str) else str(response),
            GuardrailKind.TOOL_OUTPUT,
            context={"endpoint": getattr(endpoint, "id", url), "source": "endpoint"},
        )
        if verdict.blocked:
            return EndpointCallResult(
                False, refusal="guardrail_blocked", checks=checks,
                detail=verdict.reason(), guardrail=verdict,
            )
        return EndpointCallResult(
            True, value=verdict.content if isinstance(response, str) else response,
            checks=checks, guardrail=verdict,
        )
    return EndpointCallResult(True, value=response, checks=checks)
