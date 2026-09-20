"""Platform façade: one object that wires the whole system together."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .catalog import Catalog
from .data.planes import DataPlanes
from .harness.builder import HarnessBuilder
from .harness.mcp import MCPRegistry
from .harness.sandbox import SANDBOX_TEMPLATES, SandboxRunner
from .messaging import ChannelKind, MessageBus, logging_transport
from .models import WorkflowRef
from .observability import Observability, configure_logging
from .org import OrgChart
from .runtime.engine import AgentRuntime
from .sessions import SessionManager
from .store import SANDBOX_TEMPLATES as TEMPLATE_COLLECTION
from .store import WORKFLOWS, Store
from .workflows.library import WORKFLOW_LIBRARY


class Platform:
    """Composition root.

    Construct once per process; hand the sub-services to the API, the CLI or
    a notebook. Bootstrapping seeds the built-in sandbox templates and the
    reference workflow library so a fresh database is immediately usable.
    """

    def __init__(
        self,
        db_path: str | Path = "orgagents.db",
        *,
        base_url: str = "http://localhost:8000",
        configure_logs: bool = True,
    ) -> None:
        if configure_logs:
            configure_logging()
        self.base_url = base_url.rstrip("/")
        self.store = Store(db_path)
        self.registry = MCPRegistry()
        self.sandboxes = SandboxRunner(self.store)
        self.harness = HarnessBuilder(
            self.store, registry=self.registry, sandboxes=self.sandboxes
        )
        self.org = OrgChart(self.store)
        self.planes = DataPlanes(self.store)
        self.sessions = SessionManager(self.store, base_url)
        self.catalog = Catalog(self.store, base_url)
        self.obs = Observability(self.store)
        self.runtime = AgentRuntime(self.store, base_url=base_url, harness=self.harness)
        self.bus = self.runtime.bus
        self._bootstrap()

    def _bootstrap(self) -> None:
        if self.store.count(TEMPLATE_COLLECTION) == 0:
            self.store.put_many(TEMPLATE_COLLECTION, SANDBOX_TEMPLATES)
        if self.store.count(WORKFLOWS) == 0:
            self.store.put_many(WORKFLOWS, WORKFLOW_LIBRARY)
        # Enterprise channels are recorded by default; swap in real transports
        # with `bus.register_transport`.
        for channel in (
            ChannelKind.SLACK,
            ChannelKind.TEAMS,
            ChannelKind.EMAIL,
            ChannelKind.INTERNAL_BUS,
        ):
            self.bus.register_transport(channel, logging_transport(channel))

    def workflow(self, workflow_id: str) -> Optional[WorkflowRef]:
        return self.store.get(WORKFLOWS, workflow_id, WorkflowRef)

    def close(self) -> None:
        self.store.close()
