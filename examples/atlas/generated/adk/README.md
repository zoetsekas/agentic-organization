# atlas — Google ADK + Vertex AI Agent Engine

A Python ADK package:

- `agents/` — one `LlmAgent` per agent, with tool stubs.
- `agents/__init__.py` — wires `sub_agents` from the org chart and names
  `root_agent`.
- `agent_engine.py` — deploys `root_agent` to Vertex AI Agent Engine.
- `requirements.txt` — `google-adk`, `google-cloud-aiplatform`.

Run locally with `adk run agents`, or deploy with
`python agent_engine.py --project YOUR_PROJECT --location us-central1`.

Tools behind a declared server are wired for real: a capability the binding
puts on an MCP or database server (ADR-0085) is emitted in `agents/_backends.py`
as a working client — an MCP call, or a bounded SQL query that enforces the
design's operation allowlist and row cap. Set the credential env vars the
binding named. Only a capability with no server bound stays a stub you fill in.

**Read `CONFORMANCE.md` first.** 12 agents come across; the
authority model does not, and the report says what that costs.
