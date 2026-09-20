# The Agentic Designer UI

`http://localhost:8000/ui/` after `orgagents serve`. Five views, one API.

## Org chart
The reporting tree, each node an agent with its human counterpart. Selecting an
agent loads its detail: composed system prompt, assembled toolset (approval-
gated tools flagged), resolved sandbox run spec, and recent sessions with their
URLs. **Run a task** starts a session from here.

## Designer
The agent builder. Left is the component palette — runtimes, sandbox templates,
workflows, plugins — served from `/api/components`. Centre is the definition
form:

- identity, org unit and **reports-to** (which writes both ends of the edge);
- the **human counterpart**, including the tools that must stop for approval;
- the **harness**: runtime, model, sandbox template, system prompt and budgets;
- **relational access**: connection, engine, DSN secret *reference*, reachable
  tables, allowed statement classes and masked columns;
- **data planes, skills, workflows and channels** as multi-selects, plus the
  groups that define protected-data reach.

Right is the preview pane with three actions: **Preview harness** renders the
payload, **Create agent** persists it and shows the composed prompt and
resolved toolset, and **Dry run** creates the agent on the `echo` runtime and
runs one turn — verifying wiring (prompt, tools, delegation legality) without
spending a model call.

Below the canvas, the **infrastructure** section lists the platform services
each concern maps onto: storage, compute, communication, operations, security.

## Marketplace
Search across agents, skills, plugins, workflows, sandbox templates and shared
sessions. Filter by kind, sort by newest/installs/rating, and set *viewer
groups* to see what a member of that group would see — a protected listing only
surfaces to its groups. Install attaches the item to an agent (installing an
*agent* hires a copy as a direct report); rate feeds the marketplace ranking.

## Sessions
Every run, with its state, turn count and its `/sessions/<id>` URL. Selecting
one renders the full trace: the event stream plus nested child sessions, so a
delegation tree reads top to bottom.

## Operations
Metric tiles (agents, sessions by state, tokens, cost), open alerts with
acknowledge, and a per-agent breakdown. Alerts come from the default rules —
failure rate, cost budget, approval backlog, tool-error spike — evaluated on
each load.

## Notes
The UI is dependency-free vanilla JS against the documented REST API, so it can
be replaced wholesale without touching the platform. It follows the system
colour scheme in both light and dark mode.

---

# Review: what the designer is today, and what is missing

Written 2026-09-20, from the code rather than from intent. Line counts and
route lists are as measured.

## What exists

Two separate front ends, correctly separate (ADR-0051):

| Bundle | Size | Serves |
|---|---|---|
| `web/` | ~1,700 lines | The designer: org, canvas, designer, platform, catalog, sessions, ops |
| `web/command/` | ~990 lines | The command centre: tenants, deployments, health, quotas, services, audit, operators |

The designer backend is substantially richer than the UI in front of it:
workspaces and membership, five-role RBAC, OIDC and trusted-proxy identity,
advisory locks with heartbeat and break, versions with revisions and restore,
three-way structural merge, an append-only audit log, settings, and a
component palette — about 2,700 lines under `src/orgagents/designer/`.

## The structural gap: two data models in one application

This is the finding that matters, and everything else is downstream of it.

- **`web/canvas.js` edits the spec.** It loads `/api/designer/systems`, mutates
  `record.spec` directly, and uses workspaces, locks, revisions and restore.
  It is a genuine spec-backed editor.
- **`web/app.js` never calls `/api/designer` at all.** Its seven views read the
  *runtime* model: `/agents`, `/org/tree`, `/org/units`, `/sessions`, `/ops`,
  `/catalog`, `/components`.

So the "Designer" tab builds an agent in the runtime model while the "Canvas"
tab edits a System Spec, and nothing reconciles them. A user can reasonably
believe they have designed one system when they have edited two different
things. This is WS-009 M3 ("UI migrated to spec-backed editing", not started)
stated in terms of what it actually costs.

## Backend capability with no UI in front of it

Built, tested, and unreachable from the browser:

| Capability | API | In the UI |
|---|---|---|
| Who am I, and with what role | `GET /api/designer/whoami` | **No** |
| Workspace membership | `POST/DELETE .../members` | **No** |
| Audit log — including refusals | `GET /api/designer/audit` | **No** |
| Lock heartbeat and break | `.../lock/heartbeat`, `.../lock/break` | Partial (canvas) |
| Revisions and restore | `.../revisions`, `.../restore/{v}` | Partial (canvas) |

An audit log nobody can read is a compliance artifact, not a control. Identity
and membership being invisible is what makes the multi-user story feel absent
even though it is implemented.

## The platform's own purpose is not reachable from the UI

There is no **compile**, **publish** or **deploy** action anywhere in the
designer, and no mention of a tenant or a binding. ADR-0049 says publishing is
a request to the fabric — the UI cannot make that request. Nor can it show:

- the **evaluation gate** per agent (`orgagents gate`), now a real verdict;
- an **IR diff** against the previous version (`orgagents spec diff`), which is
  where a widened permission becomes visible;
- the **record graph** of ADRs and workstreams (WS-009 M5).

All three exist as libraries with CLI surfaces. The reviewer who most needs
them is looking at a screen.

## Smaller gaps, already tracked

Presence — who else has this open (WS-022 M4); auto-layout and grouping frames
(WS-023 M4); full spec coverage in the forms (WS-023 M5, in progress);
round-trip stability tests (WS-009 M4); per-system access within a workspace
(WS-021 M4).

## Recommendations, in order

1. **Unify on the spec (WS-009 M3).** Make the org and agent views read and
   write the same System Spec the canvas edits. Until this is done, every
   other UI feature has to be built twice or built against the wrong model.
   It is also the decision ADR-0002 already made — the UI and the SDK are
   *peers* of the spec — so the current split is a deviation, not a design.
2. **Surface identity, workspace and audit.** Cheap: the backend is finished.
   Without it nobody can see who they are, who else has access, or what was
   changed, and the RBAC work reads as theoretical.
3. **Add the publish path.** Validate → compile → request deployment, with the
   phase gate's output shown as the reason when it refuses. This is the one
   thing the platform exists to do and the one thing the UI cannot do.
4. **Show the two review surfaces** — the evaluation gate and the IR diff — on
   the system being edited. A widened permission discovered in a terminal is
   discovered by the wrong person at the wrong time.
5. **Then presence, auto-layout and form coverage**, which are experience
   rather than correctness.

## What this review did not check

Nothing here was exercised in a browser: there is no daemon in this
environment, so the UI has not been loaded since the SPA landed. Accessibility,
keyboard navigation, undo/redo and behaviour on a large organization are
unassessed, and the line counts above say nothing about whether the views
work.
