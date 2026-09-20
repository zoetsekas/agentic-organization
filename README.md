# agent-ai — an organizational agentic system

An enterprise agent platform where **every agent mirrors a role in the company
org chart**. Each agent has an accountable human counterpart, reports to a
manager agent, delegates to sub-agents, runs encoded LangGraph workflows, and
reaches systems of record through an MCP harness — all under data, sandbox and
approval policy that is declared, not improvised.

```
Human org chart                     Agent org chart
  CEO  ── Dana Whitfield     ⟷        ceo-agent
   ├─ CFO  ── Priya Raman    ⟷         ├─ cfo-agent ── warehouse grant, erp plugin
   │   └─ Analyst ── Tom B.  ⟷         │   └─ financial-analyst-agent
   ├─ CTO  ── Iris Nakamura  ⟷         ├─ cto-agent
   │   ├─ Staff Eng ── Samir ⟷         │   ├─ platform-engineer-agent (sandbox: SWE)
   │   └─ SRE Lead ── Lena   ⟷         │   └─ sre-agent  (shared service)
   └─ CRO  ── Marco O.       ⟷         └─ cro-agent  ⟷ peer link ⟷ cfo-agent
```

## Quick start

```bash
pip install -e '.[dev]'         # add '[langgraph]' or '[openai]' for real runtimes
orgagents seed                  # build the demo company + warehouse
orgagents serve                 # API + designer UI on http://localhost:8000
orgagents tree                  # print the org chart
orgagents run agt_cfo "Kick off the quarterly close"
orgagents catalog --kind agent  # browse the marketplace
pytest                          # 36 tests, no network or API keys needed
```

Open <http://localhost:8000/ui/> for the **Agentic Designer**: org chart,
agent/harness designer, marketplace, session traces and the operations console.

## What the platform gives you

| Capability | Where |
|---|---|
| Org hierarchy, delegation and escalation rules | `org.py` |
| Agent, harness, session and catalog models | `models.py` |
| Private / protected / public data planes with ACLs | `data/planes.py` |
| MCP mounting (stdio, http, in-process) | `harness/mcp.py` |
| Relational DB access with SQL policy + column masking | `harness/relational.py` |
| Eight reviewed sandbox environment templates | `harness/sandbox.py`, [docs](docs/SANDBOX_TEMPLATES.md) |
| Toolset assembly, approval gates, prompt composition | `harness/builder.py` |
| Declarative LangGraph workflows + reference library | `workflows/` |
| Runtime adapters: deep agents, OpenAI Agents SDK, LangGraph, echo | `runtime/adapters.py` |
| Sessions with URLs, traces and delegation trees | `sessions.py` |
| Direct tool calls + Slack/Teams/email/bus messaging | `messaging.py` |
| Catalog with marketplace search, install and ratings | `catalog.py` |
| Logging, tracing, metrics, alerting | `observability.py` |
| REST API + designer UI | `api.py`, `web/` |

## Design in one page

**Agents are org-shaped.** `Agent.manager_agent_id` / `report_agent_ids` form
the reporting tree. `OrgChart.can_delegate` enforces the routing rule: delegate
down your own subtree, call registered peers laterally, escalate up your chain,
and call shared-service agents from anywhere. Anything else must go through a
shared manager or an enterprise channel.

**Every agent has a human.** `HumanCounterpart` carries the accountable person,
their notification channels, and the tool names that must stop for approval.
`ask_human` pauses the session (`waiting_human`) and notifies; `resume` picks it
back up with the decision recorded in the trace.

**Data lives in three planes.** Private (one agent), protected (a group),
public (org-wide, any agent may contribute). `DataPlanes` resolves the caller's
`DataGrant`s against each record and raises `AccessDenied` rather than silently
returning nothing, so refusals are visible in the trace.

**The harness is the governed unit.** Model, MCP mounts, relational grants,
data grants, tool approvals, budgets and sandbox — one reviewable object the
designer UI edits. `HarnessBuilder.build` turns it into callables; policy is
enforced there, so every runtime obeys the same rules.

**Databases are reached through MCP, never credentials.** A `RelationalGrant`
fixes the connection, reachable tables, allowed statement classes, row limit and
masked columns; `RelationalMCP` enforces them and exposes `list_tables`,
`describe_table` and `query`.

**Work happens in sandbox templates.** Eight published templates span reasoning,
analysis, software engineering, browser automation, documents, GPU training,
regulated clean rooms and integration. Agents instantiate a template and may
only *narrow* it. See [docs/SANDBOX_TEMPLATES.md](docs/SANDBOX_TEMPLATES.md).

**Processes that must be auditable are encoded.** Workflows are declarative
graphs (`tool`, `agent`, `workflow`, `human`, `branch`, `transform` nodes) that
compile onto LangGraph when installed, and run on a built-in interpreter
otherwise — so the same definition is testable without a model.

**Everything is catalogued.** Agents, skills, plugins, workflows, sandbox
templates and shareable sessions publish into one marketplace with
visibility-aware search, install, and ratings.

Further reading: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/DESIGNER.md](docs/DESIGNER.md) ·
[docs/SANDBOX_TEMPLATES.md](docs/SANDBOX_TEMPLATES.md)

## Runtimes

The agent definition is framework-neutral. Pick per agent via
`harness.runtime`:

- `langchain_deepagents` — LangChain deep agents (planning, virtual FS, sub-agents)
- `openai_agents_sdk` — OpenAI Agents SDK (`Agent` + `Runner`, handoffs as delegation)
- `langgraph_native` — a LangGraph ReAct loop
- `echo` — deterministic, no model call; used by the test suite and the
  designer's **Dry run**, which verifies wiring rather than model quality

Adapters import their framework lazily, so a deployment installs only what it uses.

## Status

The domain model, policy enforcement, workflow engine, catalog, API and UI are
implemented and tested end to end against the `echo` runtime. The deep-agents
and OpenAI Agents SDK adapters are thin, lazily imported bindings that require
their optional dependency and provider credentials; the container sandbox
backend emits orchestrator run specs rather than launching pods, which is the
platform's job.
