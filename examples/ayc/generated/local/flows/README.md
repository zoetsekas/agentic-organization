# Flows for ayc

Put the flow exports this system's out-of-process engines (`langflow`) run in
this directory. They are mounted **read-only** into the engine container.

These flows are outside the system spec: nothing here is validated, diffed or
version-gated by `orgagents spec validate`, and a flow can change under a
system that was reviewed and approved. A flow also runs outside the calling
agent's sandbox — the engine's limits, network and filesystem apply, not the
agent environment class's (ADR-0056).
