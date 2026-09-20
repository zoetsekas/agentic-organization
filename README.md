# agent-ai — an agentic system designer and compiler

Define an organization of agents **once, abstractly** — through a UI or an SDK —
then generate the agent code *and* the infrastructure to run it, on a laptop or
in your own cloud account (ADR-0003).

Every agent mirrors a role in the company org chart: it has an accountable human
counterpart, belongs to a team with one leader, delegates down the tree, runs
encoded workflows, and reaches systems of record through an MCP harness — all
under RBAC, data classification, sandbox and approval policy that is **declared
in the spec and compiled into every target**, never improvised per deployment.

```
 spec (what)  +  binding (how)  →  IR (resolved once)  →  targets  →  artifacts
 ─────────────   ──────────────    ──────────────────     ───────    ─────────
 teams, roles,   framework,        permissions,           local      compose,
 capabilities,   images, cloud,    identities,            tf:gcp     Terraform,
 environments,   MCP servers       environments,          tf:aws     IAM, agent
 policies                          delegation edges       tf:azure   manifests
```

Teams nest, and each has exactly one leader agent who is also a member of it —
`examples/acme.system.yaml` is four levels deep:

```
Acme Corp                     leader: ceo         (human: Dana Whitfield)
├── Finance                   leader: cfo         (human: Priya Raman)
│   ├── analyst               role: financial_analyst      env: analysis
│   └── reconciler            role: reconciliation_specialist
│                                                          env: isolated_review
├── Technology                leader: cto         (human: Iris Nakamura)
│   └── Platform Engineering  leader: platform_lead
│       ├── platform_engineer role: platform_engineer      env: build
│       └── sre               shared service — callable from anywhere
└── Revenue                   leader: cro         ⟷ peer link ⟷ cfo
```

## Quick start

```bash
pip install -e '.[dev]'         # add '[langgraph]' or '[openai]' for real runtimes

# Design → compile → run
orgagents spec validate examples/acme.system.yaml
orgagents spec show     examples/acme.system.yaml       # resolved org at a glance
orgagents targets                                       # what we can generate
orgagents compile examples/acme.system.yaml \
  --binding examples/acme.binding.yaml \
  --target local --target terraform:gcp --out build
cd build/local && make up                               # or `make single`

# Or explore the runtime directly
orgagents seed && orgagents serve                       # UI on localhost:8000

# Governance
orgagents records validate      # ADR/WS graph integrity
orgagents records index         # regenerate the record indexes
pytest                          # 91 tests, no network or API keys needed
```

Open <http://localhost:8000/ui/> for the **Agentic Designer**: org chart,
agent/harness designer, marketplace, session traces and the operations console.

## Decisions and delivery

Architecture is recorded, not remembered (ADR-0001). Eighteen decision records
and ten workstream records, machine-validated in CI:

- [docs/decisions/index.md](docs/decisions/index.md) — **ADRs**: why, who, what,
  where, how, when, advantages *and* disadvantages, with statuses, semver,
  changelogs and explicit supersession.
- [docs/workstreams/index.md](docs/workstreams/index.md) — **WS records**: the
  delivery side, owned and dated, cross-linked to the decisions they implement.

`orgagents records validate` fails the build on a dangling reference, an
asymmetric supersession, a missing section or a changelog that disagrees with
its version. Two decisions were already corrected and versioned by writing the
implementation: [ADR-0006 v1.1.0](docs/decisions/ADR-0006-recursive-teams-with-leader-agents.md)
(implicit parent-team participation) and
[ADR-0008 v1.1.0](docs/decisions/ADR-0008-deny-by-default-rbac-with-policies.md)
(the explicit `unless` guard).

## What the platform gives you

| Capability | Where |
|---|---|
| Implementation-neutral System Spec + bindings | `spec/`, [docs](docs/SPEC.md) |
| Spec validation incl. least-privilege rules | `spec/validate.py` |
| Two-phase compiler: spec → IR → target plugins | `compiler/` |
| Local target: Compose stack + single-process mode | `compiler/targets/local.py` |
| Terraform targets: GCP, AWS, Azure + mapping reports | `compiler/targets/terraform.py` |
| Deny-by-default RBAC, policies, auditable decisions | `security/rbac.py` |
| ADR / workstream records with validation and indexes | `records.py`, `docs/` |
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

**The spec is the source of truth.** One document describes the system in
capability terms; a binding says how each capability is realized for one target.
A test walks the spec schema and fails if a vendor, SDK or provider name leaks
into it, so neutrality is enforced rather than intended (ADR-0004).

**Everything is resolved exactly once.** Phase 1 turns the spec into an IR:
team inheritance, role expansion, effective permissions, environment narrowing,
delegation edges and one workload identity per agent. Phase 2 targets may only
render that IR — a test asserts no target imports the spec package, so the
permissions Terraform grants cannot drift from the ones the runtime enforces
(ADR-0005).

**Teams nest, with one leader each.** A leader is a member of the team it leads
and participates in its parent implicitly. Delegation follows the tree; declared
peers go sideways; shared services are callable from anywhere; everything else
escalates (ADR-0006).

**Roles carry accountability and authority together.** Responsibilities,
capabilities and permissions are one contract, so the prompt cannot describe
work the agent has no permission to do. A capability nobody's responsibility
mentions is a warning (ADR-0007).

**Security is deny-by-default and narrow-only.** Deny wins and cannot be
overridden; inheritance only narrows, leaders included; wildcards are errors in
production; every decision names the rule that produced it. Cloud IAM is derived
from the same resolved set, and where a provider's IAM is coarser, the target
says so in `MAPPING.md` rather than hiding it (ADR-0008, ADR-0012).

**Agents are org-shaped at runtime too.** `OrgChart.can_delegate` enforces the
same routing rule the spec declares: delegate down your own subtree, call
registered peers laterally, escalate up your chain, call shared-service agents
from anywhere.

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

Further reading: [docs/SPEC.md](docs/SPEC.md) ·
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/DESIGNER.md](docs/DESIGNER.md) ·
[docs/SANDBOX_TEMPLATES.md](docs/SANDBOX_TEMPLATES.md) ·
[decisions](docs/decisions/index.md) · [workstreams](docs/workstreams/index.md)

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

Implemented and tested end to end: the record layer, the spec and its
validators, the compiler and IR, the local and three Terraform targets, the
RBAC engine, and loading a compiled system into the runtime (91 tests, no
network or API keys).

Known gaps, tracked in the workstreams rather than glossed:

- **Terraform is generated but not applied or `terraform validate`-ed here** —
  the binary is not installed in this environment, so the tests check
  identifier validity and block balance instead. Real `plan`/`apply` against a
  scratch account is WS-007's exit criterion, and the provider mappings are
  first-cut.
- **The designer UI still edits the runtime model, not the spec** — spec-backed
  editing and SDK parity are WS-009 M2/M3.
- **The deep-agents and OpenAI Agents SDK adapters** are thin, lazily imported
  bindings needing their optional dependency and provider credentials; only the
  `echo` adapter is exercised in CI (WS-008 M3).
- **Compose cannot represent cloud IAM or real network policy**, so a local run
  does not verify those controls — stated in the generated README, not implied
  away (ADR-0011).
