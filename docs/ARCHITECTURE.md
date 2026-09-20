# Architecture

## Layers

```
┌───────────────────────────────────────────────────────────────────┐
│  Agentic Designer UI  (web/)   — org chart · designer · market ·  │
│                                  sessions · operations            │
├───────────────────────────────────────────────────────────────────┤
│  REST API  (api.py)            — /api/org /agents /sessions       │
│                                  /catalog /components /ops        │
├───────────────────────────────────────────────────────────────────┤
│  Runtime  (runtime/)           — session loop, delegation,        │
│    adapters: deep agents · OpenAI Agents SDK · LangGraph · echo    │
├───────────────────────────────────────────────────────────────────┤
│  Harness  (harness/)  │  Workflows  │  Messaging  │  Catalog      │
│  MCP · SQL policy ·   │  declarative│  direct +   │  marketplace  │
│  sandboxes · tools    │  LangGraph  │  enterprise │               │
├───────────────────────────────────────────────────────────────────┤
│  Org (org.py) · Data planes (data/) · Sessions · Observability    │
├───────────────────────────────────────────────────────────────────┤
│  Store (store.py)  — JSON documents over SQLite / Postgres        │
└───────────────────────────────────────────────────────────────────┘
```

`Platform` (platform.py) is the composition root: construct it once, hand the
sub-services to the API, the CLI or a notebook.

## The request path

A task arriving at an agent takes the same path regardless of runtime:

1. **Session opens.** `SessionManager.create` mints a session id, a trace id and
   a URL (`/sessions/<id>`). A delegated run is a *child* session sharing the
   parent's trace id, so the delegation tree is reconstructable from the store.
2. **Harness assembles.** `HarnessBuilder.build` produces the callable toolset:
   MCP proxies, relational tools, data-plane access, sandbox execution and org
   introspection. `system_prompt` composes the operating instructions from the
   agent's place in the org chart, its human counterpart's approval list and its
   installed skills.
3. **Session-bound tools attach.** The runtime adds `delegate`,
   `spawn_subagent`, `run_workflow`, `send_message`, `ask_human` and `escalate`,
   each closed over *this* agent and *this* session so every call lands in the
   right trace.
4. **The adapter runs.** One of four runtimes executes the loop.
5. **Everything is recorded.** Messages, tool calls, delegations, workflow
   paths, approvals and errors append to the session's event log; usage rolls
   up into token and cost counters.
6. **Policy stops, not crashes.** An approval gate returns a
   `requires_approval` result; an access violation raises `AccessDenied` and is
   recorded; a failure raises an alert and escalates to the human counterpart or
   the manager agent.

## Delegation and escalation

`OrgChart.can_delegate(from, to)` is the single routing rule:

| Relationship | Allowed |
|---|---|
| Direct report | yes |
| Anywhere in your subtree (skip-level) | yes |
| Registered peer (`peer_agent_ids`) | yes |
| Shared-service agent (`kind == service`) | yes, from anywhere |
| Your manager, or another branch | **no** — escalate, or use a channel |

Sub-agents are real agents: `spawn_subagent` creates an ephemeral
`AgentKind.SUBAGENT` under the caller, inheriting its harness with the depth
budget decremented, and runs it as a child session. Depth is capped by
`harness.max_subagent_depth`.

## Data planes

| Plane | Readers | Writers |
|---|---|---|
| Private | the owning agent (and its human) | the owner |
| Protected | agents sharing a group on the record | grant holders in those groups |
| Public | every agent | any agent with a public write grant |

Group membership is the agent's own `groups` plus everything inherited down its
org-unit chain, so adding a team to a division grants its agents the division's
protected data without editing each agent.

An agent cannot publish to a group it does not belong to — the check is on the
*writer's* membership, not just the grant.

## Harness

The harness is what governance reviews: model, MCP mounts, relational grants,
data grants, tool approvals, budgets, interrupt policy and sandbox. Because
`HarnessBuilder` enforces policy while assembling the toolset, the rules hold
identically for deep agents, the OpenAI Agents SDK, LangGraph and the echo
runtime — swapping the framework cannot widen an agent's reach.

Secrets are never inlined. `MCPServerRef.secret_refs` and
`RelationalGrant.dsn_secret_ref` are names resolved at build time through the
secret manager (`HarnessBuilder.dsn_resolver`).

## Workflows

Workflows are data, so the designer can render them and governance can diff
them. Node kinds: `tool`, `agent`, `workflow`, `human`, `branch`, `transform`.
`WorkflowEngine.run` interprets the graph directly; `compile_langgraph` emits an
equivalent `StateGraph` with `interrupt_before` wired to the human nodes. Node
arguments interpolate `{{ expr }}` against workflow state, evaluated with no
builtins beyond a small safe set.

A `human` node interrupts the run and moves the session to `waiting_human`; the
session URL is the resume point.

## Communication

- **Direct tool call** — synchronous, checked against `can_delegate` first.
- **Enterprise channels** — Slack, Teams, email, webhooks and an internal bus,
  behind pluggable transports (`bus.register_transport`). The default transport
  records delivery so the system is fully exercisable without external
  services.

Messages persist, so an inbox and channel history survive process restarts and
show up in the trace.

## Sessions, tracing and operations

Every session carries `trace_id` / `span_id` / `parent_span_id` on each event,
which `Observability.export_span` forwards to an OpenTelemetry exporter.
`metrics()` computes session states, token and cost rollups and per-agent
breakdowns from the same event store the UI reads; four default alert rules
(failure rate, cost budget, approval backlog, tool-error spike) evaluate against
it, and custom rules are plain predicates over the metrics dict.

## Persistence

`Store` keeps each domain object as a JSON document in a typed collection with
indexed `parent` and `name` columns. SQLite by default with WAL; reimplement the
same six methods against Postgres for production. Collections: agents, org
units, sessions, events, messages, records, skills, plugins, workflows, sandbox
templates, catalog, alerts.

## Deployment shape

The designer's infrastructure palette (`/api/components` → `infrastructure`)
names the concerns a deployment must choose: Postgres + object storage + vector
index + Redis for storage; Kubernetes jobs, microVMs or a GPU pool for sandbox
compute; NATS/Kafka plus Slack/Teams/SMTP for communication; OpenTelemetry,
Loki, Prometheus and PagerDuty for operations; and a secret manager, OIDC, an
egress proxy and an immutable audit log for security.
