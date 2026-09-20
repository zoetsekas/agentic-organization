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
