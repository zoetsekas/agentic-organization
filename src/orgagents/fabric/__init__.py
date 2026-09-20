"""The fabric plane: tenants, their deployments and the operations over them.

The designer authors; the fabric decides whether, where and for whom a design
runs (ADR-0049). This package owns the operational half of that split.

Nothing here has ever spoken to a cloud account or a container daemon — there
is neither in this environment. Every backend is injected behind a protocol and
exercised against stubs, so what is implemented is a *contract* for operations,
not a proven operation (WS-030 M5 is where that is settled).
"""
