"""The workflow engine registry (ADR-0056).

A workflow is declared neutrally in the spec; *which* engine runs it is a
binding choice, and the engine's **invocation mode** decides what governs the
run:

``in_process``
    The engine is a library. It executes inside this process, under the
    calling agent's identity, permissions and sandbox, and gains nothing its
    caller did not already have.
``out_of_process``
    The engine is a service. Invoking it is egress: the payload leaves the
    agent's boundary, the engine authenticates as itself, and its reply is
    untrusted input. Those calls go through `runtime.endpoints`, the same path
    an external agent endpoint uses (ADR-0030), rather than a second one.

Engine *names* live here and never in `orgagents.spec`, exactly as no model
vendor does (ADR-0002).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib.util import find_spec
from typing import Any, Callable, Optional, Sequence

from ..guardrails import GuardrailEngine
from ..models import WorkflowRef
from ..spec.model import AgentEndpoint, EndpointTrust
from ..workflows.engine import WorkflowEngine, WorkflowResult
from .endpoints import CallerBoundary, EndpointCallResult, Transport, call_endpoint


class InvocationMode(str, Enum):
    IN_PROCESS = "in_process"
    OUT_OF_PROCESS = "out_of_process"


class UnknownEngine(KeyError):
    """A binding named an engine nothing here knows how to run."""


@dataclass(frozen=True)
class EngineDescriptor:
    name: str
    mode: InvocationMode
    summary: str
    # The import that would make this engine real, if it is a library.
    module: Optional[str] = None
    # Honesty flag: has this path been exercised anywhere, or is it a binding
    # waiting for credentials and a daemon nobody has here (ADR-0056).
    verified: bool = False

    @property
    def in_process(self) -> bool:
        return self.mode is InvocationMode.IN_PROCESS

    @property
    def available(self) -> bool:
        """Is the library actually installed in this process?"""
        if self.module is None:
            return self.in_process  # the built-in interpreter is always there
        try:
            return find_spec(self.module) is not None
        except (ImportError, ValueError):
            return False


NATIVE = EngineDescriptor(
    "native", InvocationMode.IN_PROCESS,
    "The built-in declarative interpreter. The default, and the only engine "
    "with no third-party dependency.",
    verified=True,
)

_BUILTIN = [
    NATIVE,
    EngineDescriptor(
        "langgraph", InvocationMode.IN_PROCESS,
        "Graph library; compiled in-process when installed.",
        module="langgraph",
    ),
    EngineDescriptor(
        "langchain", InvocationMode.IN_PROCESS,
        "Chain library; runs in-process under the caller's identity.",
        module="langchain",
    ),
    EngineDescriptor(
        "gemini_adk", InvocationMode.IN_PROCESS,
        "Agent Development Kit; in-process, and needs provider credentials.",
        module="google.adk",
    ),
    EngineDescriptor(
        "langflow", InvocationMode.OUT_OF_PROCESS,
        "A flow server. Flows live in its process, are invoked over HTTP, and "
        "run outside the agent's sandbox.",
    ),
]

_REGISTRY: dict[str, EngineDescriptor] = {e.name: e for e in _BUILTIN}


def register_engine(descriptor: EngineDescriptor) -> EngineDescriptor:
    _REGISTRY[descriptor.name] = descriptor
    return descriptor


def engines() -> list[EngineDescriptor]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def engine_names() -> list[str]:
    return sorted(_REGISTRY)


def engine(name: str) -> EngineDescriptor:
    try:
        return _REGISTRY[name or "native"]
    except KeyError:
        raise UnknownEngine(
            f"unknown workflow engine '{name}'; known: {', '.join(engine_names())}"
        ) from None


def mode_for(name: str) -> InvocationMode:
    return engine(name).mode


# --------------------------------------------------------------------------
# In-process engines
# --------------------------------------------------------------------------


class InProcessEngine:
    """A library engine, run under the caller's own boundary.

    It is handed the caller's callables and the caller's frozen boundary. It
    has no way to add a permission, because it is never given anything that
    could mint one — the callers it runs through are the agent's own.
    """

    def __init__(self, descriptor: EngineDescriptor, caller: CallerBoundary) -> None:
        self.descriptor = descriptor
        self.caller = caller

    @property
    def permissions(self) -> frozenset[str]:
        return self.caller.permissions

    @property
    def degraded(self) -> bool:
        """True when the named library is absent and the interpreter stands in."""
        return not self.descriptor.available

    def run(
        self,
        ref: WorkflowRef,
        state: Optional[dict[str, Any]] = None,
        *,
        tool_caller: Optional[Callable[[str, dict], Any]] = None,
        agent_caller: Optional[Callable[[str, dict], Any]] = None,
        human_caller: Optional[Callable[[str, dict], Any]] = None,
        workflows: Optional[dict[str, WorkflowRef]] = None,
        max_steps: int = 100,
    ) -> WorkflowResult:
        engine = WorkflowEngine(
            tool_caller=tool_caller,
            agent_caller=agent_caller,
            human_caller=human_caller,
            workflows=workflows,
        )
        return engine.run(ref, state or {}, max_steps=max_steps)


# --------------------------------------------------------------------------
# Out-of-process engines
# --------------------------------------------------------------------------


def endpoint_for(binding: Any) -> AgentEndpoint:
    """The endpoint a service engine *is*, derived from its binding.

    A service engine is not a special case of workflow execution; it is an
    external endpoint that happens to run flows, so it is described as one and
    governed by the endpoint rules already in force (ADR-0030, ADR-0056).
    """
    trust = EndpointTrust(getattr(binding, "trust", None) or EndpointTrust.PARTNER)
    return AgentEndpoint(
        id=f"workflow-engine:{binding.engine}",
        description=f"Out-of-process workflow engine '{binding.engine}'.",
        trust=trust,
        send_data_classes=list(getattr(binding, "send_data_classes", []) or []),
        treat_output_as_data=True,
        requires_approval=bool(getattr(binding, "requires_approval", False)),
        secret_ref=getattr(binding, "secret_ref", None),
    )


class ServiceEngine:
    """A workflow engine in another process. Every run is an egress event."""

    def __init__(
        self,
        descriptor: EngineDescriptor,
        binding: Any,
        caller: CallerBoundary,
        *,
        transport: Optional[Transport] = None,
        guardrails: Optional[GuardrailEngine] = None,
    ) -> None:
        self.descriptor = descriptor
        self.binding = binding
        self.caller = caller
        self.transport = transport
        self.guardrails = guardrails
        self.endpoint = endpoint_for(binding)

    @property
    def secret_ref(self) -> Optional[str]:
        return self.endpoint.secret_ref

    def invoke(
        self,
        ref: WorkflowRef,
        inputs: Optional[dict[str, Any]] = None,
        *,
        input_data_classes: Sequence[str] = (),
        approval_granted: bool = False,
    ) -> EndpointCallResult:
        url = (self.binding.endpoint or "").rstrip("/")
        flow = getattr(self.binding, "flow", "") or ref.id
        payload = {"flow": flow, "workflow": ref.id, "inputs": dict(inputs or {})}
        return call_endpoint(
            self.endpoint,
            f"{url}/{flow}" if url else "",
            payload,
            caller=self.caller,
            transport=self.transport,
            guardrails=self.guardrails,
            payload_data_classes=input_data_classes,
            endpoint_tenant=getattr(self.binding, "tenant", None) or None,
            approval_granted=approval_granted,
        )


def engine_for_binding(
    binding: Any,
    caller: CallerBoundary,
    *,
    transport: Optional[Transport] = None,
    guardrails: Optional[GuardrailEngine] = None,
) -> Any:
    """Resolve a `WorkflowBinding` to the thing that will run it."""
    descriptor = engine(getattr(binding, "engine", "native"))
    # The registry decides the mode, never the binding document: a service
    # engine declared `in_process` would be the convenient lie ADR-0056
    # rejects, so `binding.mode` is a statement of intent that is checked
    # against the registry rather than obeyed.
    if descriptor.in_process:
        return InProcessEngine(descriptor, caller)
    return ServiceEngine(
        descriptor, binding, caller, transport=transport, guardrails=guardrails
    )
