"""The local reference backend, run against the adapter conformance suite.

The suite itself lives in `orgagents.tasks.conformance` so that an adapter
written elsewhere can import and subclass it. This module is what that looks
like for the one implementation we have (WS-031 M2).
"""
from orgagents.store import Store
from orgagents.tasks import LocalTaskBackend
from orgagents.tasks.conformance import TENANT, TaskPortConformance


class TestLocalBackendConformance(TaskPortConformance):
    def make_backend(self, tmp_path):
        return LocalTaskBackend(
            Store(str(tmp_path / "tasks.db")), tenant_id=TENANT
        )

    def open_task(self, backend, **kwargs):
        return backend.open_task(**kwargs)
