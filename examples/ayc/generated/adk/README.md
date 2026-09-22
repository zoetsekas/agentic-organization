# ayc — Google ADK + Vertex AI Agent Engine

A Python ADK package:

- `agents/` — one `LlmAgent` per agent, with tool stubs.
- `agents/__init__.py` — wires `sub_agents` from the org chart and names
  `root_agent`.
- `agent_engine.py` — deploys `root_agent` to Vertex AI Agent Engine.
- `requirements.txt` — `google-adk`, `google-cloud-aiplatform`.

Run locally with `adk run agents`, or deploy with
`python agent_engine.py --project YOUR_PROJECT --location us-central1`.

Tool callables are stubs that raise until you bind them: a binding names a
Python callable in your process, which is a property of your application, not
of this design.

**Read `CONFORMANCE.md` first.** 12 agents come across; the
authority model does not, and the report says what that costs.
