# ayc — Nomad deployment

Generated from the `ayc` design (spec_version 1.4.0) by the
`template` target. 12 agents:

ceo_agent, coo_agent, buyer_agent, warehouse_agent, inventory_agent, ap_agent, ar_agent, software_agent, cgo_agent, ecommerce_agent, cs_agent, marketing_agent

## What this carries, and what it does not

Each job below names the agent, its team, its model and the tools it may call.
Those are **descriptive**: Nomad runs the container, it does not check them.

The authority model — who may decide what, which duties stay separated, which
human approves a gated action — is enforced at this platform's harness
boundary, never by a scheduler. See `CONFORMANCE.md`.
