# The Agentic Designer UI

`http://localhost:8000/ui/` after `orgagents serve`. One System Spec, several
views of it, plus two runtime views that observe what is running.

## The context bar

Above every view: who you are (`GET /api/designer/whoami`), the role you hold
in the current workspace, the workspace and system you have open, the version
and lock badges, and Save, Lock, History, People and Settings. A refusal is
only understandable if the role behind it is on screen, so identity is
persistent rather than a dialog.

## Design views — they edit the open spec

The org chart, the canvas and the agent editor read and write the *same*
`record.spec`: one document in the browser, owned by `canvas.js` and reached
through `window.designer`. Editing an agent in the agent editor and moving its
box on the canvas are the same edit. When no system is open they say so; none
of them falls back to runtime data.

### Org chart
The organization as the spec defines it: teams, their leaders and mandates,
their agents, each agent's roles, capabilities, human counterparts and
sub-agents. **Edit this agent** opens it in the agent editor.

### Canvas
Drag-and-drop over the same document, with locks, revisions, restore and
three-way merge with conflict resolution. Edges are derived from the spec, so
the picture always matches what would compile.

### Agents
The agent definition form: id, name, description, team membership and
leadership (which writes both ends of the edge), human counterparts with the
capabilities each may approve, and the bindings — roles, capabilities,
knowledge, workflows, external agents, skills, plugins, channels, environment
class, artifact store and output contract. Every binding is chosen from what
*this spec* declares, so an agent cannot reach something the document does not
define. Save writes the system; validation from the last open is shown beside
it.

### Workspace
Membership — add, change and remove — offered only to a role that holds
`workspace.members`, and the audit log (`GET /api/designer/audit`) filtered by
system, actor and action, with refusals marked. Where the API refuses, its own
message is shown: it knows which role refused and why.

## Runtime views — they observe the running system

Deliberately *not* unified on the spec, because they are observations rather
than design:

- **Sessions**: every run with its state, turn count and `/sessions/<id>` URL;
  selecting one renders the full trace including nested child sessions.
- **Operations**: metric tiles, open alerts with acknowledge, and a per-agent
  breakdown.

## Platform views

The **Catalog** is the approved building blocks a design may choose from, and
the **Marketplace** is search, install and rating across shared items. Both are
platform facts, not per-design ones, and are unchanged.

## Notes
Dependency-free vanilla JS against the documented REST API, so it can be
replaced wholesale without touching the platform. It follows the system colour
scheme in both light and dark mode. The operator's command centre is a separate
application at `/command/` (ADR-0051); nothing here imports from it or links
into it.

---

# Review: what the designer is today, and what is missing

Written 2026-09-20, from the code rather than from intent. Line counts and
route lists are as measured.

## What exists

Two separate front ends, correctly separate (ADR-0051):

| Bundle | Serves |
|---|---|
| `web/` | The designer: context bar, org, canvas, agents, workspace, catalog, marketplace, sessions, ops |
| `web/command/` | The command centre: tenants, deployments, health, quotas, services, audit, operators |

The designer backend is substantially richer than the UI was: workspaces and
membership, five-role RBAC, OIDC and trusted-proxy identity, advisory locks
with heartbeat and break, versions with revisions and restore, three-way
structural merge, an append-only audit log, settings, and a component palette —
about 2,700 lines under `src/orgagents/designer/`.

## The structural gap: closed (WS-009 M3)

The finding this review opened with was two data models in one application:
`web/canvas.js` edited the spec while `web/app.js` never called
`/api/designer` at all, so the "Designer" tab built an agent in the runtime
model while the "Canvas" tab edited a System Spec.

The design views now share one document. `canvas.js` owns the open record and
publishes it as `window.designer`; the org chart and the agent editor read and
write that same `record.spec` and are told when it changes. No design view
reads `/org/tree`, `/org/units`, `/agents` or `/components` any more, and where
a design view has no spec equivalent it shows nothing rather than a runtime
value wearing a design label. Sessions and operations stay on `/sessions` and
`/ops` on purpose: they are observations of a running system, and moving them
onto the spec would make them lie. `tests/test_designer_ui.py` holds this line
from outside the bundle.

## Backend capability with no UI in front of it

| Capability | API | In the UI |
|---|---|---|
| Who am I, and with what role | `GET /api/designer/whoami` | Yes — the context bar, every view |
| Workspace membership | `GET/POST /workspaces`, `POST/DELETE .../members` | Yes — the Workspace view, offered only to `workspace.members` |
| Audit log — including refusals | `GET /api/designer/audit` | Yes — filtered, with denials marked |
| Lock heartbeat and break | `.../lock/heartbeat`, `.../lock/break` | Break yes; **no heartbeat is sent**, so a long edit can lose its lock |
| Revisions and restore | `.../revisions`, `.../restore/{v}` | Yes, but through a `prompt()` rather than a history panel |

Two caveats worth keeping honest. An unscoped audit read that is refused is
*not* itself recorded — the service raises before it logs — so the log shows
refusals of changes, not of reads. And the agent editor covers the agent
portion of the spec; the rest of the document (guardrails, budgets, policies,
compliance, missions, model policy) is reachable only through the canvas
inspector, which is form coverage still owed by WS-023 M5.

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

1. ~~**Unify on the spec (WS-009 M3).**~~ Done: the design views edit one
   document. What remains of it is form coverage, not architecture.
2. ~~**Surface identity, workspace and audit.**~~ Done, with the two caveats
   above (no lock heartbeat; a refused audit *read* leaves no entry).
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
environment, so the UI has not been loaded since the SPA landed, and that
includes the changes described above. The tests assert which routes the bundle
calls and which it must not; they say nothing about whether a single pixel
renders. Accessibility, keyboard navigation, undo/redo and behaviour on a large
organization remain unassessed.
