# The Agentic Designer UI

`http://localhost:8000/ui/` after `orgagents serve`. One saved design holds
exactly one org chart (`SystemSpec.organization`), so **one design is one
organisation**: the UI says organisation throughout, while the wire keeps the
word it has always used — `/api/designer/systems`, `workspace_id`, `system_id`
— because the protocol is not the vocabulary a user reads. Several views of
that one organisation, plus two runtime views that observe what is running.

## The context bar

Above every view: who you are (`GET /api/designer/whoami`), the role you hold
in the current workspace, the workspace and organisation you have open, the version
and lock badges, and Save, Lock, History, People and Settings. A refusal is
only understandable if the role behind it is on screen, so identity is
persistent rather than a dialog.

## Design views — they edit the open spec

The org chart, the canvas and the agent editor read and write the *same*
`record.spec`: one document in the browser, owned by `canvas.js` and reached
through `window.designer`. Editing an agent in the agent editor and moving its
box on the canvas are the same edit. When no organisation is open they say so
and offer to create one; none of them falls back to runtime data.

### Org chart
The organisation as the spec defines it: teams, their leaders and mandates,
their agents, each agent's roles, capabilities, human counterparts and
sub-agents. **Edit this agent** opens it in the agent editor.

#### Managing the organisation
A toolbar above the chart carries the whole life of one organisation:

| Affordance | What it does | API |
|---|---|---|
| The selector | Lists the organisations in the open workspace and switches the open one | `GET /systems?workspace_id=` |
| **New…** | Name, description, owner, environment and labels — the fields `SystemSpec.metadata` actually holds, and no others | `POST /systems`, then the ordinary save |
| **Edit definition…** | The same form over the open organisation: rename and change its metadata | `PUT /systems/{id}` |
| **Duplicate** | Client-side: read the organisation, post a copy of its spec named `… (copy)`, then carry its layout over with one save. There is no server-side copy route and none was added | `GET` + `POST /systems` + `PUT` |
| **Delete…** | Confirms by name, and on refusal shows the API's own `detail` | `DELETE /systems/{id}` |

The selector here and the one in the context bar are the *same* state: both
carry `data-org-select`, and `canvas.js` fills every one of them from the one
open id and wires them to the one handler, so neither can show something the
other does not. The context bar's **New organisation** opens the same form on
the org chart tab rather than a second creation path.

Edit, duplicate and delete are offered only when the open record reports
`system.edit`, and edit and delete also stand down while someone else holds the
lock. The version and the lock are repeated beside the toolbar, so the reason a
control is unavailable is on the same screen as the control.

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
define. Save writes the organisation; validation from the last open is shown
beside it.

### Workspace
Membership — add, change and remove — offered only to a role that holds
`workspace.members`, and the audit log (`GET /api/designer/audit`) filtered by
organisation, actor and action, with refusals marked. Where the API refuses, its own
message is shown: it knows which role refused and why.

## Review surfaces — they report on the open design

Two read-only routes put the review libraries in front of the designer. Neither
writes anything: the gate answers *what does the evidence say today*, and a
route that quietly ran evaluations in order to report on them would defeat the
gate.

- `GET /api/designer/systems/{system_id}/gate` — the evaluation gate per agent:

  ```json
  {"agents": {"analyst": {"state": "not_evaluated", "reason": "no evaluation run has judged this agent", "required": true}}}
  ```

  `state` is one of `passed`, `failed`, `not_evaluated`, `stale`.
  `not_evaluated` is deliberately not `failed`: nobody has run the cases, which
  is an open question rather than a verdict. `required` is false when the stage
  gate does not ask for evaluations at all. `?stage=` selects the lifecycle
  stage (default `production`).

- `GET /api/designer/systems/{system_id}/diff?from=<version>&to=<version>` —
  what moved between two stored revisions of this design, as resolved IR rather
  than as text:

  ```json
  {"changes": [{"severity": "high", "direction": "widened", "path": "agent:analyst.permissions", "summary": "permission granted: read:data_class:customer_pii", "rationale": "…", "security_relevant": true}],
   "summary": {"total": 2, "security_findings": 1, "worst_severity": "high"}}
  ```

  Omitting both versions means the previous version against the current one; a
  design with only one version compares against itself and reports an empty
  diff. Changes come back worst first, and only a widening is a security
  finding.

Both follow the designer's own RBAC and workspace scoping: each request takes
the `system.view` check, so a user who may not read the system may not read its
gate or its diff. Their refusals are distinct on purpose — `404` for an unknown
system or version, `409` for an `IncomparableIRError` (two revisions that are
not two versions of one thing, carrying the refusal's own reasoning), and `422`
for a spec that does not compile yet, which is the normal state of a design
mid-edit and is reported with the loader's own message.

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

## The visual system — "instrument panel"

One token layer at the top of `web/styles.css` declares the whole palette, the
type scale and the spacing step; every component rule below it is built from
those tokens, and neither `app.js` nor `canvas.js` contains a colour at all.
`tests/test_designer_visual_system.py` holds that from outside: a hex below
`:root`, or one anywhere in the JS, fails.

Two rules the tokens encode:

- **Shape says what a thing is; colour says what state it is in.** A team is a
  dashed container, an agent a solid card whose left stripe is its highest data
  classification, a mission a capsule that always carries its end date, a
  sandbox a rounded card with its network posture on its face, an endpoint a
  chevron. Edges: solid is a reporting line, dashed a mission peer, dotted
  amber an egress. The accent (teal-ink `#4FB3A3`) marks selection and the
  primary action and is therefore *never* a state, which is why the semantic
  four — moss approved/built/fresh, amber proposed/stale/locked, clay
  refused/widened/failed, slate retired/unobserved — are separate hues.
- **Where the platform refuses, the refusal's own words and the way forward.**
  The catalog drawer is the worked example: editorial fields stay live,
  substantive ones are rendered `disabled` before anything is typed, and the
  API's `amend_refusal` sits above both remedies as buttons.

Interface type is Archivo; anything a machine decided — ids, versions,
permission keys, state chips, ADR references — is JetBrains Mono, so a value
reads differently from a label. Both are linked from `fonts.googleapis.com`,
the only external host the bundle uses.

The design names `#6B7681` for the smallest labels. On the graphite ground that
measures 4.09:1, so label text uses `--ink-dim` (`#7C8894`, 5.2:1) and
`--ink-faint` is kept for rules, icons and display sizes.

### The consequence rail

Beside the agent editor: what this change does, before it is saved. It reads
the two review routes below, leads with security-relevant findings, and renders
`not_evaluated` as its own amber state — a case nobody could verify is not a
case that failed. Both routes are treated as optional: a 404 or an absent
payload renders nothing rather than a broken panel.

## Notes
Dependency-free vanilla JS against the documented REST API, so it can be
replaced wholesale without touching the platform. The bundle is dark-only: the
instrument panel is one ground, not a theme pair. The operator's command centre is a separate
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
from outside the bundle, and `tests/test_designer_org_crud.py` holds the
organisation affordances the same way: the routes they call, the one state
behind the two selectors, the `canEdit` gate, and that refusal text is quoted
from the API rather than written in the bundle.

## Backend capability with no UI in front of it

| Capability | API | In the UI |
|---|---|---|
| Who am I, and with what role | `GET /api/designer/whoami` | Yes — the context bar, every view |
| Workspace membership | `GET/POST /workspaces`, `POST/DELETE .../members` | Yes — the Workspace view, offered only to `workspace.members` |
| Audit log — including refusals | `GET /api/designer/audit` | Yes — filtered, with denials marked |
| Lock heartbeat and break | `.../lock/heartbeat`, `.../lock/break` | Break yes; **no heartbeat is sent**, so a long edit can lose its lock |
| Revisions and restore | `.../revisions`, `.../restore/{v}` | Yes, but through a `prompt()` rather than a history panel |
| Create, rename, duplicate, delete an organisation | `POST/PUT/DELETE /systems` | Yes — the org chart toolbar, gated on `system.edit` |
| Evaluation gate and IR diff for the open design | `.../gate`, `.../diff` | Yes — the consequence rail beside the agent editor |

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

- the **evaluation gate** per agent (`orgagents gate`), now a real verdict —
  served at `GET /api/designer/systems/{id}/gate`, with no view in front of it
  yet;
- an **IR diff** against the previous version (`orgagents spec diff`), which is
  where a widened permission becomes visible — served at
  `GET /api/designer/systems/{id}/diff`, likewise unrendered;
- the **record graph** of ADRs and workstreams (WS-009 M5).

All three exist as libraries with CLI surfaces. The reviewer who most needs
them is looking at a screen.

## Organisation management: what it does not do

Duplicate copies the spec and the layout, and nothing else: the copy starts at
v1 with no revision history, and the source's locks and audit trail stay with
the source, which is what a copy should mean but is worth saying. There is no
per-organisation access within a workspace (WS-021 M4), so the gate on the
destructive controls is the workspace role. Deleting is permanent and the UI
says so; there is no archive or restore-after-delete route to offer.

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
includes the changes described above — organisation management among them,
which is asserted from outside the bundle and has never been clicked. The tests assert which routes the bundle
calls and which it must not; they say nothing about whether a single pixel
renders. Accessibility, keyboard navigation, undo/redo and behaviour on a large
organization remain unassessed.

## Authority

A tab that answers two questions the other views could not: what each agent may
**decide**, and how much of each activity it does without a person. Neither is
a permission — permission is whether the door opens, a mandate is whether you
were the one to open it.

What it shows is the **effective** mandate, resolved the way the phase gate
resolves it: the intersection with every unit above (ADR-0065). Rendering the
declaration would let a reader believe an agent holds something its line
excludes, which is the mistake this view exists to prevent. Each decision is
marked `declared here` or `inherited`, and the unit line is printed root-first
so a refusal can be explained to somebody who did not write the spec.

Alongside it:

* **Activities** carry their autonomy posture (ADR-0072) — advisory, human
  decides, supervised, autonomous — with the posture as a colour because it is
  a state, and the capability in mono because it is an identifier. An activity
  whose control belongs to an application says so and names the system
  (ADR-0073).
* **Separation of duties** lists each rule with its reason, since a rule
  without one is a rule nobody defends when it is inconvenient.
* **What the gate says** carries the authority and autonomy findings into the
  view where the thing they refuse is being edited, rather than leaving them in
  a validation log.

A design mid-edit legitimately does not resolve. That renders as an empty view
with a line saying so, not an error somebody has to dismiss.

### Editing authority

The palette gained `decision` and `separation` kinds, `mandate` on teams and
agents, and `autonomy` on an agent. Two of those needed real controls rather
than a text box, because each carries a distinction a plain field would
flatten.

**The mandate control asks its question outright.** A mandate that is *absent*
inherits its parent's; one that is present and empty decides nothing. Those are
opposite meanings, and a picker showing an empty list cannot tell you which it
means — which is how Corporate Development, written as advisory in the worked
finance example, inherited the root's entire authority. So the control carries
a `declare a mandate here` toggle: off writes `null` and says *inherits its
parent's mandate*; on writes `{decisions: []}` and says *this unit decides
nothing* until decisions are picked.

**The posture selector offers only tighter options.** An assignment may tighten
what a capability declares and never loosen it (ADR-0072 rule 6), so the
loosening options are not offered at all — a control that looks available and is
then refused by the gate is the bug. Each row shows the capability, an
`as declared — <posture>` default, and the postures below it; a capability
already at `advisory` says *already the tightest*.

A separation's `decisions` is a plain reference list over the declared
vocabulary, not a mandate: there is no inherit-or-empty question to ask, so the
two use different field types.

## Review, 2026-09-21 — where this stands

A holistic pass over the component: ~2,800 lines of front end, 2,400 of
designer service, 23 routes, nine test files. Recorded here rather than in a
ticket because most of it is a judgement about scope, not a defect list.

**What is solid.** The design/runtime separation is real and tested from the
outside — design views reach `/api/designer` and never a runtime endpoint for
a design fact. Concurrency is properly handled: locks with heartbeats, a break
path, three-way merge and revisions with restore. The consequence rail, the
validation strip and the authority view mean a designer sees what a change
*does*, not only what it says. RBAC, audit and OIDC are in place.

**The largest gap is coverage.** The palette offers 16 spec kinds against
roughly 30 authored blocks. Missing, in rough order of how often somebody
needs them: `guardrails`, `lifecycle` (gates and evaluation cases), `policies`,
`skills`/`plugins`/`tools`, `output_contracts`, `model_policy`, `budgets`,
`operating_principles`, `artifact_stores`/`context`, `observability`. A design
authored entirely in the UI cannot declare a guardrail — and cannot declare an
evaluation case, which autonomy now *requires* (ADR-0072 rule 5), so the
designer can set a posture it cannot satisfy.

**The platform policy is invisible here.** A design is judged by house rules
(ADR-0076) whose verdict the designer never shows, so the first anybody learns
of a refusal is at compile. The `authority` route already carries findings;
the same shape would carry the policy's stamp, its strictness and what it
lowered.

**Accessibility is thin.** Fourteen `aria-` attributes and two `:focus` rules
across the bundle. The canvas has a keyboard path for selection and nudging,
which is more than most canvases manage, and creating a node still needs a
pointer.

**No undo.** Revisions cover the catastrophic case; a misdrag has no
one-keystroke answer, which is the most-used affordance in every comparable
tool.
