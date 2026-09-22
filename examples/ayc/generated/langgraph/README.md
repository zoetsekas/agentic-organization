# ayc — LangGraph Platform + LangSmith (deepagents)

A deepagents graph package for the hosted LangChain platform:

- `graphs/` — one `create_deep_agent` graph per agent, with tool stubs and
  `subagents` wired from the org chart.
- `graphs/__init__.py` — names `root`, the org's entry point.
- `langgraph.json` — the deployment manifest LangGraph Platform reads: `org`
  plus one graph per agent.
- `.env.example` — LangSmith and model-provider credentials.
- `requirements.txt` — `deepagents`, `langgraph`, `langchain`.

Run locally with `langgraph dev` (or `langgraph up`), or deploy to LangGraph
Platform from this directory. Tracing and evaluation light up in LangSmith once
`LANGSMITH_API_KEY` is set.

This is the **same `langchain_deepagents` runtime** this platform runs under
`terraform:gcp` — the difference is only where it is served: the hosted
LangChain platform here, your own Google Cloud there. The runtime is a binding
choice; the destination is a target. They compose.

Tool callables are stubs that raise until you bind them: a binding names a
Python callable in your process, which is a property of your application, not of
this design.

**Read `CONFORMANCE.md` first.** 12 agents come across, with
their delegation hierarchy and a real interrupt gate; the authority model does
not, and the report says what that costs.
