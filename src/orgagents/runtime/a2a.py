"""A2A (Agent2Agent) as a transport beneath the endpoint rules (ADR-0058).

**The subset implemented here, stated plainly.** A2A 1.0.0 defines three
functionally-equivalent bindings (JSON-RPC 2.0 over HTTP(S), gRPC, REST), nine
operations, SSE streaming and asynchronous push notifications. This module
implements:

* the **JSON-RPC 2.0 binding only** — not gRPC, not REST;
* **`SendMessage`, `GetTask`, `CancelTask`** and **Agent Card discovery** at
  ``/.well-known/agent-card.json``;
* the nine documented error codes, as data;
* the two task states this platform must react to, ``input-required`` and
  ``auth-required``.

Not implemented, and deliberately not faked: ``SendStreamingMessage``,
``ListTasks``, ``SubscribeToTask``, push-notification configuration CRUD and
``GetExtendedAgentCard``. Inbound — serving our own card — is WS-019 M5 and the
default is that no agent is exposed (ADR-0058 rule 6). Calling an unimplemented
operation raises `UnsupportedOperation` here rather than guessing a wire shape.

Nothing in this file is a governance decision. Every outbound call, **including
the card fetch** (rule 3), goes through `runtime.endpoints.call_endpoint`, so
tenant, egress, classification, credential, approval and the tool-output
guardrail all run in their existing order, before the transport is touched.

The transport is injected. No HTTP client is imported and `a2a-sdk` is not a
dependency: nothing here can be verified against a real peer in this
environment, and a fake peer is how it is tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from ..guardrails import GuardrailEngine
from ..humans import RoutingPlan, choose_channel, plan as routing_plan
from ..spec.model import ChannelPurpose, ChannelSpec, EndpointTrust
from .endpoints import CallerBoundary, EndpointCallResult, Transport, call_endpoint

#: The protocol this binding names. It lives in the runtime vocabulary, never
#: in `orgagents.spec` — a spec says an endpoint exists, not what wire it
#: speaks (ADR-0002, ADR-0058 rule 1).
A2A_PROTOCOL = "a2a"
A2A_SPEC_VERSION = "1.0.0"

#: Transport variants the protocol defines, and the one we actually speak.
A2A_TRANSPORTS = ("jsonrpc", "grpc", "rest")
IMPLEMENTED_TRANSPORT = "jsonrpc"

AGENT_CARD_PATH = "/.well-known/agent-card.json"

JSONRPC_VERSION = "2.0"

#: Documented A2A error codes. Names are the spec's; they are reported, never
#: acted on as instructions.
A2A_ERROR_NAMES: dict[int, str] = {
    -32001: "TaskNotFoundError",
    -32002: "TaskNotCancelableError",
    -32003: "PushNotificationNotSupportedError",
    -32004: "UnsupportedOperationError",
    -32005: "ContentTypeNotSupportedError",
    -32006: "InvalidAgentResponseError",
    -32007: "ExtendedAgentCardNotConfiguredError",
    -32008: "ExtensionSupportRequiredError",
    -32009: "VersionNotSupportedError",
}

#: Operations the spec defines. Only the first three are implemented.
A2A_OPERATIONS = (
    "SendMessage",
    "SendStreamingMessage",
    "GetTask",
    "ListTasks",
    "CancelTask",
    "SubscribeToTask",
    "GetExtendedAgentCard",
)
IMPLEMENTED_OPERATIONS = ("SendMessage", "GetTask", "CancelTask")

# TODO(a2a-wire): the JSON-RPC *method name strings* for each operation, the
# exact Message/Part/Artifact field names, and the full task-state vocabulary
# are not in the spec extract available in this repository (a2a-protocol.org is
# blocked by the egress proxy and `a2a-sdk` is not installed). Everything below
# this line is therefore **our** model of the wire, in our own types, using the
# documented operation names as method names. Confirm against A2A 1.0.0 before
# pointing this at a real peer; the same stance `sandboxes/openshell.py` takes
# for an undocumented schema.
METHOD_NAMES: dict[str, str] = {op: op for op in IMPLEMENTED_OPERATIONS}

#: Task states we must react to. The lifecycle has more; we model only the two
#: the ADR gives us plus a catch-all, rather than inventing the rest.
STATE_INPUT_REQUIRED = "input-required"
STATE_AUTH_REQUIRED = "auth-required"
HUMAN_ROUTED_STATES = (STATE_INPUT_REQUIRED, STATE_AUTH_REQUIRED)

#: Security scheme families A2A cards may advertise. Listed so a card's claim
#: can be *named* in a report; never so one can select a credential.
CARD_SECURITY_SCHEMES = ("apiKey", "http", "oauth2", "openIdConnect")


class UnsupportedOperation(NotImplementedError):
    """An A2A operation outside the implemented subset was asked for."""


@dataclass(frozen=True)
class A2AError:
    """A JSON-RPC error from the peer, as data."""

    code: int
    message: str = ""

    @property
    def name(self) -> str:
        return A2A_ERROR_NAMES.get(self.code, "JsonRpcError")


@dataclass(frozen=True)
class AgentCard:
    """A remote party's self-description. **Claims, not configuration.**

    Deliberately inert: this holds strings for display and audit and exposes
    no method that could be mistaken for a decision. Nothing reads `skills`,
    `security_schemes` or `extensions` to widen anything — see
    `IGNORED_CARD_CLAIMS` and `A2AClient.fetch_agent_card` (ADR-0058 rule 3).
    """

    name: str = ""
    description: str = ""
    version: str = ""
    url: str = ""
    skills: tuple[str, ...] = ()
    security_schemes: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def parse(payload: Any) -> "AgentCard":
        """Read a card defensively; a malformed card is an empty one."""
        data = payload if isinstance(payload, Mapping) else {}

        def names(key: str) -> tuple[str, ...]:
            value = data.get(key)
            if isinstance(value, Mapping):
                return tuple(str(k) for k in value)
            if isinstance(value, (list, tuple)):
                out = []
                for item in value:
                    if isinstance(item, Mapping):
                        out.append(str(item.get("id") or item.get("name") or item))
                    else:
                        out.append(str(item))
                return tuple(out)
            return ()

        return AgentCard(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            version=str(data.get("version", "")),
            url=str(data.get("url", "")),
            skills=names("skills"),
            security_schemes=names("securitySchemes") or names("security_schemes"),
            extensions=names("extensions"),
            raw=dict(data),
        )


#: Keys a card may carry that this platform refuses to read as configuration.
#: Refusing them by name makes the refusal reviewable instead of implicit.
IGNORED_CARD_CLAIMS = (
    "trust",
    "trustLevel",
    "trust_class",
    "send_data_classes",
    "sendDataClasses",
    "dataClasses",
    "securitySchemes",
    "security_schemes",
    "security",
    "secret_ref",
    "secretRef",
    "credentials",
    "requiresApproval",
    "requires_approval",
    "extensions",
    "skills",
)


def ignored_claims(card: AgentCard) -> tuple[str, ...]:
    """Which escalating claims this card made, so a report can say so."""
    return tuple(k for k in IGNORED_CARD_CLAIMS if k in (card.raw or {}))


@dataclass(frozen=True)
class A2AProtocolBinding:
    """Binding-layer configuration naming the protocol (ADR-0058 rule 1).

    A binding names `a2a`, its transport variant and the peer's base URL; the
    spec continues to say only that an endpoint exists, what it is trusted as
    and what may be sent to it.
    """

    endpoint: str                          # the endpoint id in the spec
    base_url: str = ""
    protocol: str = A2A_PROTOCOL
    transport: str = IMPLEMENTED_TRANSPORT
    protocol_version: str = A2A_SPEC_VERSION
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.protocol != A2A_PROTOCOL:
            raise ValueError(f"not an A2A binding: protocol={self.protocol!r}")
        if self.transport != IMPLEMENTED_TRANSPORT:
            # Saying "we support A2A" while speaking one binding is the honesty
            # problem ADR-0058 names; refuse rather than imply parity.
            raise UnsupportedOperation(
                f"only the '{IMPLEMENTED_TRANSPORT}' binding is implemented; "
                f"'{self.transport}' is defined by A2A {A2A_SPEC_VERSION} but "
                f"not here"
            )

    @property
    def card_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{AGENT_CARD_PATH}"

    @property
    def rpc_url(self) -> str:
        return self.base_url.rstrip("/")


@dataclass
class RemoteTask:
    """An A2A Task as we model it, mapped to exactly one of our sessions."""

    id: str
    state: str = ""
    session_id: str = ""
    messages: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def needs_human(self) -> bool:
        return self.state in HUMAN_ROUTED_STATES


@dataclass
class A2AResult:
    """What one A2A operation produced, governance verdict included."""

    ok: bool
    call: EndpointCallResult
    task: Optional[RemoteTask] = None
    card: Optional[AgentCard] = None
    error: Optional[A2AError] = None
    routing: Optional["HumanRoutingRequest"] = None
    ignored_card_claims: tuple[str, ...] = ()

    @property
    def refusal(self) -> Optional[str]:
        return self.call.refusal

    @property
    def detail(self) -> str:
        return self.call.detail


@dataclass
class HumanRoutingRequest:
    """A remote task that stopped and asked us for something (rule 5).

    `input-required` and `auth-required` are questions for a person. The
    runtime does not answer either: it carries no credential here, and there is
    no code path that mints or forwards one to satisfy a remote prompt.
    """

    task_id: str
    session_id: str
    state: str
    endpoint_id: str
    prompt: str = ""
    purpose: ChannelPurpose = ChannelPurpose.APPROVE
    plan: Optional[RoutingPlan] = None
    #: Invariant, asserted in tests: never anything but None.
    credential: None = None

    @property
    def satisfied_automatically(self) -> bool:
        return False


def route_to_human(
    request: HumanRoutingRequest,
    channels: Sequence[ChannelSpec],
    *,
    now: Optional[datetime] = None,
    preferred: Optional[list[str]] = None,
    data_classes: Optional[list[str]] = None,
) -> HumanRoutingRequest:
    """Attach a routing plan (ADR-0021) to a request; answer nothing."""
    channel = choose_channel(
        list(channels), request.purpose, preferred=preferred, now=now
    )
    if channel is None:
        return request
    request.plan = routing_plan(
        channel, request.purpose, now=now, data_classes=data_classes or []
    )
    return request


class A2AClient:
    """The JSON-RPC subset of A2A, over the governed endpoint path.

    The endpoint object is read, never written: a call cannot widen the
    boundary it is made from, and neither can a card fetched over it.
    """

    def __init__(
        self,
        endpoint: Any,
        binding: A2AProtocolBinding,
        caller: CallerBoundary,
        *,
        transport: Optional[Transport] = None,
        guardrails: Optional[GuardrailEngine] = None,
        sessions: Optional[Any] = None,
        endpoint_tenant: Optional[str] = None,
    ) -> None:
        self.endpoint = endpoint
        self.binding = binding
        self.caller = caller
        self.transport = transport
        self.guardrails = guardrails
        self.sessions = sessions
        self.endpoint_tenant = endpoint_tenant
        self._card: Optional[AgentCard] = None
        # One session per remote task (ADR-0058 mapping), memoised so a task
        # seen twice does not become two sessions.
        self._task_sessions: dict[str, str] = {}
        self._rpc_id = 0

    # -- properties a card must never change -------------------------------

    @property
    def trust(self) -> EndpointTrust:
        return getattr(self.endpoint, "trust", EndpointTrust.EXTERNAL)

    @property
    def send_data_classes(self) -> tuple[str, ...]:
        return tuple(getattr(self.endpoint, "send_data_classes", []) or [])

    @property
    def secret_ref(self) -> Optional[str]:
        """The credential is the endpoint's, chosen in the spec. Full stop."""
        return getattr(self.endpoint, "secret_ref", None)

    @property
    def card(self) -> Optional[AgentCard]:
        return self._card

    # -- plumbing ----------------------------------------------------------

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _envelope(self, operation: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if operation not in IMPLEMENTED_OPERATIONS:
            raise UnsupportedOperation(
                f"'{operation}' is an A2A {A2A_SPEC_VERSION} operation that "
                f"this client does not implement; implemented: "
                f"{', '.join(IMPLEMENTED_OPERATIONS)}"
            )
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": self._next_id(),
            "method": METHOD_NAMES[operation],
            "params": dict(params),
        }

    def _call(
        self,
        url: str,
        payload: Any,
        *,
        data_classes: Sequence[str] = (),
        approval_granted: bool = False,
        discovery: bool = False,
    ) -> EndpointCallResult:
        return call_endpoint(
            self.endpoint,
            url,
            payload,
            caller=self.caller,
            discovery=discovery,
            transport=self.transport,
            guardrails=self.guardrails,
            payload_data_classes=data_classes,
            endpoint_tenant=self.endpoint_tenant,
            approval_granted=approval_granted,
        )

    # -- discovery ---------------------------------------------------------

    def fetch_agent_card(self, *, approval_granted: bool = False) -> A2AResult:
        """Fetch `/.well-known/agent-card.json` — an egress event like any other.

        The card comes back as data: it is parsed, kept for display, and
        changes nothing about trust, data classes or credentials.
        """
        result = self._call(
            self.binding.card_url, None, approval_granted=approval_granted,
            discovery=True,
        )
        if result.refused:
            return A2AResult(False, result)
        card = AgentCard.parse(result.value)
        self._card = card
        return A2AResult(
            True, result, card=card, ignored_card_claims=ignored_claims(card)
        )

    # -- operations --------------------------------------------------------

    def send_message(
        self,
        text: str,
        *,
        task_id: Optional[str] = None,
        data_classes: Sequence[str] = (),
        approval_granted: bool = False,
        parent_session_id: Optional[str] = None,
    ) -> A2AResult:
        params: dict[str, Any] = {
            # Our shape for a message; see the TODO above.
            "message": {"role": "user", "parts": [{"kind": "text", "text": text}]},
        }
        if task_id:
            params["taskId"] = task_id
        return self._operation(
            "SendMessage", params,
            data_classes=data_classes,
            approval_granted=approval_granted,
            parent_session_id=parent_session_id,
        )

    def get_task(self, task_id: str, *, approval_granted: bool = False) -> A2AResult:
        return self._operation(
            "GetTask", {"id": task_id}, approval_granted=approval_granted
        )

    def cancel_task(self, task_id: str, *, approval_granted: bool = False) -> A2AResult:
        return self._operation(
            "CancelTask", {"id": task_id}, approval_granted=approval_granted
        )

    def _operation(
        self,
        operation: str,
        params: Mapping[str, Any],
        *,
        data_classes: Sequence[str] = (),
        approval_granted: bool = False,
        parent_session_id: Optional[str] = None,
    ) -> A2AResult:
        envelope = self._envelope(operation, params)
        result = self._call(
            self.binding.rpc_url, envelope,
            data_classes=data_classes, approval_granted=approval_granted,
        )
        if result.refused:
            return A2AResult(False, result)
        return self._interpret(result, parent_session_id=parent_session_id)

    # -- reading the peer's answer ----------------------------------------

    def _interpret(
        self, result: EndpointCallResult, *, parent_session_id: Optional[str] = None
    ) -> A2AResult:
        body = result.value if isinstance(result.value, Mapping) else {}
        error = body.get("error")
        if isinstance(error, Mapping):
            return A2AResult(
                False, result,
                error=A2AError(int(error.get("code", 0)), str(error.get("message", ""))),
            )
        payload = body.get("result")
        payload = payload if isinstance(payload, Mapping) else {}
        task = self._task_from(payload, parent_session_id=parent_session_id)
        routing = None
        if task is not None and task.needs_human:
            routing = HumanRoutingRequest(
                task_id=task.id,
                session_id=task.session_id,
                state=task.state,
                endpoint_id=str(getattr(self.endpoint, "id", "")),
                prompt=task.messages[-1] if task.messages else "",
                # Both states are a person's call; `auth-required` especially,
                # because the alternative is handing a remote party a secret.
                purpose=ChannelPurpose.APPROVE,
            )
        return A2AResult(True, result, task=task, routing=routing)

    def _task_from(
        self, payload: Mapping[str, Any], *, parent_session_id: Optional[str] = None
    ) -> Optional[RemoteTask]:
        task_id = payload.get("id") or payload.get("taskId")
        if not task_id:
            return None
        status = payload.get("status")
        state = ""
        prompt_parts: list[str] = []
        if isinstance(status, Mapping):
            state = str(status.get("state", ""))
            message = status.get("message")
            if isinstance(message, Mapping):
                for part in message.get("parts") or []:
                    if isinstance(part, Mapping) and "text" in part:
                        prompt_parts.append(str(part["text"]))
        elif isinstance(status, str):
            state = status
        session_id = self.session_for_task(
            str(task_id), parent_session_id=parent_session_id
        )
        return RemoteTask(
            id=str(task_id),
            state=state,
            session_id=session_id,
            messages=tuple(prompt_parts),
            raw=dict(payload),
        )

    # -- traceability ------------------------------------------------------

    def session_for_task(
        self, task_id: str, *, parent_session_id: Optional[str] = None
    ) -> str:
        """One remote task, exactly one session — created once, reused after."""
        existing = self._task_sessions.get(task_id)
        if existing is not None:
            return existing
        if self.sessions is None:
            return ""
        session = self.sessions.create(
            self.caller.agent_id,
            title=f"a2a task {task_id}",
            created_by=self.caller.agent_id,
            parent_session_id=parent_session_id,
            tags=[A2A_PROTOCOL, f"endpoint:{getattr(self.endpoint, 'id', '')}"],
        )
        self._task_sessions[task_id] = session.id
        return session.id


def binding_from(protocol_binding: Any) -> A2AProtocolBinding:
    """Read a neutral `spec.binding.ProtocolBinding` as an A2A binding.

    The spec layer holds no protocol name; the name is matched here, where the
    vocabulary belongs (ADR-0002).
    """
    name = getattr(protocol_binding, "protocol", "") or ""
    if name != A2A_PROTOCOL:
        raise ValueError(f"binding names protocol {name!r}, not {A2A_PROTOCOL!r}")
    return A2AProtocolBinding(
        endpoint=getattr(protocol_binding, "endpoint", "") or "",
        base_url=getattr(protocol_binding, "base_url", "") or "",
        transport=getattr(protocol_binding, "transport", "") or IMPLEMENTED_TRANSPORT,
        protocol_version=(
            getattr(protocol_binding, "protocol_version", "") or A2A_SPEC_VERSION
        ),
        options=dict(getattr(protocol_binding, "options", {}) or {}),
    )
