/* The agentic designer canvas: drag-and-drop components, forms, and
   multi-user save with locking and conflict resolution.

   The frontend holds no rules: every permission, lock and merge decision comes
   from /api/designer (ADR-0031). This file only draws and asks. */

const DAPI = "/api/designer";

const canvas = {
  user: "ana",
  workspaces: [],
  workspaceId: null,
  systems: [],
  systemId: null,
  record: null,          // the open SystemRecord
  palette: null,
  selected: null,        // { kind, id }
  role: null,
  permissions: [],
  locks: [],
  conflicts: [],
  //: `{id, kind}` while a link is being drawn from a node, else null.
  linking: null,
  mergedSpec: null,
  resolutions: {},
  dirty: false,
  heartbeat: null,
};

/* ------------------------------------------------------------- transport */
/* A file the server sends to be saved, not parsed: the export (ADR-0113).
   Fetched with the same headers as every other call, then handed to the
   browser as a download named by the server's Content-Disposition. */
async function dfile(path) {
  const res = await fetch(DAPI + path, {
    headers: { "X-User": canvas.user, "X-User-Name": canvas.user },
  });
  const text = await res.text();
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = JSON.parse(text).detail || detail; } catch { /* not JSON */ }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  const named = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
  const filename = named ? named[1] : path.split("/").pop();
  const url = URL.createObjectURL(new Blob([text], {
    type: res.headers.get("Content-Type") || "text/plain" }));
  const link = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return { filename, size: text.length };
}

async function dapi(path, options = {}) {
  const res = await fetch(DAPI + path, {
    headers: {
      "Content-Type": "application/json",
      "X-User": canvas.user,
      "X-User-Name": canvas.user,
    },
    ...options,
  });
  const text = await res.text();
  /* A 500 is not JSON. Parsing unconditionally turned every server fault into
     `Unexpected token 'I', "Internal S"... is not valid JSON`, which tells the
     person nothing and sent the real cause to the server log alone — that is
     how a save that had been broken for months read as a parse error. */
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    if (res.ok) throw new Error(`${path} returned a body that is not JSON`);
    const err = new Error(
      `the server failed (${res.status} ${res.statusText || "error"}). `
      + "Its log has the reason.");
    err.status = res.status;
    err.detail = text.slice(0, 300);
    throw err;
  }
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : res.statusText;
    /* A structured refusal carries its sentence in `error` — a lock conflict
       sends `{error, lock}`. Stringifying the whole object put raw JSON in
       front of a person: the "Break the lock?" prompt read
       `{"error":"'*' is locked by ben until 2026-…","lock":{"id":"lck_…`.
       The sentence is the message; the object stays on `detail` for code. */
    /* A field-by-field refusal carries `{message, fields}` (ADR-0106), and
       the framework's own validation a list of `{loc, msg}`; both keep their
       structure on `detail` so a form can put each under its field. */
    const message = typeof detail === "string"
      ? detail
      : (detail && typeof detail.error === "string"
          ? detail.error
          : detail && typeof detail.message === "string"
            ? detail.message
            : Array.isArray(detail)
              ? detail.map((d) => `${(d.loc || []).slice(-1)[0] || ""}: `
                  + `${d.msg || ""}`).join("; ")
              : JSON.stringify(detail));
    const err = new Error(message);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return body;
}

/* The design views outside this file (org chart, agent editor) read and write
   the very same `canvas.record.spec`, so they are told whenever it changes
   rather than keeping a copy that could disagree with the canvas. */
function announce(reason) {
  document.dispatchEvent(new CustomEvent("designer:changed", { detail: { reason } }));
}

/* ------------------------------------------------------------ spec model */
/* The canvas edits the spec document directly; these helpers find and mutate
   the right collection for a component kind. */

const COLLECTIONS = {
  capability: "capabilities",
  data_class: "data_classes",
  environment: "environments",
  role: "role_definitions",
  channel: "channels",
  trigger: "triggers",
  knowledge: "knowledge",
  endpoint: "endpoints",
  workflow: "workflows",
  decision: "decisions",
  separation: "separations",
  person: "people",
  policy: "policies",
  mission: "missions",
  guardrail: "guardrails",
  output_contract: "output_contracts",
  skill: "skills",
  plugin: "plugins",
  tool: "tools",
};

/* Blocks that are not a top-level list. Each needs its container built on the
   way in, which is why they cannot just join COLLECTIONS. */
const NESTED = {
  memory_namespace: {
    container: (s) => (org(s).memory = org(s).memory || {}),
    list: "namespaces",
    seed: (id) => ({ id, scope: "private", data_classes: [] }),
  },
  evaluation: {
    container: (s) => (s.lifecycle = s.lifecycle || {}),
    list: "evaluations",
    // An evaluation case with no `applies_to` applies to every agent, which
    // is a wider claim than anybody means by dropping one on a canvas.
    seed: (id) => ({ id, given: "", expect: "", applies_to: [] }),
  },
};

function nestedList(s, kind) {
  const shape = NESTED[kind];
  if (!shape) return null;
  const container = shape.container(s);
  container[shape.list] = container[shape.list] || [];
  return container[shape.list];
}

function spec() {
  return canvas.record ? canvas.record.spec : null;
}

/* The Organization owns every element of the model (ADR-0101): skills,
   knowledge, flows and the rest live under `spec.organization`, not at the
   top of the spec. */
function org(s) {
  if (!s) return null;
  s.organization = s.organization || { id: "root", name: "root" };
  return s.organization;
}

function walkTeams(team, fn, path = []) {
  if (!team) return;
  fn(team, path);
  (team.teams || []).forEach((child) => walkTeams(child, fn, [...path, team.id]));
}

function allTeams() {
  const out = [];
  walkTeams(spec()?.organization, (t) => out.push(t));
  return out;
}

function allAgents() {
  const out = [];
  walkTeams(spec()?.organization, (t) =>
    (t.members || []).forEach((m) => out.push({ agent: m, team: t })));
  return out;
}

/* ------------------------------------------------------------ diagrams */
/*
   A design has one model and many diagrams. That split is what makes an
   organisation of any size drawable: a single canvas holding every team,
   agent, capability, policy and endpoint is not a diagram, it is a haystack.

   `diagram()` is the one open. Every reader of node positions goes through
   it, because a second way to reach "the nodes" is a second thing to keep in
   step with the first.
*/
function diagram() {
  const layout = canvas.record?.layout;
  if (!layout) return null;
  if (!layout.diagrams || !Object.keys(layout.diagrams).length) {
    /* A record saved before diagrams existed. The server migrates one on
       read, so this is belt and braces for a hand-made payload. */
    layout.diagrams = { main: { id: "main", name: "Organisation", root: "",
                                nodes: {}, viewport: { x: 0, y: 0, zoom: 1 } } };
    layout.active = "main";
  }
  if (!layout.diagrams[layout.active]) {
    layout.active = Object.keys(layout.diagrams)[0];
  }
  return layout.diagrams[layout.active];
}

function diagramList() {
  const layout = canvas.record?.layout;
  if (!layout) return [];
  return Object.values(layout.diagrams || {});
}

function newDiagramId() {
  const taken = canvas.record?.layout?.diagrams || {};
  let n = 1;
  while (taken[`dia_${n}`]) n += 1;
  return `dia_${n}`;
}

/* A selection belongs to the diagram it was made on. Carrying it across meant
   the Properties panel described a component that is not on the canvas in
   front of you — which reads as "this is what you are looking at" and is not. */
function clearSelectionOffDiagram() {
  const open = diagram();
  if (!canvas.selected) return;
  if (open && open.nodes && open.nodes[canvas.selected.id]) return;
  canvas.selected = null;
  renderInspector();
}

function openDiagram(id) {
  const layout = canvas.record?.layout;
  if (!layout?.diagrams?.[id]) return;
  rememberViewport();          // leave the one you were on where you left it
  layout.active = id;
  canvas.selected = null;
  renderDiagramBar();
  renderCanvas();
  renderInspector();
  setStatus(`diagram: ${layout.diagrams[id].name}`);
}

/* A new diagram of a unit: the unit, what it holds, and nothing else.

   This is the drill-down a tree view gives you and a single canvas cannot.
   The nodes are laid out rather than dropped one at a time, because a
   diagram you have to rebuild by hand is one nobody makes. */
/* A process canvas: one workflow's steps, laid out as a flow.

   The steps become nodes and the workflow's own edges are derived, so this
   adds a picture without adding a fact — which is the split the diagram model
   is built on. */
function addProcessDiagram(workflowId) {
  const layout = canvas.record?.layout;
  const workflow = (org(spec())?.workflows || []).find((w) => w.id === workflowId);
  if (!layout || !workflow) {
    setStatus(`'${workflowId}' is not a workflow in this design`);
    return null;
  }
  const id = newDiagramId();
  const nodes = {};
  (workflow.graph?.nodes || []).forEach((step, index) => {
    if (!step.id) return;
    nodes[step.id] = {
      id: step.id, kind: "step", x: 60, y: 60 + index * 150,
      width: 200, height: 80, collapsed: false, note: "",
    };
  });
  layout.diagrams[id] = {
    id, name: workflow.name || workflow.id, kind: "process",
    root: workflow.id, nodes,
    viewport: { x: 0, y: 0, zoom: 1 },
  };
  layout.active = id;
  /* Same as opening one: the selection belonged to the diagram it was made
     on, and carrying it here would describe a component that is not on this
     canvas. */
  clearSelectionOffDiagram();
  markDirty(`added a process canvas for ${workflow.id}`);
  renderCanvas();
  /* Laid out by the product's own algorithm rather than the stack above: a
     governed process wants its owners' lanes and ranks along them
     (ADR-0110), and the stack is only somewhere for the nodes to be until
     the layout runs. */
  arrangeDiagram("lanes");
  return id;
}

function addDiagram(root = "", name = "") {
  const layout = canvas.record?.layout;
  if (!layout) return null;
  const team = root ? findComponent("team", root) : null;
  if (root && !team) {
    setStatus(`'${root}' is not a team, so it has no diagram of its own`);
    return null;
  }
  const id = newDiagramId();
  const nodes = {};
  let row = 0;
  const put = (componentId, kind, column) => {
    nodes[componentId] = {
      id: componentId, kind, x: 40 + column * 300, y: 60 + row * 110,
      width: 200, height: 80, collapsed: false, note: "",
    };
    row += 1;
  };
  if (team) {
    put(team.id, "team", 0);
    (team.members || []).forEach((m) => put(m.id, "agent", 1));
    (team.teams || []).forEach((t) => put(t.id, "team", 1));
  }
  layout.diagrams[id] = {
    id, root, nodes,
    name: name || (team ? (team.name || team.id) : `Diagram ${diagramList().length + 1}`),
    viewport: { x: 0, y: 0, zoom: 1 },
    updated_at: new Date().toISOString(),
  };
  markDirty(`added the diagram '${layout.diagrams[id].name}'`);
  openDiagram(id);
  return layout.diagrams[id];
}

function removeDiagram(id) {
  const layout = canvas.record?.layout;
  if (!layout?.diagrams?.[id]) return;
  if (diagramList().length === 1) {
    return ui.alertDialog("A design has at least one diagram. Rename this one, "
      + "or add another before removing it.", { title: "Cannot remove the last diagram" });
  }
  const name = layout.diagrams[id].name;
  /* Only the picture goes — every component on it stays declared and the
     Explorer still lists them — so it is undone rather than asked about. */
  delete layout.diagrams[id];
  if (layout.active === id) layout.active = Object.keys(layout.diagrams)[0];
  markDirty(`removed the diagram '${name}'`);
  renderDiagramBar();
  renderCanvas();
  renderInspector();
  offerUndo(`Removed the diagram '${name}'. Its components are still in the model.`);
}

async function renameDiagram(d) {
  const name = await ui.promptDialog("Rename diagram", {
    label: "Name", value: d.name, submitLabel: "Rename" });
  if (!name || name === d.name) return;
  d.name = name;
  markDirty("renamed a diagram");
  renderDiagramBar();
}

/* The tabs above the canvas, and the breadcrumb that says what a nested
   diagram is *of* — a diagram called "Treasury" showing four boxes is
   otherwise indistinguishable from a design that has only four boxes. */
function renderDiagramBar() {
  const bar = $("#diagram-bar");
  if (!bar) return;
  const layout = canvas.record?.layout;
  if (!layout) return bar.replaceChildren();
  const readOnly = !canvas.permissions.includes("system.edit");
  /* The diagram tabs are a tablist of their own; the buttons after them
     (add, arrange) are a toolbar, not tabs, so they sit outside it. F2 and
     Delete do on a focused tab what double-click and right-click do. */
  const tabs = el("div", { class: "dia-tabs" },
    ...diagramList().map((d) => {
      const tab = el("button", {
        class: `dia-tab${d.id === layout.active ? " active" : ""}`,
        title: d.root ? `a diagram of ${d.root}` : "the whole organisation",
      }, d.root ? el("span", { class: "ic", "aria-hidden": "true" }, "\u21b3") : null, d.name);
      tab.addEventListener("click", () => {
        if (d.id !== layout.active) openDiagram(d.id);
      });
      tab.addEventListener("dblclick", () => { if (!readOnly) renameDiagram(d); });
      tab.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        if (!readOnly) removeDiagram(d.id);
      });
      tab.addEventListener("keydown", (e) => {
        if (readOnly) return;
        if (e.key === "F2") { e.preventDefault(); renameDiagram(d); }
        else if (e.key === "Delete") { e.preventDefault(); removeDiagram(d.id); }
      });
      return tab;
    }));
  const focusedTab = bar.contains(document.activeElement)
    && document.activeElement.classList.contains("dia-tab");
  bar.replaceChildren(
    tabs,
    el("button", {
      class: "dia-add", title: "a new, empty diagram of this design",
      disabled: readOnly ? "" : null,
      onclick: () => addDiagram(""),
    }, "+"),
    /* A process gets its own canvas, because a workflow's graph is a
       different thing from an organisation's containment and drawing them on
       one canvas produces a picture nobody can predict (ADR-0100). */
    ...((org(spec())?.workflows || []).length && !readOnly ? [(() => {
      const pick = el("select", { class: "dia-add-process",
                                  title: "a canvas for one workflow's process" },
        el("option", { value: "" }, "+ process"),
        ...(org(spec()).workflows || []).map((w) =>
          el("option", { value: w.id }, w.name || w.id)));
      pick.addEventListener("change", () => {
        if (pick.value) addProcessDiagram(pick.value);
        pick.value = "";
      });
      return pick;
    })()] : []),
    /* The Data and Deployment aspects draw the whole design, so each has one
       diagram: the button opens it, adding what the model has gained since
       (ADR-0111, ADR-0112 M7). */
    ...["data", "deployment"].map((kind) => el("button", {
      class: "dia-aspect", "data-aspect": kind,
      title: `${diagramOfKind(kind) ? "open" : "add"} the ${ASPECTS[kind].label} diagram — `
        + ASPECTS[kind].help,
      disabled: readOnly && !diagramOfKind(kind) ? "" : null,
      onclick: () => openOrAddAspect(kind),
    }, `${diagramOfKind(kind) ? "" : "+ "}${ASPECTS[kind].label.toLowerCase()}`)),
    /* Arrange. The layout runs on the server, where it is a function from a
       graph to coordinates and can be asserted about; a layout that ran only
       here is the one part of this platform nothing would check (ADR-0100).

       It moves nodes, so it is an edit: it marks the design dirty and undoes
       like any other. A layout that could not be undone would make people
       afraid of the button, and the first thing anybody does with a new
       arrange button is press it on a diagram they spent an hour on. */
    el("span", { class: "dia-spacer" }),
    ...(readOnly ? [] : Object.entries({
      ...(diagram()?.kind === "process"
          ? { lanes: "one lane per owner, the flow left to right (ADR-0110)" } : {}),
      ...(diagram()?.kind === "data"
          ? { derivation: "lineage left to right: base before derived, "
                + "producer before its data, data before who relies on it (ADR-0111)" }
          : {}),
      ...(diagram()?.kind === "deployment"
          ? { deployment: "a column per server or engine, what is deployed on it "
                + "stacked beneath (ADR-0112)" }
          : {}),
      tree: "parents over children, depth down the page",
      layered: "ranked by flow; a loop back is drawn, not ranked",
      grid: "reading order, for a set with no structure to honour",
    }).map(([name, why]) => {
      const button = el("button", { class: "dia-arrange", title: why }, name);
      button.addEventListener("click", () => arrangeDiagram(name));
      return button;
    })));
  ui.wireTabs(tabs, { label: "Diagrams", panel: () => $("#canvas") });
  if (focusedTab) tabs.querySelector(".dia-tab.active")?.focus();
  /* The legend shows the rows of the aspect that is open. */
  const legend = document.querySelector(".canvas-legend");
  if (legend) legend.dataset.aspect = aspectOf();
}

/* ------------------------------------------------------------ arranging */

async function arrangeDiagram(algorithm) {
  const current = diagram();
  if (!current) return;
  const nodes = Object.keys(current.nodes || {});
  if (!nodes.length) return setStatus("nothing on this diagram to arrange");

  /* The parent each node sits under, and the edges between them, are read
     from the spec — the same derivation the canvas draws from, so the layout
     is laid out over the picture people are actually looking at. */
  const parentOf = {};
  for (const team of allTeams()) {
    for (const member of team.members || []) parentOf[member.id] = team.id;
    for (const sub of team.teams || []) parentOf[sub.id] = team.id;
  }
  const kind = current.kind || "organisation";
  /* A deployment's containment is the binding's, not the organisation's:
     a target over its servers and environments, a server over what is
     deployed on it, an engine over the workflows it runs. */
  if (kind === "deployment") {
    for (const key of Object.keys(parentOf)) delete parentOf[key];
    for (const entry of deploymentModel().nodes.values()) {
      if (entry.parent) parentOf[entry.id] = entry.parent;
    }
  }
  const edges = kind === "process" ? processEdges(current.root)
    : kind === "data" ? dataLayoutEdges() : [];
  /* Declaration order is the layout's tie-break: data classes first, in
     the order the spec declares them, then the agents. */
  if (kind === "data") {
    const order = new Map([...dataClasses().map((d) => d.id),
                           ...dataAgents().map((a) => a.id)].map((id, i) => [id, i]));
    nodes.sort((a, b) => (order.get(a) ?? 1e9) - (order.get(b) ?? 1e9));
  }
  /* A process is laid out from its entry, and each step carries the lane of
     its owner; a step with no owner of its own sits with what leads to it. */
  const lanes = kind === "process" ? processLanes(current.root) : {};
  if (kind === "process") {
    const entry = processGraph(current.root).entry;
    nodes.sort((a, b) => (b === entry) - (a === entry));
  }

  try {
    const result = await dapi("/layout", {
      method: "POST",
      body: JSON.stringify({
        kind, algorithm,
        nodes: nodes.map((id) => ({ id, parent: parentOf[id] || null,
                                    lane: lanes[id]?.key || "" })),
        edges,
      }),
    });
    /* Layouts start at zero; these diagrams keep a margin, so the first
       column's boxes and a «deploy» label above the top row are not drawn
       against the canvas's edge. */
    const pad = kind === "data" || kind === "deployment" ? 40 : 0;
    for (const [id, at] of Object.entries(result.positions || {})) {
      if (!current.nodes[id]) continue;
      current.nodes[id].x = at.x + pad;
      current.nodes[id].y = at.y + pad;
    }
    markDirty(`arranged with ${result.algorithm}`);
    renderCanvas();
    /* Put the reader at the start of what was just arranged. There is no
       zoom on this canvas — `Layout.viewport.zoom` is persisted and read by
       nothing, which is its own gap — so a large diagram is navigated by
       scrolling and by the outline, and the least a rearrangement can do is
       not leave you looking at empty grid. */
    const surface = $("#canvas");
    if (surface) {
      const xs = Object.values(result.positions || {});
      /* A process keeps its initial node and lane headers in view. */
      const margin = kind === "process" ? 200 : 40;
      surface.scrollLeft = Math.max(0, Math.min(...xs.map((p) => p.x)) - margin);
      surface.scrollTop = Math.max(0, Math.min(...xs.map((p) => p.y)) - 40);
    }
    setStatus(result.notes?.length
      ? `${result.algorithm}: ${result.notes[0]}`
      : `arranged with ${result.algorithm}`);
    offerUndo(`Arranged with ${result.algorithm}.`);
  } catch (err) {
    setStatus(`could not arrange: ${err.message}`);
  }
}

/* A process diagram's edges are the workflow's own, read from the spec and
   never stored on the diagram. That is the one rule the diagram model has,
   and a process canvas is exactly where a second copy would drift. */
function processEdges(workflowId) {
  const workflow = (org(spec())?.workflows || []).find((w) => w.id === workflowId);
  const graph = workflow?.graph || {};
  const edges = (graph.edges || [])
    .filter((e) => e.from && e.to && e.to !== "END")
    .map((e) => ({ source: e.from, target: e.to, kind: "flow" }));
  /* A branch's arms are ways out too, and drawing them differently from a
     plain edge is the point: one of them is taken, not all of them. */
  for (const node of graph.nodes || []) {
    if (node.kind !== "branch") continue;
    for (const c of node.cases || []) {
      if (c.to && c.to !== "END") {
        edges.push({ source: node.id, target: c.to, kind: "branch_case",
                     label: c.when || "" });
      }
    }
    if (node.default && node.default !== "END") {
      edges.push({ source: node.id, target: node.default, kind: "branch_case",
                   label: "else" });
    }
  }
  return edges;
}


/* ------------------------------------------ the process view (ADR-0110)

   A governed workflow is drawn as a UML activity: an initial node at the
   entry, a final node where it ends, one swimlane per owner, the flow left
   to right. A loop back is routed below the lanes, not through the steps
   between, and labelled with the condition that takes it. */

function processGraph(workflowId) {
  const workflow = (org(spec())?.workflows || []).find((w) => w.id === workflowId);
  const graph = workflow?.graph || {};
  const nodes = graph.nodes || [];
  return { entry: graph.entry || nodes[0]?.id || "", nodes, edges: graph.edges || [] };
}

/* The lane a step is drawn in: its owner's team, or the owner itself when
   it has no team. */
function ownerLane(ownerId) {
  if (!ownerId) return null;
  const teams = allTeams();
  const team = (id) => teams.find((t) => t.id === id);
  const teamLane = (t) => ({ key: `team:${t.id}`, kind: "team", label: t.name || t.id });
  if (team(ownerId)) return teamLane(team(ownerId));
  const home = teams.find((t) => (t.members || []).some((m) => m.id === ownerId));
  if (home) return teamLane(home);
  if (allAgents().some(({ agent }) => agent.id === ownerId)) {
    return { key: `agent:${ownerId}`, kind: "agent", label: ownerId };
  }
  const person = (org(spec())?.people || []).find((p) => p.id === ownerId);
  if (person) {
    if (person.unit && team(person.unit)) return teamLane(team(person.unit));
    return { key: `person:${person.id}`, kind: "person", label: person.name || person.id };
  }
  return { key: `owner:${ownerId}`, kind: "person", label: ownerId };
}

function stepOwnerId(step) {
  return step.owner || (step.kind === "agent" ? step.agent : "")
    || (step.kind === "human" ? step.person : "") || "";
}

/* Every step's lane, a step with no owner of its own sitting with the step
   that leads to it (the server's `lanes` layout follows the same rule). */
function processLanes(workflowId) {
  const { nodes, edges } = processGraph(workflowId);
  const lane = {};
  for (const n of nodes) {
    const own = ownerLane(stepOwnerId(n))
      || (n.kind === "human" && n.role
          ? { key: `role:${n.role}`, kind: "person", label: `role ${n.role}` } : null);
    if (own) lane[n.id] = own;
  }
  const into = {};
  for (const e of processEdges(workflowId)) (into[e.target] = into[e.target] || []).push(e.source);
  for (let pass = 0; pass < nodes.length; pass += 1) {
    let changed = false;
    for (const n of nodes) {
      if (lane[n.id]) continue;
      const from = (into[n.id] || []).find((p) => lane[p]);
      if (from) { lane[n.id] = lane[from]; changed = true; }
    }
    if (!changed) break;
  }
  return lane;
}

/* A step's box as drawn, measured where it can be. */
function stepBox(node) {
  const box = document.querySelector(`#canvas-nodes [data-id="${CSS.escape(node.id)}"]`);
  const w = box?.offsetWidth || node.width || 200;
  const h = box?.offsetHeight || 84;
  return { x: node.x, y: node.y, w, h, cx: node.x + w / 2, cy: node.y + h / 2 };
}

const LANE_HEAD = 130, LANE_PAD = 22;

function laneBands(layout) {
  const lanes = processLanes(layout.root);
  const groups = {};
  for (const node of Object.values(layout.nodes || {})) {
    const lane = lanes[node.id];
    if (!lane) continue;
    const b = stepBox(node);
    const g = (groups[lane.key] ||= { ...lane, top: Infinity, bottom: -Infinity, steps: [] });
    g.top = Math.min(g.top, b.y - LANE_PAD);
    g.bottom = Math.max(g.bottom, b.y + b.h + LANE_PAD);
    g.steps.push(node.id);
  }
  const bands = Object.values(groups).sort((a, b) => a.top - b.top);
  /* Lanes laid out by `lanes` touch; close the small gaps measuring leaves,
     so the lanes read as one pool. */
  for (let i = 0; i + 1 < bands.length; i += 1) {
    const gap = bands[i + 1].top - bands[i].bottom;
    if (gap > 0 && gap < 4 * LANE_PAD) {
      bands[i].bottom += gap / 2;
      bands[i + 1].top -= gap / 2;
    }
  }
  return bands;
}

function processExtent(layout) {
  const boxes = Object.values(layout.nodes || {}).map(stepBox);
  if (!boxes.length) return null;
  return {
    left: Math.max(0, Math.min(...boxes.map((b) => b.x)) - LANE_HEAD - 20),
    right: Math.max(...boxes.map((b) => b.x + b.w)) + 110,
    bottom: Math.max(...boxes.map((b) => b.y + b.h)),
  };
}

function renderLanes(host) {
  const layout = diagram();
  const extent = layout && processExtent(layout);
  if (!extent) return host.replaceChildren();
  host.replaceChildren(...laneBands(layout).map((lane) =>
    el("div", {
      class: "lane", "data-lane-kind": lane.kind,
      style: `left:${extent.left}px; top:${lane.top}px; `
        + `width:${extent.right - extent.left}px; height:${lane.bottom - lane.top}px`,
      title: `${lane.label}: ${lane.steps.length} step(s) owned here`,
    },
      el("span", { class: "lane-head" },
        el("span", { class: "lane-kind" }, lane.kind), lane.label))));
}

function renderProcessEdges(svg, layout) {
  const ns = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs, styles = {}, text) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    Object.assign(n.style, styles);
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const parts = [];
  const defs = mk("defs", {});
  for (const [id, color] of [["pv-arrow", "--edge-report"], ["pv-arrow-case", "--edge-peer"],
                             ["pv-arrow-loop", "--amber"]]) {
    const marker = mk("marker", { id, viewBox: "0 0 10 10", refX: "9", refY: "5",
                                  markerWidth: "7", markerHeight: "7",
                                  orient: "auto-start-reverse" });
    marker.appendChild(mk("path", { d: "M0,0 L10,5 L0,10 z" }, { fill: `var(${color})` }));
    defs.appendChild(marker);
  }
  parts.push(defs);
  const nodes = layout.nodes || {};
  const extent = processExtent(layout);
  if (!extent) return svg.replaceChildren(...parts);
  const bands = laneBands(layout);
  const floor = Math.max(extent.bottom, ...bands.map((b) => b.bottom));
  let loops = 0;

  for (const edge of derivedEdges().filter(edgeShown)) {
    const a = nodes[edge.source], b = nodes[edge.target];
    if (!a || !b) continue;
    const s = stepBox(a), t = stepBox(b);
    const isCase = edge.kind === "branch_case";
    const back = t.x <= s.x;
    const color = back ? "--amber" : isCase ? "--edge-peer" : "--edge-report";
    let d, lx, ly;
    if (back) {
      /* Routed below every lane and back up, so it never crosses a step. */
      loops += 1;
      const y = floor + 44 + loops * 16;
      d = `M ${s.cx} ${s.y + s.h} C ${s.cx} ${y}, ${s.cx} ${y}, ${s.cx - 20} ${y} `
        + `L ${t.cx + 20} ${y} C ${t.cx} ${y}, ${t.cx} ${y}, ${t.cx} ${t.y + t.h}`;
      lx = (s.cx + t.cx) / 2; ly = y - 5;
    } else {
      const x1 = s.x + s.w, y1 = s.cy, x2 = t.x, y2 = t.cy;
      const bend = Math.max(30, (x2 - x1) / 2);
      d = `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
      lx = (x1 + x2) / 2; ly = (y1 + y2) / 2 - 6;
    }
    parts.push(mk("path", {
      d, fill: "none", "stroke-width": "1.6", class: `pv-edge${back ? " pv-loop" : ""}`,
      "marker-end": `url(#${back ? "pv-arrow-loop" : isCase ? "pv-arrow-case" : "pv-arrow"})`,
      ...(isCase || back ? { "stroke-dasharray": back ? "6 4" : "5 4" } : {}),
    }, { stroke: `var(${color})` }));
    const label = edge.label || (back ? "loop" : "");
    if (label) {
      const full = back && edge.label ? `↺ ${edge.label}` : back ? "↺ loop" : label;
      const text = mk("text", { x: lx, y: ly, "text-anchor": "middle", class: "edge-label pv-label" },
                      { fill: `var(${back ? "--amber-ink" : "--ink-muted"})` },
                      full.length > 26 ? `${full.slice(0, 25)}…` : full);
      text.appendChild(mk("title", {}, {}, full));
      parts.push(text);
    }
  }

  /* The initial node at the entry, and a final node after every step that
     ends the workflow. */
  const graph = processGraph(layout.root);
  const entry = nodes[graph.entry];
  if (entry) {
    const e = stepBox(entry);
    parts.push(mk("circle", { cx: e.x - 20, cy: e.cy, r: 7, class: "pv-initial" },
                  { fill: "var(--ink)" }));
    parts.push(mk("path", { d: `M ${e.x - 13} ${e.cy} L ${e.x - 2} ${e.cy}`,
                            "stroke-width": "1.6", "marker-end": "url(#pv-arrow)" },
                  { stroke: "var(--edge-report)" }));
  }
  /* Where a step ends the workflow: to its right, or — for a decision,
     whose ways out already leave to the right — below it, labelled with the
     case that ends it. */
  const ends = new Map(graph.edges.filter((e) => e.to === "END")
    .map((e) => [e.from, ""]));
  for (const n of graph.nodes) {
    if (n.kind !== "branch") continue;
    const c = (n.cases || []).find((x) => x.to === "END");
    if (c) ends.set(n.id, c.when || "");
    else if (n.default === "END") ends.set(n.id, "else");
  }
  const bullseye = (fx, fy) => {
    parts.push(mk("circle", { cx: fx, cy: fy, r: 11, fill: "none", "stroke-width": "2",
                              class: "pv-final" }, { stroke: "var(--ink)" }));
    parts.push(mk("circle", { cx: fx, cy: fy, r: 6, class: "pv-final" },
                  { fill: "var(--ink)" }));
  };
  for (const [id, label] of ends) {
    const node = nodes[id];
    if (!node) continue;
    const e = stepBox(node);
    const below = graph.nodes.find((n) => n.id === id)?.kind === "branch";
    if (below) {
      const fy = e.y + e.h + 36;
      parts.push(mk("path", { d: `M ${e.cx} ${e.y + e.h} L ${e.cx} ${fy - 13}`,
                              "stroke-width": "1.6", "stroke-dasharray": "5 4",
                              "marker-end": "url(#pv-arrow-case)" },
                    { stroke: "var(--edge-peer)" }));
      if (label) {
        parts.push(mk("text", { x: e.cx + 8, y: e.y + e.h + 18, class: "edge-label pv-label" },
                      { fill: "var(--ink-muted)" }, label));
      }
      bullseye(e.cx, fy);
    } else {
      const fx = e.x + e.w + 46;
      parts.push(mk("path", { d: `M ${e.x + e.w} ${e.cy} L ${fx - 13} ${e.cy}`,
                              "stroke-width": "1.6", "marker-end": "url(#pv-arrow)" },
                    { stroke: "var(--edge-report)" }));
      bullseye(fx, e.cy);
    }
  }
  svg.replaceChildren(...parts);
}

/* ------------------------------------------ aspects and their profiles
   (ADR-0100, ADR-0112 M7)

   Every diagram is of one aspect, and each aspect declares the UML profiles
   it draws: the palette offers only those profiles' stereotypes, grouped by
   profile, so what can be dropped on a canvas is what that canvas is about.
   `Core` (the canvas note) is annotation and goes everywhere. */
const ASPECTS = {
  organisation: {
    label: "Organisation", profiles: ["Organisation", "Authority", "Access"],
    arrange: "tree",
    help: "who is in the organisation, what they may decide and reach",
  },
  process: {
    label: "Process", profiles: ["Process"], arrange: "lanes",
    help: "one governed workflow: its steps, owners and flow (ADR-0110)",
  },
  data: {
    label: "Data", profiles: ["Data"], arrange: "derivation",
    help: "the conceptual data model: classes, lineage, producers and "
      + "consumers (ADR-0111)",
  },
  deployment: {
    label: "Deployment", profiles: ["Deployment"], arrange: "deployment",
    help: "the binding: targets, servers and what is deployed on them "
      + "(ADR-0112)",
    readOnly: true,
  },
};

function aspectOf(d = diagram()) {
  return ASPECTS[d?.kind] ? d.kind : "organisation";
}

/* Profiles no aspect draws yet (Knowledge, Assurance, and the Process
   profile's organisation-level elements). They stay placeable on the
   Organisation diagram, under a closed "Other profiles" group, until they
   have a diagram of their own — a stereotype nobody can place is a model
   the designer does not show (ADR-0111 v1.1.0). */
function undrawnProfiles() {
  const drawn = new Set(["Core", ...ASPECTS.organisation.profiles,
                         ...ASPECTS.data.profiles, ...ASPECTS.deployment.profiles]);
  const all = new Set(Object.values(canvas.palette?.profiles || {}));
  return [...all].filter((p) => !drawn.has(p));
}

/* The one diagram of an aspect a design has, if any. Data and Deployment
   draw the whole design, so a second one would be the same picture. */
function diagramOfKind(kind) {
  return diagramList().find((d) => d.kind === kind) || null;
}

function openOrAddAspect(kind) {
  const existing = diagramOfKind(kind);
  if (kind === "data") return addDataDiagram(existing);
  if (kind === "deployment") return addDeploymentDiagram(existing);
  return null;
}

/* ------------------------------------------- the Data diagram (ADR-0111)

   The conceptual data model as a UML class diagram: every data class, its
   classification as the stripe an agent's uses (ADR-0080), its semantics as
   a stereotype; the four relations in their UML notation; the agents that
   produce and rely on each class, a reliance drawn as the association class
   it is (fields, freshness). Nothing here is stored but positions: every
   edge is read from the spec. */

function dataClasses() {
  return org(spec())?.data_classes || [];
}

function dataClassById() {
  return Object.fromEntries(dataClasses().map((d) => [d.id, d]));
}

/* The agents on a Data diagram: those that produce or rely on a class. */
function dataAgents() {
  return allAgents().map(({ agent }) => agent).filter((a) =>
    (a.produces_data || []).length || (a.data_dependencies || []).length);
}

/* The link rule for one data-class relation, by its `kind`. */
function dataRelationRule(kind) {
  return (canvas.palette?.links || []).find((r) =>
    r.source === "data_class" && r.target === "data_class"
    && r.selector && r.selector[1] === kind) || null;
}

/* UML notation per relation (ADR-0111): «derive» a dashed arrow with an open
   head at the class derived from; part_of a filled diamond at the whole;
   the associations plain lines. */
const DATA_NOTATION = {
  derived_from: { label: "«derive»", head: "open", dash: "6 4",
                  stroke: "--edge-report" },
  part_of: { label: "", head: "diamond", stroke: "--edge-report" },
  identifies: { label: "«identifies»", head: "", stroke: "--edge-peer" },
  references: { label: "references", head: "", stroke: "--edge-peer" },
};

function freshness(seconds) {
  if (seconds === null || seconds === undefined || seconds === "") return "";
  const s = Number(seconds);
  if (s % 86400 === 0) return `≤ ${s / 86400} d`;
  if (s % 3600 === 0) return `≤ ${s / 3600} h`;
  if (s % 60 === 0) return `≤ ${s / 60} min`;
  return `≤ ${s} s`;
}

function dataEdges() {
  const out = [];
  for (const dc of dataClasses()) {
    for (const r of dc.relations || []) {
      if (!r?.target) continue;
      const kind = r.kind || "references";
      const rule = dataRelationRule(kind);
      const look = DATA_NOTATION[kind] || DATA_NOTATION.references;
      out.push({
        source: dc.id, target: r.target, kind: `rel:relations.${kind}`,
        uml: rule?.uml || "association", rel: rule?.label || kind,
        label: look.label, head: look.head, dash: look.dash || "",
        stroke: look.stroke, relation: kind,
      });
    }
  }
  for (const agent of dataAgents()) {
    for (const id of agent.produces_data || []) {
      out.push({ source: agent.id, target: id, kind: "rel:produces_data",
                 uml: "association", rel: "produces", label: "produces",
                 stroke: "--edge-peer" });
    }
    for (const dep of agent.data_dependencies || []) {
      if (!dep?.data_class) continue;
      /* The association class: the reliance carries attributes of its own,
         and they are the point of drawing it (ADR-0099). */
      const fields = (dep.fields || []).length
        ? dep.fields.join(", ") : "the whole class";
      const when = [freshness(dep.max_age_seconds), dep.on_stale || "refuse"]
        .filter(Boolean).join(" · on stale ");
      out.push({ source: agent.id, target: dep.data_class,
                 kind: "rel:data_dependencies", uml: "association",
                 rel: "relies on", label: "relies on", stroke: "--edge-peer",
                 assocClass: { name: "«DataDependency»",
                               lines: [fields, when].filter(Boolean),
                               producer: dep.produced_by || "" } });
    }
  }
  return out;
}

/* What the layout ranks: base before derived, part before whole, producer
   before its data, data before who relies on it — so lineage reads left to
   right. The associations that carry no direction are not ranked. */
function dataLayoutEdges() {
  const out = [];
  for (const e of dataEdges()) {
    if (e.relation === "derived_from") out.push({ source: e.target, target: e.source });
    else if (e.relation === "part_of") out.push({ source: e.source, target: e.target });
    else if (e.kind === "rel:produces_data") out.push({ source: e.source, target: e.target });
    else if (e.kind === "rel:data_dependencies") out.push({ source: e.target, target: e.source });
  }
  return out;
}

/* A restriction a class inherits along `derived_from`, transitively, with
   the class it came from — and whether this class says otherwise, which the
   validator refuses (ADR-0099) and the diagram shows where it happens. */
const SCOPE_WORDS = { private: "private", protected: "protected", public: "public" };

function inheritedRestrictions(classId) {
  const byId = dataClassById();
  const own = byId[classId];
  if (!own) return [];
  const out = new Map();
  const seen = new Set([classId]);
  const walk = (id) => {
    for (const r of byId[id]?.relations || []) {
      if (r?.kind !== "derived_from" || seen.has(r.target)) continue;
      seen.add(r.target);
      const src = byId[r.target];
      if (!src) continue;
      if (src.may_leave_region === false && !out.has("region")) {
        out.set("region", { key: "region", text: "stays in its region",
                            from: src.id, dropped: own.may_leave_region !== false });
      }
      if (src.may_appear_in_traces === false && !out.has("traces")) {
        out.set("traces", { key: "traces", text: "kept out of traces",
                            from: src.id, dropped: own.may_appear_in_traces !== false });
      }
      const srcRank = SCOPE_RANK[src.scope || "private"];
      const ownRank = SCOPE_RANK[own.scope || "private"];
      if (srcRank < ownRank && !out.has("scope")) {
        out.set("scope", { key: "scope",
                           text: `source is ${SCOPE_WORDS[src.scope || "private"]}`,
                           from: src.id, dropped: true, advisory: true });
      }
      walk(r.target);
    }
  };
  walk(classId);
  return [...out.values()];
}

/* The data class's face: the compartments of a UML class. */
function dataClassBody(dc) {
  const rows = [];
  if (dc.may_leave_region === false) rows.push(el("div", { class: "n-attr" }, "may_leave_region = false"));
  if (dc.may_appear_in_traces === false) rows.push(el("div", { class: "n-attr" }, "may_appear_in_traces = false"));
  if (dc.retention_days) rows.push(el("div", { class: "n-attr" }, `retention_days = ${dc.retention_days}`));
  for (const r of inheritedRestrictions(dc.id)) {
    rows.push(el("div", {
      class: `n-inherit${r.dropped ? " dropped" : ""}${r.advisory ? " advisory" : ""}`,
      "data-restriction": r.key,
      title: r.dropped && !r.advisory
        ? `${dc.id} is derived from ${r.from}, which is ${r.text.replace("stays", "must stay")}, `
          + "and says otherwise: the validator refuses this (ADR-0099)"
        : `inherited along «derive» from ${r.from} (ADR-0099)`,
    }, `⇠ ${r.text} `, el("span", { class: "n-from" }, `from ${r.from}`)));
  }
  const out = rows.length ? [el("div", { class: "n-compartment" }, ...rows)] : [];
  if (dc.schema_ref) {
    /* A pointer, not a promise: shown as a link out when it is one, and as
       the catalogue id it is otherwise (ADR-0111). */
    const url = /^https?:\/\//i.test(dc.schema_ref);
    const link = url
      ? el("a", { class: "n-schema", href: dc.schema_ref, target: "_blank",
                  rel: "noopener noreferrer",
                  title: `the schema lives at ${dc.schema_ref} — a pointer; nothing here reads it` },
           "schema ↗")
      : el("span", { class: "n-schema", title: "a data-catalogue id; nothing here reads it" },
           `schema: ${dc.schema_ref}`);
    if (url) {
      link.addEventListener("pointerdown", (e) => e.stopPropagation());
      link.addEventListener("click", (e) => e.stopPropagation());
    }
    out.push(link);
  }
  return out;
}

function addDataDiagram(existing = null) {
  const layout = canvas.record?.layout;
  if (!layout) return null;
  const d = existing || {
    id: newDiagramId(), name: "Data", kind: "data", root: "", nodes: {},
    viewport: { x: 0, y: 0, zoom: 1 }, updated_at: new Date().toISOString(),
  };
  /* Opening it again adds what the model has gained since, and nothing
     else: a class declared elsewhere shows up here, positions stay put. */
  let added = 0;
  const put = (id, kind) => {
    if (d.nodes[id]) return;
    d.nodes[id] = { id, kind, x: 40, y: 40 + added * 110, width: 200, height: 90,
                    collapsed: false, note: "" };
    added += 1;
  };
  dataClasses().forEach((dc) => put(dc.id, "data_class"));
  dataAgents().forEach((a) => put(a.id, "agent"));
  layout.diagrams[d.id] = d;
  if (!existing) markDirty("added the Data diagram");
  else if (added) markDirty(`added ${added} to the Data diagram`);
  openDiagram(d.id);
  if (!existing || added) arrangeDiagram("derivation");
  return d;
}

/* ------------------------------- the Deployment diagram (ADR-0112 M7)

   The binding drawn as UML deployment: a target, the servers it declares as
   nodes, each capability deployed on the server that realises it, the
   environments and engines as execution environments, and the workflows an
   engine runs. Read-only: the binding is a document of its own (ADR-0110),
   and a design carries one only when it came from an example that ships
   one. Without one the diagram says so rather than drawing a guess. */

function bindingTargets() {
  const b = canvas.record?.binding;
  if (!b || typeof b !== "object") return [];
  if (Array.isArray(b.targets)) return b.targets.filter((t) => t && typeof t === "object");
  return b.target ? [b] : [];
}

/* Kinds the Deployment profile draws that are not spec components. */
const BINDING_KINDS = {
  target: { kind: "target", label: "Target", icon: "⌂", stereo: "«deployment spec»", fields: [] },
  server: { kind: "server", label: "Server", icon: "▣", stereo: "«node»", fields: [] },
  engine: { kind: "engine", label: "Engine", icon: "⚙", stereo: "«executionEnvironment»", fields: [] },
};

/* Every box and edge of the binding, keyed by node id. A capability's id is
   its own, so its Properties are the capability's; a second target's are
   prefixed, so two targets binding one capability are two boxes. */
function deploymentModel() {
  /* Computed once per binding object: every node's title, subtitle and
     signature asks for it, and the binding only changes when a design is
     opened. */
  const binding = canvas.record?.binding || null;
  if (canvas.deploymentCache?.binding === binding) return canvas.deploymentCache.model;
  const model = buildDeploymentModel();
  canvas.deploymentCache = { binding, model };
  return model;
}

function buildDeploymentModel() {
  const nodes = new Map();
  const edges = [];
  bindingTargets().forEach((t, index) => {
    const name = t.target || `target_${index + 1}`;
    const pre = index ? `${name}:` : "";
    const tid = `target:${name}`;
    const put = (id, kind, parent, record, extra = {}) => {
      if (!nodes.has(id)) nodes.set(id, { id, kind, parent, record, target: name, ...extra });
    };
    put(tid, "target", null, t);
    for (const s of t.servers || []) {
      if (!s?.id) continue;
      put(`server:${pre}${s.id}`, "server", tid, s);
      edges.push({ source: tid, target: `server:${pre}${s.id}`, kind: "binding:server",
                   uml: "composition", rel: "declares server", head: "diamond-start",
                   stroke: "--edge-report" });
    }
    for (const c of t.capabilities || []) {
      if (!c?.capability) continue;
      const host = c.server ? `server:${pre}${c.server}` : tid;
      put(`${pre}${c.capability}`, "capability", host, c, { specId: c.capability });
      edges.push({ source: `${pre}${c.capability}`, target: host, kind: "binding:deploy",
                   uml: "deployment", rel: c.server ? "deployed on" : "mounts its own server",
                   label: "«deploy»", head: "open", dash: "6 4",
                   stroke: "--edge-report" });
    }
    for (const e of t.environments || []) {
      if (!e?.environment) continue;
      put(`${pre}${e.environment}`, "environment", tid, e, { specId: e.environment });
      edges.push({ source: tid, target: `${pre}${e.environment}`, kind: "binding:env",
                   uml: "composition", rel: "provides environment", head: "diamond-start",
                   stroke: "--edge-report" });
    }
    for (const w of t.workflows || []) {
      const engine = w?.engine || "native";
      const eid = `engine:${pre}${engine}`;
      put(eid, "engine", tid, { engine, mode: w.mode, endpoint: w.endpoint });
      if (!edges.some((x) => x.source === tid && x.target === eid)) {
        edges.push({ source: tid, target: eid, kind: "binding:engine",
                     uml: "composition", rel: "runs engine", head: "diamond-start",
                     stroke: "--edge-report" });
      }
      const wid = `${pre}${w.workflow || "*"}`;
      put(wid, "workflow", eid, w, { specId: w.workflow || "" });
      edges.push({ source: wid, target: eid, kind: "binding:workflow",
                   uml: "deployment", rel: "runs in engine", label: "«deploy»",
                   head: "open", dash: "6 4", stroke: "--edge-report" });
    }
    for (const [list, key, kind] of [["channels", "channel", "channel"],
                                     ["knowledge", "knowledge", "knowledge"]]) {
      for (const b of t[list] || []) {
        if (!b?.[key]) continue;
        put(`${pre}${b[key]}`, kind, tid, b, { specId: b[key] });
        edges.push({ source: `${pre}${b[key]}`, target: tid, kind: `binding:${kind}`,
                     uml: "deployment", rel: `binds ${kind}`, label: "«deploy»",
                     head: "open", dash: "6 4", stroke: "--edge-report" });
      }
    }
  });
  return { nodes, edges };
}

function deploymentSubtitle(entry) {
  const r = entry?.record || {};
  switch (entry?.kind) {
    case "target": return [r.runtime?.adapter, r.model?.provider && `model ${r.model.provider}`,
                           r.infrastructure?.provider].filter(Boolean).join(" · ");
    case "server": return [r.kind, r.transport, r.url || r.command].filter(Boolean).join(" · ");
    case "engine": return [r.mode, r.endpoint].filter(Boolean).join(" · ");
    case "capability": return [r.server ? `on ${r.server}` : "its own server",
                               r.dsn_secret_ref || r.secret_ref].filter(Boolean).join(" · ");
    case "environment": return [r.image, r.cpu && `${r.cpu} cpu`, r.memory].filter(Boolean).join(" · ");
    case "workflow": return [r.flow && `flow ${r.flow}`, r.mode].filter(Boolean).join(" · ");
    case "channel": case "knowledge": return [r.provider, r.address || r.location].filter(Boolean).join(" · ");
    default: return "";
  }
}

function addDeploymentDiagram(existing = null) {
  const layout = canvas.record?.layout;
  if (!layout) return null;
  const d = existing || {
    id: newDiagramId(), name: "Deployment", kind: "deployment", root: "", nodes: {},
    viewport: { x: 0, y: 0, zoom: 1 }, updated_at: new Date().toISOString(),
  };
  let added = 0;
  for (const entry of deploymentModel().nodes.values()) {
    if (d.nodes[entry.id]) continue;
    d.nodes[entry.id] = { id: entry.id, kind: entry.kind, x: 40, y: 40 + added * 110,
                          width: 200, height: 90, collapsed: false, note: "" };
    added += 1;
  }
  layout.diagrams[d.id] = d;
  if (!existing) markDirty("added the Deployment diagram");
  else if (added) markDirty(`added ${added} to the Deployment diagram`);
  openDiagram(d.id);
  if (added) arrangeDiagram("deployment");
  return d;
}

/* ------------------------------------ edges drawn in UML notation
   The Data and Deployment diagrams draw each edge with its UML ends: an
   open arrowhead, a filled diamond at a whole, and an association class
   hung off the middle of the line it qualifies. */

/* A node's box as drawn, measured where it can be. */
function nodeBox(node) {
  return stepBox(node);
}

/* Left to right (Data): leave the source on the side facing the target. */
function lrGeometry(a, b) {
  const s = nodeBox(a), t = nodeBox(b);
  let x1, y1, x2, y2, horizontal = true;
  if (t.x >= s.x + s.w - 10) { x1 = s.x + s.w; y1 = s.cy; x2 = t.x; y2 = t.cy; }
  else if (s.x >= t.x + t.w - 10) { x1 = s.x; y1 = s.cy; x2 = t.x + t.w; y2 = t.cy; }
  else {
    horizontal = false;
    x1 = s.cx; x2 = t.cx;
    if (t.y >= s.y) { y1 = s.y + s.h; y2 = t.y; } else { y1 = s.y; y2 = t.y + t.h; }
  }
  const bend = Math.max(30, Math.abs(horizontal ? x2 - x1 : y2 - y1) / 2);
  const d = horizontal
    ? `M ${x1} ${y1} C ${x1 + Math.sign(x2 - x1) * bend} ${y1}, ${x2 - Math.sign(x2 - x1) * bend} ${y2}, ${x2} ${y2}`
    : `M ${x1} ${y1} C ${x1} ${y1 + Math.sign(y2 - y1) * bend}, ${x2} ${y2 - Math.sign(y2 - y1) * bend}, ${x2} ${y2}`;
  return { d, labelX: (x1 + x2) / 2, labelY: (y1 + y2) / 2 - 6 };
}

/* Top to bottom (Deployment): from the facing edges of the two boxes. */
function tbGeometry(a, b) {
  const s = nodeBox(a), t = nodeBox(b);
  /* An artifact stacked under the node it is deployed on (the `deployment`
     layout indents it): routed along a bus down the node's left side, so
     the arrow to the node never crosses the artifacts stacked above it. */
  if (t.y + t.h <= s.y && s.x > t.x && s.x < t.x + t.w) {
    const bus = t.x + 16;
    return { d: `M ${s.x} ${s.cy} L ${bus} ${s.cy} L ${bus} ${t.y + t.h}`,
             labelX: (bus + s.x) / 2 + 4, labelY: s.cy - 5 };
  }
  const down = t.cy >= s.cy;
  const x1 = s.cx, y1 = down ? s.y + s.h : s.y;
  const x2 = t.cx, y2 = down ? t.y : t.y + t.h;
  const mid = (y1 + y2) / 2;
  return { d: `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`,
           labelX: (x1 + x2) / 2, labelY: mid - 4 };
}

function geometryFor(kind, a, b) {
  if (kind === "data") return lrGeometry(a, b);
  if (kind === "deployment") return tbGeometry(a, b);
  return edgeGeometry(a, b);
}

/* One marker per head and colour: an SVG marker cannot take its colour from
   the line it ends (not portably), so each colour has its own. */
const UML_MARKER_COLOURS = ["--edge-report", "--edge-peer"];

function umlMarkers(mk) {
  const defs = mk("defs", {});
  for (const colour of UML_MARKER_COLOURS) {
    const c = `var(${colour})`;
    const tag = colour.replace("--", "");
    const open = mk("marker", { id: `um-open-${tag}`, viewBox: "0 0 12 12", refX: "11",
                                refY: "6", markerWidth: "10", markerHeight: "10",
                                orient: "auto-start-reverse", markerUnits: "userSpaceOnUse" });
    open.appendChild(mk("path", { d: "M1,1 L11,6 L1,11", fill: "none", "stroke-width": "1.5" },
                        { stroke: c }));
    const diamond = mk("marker", { id: `um-diamond-${tag}`, viewBox: "0 0 20 10", refX: "19",
                                   refY: "5", markerWidth: "18", markerHeight: "9",
                                   orient: "auto-start-reverse", markerUnits: "userSpaceOnUse" });
    diamond.appendChild(mk("path", { d: "M1,5 L10,1 L19,5 L10,9 z" }, { fill: c, stroke: c }));
    defs.append(open, diamond);
  }
  return defs;
}

/* An edge's label: a word, or — for an association class — a small class
   box hung off the line by a dashed tether, as UML draws one. */
function umlLabel(mk, edge, x, y) {
  if (!edge.assocClass) {
    if (!edge.label) return null;
    return mk("text", { x, y, "text-anchor": "middle", class: "edge-label uml-label" },
              { fill: `var(${edge.stroke || "--ink-muted"})` }, edge.label);
  }
  const g = mk("g", { class: "assoc-class", "data-source": edge.source,
                      "data-target": edge.target });
  const lines = [edge.assocClass.name, ...edge.assocClass.lines];
  const width = Math.max(...lines.map((l) => l.length)) * 6 + 16;
  const height = 8 + lines.length * 13;
  g.appendChild(mk("path", { d: "M 0 0 L 0 16", "stroke-dasharray": "3 3", fill: "none",
                             class: "assoc-tether" }, { stroke: "var(--ink-dim)" }));
  g.appendChild(mk("rect", { x: -width / 2, y: 16, width, height, rx: 3,
                             class: "assoc-box" }));
  lines.forEach((line, i) => g.appendChild(mk("text", {
    x: 0, y: 28 + i * 13, "text-anchor": "middle",
    class: i ? "assoc-line" : "assoc-name",
  }, {}, line)));
  g.appendChild(mk("title", {}, {}, `${edge.source} relies on ${edge.target}: `
    + edge.assocClass.lines.join("; ")
    + (edge.assocClass.producer ? `; produced by ${edge.assocClass.producer}` : "")));
  placeEdgeLabel(g, x, y + 6);
  return g;
}

function placeEdgeLabel(label, x, y) {
  if (!label) return;
  if (label.tagName === "g") label.setAttribute("transform", `translate(${x} ${y})`);
  else { label.setAttribute("x", String(x)); label.setAttribute("y", String(y)); }
}

function renderUmlEdges(svg, layout) {
  const ns = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs, styles = {}, text) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    Object.assign(n.style, styles);
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const parts = [umlMarkers(mk)];
  for (const edge of derivedEdges().filter(edgeShown)) {
    const a = layout.nodes[edge.source], b = layout.nodes[edge.target];
    if (!a || !b) continue;
    const { d, labelX, labelY } = geometryFor(layout.kind, a, b);
    const tag = (edge.stroke || "--edge-peer").replace("--", "");
    const path = mk("path", {
      d, fill: "none", "stroke-width": "1.5", class: "uml-edge",
      "data-source": edge.source, "data-target": edge.target,
      "data-uml": edge.uml || "", "data-rel": edge.rel || "",
      ...(edge.dash ? { "stroke-dasharray": edge.dash } : {}),
      ...(edge.head === "open" ? { "marker-end": `url(#um-open-${tag})` } : {}),
      ...(edge.head === "diamond" ? { "marker-end": `url(#um-diamond-${tag})` } : {}),
      ...(edge.head === "diamond-start" ? { "marker-start": `url(#um-diamond-${tag})` } : {}),
    }, { stroke: `var(${edge.stroke || "--edge-peer"})` });
    path.appendChild(mk("title", {}, {}, `${edge.source} ${edge.rel} ${edge.target}`));
    parts.push(path);
    const label = umlLabel(mk, edge, labelX, labelY);
    if (label) { path.edgeLabel = label; parts.push(label); }
  }
  svg.replaceChildren(...parts);
}

/* The nodes of the diagram currently open, always an object. */
function layoutNodes() {
  return diagram()?.nodes || {};
}

function findComponent(kind, id) {
  const s = spec();
  if (!s) return null;
  /* A step belongs to the workflow the open process canvas draws, not to the
     organisation. It is looked up there so the inspector edits the step
     itself rather than showing an empty form (ADR-0100). */
  if (kind === "step") {
    const open = diagram();
    const workflow = (org(s).workflows || []).find((w) => w.id === open?.root);
    return (workflow?.graph?.nodes || []).find((n) => n.id === id) || null;
  }
  /* On the Deployment diagram a box is a binding record (ADR-0112 M7): a
     target, a server or an engine is the record itself; a bound capability
     or workflow is the spec's own component where there is one, so its
     Properties are the ones every other diagram shows. */
  if (BINDING_KINDS[kind] || diagram()?.kind === "deployment") {
    const entry = deploymentModel().nodes.get(id);
    if (entry && entry.kind === kind) {
      if (BINDING_KINDS[kind]) return entry.record;
      if (entry.specId && entry.specId !== id) return findComponent(kind, entry.specId) || entry.record;
    } else if (BINDING_KINDS[kind]) {
      return null;
    }
  }
  if (kind === "team") return allTeams().find((t) => t.id === id) || null;
  if (kind === "agent") return allAgents().find((a) => a.agent.id === id)?.agent || null;
  if (kind === "subagent") {
    for (const { agent } of allAgents()) {
      const sub = (agent.subagents || []).find((x) => x.id === id);
      if (sub) return sub;
    }
    return null;
  }
  if (NESTED[kind]) return nestedList(s, kind).find((x) => x.id === id) || null;
  if (kind === "note") return layoutNodes()[id] || null;
  const collection = COLLECTIONS[kind];
  return collection ? (org(s)[collection] || []).find((x) => x.id === id) || null : null;
}

/* A dropped component lands **unlinked**, at the top of the organisation.

   It used to land wherever it was dropped *near*: `nearestNode` had no
   distance limit, so "near" meant "the nearest one anywhere", and dropping a
   second Team silently nested it under the first. The line you then saw was
   real — the drop had written a parent into the spec that nobody asked for,
   and the only way to see it was to read the YAML.

   The spec is a tree and has nowhere to put a free-floating team or an agent
   in no team, so "unlinked" means "at the root": valid, visible, and
   obviously not where you want it yet. Linking is then an explicit act, and
   `LINK_RULES` says what is legal. */
function rootTeam() {
  return spec()?.organization || null;
}

function addComponent(kind, id, at = null) {
  const s = spec();
  if (kind === "team") {
    // No `mandate`: none means "inherit the parent's" (ADR-0065), and an empty
    // list is not a mandate the model accepts.
    const team = { id, name: id, leader: "", members: [], teams: [] };
    const empty = !s.organization?.id
      || (!(s.organization.members || []).length
          && !(s.organization.teams || []).length
          && !layoutNodes()[s.organization.id]);
    if (empty) {
      // The first team dropped *is* the organization, rather than a child of an
      // invisible root nobody asked for. Merged, not replaced: the
      // organisation owns every collection in the design (ADR-0101).
      s.organization = { ...(s.organization || {}), ...team };
      return s.organization;
    } else {
      (s.organization.teams = s.organization.teams || []).push(team);
    }
    return team;
  }
  if (kind === "agent") {
    const team = rootTeam();
    if (!team) throw new Error("drop a Team onto the canvas first");
    const agent = { id, name: id, roles: [], humans: [] };
    (team.members = team.members || []).push(agent);
    if (!team.leader) team.leader = id;
    return agent;
  }
  if (kind === "subagent") {
    const parent = allAgents()[0]?.agent;
    if (!parent) throw new Error("add an Agent before adding a sub-agent");
    const sub = { id, name: id, kind: "research", purpose: "", returns: "" };
    (parent.subagents = parent.subagents || []).push(sub);
    return sub;
  }
  if (NESTED[kind]) {
    const item = NESTED[kind].seed(id);
    nestedList(s, kind).push(item);
    return item;
  }
  if (kind === "note") return { note: "" };
  const collection = COLLECTIONS[kind];
  if (!collection) throw new Error(`cannot place '${kind}' yet`);
  org(s)[collection] = org(s)[collection] || [];
  const item = { id };
  org(s)[collection].push(item);
  return item;
}

function removeComponent(kind, id) {
  const s = spec();
  if (kind === "team") {
    walkTeams(s.organization, (t) => {
      t.teams = (t.teams || []).filter((c) => c.id !== id);
    });
  } else if (kind === "agent") {
    allTeams().forEach((t) => {
      t.members = (t.members || []).filter((m) => m.id !== id);
      if (t.leader === id) t.leader = (t.members[0] || {}).id || "";
    });
  } else if (kind === "subagent") {
    allAgents().forEach(({ agent }) => {
      agent.subagents = (agent.subagents || []).filter((x) => x.id !== id);
    });
  } else if (NESTED[kind]) {
    const shape = NESTED[kind];
    const container = shape.container(s);
    container[shape.list] = (container[shape.list] || [])
      .filter((x) => x.id !== id);
  } else if (COLLECTIONS[kind]) {
    org(s)[COLLECTIONS[kind]] = (org(s)[COLLECTIONS[kind]] || []).filter((x) => x.id !== id);
  }
  delete layoutNodes()[id];
}

/* --------------------------------------------------------------- palette */
async function loadPalette() {
  canvas.palette = await dapi("/palette");
  // The UML profile (ADR-0101): stereotypes shown in Properties.
  canvas.metamodel = await dapi("/metamodel").catch(() => null);
  // The designer's specification: what each gesture does (ADR-0103).
  canvas.gestures = (await dapi("/gestures").catch(() => null))?.gestures || [];
  const root = $("#palette-groups");

  /* A nested entry is a component the parent *contains* in the spec: a Tool
     under an Agent is `agent.tools`. The indent is the model, not styling,
     so it is rendered from the tree the server sends rather than guessed. */
  const item = (kind, depth) => el("div", {
    class: "drag-item", draggable: "true", "data-kind": kind.kind,
    style: depth ? `margin-left:${depth * 14}px` : null,
    title: kind.help || kind.label,
    ondragstart: (e) => {
      e.dataTransfer.setData("text/kind", kind.kind);
      e.dataTransfer.effectAllowed = "copy";
    },
  }, el("span", { class: "ic" }, kind.icon || "▫"), kind.label);

  /* Grouped by UML profile (ADR-0112 M7): each kind sits under the profile
     that declares its stereotype, in the palette tree's order, and keeps
     its nesting under a parent of the same profile. */
  const profileOf = canvas.palette.profiles || {};
  const byProfile = (profiles) => {
    const groups = new Map(profiles.map((p) => [p, []]));
    const walk = (kind, depth, parentProfile) => {
      const p = profileOf[kind.kind] || "Core";
      const d = p === parentProfile ? depth : 0;
      if (groups.has(p)) groups.get(p).push(item(kind, d));
      (kind.children || []).forEach((c) => walk(c, d + 1, p));
    };
    for (const group of canvas.palette.groups) {
      for (const kind of group.kinds) walk(kind, 0, null);
    }
    return groups;
  };
  const docOf = (name) =>
    (canvas.metamodel?.profiles || []).find((p) => p.name === name)?.doc || "";
  const profileGroup = (name, items) => el("div", {
    class: "group", "data-profile": name,
  },
    el("h4", { title: docOf(name) },
       el("span", { class: "profile-mark", "aria-hidden": "true" }, "«profile»"),
       ` ${name}`),
    ...items);

  canvas.paletteFor = (aspect) => {
    const aspectSpec = ASPECTS[aspect] || ASPECTS.organisation;
    const groups = byProfile([...aspectSpec.profiles, "Core", ...undrawnProfiles()]);
    const drawn = aspectSpec.profiles
      .filter((p) => groups.get(p)?.length)
      .map((p) => profileGroup(p, groups.get(p)));
    /* Annotation belongs to no aspect, so every editable canvas has it. */
    const core = groups.get("Core")?.length ? [profileGroup("Core", groups.get("Core"))] : [];
    const others = aspect === "organisation"
      ? undrawnProfiles().filter((p) => groups.get(p)?.length) : [];
    return [
      ...drawn, ...core,
      ...(others.length ? [el("details", { class: "group other-profiles" },
        el("summary", { title: "profiles no diagram draws yet; placed on the "
                          + "Organisation diagram until one does" },
           `Other profiles (${others.join(", ")})`),
        ...others.map((p) => profileGroup(p, groups.get(p))))] : []),
      el("p", { class: "note" },
        `The ${aspectSpec.label} diagram draws the ${aspectSpec.profiles.join(", ")} `
        + `profile${aspectSpec.profiles.length > 1 ? "s" : ""}. An indented component `
        + "is one the component above it contains. Drop to place, then draw "
        + "the links the model allows."),
    ];
  };
  root.dataset.mode = "";
  syncPalette();
}

/* On a process canvas the palette holds steps, not components (ADR-0110):
   you do not drop a team into a workflow, and a step is not a component of
   the organisation. Dropping one creates it in the workflow's graph through
   the same model operation every other drop uses. */
const STEP_PALETTE = [
  ["agent", "Agent task", "\u25C8", "hands the step to an agent, who owns it"],
  ["tool", "Tool call", "\u2699", "calls a tool or capability"],
  ["human", "Human approval", "\u2713", "pauses for a named person or role"],
  ["branch", "Decision", "\u25C7", "chooses one way out by condition"],
  ["fork", "Fork", "\u2AF4", "every way out runs at once"],
  ["join", "Join", "\u2AF4", "waits for every branch of a fork"],
  ["workflow", "Sub-workflow", "\u2933", "runs another workflow, drawn here or built in an engine"],
  ["transform", "Transform", "\u0192", "reshapes the workflow's state"],
];

function syncPalette() {
  const root = $("#palette-groups");
  if (!root || !canvas.paletteFor) return;
  const mode = aspectOf();
  if (root.dataset.mode === mode) return;
  root.dataset.mode = mode;
  if (mode === "organisation" || mode === "data") {
    return root.replaceChildren(...canvas.paletteFor(mode));
  }
  if (mode === "deployment") {
    /* The Deployment profile has no palette: its elements are the binding's,
       and the binding is edited as a document of its own (ADR-0110). */
    return root.replaceChildren(
      el("div", { class: "group", "data-profile": "Deployment" },
        el("h4", {}, el("span", { class: "profile-mark", "aria-hidden": "true" },
                        "«profile»"), " Deployment"),
        el("p", { class: "hint" },
          "Targets, servers, environments and engines come from the design's "
          + "binding. This diagram draws it and does not edit it: change the "
          + "binding file and load the design again.")));
  }
  root.replaceChildren(
    el("div", { class: "group", "data-profile": "Process" },
      el("h4", { title: "the steps of a governed workflow (ADR-0110)" },
         el("span", { class: "profile-mark", "aria-hidden": "true" }, "«profile»"),
         " Process · steps"),
      ...STEP_PALETTE.map(([kind, label, icon, help]) => el("div", {
        class: "drag-item", draggable: "true", "data-kind": "step",
        "data-step": kind, title: help,
        ondragstart: (e) => {
          e.dataTransfer.setData("text/step", kind);
          e.dataTransfer.effectAllowed = "copy";
        },
      }, el("span", { class: "ic" }, icon), label))),
    el("p", { class: "note" },
      "Drop a step onto the process, then set its owner in Properties. "
      + "Edges are edited under the workflow's graph."));
}

/* A new step in the open workflow, by the model operation path: the model
   creates it inside the workflow and says what it still lacks. */
async function placeStep(stepKind, x, y) {
  const open = diagram();
  const workflow = (org(spec())?.workflows || []).find((w) => w.id === open?.root);
  if (!workflow) return setStatus("this process canvas has no workflow");
  const taken = new Set((workflow.graph?.nodes || []).map((n) => n.id));
  let n = 1;
  while (taken.has(`${stepKind}_${n}`)) n += 1;
  const id = `${stepKind}_${n}`;
  let said = "";
  try {
    const answer = await modelOperation({ op: "create", kind: "action", id,
                                          owner: workflow.id,
                                          attrs: { kind: stepKind } });
    said = `step ${id} added to ${workflow.id}${effectsLine(answer)}`;
  } catch (err) {
    setStatus(err.message);
    return;
  }
  layoutNodes()[id] = { id, kind: "step", x, y, width: 200, height: 80,
                        collapsed: false, note: "" };
  markDirty(said);
  renderCanvas();
  selectNode(layoutNodes()[id]);
  setStatus(said);
}

/* The node vocabulary the design fixes: shape carries the kind, so a reader
   never has to consult the legend twice.
     team     dashed container — it holds, it does not act
     agent    solid card, left stripe = its highest data classification
     mission  capsule, because it is temporary; it always shows its end date
     sandbox  rounded card with the network posture on its face
     endpoint chevron, because what leaves the boundary is drawn leaving */
const SHAPES = {
  team: "container",
  agent: "card", subagent: "card",
  mission: "capsule",
  environment: "sandbox",
  endpoint: "chevron",
};
const shapeOf = (kind) => SHAPES[kind] || "plain";

/* An agent has no classification of its own: it inherits the widest scope of
   any data class its capabilities touch. `private` is the tightest and is
   drawn clay, `public` the widest and drawn moss — the stripe is a warning
   about reach, not a score. */
const SCOPE_RANK = { public: 3, protected: 2, private: 1 };

function classificationOf(agent) {
  const s = spec();
  if (!s || !agent) return "unclassified";
  const byId = Object.fromEntries((org(s).data_classes || []).map((d) => [d.id, d]));
  const touched = new Set();
  for (const capabilityId of agent.capabilities || []) {
    const capability = (org(s).capabilities || []).find((c) => c.id === capabilityId);
    for (const id of capability?.data_classes || []) touched.add(id);
  }
  let worst = null;
  for (const id of touched) {
    const scope = byId[id]?.scope;
    if (!scope) continue;
    if (!worst || SCOPE_RANK[scope] < SCOPE_RANK[worst]) worst = scope;
  }
  return worst || "unclassified";
}

/* The network posture a sandbox runs under, shown on its face rather than
   one dialog away: `none · allowlist · internal · open`. */
const postureOf = (environment) => environment?.network || "none";

function paletteKinds() {
  const out = [];
  const walk = (kinds) => {
    for (const k of kinds) { out.push(k); walk(k.children || []); }
  };
  for (const group of canvas.palette?.groups || []) walk(group.kinds || []);
  return out;
}

/* A step is part of a workflow, not a component of the design, so it is not in
   the palette — you do not drag one onto an organisation. It still needs
   fields, or selecting one on a process canvas would open an empty form. */
const STEP_SPEC = {
  kind: "step", label: "Step", icon: "\u25cb",
  fields: [
    { name: "id", type: "string", required: true },
    { name: "kind", type: "enum",
      options: ["tool", "agent", "workflow", "branch", "transform", "human",
                "fork", "join"],
      help: "what this step does. A branch chooses one way out; a fork takes "
            + "every way out at once, and its branches meet at a join "
            + "(ADR-0110)" },
    { name: "owner", type: "string",
      help: "who does this step: an agent, a team or a person. An agent step "
            + "is its agent's unless this says otherwise; a step calling a "
            + "workflow built in an engine is checked against its owner" },
    { name: "person", type: "string",
      help: "for a human step: the person who approves" },
    { name: "role", type: "string",
      help: "for a human step: or the role whose holder approves" },
    { name: "tool", type: "string" },
    { name: "agent", type: "string" },
    { name: "workflow", type: "string" },
    { name: "expr", type: "string",
      help: "for a transform: an expression over the workflow's state" },
    { name: "output", type: "string",
      help: "the state key this step's result is written to" },
  ],
};

function kindSpec(kind) {
  if (kind === "step") return STEP_SPEC;
  if (BINDING_KINDS[kind]) return BINDING_KINDS[kind];
  return paletteKinds().find((k) => k.kind === kind)
    || { kind, label: kind, fields: [] };
}

/* ---------------------------------------------------------------- canvas */
/* Where the reader was looking, kept with the layout (ADR-0034: presentation,
   never the spec). `Layout.viewport` was persisted from the day it was
   declared and restored by nothing, so reopening a large design always put
   you back at the origin. */
function restoreViewport() {
  const surface = $("#canvas");
  const view = canvas.record?.layout?.viewport;
  if (!surface || !view) return;
  surface.scrollLeft = Number(view.x) || 0;
  surface.scrollTop = Number(view.y) || 0;
}

function rememberViewport() {
  const surface = $("#canvas");
  const layout = diagram();
  if (!surface || !layout) return;
  const view = layout.viewport || (layout.viewport = { x: 0, y: 0, zoom: 1 });
  /* Scrolling is not an edit: it must not mark the design dirty or take a
     revision. It rides along with the next save somebody actually makes. */
  view.x = surface.scrollLeft;
  view.y = surface.scrollTop;
}

/* ---------------------------------------------------- incremental drawing
   Every change used to call a renderCanvas that rebuilt every node, region,
   edge, the edge filter, the Explorer, the Outline and the diagram bar —
   thirty-odd call sites, and a click to select one box paid for all of it
   (ADR-0117). Now:

   - a node's element is kept, keyed by id, and rebuilt only when what it was
     drawn from changes (its signature below);
   - selection is a class and an attribute, toggled in place (syncSelection);
   - a drag moves one box and redraws only the edges that touch it;
   - the side panels are rebuilt when the model, the diagram or the reader's
     rights change, not on every redraw of the canvas.

   Keeping elements also keeps keyboard focus: a node that is replaced takes
   focus with it, which is what made a keyboard canvas impossible before. */
const nodeEls = new Map();      // id -> { el, sig, node, component }

/* Everything renderNode reads, as one string. If two renders produce the same
   signature from the same objects, the element already on the page is right.
   The objects are compared too, not only their JSON: the element's handlers
   close over them, and after an undo they are different objects with the
   same content. */
function nodeSignature(node, component, context) {
  const linking = context.linking;
  return [
    node.x, node.y, node.width, node.height, node.note || "",
    /* A team's box shows the team, not its members: its nested members and
       sub-teams are their own boxes, and stringifying them made the root
       team's signature the whole organisation on every redraw. */
    JSON.stringify(component, (key, value) =>
      key === "members" || key === "teams" ? undefined : value),
    nodeSubtitle(node.kind, component, node),
    // The chips inside an agent carry the held components' own names.
    node.kind === "agent"
      ? context.held + Object.entries(HELD_KINDS).map(([kind, field]) =>
          (component[field] || []).map((id) => findComponent(kind, id)?.name || id).join(","))
          .join(";")
      : "",
    linking ? (linking.id === node.id ? "src" : linkRule(linking.kind, node.kind) ? "tgt" : "no") : "",
    lockOn(node.id) ? "locked" : "",
    context.edit,
    context.aspect,
    /* A data class shows what it inherits along «derive», which is read
       from the classes it derives from — not from its own object. */
    node.kind === "data_class" ? JSON.stringify(inheritedRestrictions(node.id)) : "",
  ].join("\u0001");
}

function renderCanvas() {
  const started = performance.now();
  const host = $("#canvas-nodes");
  const layout = diagram() || { nodes: {} };
  /* A component one agent holds lives inside that agent's box (below), so
     drawing it a second time as its own node would say two different things
     about one fact. Held by two or more, it is shared, it keeps its node, and
     the edges are the point. */
  const inlined = inlinedComponents();
  const context = {
    held: [...inlined].sort().join(","),
    linking: canvas.linking,
    edit: canvas.permissions.includes("system.edit"),
    aspect: aspectOf(layout),
  };
  const focusedId = host.contains(document.activeElement)
    ? document.activeElement.closest?.(".node")?.dataset.id : null;
  let built = 0;
  const wanted = Object.values(layout.nodes)
    .filter((node) => !inlined.has(node.id))
    // Containers first, so the workers deployed in them draw on top.
    .sort((a, b) => isContainer(b) - isContainer(a));
  const keep = new Set();
  const ordered = wanted.map((node) => {
    const component = findComponent(node.kind, node.id) || {};
    const sig = nodeSignature(node, component, context);
    keep.add(node.id);
    const cached = nodeEls.get(node.id);
    if (cached && cached.sig === sig && cached.node === node
        && cached.component === component) {
      return cached.el;
    }
    built += 1;
    const box = renderNode(node, component);
    nodeEls.set(node.id, { el: box, sig, node, component });
    return box;
  });
  for (const id of [...nodeEls.keys()]) if (!keep.has(id)) nodeEls.delete(id);
  /* Put the elements in order without moving the ones already in place: a
     moved element loses focus, and replaceChildren moves all of them. */
  let ref = host.firstChild;
  for (const box of ordered) {
    if (box === ref) { ref = ref.nextSibling; continue; }
    host.insertBefore(box, ref);
  }
  while (ref) { const next = ref.nextSibling; ref.remove(); ref = next; }

  $("#canvas-empty").hidden = Object.keys(layout.nodes).length > 0;
  syncEmptyState(layout);
  syncPalette();
  renderRegions();
  renderEdges();
  renderPanels();
  syncSelection();
  if (focusedId && !host.contains(document.activeElement)) {
    nodeEls.get(focusedId)?.el.focus({ preventScroll: true });
  }
  canvas.renderStats = { ms: performance.now() - started, built,
                         kept: ordered.length - built };
}

/* What an empty canvas says depends on what it is a diagram of. A
   Deployment diagram of a design with no binding is empty for a reason
   the person can act on, and says which (ADR-0110 notes). */
function syncEmptyState(layout) {
  const box = $("#canvas-empty");
  if (!box || box.hidden) return;
  const aspect = aspectOf(layout);
  if (box.dataset.aspect === aspect) return;
  box.dataset.aspect = aspect;
  if (aspect === "deployment") {
    /* One child: the empty state is a centred flex box. */
    box.replaceChildren(el("div", { class: "empty-binding" },
      el("strong", {}, "No binding to draw."), el("br", {}),
      "This design was saved without a binding, so there is no target, server "
      + "or engine to show. A design loaded from an example that ships one "
      + "(Load example… AYC) carries its binding; the spec never names a "
      + "server (ADR-0004)."));
  } else if (aspect === "data") {
    box.replaceChildren(el("div", {}, "No data classes yet. Drag a ",
      el("strong", {}, "Data class"), " here, then draw «derive», part-of and "
      + "association links between classes."));
  } else {
    box.replaceChildren(el("div", {}, "Drag a ", el("strong", {}, "Team"),
      " here to start, then drop ", el("strong", {}, "Agents"), " onto it."));
  }
}

/* The panels beside the canvas describe the model, so they are rebuilt when
   the model (or which diagram, or the reader's rights) changes — not on every
   selection or drag. `canvas.rev` is bumped by every recorded change. */
function panelsKey() {
  return [canvas.record ? canvas.rev || 0 : "none", canvas.record?.layout?.active,
          canvas.permissions.includes("system.edit"), canvas.systemId].join("|");
}

function renderPanels(force = false) {
  const key = panelsKey();
  if (!force && key === canvas.panelsKey) return;
  const where = `${canvas.systemId}|${canvas.record?.layout?.active}`;
  canvas.panelsKey = key;
  renderEdgeFilter();
  /* Only on arriving somewhere: restoring the stored viewport on every
     redraw put the reader back where the diagram was last saved each time
     anything changed. */
  if (where !== canvas.viewportFor) {
    canvas.viewportFor = where;
    restoreViewport();
  }
  renderExplorer();
  /* Next frame: the outline measures its panel, and measuring straight after
     the nodes changed forced a synchronous layout of the whole page — most of
     what an undo cost. A frame later the browser has laid out anyway. */
  if (!canvas.outlineFrame) {
    canvas.outlineFrame = requestAnimationFrame(() => {
      canvas.outlineFrame = 0;
      renderOutline();
      syncSelection();
    });
  }
  renderDiagramBar();
}

/* A change recorded after the canvas was last drawn (a drop answered by the
   server, say) still reaches the panels, once, on the next frame. */
function schedulePanels() {
  if (canvas.panelsFrame) return;
  canvas.panelsFrame = requestAnimationFrame(() => {
    canvas.panelsFrame = 0;
    renderPanels();
  });
}

/* Selection, drawn in place: the node, its row in the Explorer and its box in
   the Outline. No element is rebuilt. */
function syncSelection() {
  const id = canvas.selected?.id;
  for (const { el: box } of nodeEls.values()) {
    const on = box.dataset.id === id;
    box.classList.toggle("selected", on);
    if (on) box.setAttribute("aria-current", "true");
    else box.removeAttribute("aria-current");
  }
  for (const row of document.querySelectorAll("#explorer-tree .ex-row[data-id]")) {
    row.classList.toggle("selected", row.dataset.id === id);
  }
  for (const rect of document.querySelectorAll("#outline-svg rect[data-id]")) {
    rect.classList.toggle("selected", rect.dataset.id === id);
  }
  syncRovingFocus();
}

/* One node is in the Tab order at a time (the roving tabindex pattern): Tab
   reaches the diagram once, arrows move inside it, and Tab again leaves. The
   one is the node last focused, else the selection, else the first. */
function syncRovingFocus() {
  const boxes = [...nodeEls.values()].map((c) => c.el).filter((b) => b.isConnected);
  if (!boxes.length) return;
  const pick = boxes.find((b) => b.dataset.id === canvas.focusId)
    || boxes.find((b) => b.dataset.id === canvas.selected?.id)
    || boxes[0];
  for (const box of boxes) box.tabIndex = box === pick ? 0 : -1;
}

function focusNode(box) {
  if (!box) return;
  canvas.focusId = box.dataset.id;
  syncRovingFocus();
  box.focus({ preventScroll: true });
  box.scrollIntoView?.({ block: "nearest", inline: "nearest" });
}

/* How a node is named to a screen reader: its kind, its name, and the line
   under it, which is what a sighted reader takes in at a glance. */
function nodeLabel(node, component) {
  const name = component?.name || component?.id || node.id;
  const sub = nodeSubtitle(node.kind, component || {}, node);
  return `${name}${sub ? `, ${sub}` : ""}`;
}

function lockOn(id) {
  return canvas.locks.find(
    (l) => (l.scope === "system" || l.target === id) && l.holder !== canvas.user);
}

/* Ids of the components drawn inside an agent rather than beside it: held,
   and held by exactly one agent. Two holders makes it shared, and sharing is
   what an edge is for. */
function inlinedComponents() {
  const out = new Set();
  for (const [kind, field] of Object.entries(HELD_KINDS)) {
    for (const [id, holders] of Object.entries(holdersByComponent(field))) {
      if (holders.length === 1) out.add(id);
    }
  }
  return out;
}

/* The held components drawn inside this agent's box, as chips. */
function heldChips(agent, readOnly) {
  if (!agent) return [];
  const chips = [];
  for (const [kind, field] of Object.entries(HELD_KINDS)) {
    const holders = holdersByComponent(field);
    for (const id of agent[field] || []) {
      if ((holders[id] || []).length !== 1) continue;   // shared: it has a node
      const component = findComponent(kind, id);
      const chip = el("span", {
        class: "n-held", "data-kind": kind,
        title: `${kindSpec(kind).label} ${id} — held only by ${agent.id}. `
          + "Link it to a second agent and it becomes a shared component with "
          + "an edge of its own.",
      },
        el("span", { class: "ic" }, kindSpec(kind).icon || "\u25ab"),
        component?.name || id);
      chip.addEventListener("click", (e) => {
        e.stopPropagation();
        if (canvas.linking) return;
        canvas.selected = { kind, id };
        showSide("details");
        syncSelection();
        renderInspector();
      });
      chips.push(chip);
    }
  }
  return chips.length
    ? [el("div", { class: "n-held-row" }, ...chips)]
    : [];
}

function renderNode(node, component = findComponent(node.kind, node.id) || {}) {
  const linking = canvas.linking;
  const blocked = lockOn(node.id);
  /* The Deployment diagram draws the binding and does not edit it
     (ADR-0112 M7): a box there can be moved and inspected, not deleted,
     renamed or linked. */
  const readOnly = !canvas.permissions.includes("system.edit") || !!blocked
    || !!ASPECTS[aspectOf()].readOnly;
  const aspect = aspectOf();

  /* × delete button — top-right corner */
  /* Out of the Tab order on purpose: Delete on the focused node does the
     same, and a button inside every box would put two stops per node in a
     diagram a keyboard user is moving through with arrows. */
  const delBtn = el("button", {
    class: "node-delete-btn", title: "Delete", tabindex: "-1",
    "aria-label": `Delete ${component.name || node.id}`,
    disabled: readOnly ? "" : null,
  }, el("span", { "aria-hidden": "true" }, "×"));
  delBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    deleteNode(node.kind, node.id);
  });

  /* inline-editable title */
  const titleEl = el("div", { class: "n-title" }, component.name || component.id || component.target || component.engine || component.capability || component.workflow || node.id);
  if (!readOnly) {
    titleEl.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      startInlineRename(node, component, titleEl);
    });
  }

  const box = el("div", {
    class: `node${isContainer(node) ? " container" : ""}`
      + `${canvas.selected?.id === node.id ? " selected" : ""}`
      + `${blocked ? " locked" : ""}`
      + (linking
          ? (linking.id === node.id ? " link-source"
             : linkRule(linking.kind, node.kind) ? " link-target" : " link-no")
          : ""),
    "data-kind": node.kind, "data-id": node.id,
    "data-shape": shapeOf(node.kind),
    "data-step": node.kind === "step" ? (component.kind || "tool") : null,
    "data-classification": node.kind === "agent"
      ? classificationOf(component)
      /* A data class carries its own classification on the same stripe an
         agent's inherited one uses (ADR-0080, ADR-0111). */
      : node.kind === "data_class" ? (component.scope || "private") : null,
    "data-aspect": aspect,
    "data-semantics": node.kind === "data_class" ? (component.semantics || "unspecified") : null,
    style: `left:${node.x}px; top:${node.y}px; min-width:${node.width}px`
      + (isContainer(node)
          ? `; width:${node.width}px; height:${node.height || CONTAINER.height}px`
          : ""),
    title: blocked ? `locked by ${blocked.holder_name || blocked.holder}` : "",
    tabindex: "-1",
    role: "group",
    "aria-roledescription": node.kind === "step" ? "step" : kindSpec(node.kind).label,
    "aria-label": nodeLabel(node, component)
      + (blocked ? `, locked by ${blocked.holder_name || blocked.holder}` : ""),
  },
    delBtn,
    node.kind === "environment"
      ? el("span", { class: "n-net" }, postureOf(component))
      : el("span", { class: "n-icon", "aria-hidden": "true" }, kindSpec(node.kind).icon || "▫"),
    el("div", { class: "n-kind" }, nodeKindLabel(node, component, aspect)),
    titleEl,
    el("div", { class: "n-sub" }, nodeSubtitle(node.kind, component, node)),
    ...(node.kind === "step" ? stepEngine(component) : []),
    ...(node.kind === "data_class" ? dataClassBody(component) : []),
    ...(node.kind === "agent" && aspect !== "data" ? heldChips(component, readOnly) : []),
    ...(isContainer(node) && !readOnly
        ? [el("span", { class: "n-resize", title: "Drag to resize" })] : []));
  /* Pointer events, not mouse events: one handler for a mouse, a pen and a
     finger. `touch-action: none` on the node (styles.css) stops a touch drag
     from scrolling the canvas instead. */
  box.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    if (delBtn.contains(e.target)) return;
    if (e.target.closest?.("a, input")) return;
    if (e.target.classList?.contains("n-resize")) {
      startResize(e, node, box);
      return;
    }
    /* While a link is being drawn, a press is aiming at a target rather than
       picking the node up. */
    if (canvas.linking) return;
    startDrag(e, node, box);
  });
  box.addEventListener("click", (e) => {
    if (delBtn.contains(e.target)) return;
    canvas.focusId = node.id;
    box.focus({ preventScroll: true });
    if (canvas.linking) return completeLink(node);
    selectNode(node);
  });
  box.addEventListener("keydown", (e) => handleNodeKey(e, node, box, readOnly));
  box.addEventListener("focus", () => { canvas.focusId = node.id; });
  box.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    selectNode(node);
    showContextMenu(e.clientX, e.clientY, node, readOnly);
  });
  return box;
}


/* The line above a box's name. On the Data and Deployment diagrams it is
   the UML stereotype, as a class diagram and a deployment diagram print it:
   a data class's semantics («event», «aggregate»), a server's «node», a
   bound capability's «artifact». */
function nodeKindLabel(node, component, aspect) {
  if (node.kind === "step") return stepKindLabel(component);
  if (node.kind === "data_class") {
    const s = component?.semantics;
    return s && s !== "unspecified" ? `«${s}»` : "«DataClass»";
  }
  if (BINDING_KINDS[node.kind]) return BINDING_KINDS[node.kind].stereo;
  if (aspect === "deployment") {
    return node.kind === "environment" ? "«executionEnvironment»" : "«artifact»";
  }
  if (aspect === "data" && node.kind === "agent") return "Agent";
  return kindSpec(node.kind).label;
}

/* A pairing that names a declared person carries no name of its own
   (ADR-0079), so reading `human.name` inline printed `undefined` on every
   agent in a migrated design. The reference resolves here the way it resolves
   in the IR — and the fallbacks matter, because a pairing may legitimately be
   inline, or name somebody the spec has not declared yet while it is being
   edited. */
function personLabel(human) {
  if (!human) return "";
  if (human.person) {
    const person = (org(spec())?.people || []).find((p) => p.id === human.person);
    if (person) return person.name || person.id;
    return human.person;
  }
  return human.name || human.contact || "";
}

/* A policy's `conditions` and `unless` (ADR-0008).

   The keys come from the palette, which takes them from the model's
   `POLICY_CONDITION_KEYS` — a form that offered its own list could offer one
   nothing evaluates, which is the defect this control exists to prevent. A
   single transposed letter in `unless` used to disapply the whole rule, so a
   deny on PII became an allow.

   Values are typed per key because the evaluator expects shapes: a list for
   the `_in`-style keys, a number for a depth, a flag for an approval, and a
   pair of hours for a window. */
const CONDITION_SHAPES = {
  max_delegation_depth: "number",
  requires_approval: "bool",
  environments: "list",
  data_classes: "list",
  groups: "list",
  time_window: "hours",
};

function conditionValueControl(key, value, readOnly, onValue) {
  const shape = CONDITION_SHAPES[key] || "list";
  if (shape === "bool") {
    const box = el("input", { type: "checkbox", ...(readOnly ? { disabled: "" } : {}) });
    box.checked = value === true;
    box.addEventListener("change", () => onValue(box.checked));
    return box;
  }
  if (shape === "number") {
    const input = el("input", { type: "number", min: "0",
                                ...(readOnly ? { disabled: "" } : {}) });
    input.value = value ?? "";
    input.addEventListener("input", () =>
      onValue(input.value === "" ? null : Number(input.value)));
    return input;
  }
  if (shape === "hours") {
    const [from, to] = Array.isArray(value) ? value : ["", ""];
    const start = el("input", { type: "number", min: "0", max: "23",
                                placeholder: "from",
                                ...(readOnly ? { disabled: "" } : {}) });
    const end = el("input", { type: "number", min: "0", max: "24",
                              placeholder: "to",
                              ...(readOnly ? { disabled: "" } : {}) });
    start.value = from ?? ""; end.value = to ?? "";
    const push = () => onValue([Number(start.value || 0), Number(end.value || 0)]);
    start.addEventListener("input", push);
    end.addEventListener("input", push);
    return el("span", { class: "hours" }, start, "–", end);
  }
  const input = el("input", { type: "text", placeholder: "one or more, comma separated",
                              ...(readOnly ? { disabled: "" } : {}) });
  input.value = Array.isArray(value) ? value.join(", ") : (value ?? "");
  input.addEventListener("input", () =>
    onValue(input.value.split(",").map((v) => v.trim()).filter(Boolean)));
  return input;
}

function renderConditions(field, value, readOnly, onChange) {
  const keys = field.options || [];
  const current = { ...(value || {}) };
  const wrap = el("div", { class: "conditions-wrap" });

  const redraw = () => {
    const rows = Object.keys(current).sort().map((key) => {
      const drop = el("button", { class: "chip-x", type: "button",
                                  title: `remove ${key}`,
                                  ...(readOnly ? { disabled: "" } : {}) }, "×");
      drop.addEventListener("click", () => {
        delete current[key];
        onChange({ ...current });
        redraw();
      });
      return el("div", { class: "condition-row" },
        el("code", {}, key),
        conditionValueControl(key, current[key], readOnly, (v) => {
          current[key] = v;
          onChange({ ...current });
        }),
        drop);
    });
    const unused = keys.filter((k) => !(k in current));
    const add = el("select", { ...(readOnly || !unused.length ? { disabled: "" } : {}) },
      el("option", { value: "" },
        unused.length ? "add a condition…" : "every condition is set"),
      ...unused.map((k) => el("option", { value: k }, k)));
    add.addEventListener("change", () => {
      if (!add.value) return;
      current[add.value] = CONDITION_SHAPES[add.value] === "bool" ? true
        : CONDITION_SHAPES[add.value] === "number" ? 0
        : CONDITION_SHAPES[add.value] === "hours" ? [9, 17] : [];
      onChange({ ...current });
      redraw();
    });
    wrap.replaceChildren(
      ...(rows.length ? rows
        : [el("p", { class: "hint" }, "No conditions: this rule always applies.")]),
      add);
  };
  redraw();
  return wrap;
}

/* ------------------------------------------------------------- linking */

/* What may be linked to what comes from the server with the palette, so the
   canvas cannot offer a relationship the spec has no field for. */
/* "a agent" reads as carelessness in a message whose job is to explain a
   refusal, so the article follows the word. */
function an(kind, capitalise = false) {
  const article = /^[aeiou]/i.test(kind) ? "an" : "a";
  return `${capitalise ? article[0].toUpperCase() + article.slice(1) : article} ${kind}`;
}

/* Two kinds may be related in more than one way. Team to team is the case
   that forced this: "is part of" and "is associated with" are different
   relationships (ADR-0081), and a canvas that picked one for you would be
   guessing — which is what the proximity-parenting bug was. */
/* The rules between two kinds, in either direction where the model says a
   relationship may be drawn from either end (ADR-0101): knowledge is linked
   to an agent whether the line starts at the knowledge or at the agent. A
   reversed rule is marked so the request names its ends the model's way. */
function linkRules(sourceKind, targetKind) {
  const links = canvas.palette?.links || [];
  const forward = links.filter(
    (r) => r.source === sourceKind && r.target === targetKind);
  const backward = sourceKind === targetKind ? [] : links
    .filter((r) => r.bidirectional && r.source === targetKind
                   && r.target === sourceKind)
    .map((r) => ({ ...r, reversed: true }));
  return [...forward, ...backward];
}

/* --------------------------------------------------------- the model decides
   Every gesture is one model operation (ADR-0103). The canvas sends it with
   the draft it holds and takes back the model's answer: the new draft, and
   what else changed — or the refusal and why. The canvas adds no rules. */
class ModelRefusal extends Error {
  constructor(answer) {
    super((answer.violations || []).map((v) => `${v.element}: ${v.message}`)
      .join("\n") || "the model refused the change");
    this.answer = answer;
  }
}

async function modelOperation(request) {
  const answer = await dapi("/operations", {
    method: "POST", body: JSON.stringify({ spec: spec(), request }),
  });
  if (!answer.accepted) throw new ModelRefusal(answer);
  canvas.record.spec = answer.spec;
  return answer;
}

/* What else the model changed, and what a new element still lacks: a draft
   may be incomplete, and says so rather than hiding it (ADR-0103). */
function effectsLine(answer) {
  const effects = (answer.effects || []).length
    ? ` — ${answer.effects.join("; ")}` : "";
  const lacks = (answer.incomplete || []).length
    ? ` — still needs: ${answer.incomplete.map((v) => v.message).join("; ")}`
    : "";
  return effects + lacks;
}

function linkRule(sourceKind, targetKind) {
  return linkRules(sourceKind, targetKind)[0] || null;
}

/* When several apply, the reader says which. The prompt states what each one
   does, because "contains" and "associates" have very different consequences
   and neither is obvious from a line on a canvas. */
async function chooseRule(rules, from, target) {
  if (rules.length === 1) return rules[0];
  /* A list of choices, each with what it does — it was a numbered prompt,
     and a mistyped number was an error after the fact. */
  const answer = await ui.formDialog({
    title: `How is ${target.id} related to ${from.id}?`,
    submitLabel: "Link", wide: true,
    fields: [{ name: "rule", label: "Relationship", type: "radio",
               options: rules.map((r, i) => [String(i), r.label, r.help]) }],
  });
  return answer ? rules[Number(answer.rule)] : null;
}

/* Where a line from this kind may go: its own relationships, and the ones
   that may be drawn from either end (ADR-0101) — a knowledge source links to
   the agents that consult it though the agent is what stores the link. */
function legalTargetsFrom(sourceKind) {
  const targets = (canvas.palette?.links || []).flatMap((r) => [
    ...(r.source === sourceKind ? [r.target] : []),
    ...(r.bidirectional && r.target === sourceKind && r.source !== sourceKind
      ? [r.source] : []),
  ]);
  return [...new Set(targets)];
}

function beginLink(node) {
  const targets = legalTargetsFrom(node.kind);
  if (!targets.length) {
    setStatus(`nothing links from ${an(node.kind)}`);
    ui.announce(`Nothing links from ${an(node.kind)}.`);
    return;
  }
  canvas.linking = { id: node.id, kind: node.kind };
  setStatus(`linking from ${node.id} — click ${targets.map((t) => an(t)).join(" or ")}`
    + " (or arrow to it and press Enter), or press Escape");
  ui.announce(`Linking from ${node.id}. Arrow to ${targets.map((t) => an(t)).join(" or ")} `
    + "and press Enter, or Escape to cancel.");
  renderCanvas();
}

function cancelLink(quiet = false) {
  canvas.linking = null;
  if (!quiet) { setStatus("link cancelled"); ui.announce("Link cancelled."); }
  renderCanvas();
}

/* Detach a component from wherever it sits, without deleting it. The spec is
   a tree, so "unlinked" is the root: there is no other place to be. */
function detach(kind, id) {
  const s = spec();
  if (kind === "team") {
    let moved = null;
    walkTeams(s.organization, (team) => {
      const at = (team.teams || []).findIndex((t) => t.id === id);
      if (at >= 0) moved = (team.teams.splice(at, 1))[0];
    });
    return moved;
  }
  if (kind === "agent") {
    let moved = null;
    walkTeams(s.organization, (team) => {
      const at = (team.members || []).findIndex((m) => m.id === id);
      if (at >= 0) {
        moved = (team.members.splice(at, 1))[0];
        if (team.leader === id) team.leader = (team.members[0] || {}).id || "";
      }
    });
    return moved;
  }
  if (kind === "subagent") {
    let moved = null;
    for (const { agent } of allAgents()) {
      const at = (agent.subagents || []).findIndex((x) => x.id === id);
      if (at >= 0) moved = (agent.subagents.splice(at, 1))[0];
    }
    return moved;
  }
  return null;
}

async function completeLink(target) {
  const from = canvas.linking;
  if (!from) return;
  if (from.id === target.id) return cancelLink();
  const rules = linkRules(from.kind, target.kind);
  if (!rules.length) {
    /* The refusal names the model, not the UI: two agents are not connected
       by a line, they are connected by a declared flow or by belonging to the
       same organisation. The link stays open, so the next target can be
       tried without starting again. */
    const legal = legalTargetsFrom(from.kind);
    const why = `${an(from.kind, true)} does not link to ${an(target.kind)}. `
      + (legal.length
          ? `From ${an(from.kind)} you can link to: ${legal.join(", ")}.`
          : `Nothing links from ${an(from.kind)}.`);
    setStatus(why);
    ui.announce(why);
    return;
  }
  const rule = await chooseRule(rules, from, target);
  if (!rule) return cancelLink();
  canvas.linking = null;
  try {
    const answer = await applyLink(rule, from, target);
    if (answer === null) { setStatus("link cancelled"); return; }
    markDirty(`linked ${from.id} → ${target.id}`);
    setStatus(`${from.id} ${rule.label} ${target.id}${effectsLine(answer)}`);
    ui.announce(`Linked ${from.id} to ${target.id}.`);
  } catch (err) {
    await ui.alertDialog(err.message, { title: "Not linked" });
  } finally {
    renderCanvas();
    renderInspector();
  }
}

/* The request for a link, named the model's way round. What the model needs
   and the canvas cannot know — a flow's kind, a unit link's reason — is
   asked for here; everything else is the model's to decide. */
async function applyLink(rule, from, target) {
  const [src, dst] = rule.reversed ? [target, from] : [from, target];
  const attrs = {};
  if (rule.kinds && rule.kinds.length) {
    /* One dialog for what the model needs and the canvas cannot know: the
       kind is a choice from the model's list (it was free text, so a typo
       went to the server), and the reason sits beside it. Cancel links
       nothing; the caller reads null as that. */
    const answer = await ui.formDialog({
      title: `${rule.label}: ${src.id} → ${dst.id}`,
      message: rule.help || "",
      submitLabel: "Link",
      fields: [
        { name: "kind", label: "Kind", type: "select", value: rule.kinds[0],
          options: rule.kinds.map((k) => [k, k]) },
        ...(rule.relationship === "association"
          ? [{ name: "reason", label: "Why does this relationship exist?",
               type: "textarea", required: false,
               help: "An association nobody can explain is decoration, and "
                 + "the validator says so." }]
          : []),
      ],
    });
    if (!answer) return null;
    attrs.kind = answer.kind;
    if (rule.relationship === "association") attrs.reason = answer.reason || "";
  }
  return modelOperation({
    op: "link",
    source: { kind: src.kind, id: src.id },
    target: { kind: dst.kind, id: dst.id },
    /* A field holding several relationships (`relations`, ADR-0111) names
       none of them: the link is asked for by its stereotype, and the model
       writes the `kind` that selects it. */
    relationship: rule.selector ? rule.relationship : rule.field,
    ...(Object.keys(attrs).length ? { attrs } : {}),
  });
}

/* Unlinking is defined for what a link created. A structural link put the
   component somewhere, so undoing it returns it to the root; a flow is a
   declaration, so undoing it removes the declaration. */
async function unlink(node) {
  const s = spec();
  if (node.kind === "subagent") {
    return ui.alertDialog("A sub-agent is a part of the agent that calls it: "
      + "move it to another agent or delete it.", { title: "Nothing to unlink" });
  }
  const requests = [];
  if (node.kind === "team" || node.kind === "agent") {
    if (node.id === s.organization?.id) {
      return ui.alertDialog("The organisation itself has nowhere to be "
        + "unlinked to.", { title: "Nothing to unlink" });
    }
    // A part has one whole; "unlinked" is moved to the organisation's root.
    requests.push({ op: "link",
      source: { kind: "team", id: s.organization.id },
      target: { kind: node.kind, id: node.id },
      relationship: node.kind === "team" ? "teams" : "members" });
  }
  if (HELD_KINDS[node.kind]) {
    for (const holder of holdersOf(node.kind, node.id)) {
      requests.push({ op: "unlink", source: { kind: "agent", id: holder },
        target: { kind: node.kind, id: node.id },
        relationship: HELD_KINDS[node.kind] });
    }
  }
  for (const l of org(s).unit_links || []) {
    if (l.source === node.id || l.target === node.id) {
      requests.push({ op: "unlink", source: { kind: "team", id: l.source },
        target: { kind: "team", id: l.target }, relationship: "unit_links" });
    }
  }
  for (const f of org(s).interaction_flows || []) {
    if (f.source === node.id || f.target === node.id) {
      requests.push({ op: "unlink", source: { kind: "agent", id: f.source },
        target: { kind: "agent", id: f.target },
        relationship: "interaction_flows" });
    }
  }
  if (!requests.length) {
    return ui.alertDialog(`Nothing links ${node.id}.`, { title: "Nothing to unlink" });
  }
  const said = [];
  for (const request of requests) {
    try {
      const answer = await modelOperation(request);
      said.push((answer.effects || []).join("; ")
        || `${request.op} ${request.target.id}`);
    } catch (err) {
      said.push(`refused: ${err.message}`);
    }
  }
  markDirty(`unlinked ${node.id}`);
  setStatus(said.filter(Boolean).join(" · "));
  renderCanvas();
  renderInspector();
  offerUndo(`Unlinked ${node.id}.`);
}

function nodeSubtitle(kind, component, node) {
  if (kind === "step") return stepOwnerText(component);
  if (node && (BINDING_KINDS[kind] || diagram()?.kind === "deployment")) {
    const entry = deploymentModel().nodes.get(node.id);
    if (entry) return deploymentSubtitle(entry);
  }
  /* A data class's classification, as words beside its stripe. */
  if (kind === "data_class") {
    return [component.scope || "private", ...(component.groups || [])].join(" · ");
  }
  if (kind === "team") return `leader: ${component.leader || "—"}`;
  if (kind === "agent") {
    const owner = (component.humans || []).find((h) => (h.roles || []).includes("owner"));
    const who = owner ? personLabel(owner) : "";
    return `${classificationOf(component)}${who ? ` · ${who}` : ""}`;
  }
  /* A mission always carries its end date: that is what makes it a mission. */
  if (kind === "mission") return `ends ${component.ends_on || "— undated"}`;
  if (kind === "environment") return `${component.tier || "minimal"} · network ${postureOf(component)}`;
  if (kind === "subagent") return `${component.kind || "custom"} · tool`;
  if (kind === "note") return node.note || "note";
  if (HELD_KINDS[kind]) {
    const holders = holdersOf(kind, component.id);
    return holders.length === 0 ? "held by nobody"
      : holders.length === 1 ? `in ${holders[0]}`
      : `shared by ${holders.length} agents`;
  }
  if (kind === "capability") return component.action || "";
  if (kind === "trigger") return component.kind || "";
  return component.description ? String(component.description).slice(0, 40) : "";
}

function renderEdges() {
  const svg = $("#canvas-edges");
  const layout = diagram();
  if (!layout) return svg.replaceChildren();
  if (layout.kind === "process") return renderProcessEdges(svg, layout);
  if (layout.kind === "data" || layout.kind === "deployment") {
    return renderUmlEdges(svg, layout);
  }
  const edges = derivedEdges().filter(edgeShown);
  const ns = "http://www.w3.org/2000/svg";
  const parts = [];
  for (const edge of edges) {
    const a = layout.nodes[edge.source], b = layout.nodes[edge.target];
    if (!a || !b) continue;
    const line = document.createElementNS(ns, "path");
    const { d, labelX, labelY } = edgeGeometry(a, b);
    line.setAttribute("d", d);
    /* Which boxes it joins, so a drag can move this line and no other. */
    line.setAttribute("data-source", edge.source);
    line.setAttribute("data-target", edge.target);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke-width", "1.5");
    /* An edge says which kind of relation it is by how it is drawn: a solid
       reporting line, a dashed mission peer that expires with the mission, a
       dotted amber egress that leaves the boundary. */
    const style = edge.association
      ? (EDGE_STYLES[`link:${edge.kind}`] || EDGE_STYLES["link:partners_with"])
      : (EDGE_STYLES[edge.kind] || EDGE_STYLES[`uml:${edge.uml}`]
         || EDGE_STYLES.member_of);
    line.setAttribute("data-uml", edge.uml || "");
    line.setAttribute("data-rel", edge.rel || "");
    line.setAttribute("stroke", `var(${style.stroke})`);
    if (style.dash) line.setAttribute("stroke-dasharray", style.dash);
    parts.push(line);
    if (edge.association || edge.label) {
      /* Containment is a line; an association is a line plus what it means.
         Without the word, the two would be told apart only by a dash
         pattern, and a reader should not have to consult a legend to know
         whether a team is inside another or supervising it. */
      const text = document.createElementNS(ns, "text");
      text.setAttribute("x", String(labelX));
      text.setAttribute("y", String(labelY));
      line.edgeLabel = text;
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("class", "edge-label");
      text.setAttribute("fill", `var(${style.stroke})`);
      text.textContent = edge.label || edge.kind.replace(/_/g, " ");
      parts.push(text);
    }
  }
  svg.replaceChildren(...parts);
}

/* The curve between two boxes, and where its label sits. */
function edgeGeometry(a, b) {
  const x1 = a.x + a.width / 2, y1 = a.y + 45;
  const x2 = b.x + b.width / 2, y2 = b.y + 45;
  const mid = (y1 + y2) / 2;
  return { d: `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`,
           labelX: (x1 + x2) / 2, labelY: mid - 4 };
}

/* During a drag only the lines touching the moving box change. A process
   diagram draws its edges its own way, so it is redrawn whole, once a frame. */
function updateEdgesFor(id) {
  const layout = diagram();
  if (!layout) return;
  if (layout.kind === "process") {
    if (!canvas.edgeFrame) {
      canvas.edgeFrame = requestAnimationFrame(() => { canvas.edgeFrame = 0; renderEdges(); });
    }
    return;
  }
  for (const path of $("#canvas-edges").querySelectorAll("path[data-source]")) {
    const source = path.getAttribute("data-source");
    const target = path.getAttribute("data-target");
    if (source !== id && target !== id) continue;
    const a = layout.nodes[source], b = layout.nodes[target];
    if (!a || !b) continue;
    const { d, labelX, labelY } = geometryFor(layout.kind, a, b);
    path.setAttribute("d", d);
    /* An association class hangs a little below the line it qualifies. */
    const hung = path.edgeLabel?.tagName === "g" ? 6 : 0;
    placeEdgeLabel(path.edgeLabel, labelX, labelY + hung);
  }
}

const EDGE_STYLES = {
  member_of: { stroke: "--edge-report" },
  flow: { stroke: "--edge-report" },
  branch_case: { stroke: "--edge-peer", dash: "5 4" },
  reports_to: { stroke: "--edge-report" },
  mission_peer: { stroke: "--edge-peer", dash: "5 4" },
  peer: { stroke: "--edge-peer", dash: "5 4" },
  uses: { stroke: "--edge-peer", dash: "5 4" },
  triggers: { stroke: "--edge-peer", dash: "5 4" },
  egress: { stroke: "--edge-egress", dash: "2 5" },
  /* Association, not containment (ADR-0081). Drawn apart from the reporting
     line on purpose: oversight that looks like a reporting line is how an
     independent function gets read as a subordinate one. */
  "link:oversees": { stroke: "--edge-egress", dash: "8 4" },
  "link:escalates_to": { stroke: "--edge-egress", dash: "2 4" },
  "link:serves": { stroke: "--edge-peer", dash: "8 4" },
  "link:partners_with": { stroke: "--edge-peer", dash: "1 5" },
  /* The model's other relationships, drawn by their UML kind (ADR-0105). */
  "uml:association": { stroke: "--edge-peer" },
  "uml:usage": { stroke: "--edge-peer", dash: "5 4" },
  "uml:realization": { stroke: "--edge-report", dash: "3 3" },
  "uml:dependency": { stroke: "--edge-peer", dash: "2 4" },
  "uml:composition": { stroke: "--edge-report" },
  /* «derive» (ADR-0111), where both classes are on an organisation diagram. */
  "uml:abstraction": { stroke: "--edge-report", dash: "6 4" },
  "uml:deployment": { stroke: "--edge-report", dash: "6 4" },
};

/* Placement regions (ADR-0069).

   Mirrors `orgagents.placements.resolve`'s membership rule, and only that:
   a team that declares `placement: true` is a boundary, a team that does not
   sits in its nearest declaring ancestor, and the root always declares. The
   network rules are the server's — they need the whole spec and they are not
   a shape on a canvas.

   Derived from the spec rather than stored in the layout, for the same reason
   the edges are: the picture always matches what would compile. A test runs
   this function under node against the Python resolver so the two cannot
   drift apart quietly. */
function derivedPlacements() {
  const root = spec()?.organization;
  if (!root) return [];
  const members = new Map();          // "unit--env" -> {unit, environment, agents}
  const walk = (team, inherited) => {
    const unit = team.placement ? team.id : inherited;
    for (const agent of team.members || []) {
      /* An agent may run in more than one sandbox (ADR-0082), and it is in
         one placement per sandbox: the blast radius of reading a ledger and
         of instructing a bank are not the same thing drawn twice. */
      for (const override of agent.environments || []) {
        // The list picker writes bare ids; a loaded spec may carry the
        // `{environment: id}` override shape. Both mean the same sandbox.
        const environment = typeof override === "string"
          ? override : override?.environment;
        if (!environment) continue;   // no environment class, so no place
        const id = `${unit}--${environment}`;
        if (!members.has(id)) members.set(id, { id, unit, environment, agents: [] });
        members.get(id).agents.push(agent.id);
      }
    }
    for (const child of team.teams || []) walk(child, unit);
  };
  walk(root, root.id);
  /* The posture comes off the environment class, because that is where it is
     declared. A region's border says it, so a reader sees which places can
     reach out at all without opening anything. */
  const postures = new Map(
    (org(spec())?.environments || []).map((e) => [e.id, e.network || "none"]));
  return [...members.values()]
    .map((p) => ({
      ...p,
      agents: p.agents.sort(),
      network: postures.get(p.environment) || "none",
    }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

/* The rectangle a region draws: the bounding box of its members' nodes, with
   room for the label. A placement whose agents are not on the canvas has no
   box, which is honest — there is nothing placed to draw around. */
const REGION_PAD = 22;
const REGION_LABEL = 26;

function regionBoxes() {
  const layout = diagram();
  if (!layout) return [];
  const out = [];
  for (const placement of derivedPlacements()) {
    const boxes = placement.agents
      .map((id) => layout.nodes[id])
      .filter(Boolean);
    if (!boxes.length) continue;
    const x = Math.min(...boxes.map((b) => b.x)) - REGION_PAD;
    const y = Math.min(...boxes.map((b) => b.y)) - REGION_PAD - REGION_LABEL;
    const right = Math.max(...boxes.map((b) => b.x + (b.width || 200)));
    const bottom = Math.max(...boxes.map((b) => b.y + (b.height || 80)));
    out.push({
      ...placement,
      x, y,
      width: right - x + REGION_PAD,
      height: bottom - y + REGION_PAD,
      drawn: boxes.length,
      missing: placement.agents.length - boxes.length,
    });
  }
  /* Largest first, so a small region nested inside a big one stays clickable
     and readable rather than being painted over. */
  return out.sort((a, b) => b.width * b.height - a.width * a.height);
}

function renderRegions() {
  const host = $("#canvas-regions");
  if (!host) return;
  if (diagram()?.kind === "process") return renderLanes(host);
  /* Placement regions are the organisation's ground: on the Data and
     Deployment diagrams an agent box is not standing in a placement. */
  if (diagram()?.kind === "data" || diagram()?.kind === "deployment") return host.replaceChildren();
  host.replaceChildren(...regionBoxes().map((region) =>
    el("div", {
      class: "region",
      "data-network": region.network || "",
      style: `left:${region.x}px; top:${region.y}px; `
        + `width:${region.width}px; height:${region.height}px`,
      title: `${region.unit} × ${region.environment} — `
        + `${region.agents.length} agent(s) share a volume and a process `
        + `namespace here. A placement is not a security boundary.`,
    },
      el("span", { class: "region-label" },
        el("code", {}, region.id),
        region.missing
          ? el("span", { class: "region-missing" },
              `${region.missing} not on the canvas`)
          : null))));
}

/* The components an agent *holds* rather than contains, and the spec field
   each is stored in. Held is many-to-one: the same tool may be on several
   agents, which is what makes "inside the box" the wrong picture for it once
   a second agent reaches for it. */
const HELD_KINDS = { skill: "skills", plugin: "plugins", tool: "tools" };

function holdersByComponent(field) {
  const out = {};
  for (const { agent } of allAgents())
    for (const id of agent[field] || []) (out[id] = out[id] || []).push(agent.id);
  return out;
}

/* Who holds this component, by id, across every held field. */
function holdersOf(kind, id) {
  const field = HELD_KINDS[kind];
  return field ? (holdersByComponent(field)[id] || []) : [];
}

function derivedEdges() {
  /* Edges come from the spec, not from the layout: the picture always matches
     what would compile. */
  const out = [];
  /* A process canvas draws one workflow's graph, and its edges are that
     workflow's own — read here, never stored on the diagram, which is the one
     rule the diagram model has (ADR-0100). */
  const open = diagram();
  if (open?.kind === "process") {
    for (const edge of processEdges(open.root)) {
      out.push({ source: edge.source, target: edge.target, kind: edge.kind,
                 label: edge.label || "" });
    }
    return out;
  }
  /* The Data diagram draws the data model's relationships and nothing of
     the organisation's; the Deployment diagram draws the binding's. */
  if (open?.kind === "data") return dataEdges();
  if (open?.kind === "deployment") return deploymentModel().edges;
  walkTeams(spec()?.organization, (team) => {
    (team.members || []).forEach((m) =>
      out.push({ source: team.id, target: m.id, kind: "member_of",
                 uml: "composition", rel: "has member" }));
    (team.teams || []).forEach((child) =>
      out.push({ source: team.id, target: child.id, kind: "member_of",
                 uml: "composition", rel: "contains" }));
    (team.members || []).forEach((m) =>
      (m.subagents || []).forEach((sub) =>
        out.push({ source: m.id, target: sub.id, kind: "uses",
                   uml: "composition", rel: "uses" })));
  });
  /* A held component drawn twice would say two different things. One holder
     means the thing lives inside that agent's box, so no edge is drawn; two
     or more means it is shared, and sharing is exactly what an edge is for. */
  for (const [kind, field] of Object.entries(HELD_KINDS)) {
    for (const [id, holders] of Object.entries(holdersByComponent(field))) {
      if (holders.length < 2) continue;
      holders.forEach((agentId) =>
        out.push({ source: agentId, target: id, kind: `holds:${kind}`,
                   uml: kind === "tool" ? "usage" : "association",
                   rel: `holds ${kind}` }));
    }
  }
  (org(spec())?.interaction_flows || []).forEach((f) =>
    out.push({ source: f.source, target: f.target, kind: f.kind,
               uml: "association", rel: "flow" }));
  /* Association, drawn as an association: dashed, labelled with its kind, and
     never mistakable for the containment line above (ADR-0081). */
  (org(spec())?.unit_links || []).forEach((l) =>
    out.push({ source: l.source, target: l.target, kind: l.kind,
               association: true, uml: "association", rel: "unit link" }));
  (org(spec())?.triggers || []).forEach((t) =>
    out.push({ source: t.id, target: t.agent, kind: "triggers",
               uml: "association", rel: "fires" }));
  out.push(...modelEdges());
  return out;
}

/* Every other relationship the model draws as an edge, from the model's own
   link rules (ADR-0101): a knowledge source to the agents that consult it, a
   role to the capabilities it grants. The ones drawn above in their own way —
   containment, held components, flows, unit links, triggers — and the ones
   drawn as nesting (deployment) are not repeated here. */
const DRAWN_ELSEWHERE = new Set(
  ["contains", "member", "uses", "holds", "flow", "association", "fires"]);

function componentsOf(kind) {
  const s = spec();
  if (!s) return [];
  if (kind === "agent") return allAgents().map(({ agent }) => agent);
  if (kind === "subagent") {
    return allAgents().flatMap(({ agent }) => agent.subagents || []);
  }
  if (kind === "team") return allTeams();
  if (NESTED[kind]) return nestedList(s, kind);
  return COLLECTIONS[kind] ? (org(s)[COLLECTIONS[kind]] || []) : [];
}

function modelEdges() {
  const out = [];
  for (const rule of canvas.palette?.links || []) {
    if (rule.draw !== "edge" || DRAWN_ELSEWHERE.has(rule.relationship)) continue;
    if (!["ref", "refs", "ref_objects"].includes(rule.shape)) continue;
    const owner = rule.owner === "target" ? rule.target : rule.source;
    const other = owner === rule.source ? rule.target : rule.source;
    for (const item of componentsOf(owner)) {
      const value = item[rule.field];
      /* One field may hold several relationships, told apart by a selector
         (`relations` by `kind`, ADR-0111): each rule draws only its own. */
      const mine = (o) => o && (!rule.selector || o[rule.selector[0]] === rule.selector[1]);
      const ids = rule.shape === "ref" ? [value]
        : rule.shape === "refs" ? (value || [])
        : (value || []).filter(mine).map((o) => o[rule.key]);
      for (const id of ids.filter((x) => x && x !== "*")) {
        out.push({
          source: owner === rule.source ? item.id : id,
          target: owner === rule.source ? id : item.id,
          kind: `rel:${rule.field}`, uml: rule.uml, rel: rule.label,
          label: rule.label, targetKind: other,
        });
      }
    }
  }
  return out;
}

/* ------------------------------------------------ filtering by relationship
   What the canvas draws is chosen by UML kind and, within it, by
   relationship (ADR-0105). Remembered per viewer, in this browser: it is a
   way of looking, not a change to the design. */
const EDGE_FILTER_KEY = "orgagents.edgeFilter";

function hiddenEdges() {
  if (!canvas.hiddenEdges) {
    try {
      canvas.hiddenEdges = new Set(
        JSON.parse(localStorage.getItem(EDGE_FILTER_KEY) || "[]"));
    } catch {
      canvas.hiddenEdges = new Set();
    }
  }
  return canvas.hiddenEdges;
}

function setEdgeHidden(key, hidden) {
  const set = hiddenEdges();
  if (hidden) set.add(key); else set.delete(key);
  try {
    localStorage.setItem(EDGE_FILTER_KEY, JSON.stringify([...set]));
  } catch { /* a private window keeps it for this page only */ }
  renderEdges();
  renderEdgeFilter();
}

function edgeShown(edge) {
  const hidden = hiddenEdges();
  if (!edge.uml) return true;
  return !hidden.has(`uml:${edge.uml}`) && !hidden.has(`rel:${edge.uml}:${edge.rel}`);
}

const UML_ORDER = ["composition", "association", "usage", "realization",
                   "dependency", "abstraction", "deployment"];

function renderEdgeFilter() {
  const host = $("#edge-filter");
  if (!host) return;
  const layout = diagram();
  if (!layout || layout.kind === "process") return host.replaceChildren();
  const counts = {};
  for (const e of derivedEdges()) {
    if (!e.uml || !layout.nodes[e.source] || !layout.nodes[e.target]) continue;
    const byRel = (counts[e.uml] = counts[e.uml] || {});
    byRel[e.rel] = (byRel[e.rel] || 0) + 1;
  }
  const hidden = hiddenEdges();
  const kinds = UML_ORDER.filter((k) => counts[k]);
  if (!kinds.length) return host.replaceChildren();
  host.replaceChildren(
    el("span", { class: "ef-title" }, "Relationships"),
    ...kinds.map((uml) => {
      const total = Object.values(counts[uml]).reduce((a, b) => a + b, 0);
      const kindBox = el("input", { type: "checkbox", "data-uml": uml });
      kindBox.checked = !hidden.has(`uml:${uml}`);
      kindBox.addEventListener("change",
        () => setEdgeHidden(`uml:${uml}`, !kindBox.checked));
      const rels = Object.entries(counts[uml]).sort().map(([rel, n]) => {
        const box = el("input", { type: "checkbox", "data-rel": rel });
        box.checked = !hidden.has(`rel:${uml}:${rel}`);
        box.disabled = !kindBox.checked;
        box.addEventListener("change",
          () => setEdgeHidden(`rel:${uml}:${rel}`, !box.checked));
        return el("label", { class: "ef-rel" }, box, `${rel} (${n})`);
      });
      // The kind's box and the list of its relationships are separate
      // controls: opening the list must not untick the kind.
      return el("span", { class: "ef-kind", "data-uml": uml },
        el("label", { class: "ef-uml", "data-uml": uml }, kindBox,
           `${uml} (${total})`),
        (() => {
          // Stays open across re-renders: ticking one box must not close
          // the list it is in.
          const open = (canvas.edgeFilterOpen = canvas.edgeFilterOpen || new Set());
          const list = el("details", { "data-uml": uml },
            el("summary", { title: `Choose which ${uml} relationships to show` },
               "\u25be"),
            ...rels);
          list.open = open.has(uml);
          list.addEventListener("toggle", () => {
            if (list.open) open.add(uml); else open.delete(uml);
          });
          return list;
        })());
    }));
}

/* -------------------------------------------------- model explorer */
/*
   What the model *contains*, as against what the canvas happens to be
   showing. They are not the same thing and the difference is the point: a
   component declared in the spec but never laid out is invisible on the
   canvas, and there is otherwise no way to find it.

   Grouped the way the palette is grouped, so the tree somebody reads and the
   tree somebody builds from have the same shape.
*/
function explorerModel() {
  const s = spec();
  if (!s) return [];

  const unit = (team) => ({
    id: team.id, kind: "team", label: team.name || team.id,
    note: team.leader ? `led by ${team.leader}` : "no leader",
    children: [
      ...(team.members || []).map((agent) => ({
        id: agent.id, kind: "agent", label: agent.name || agent.id,
        note: classificationOf(agent),
        children: (agent.subagents || []).map((sub) => ({
          id: sub.id, kind: "subagent", label: sub.name || sub.id,
          note: sub.kind || "", children: [],
        })),
      })),
      ...(team.teams || []).map(unit),
    ],
  });

  const flat = (kind, collection, note) => ({
    id: `group:${kind}`, kind: "group",
    label: kindSpec(kind).label, group: true,
    children: (org(s)[collection] || []).map((item) => ({
      id: item.id, kind, label: item.name || item.id,
      note: note ? note(item) : "", children: [],
    })),
  });

  return [
    { id: "group:organisation", kind: "group", label: "Organisation",
      group: true,
      children: s.organization?.id ? [unit(s.organization)] : [] },
    flat("person", "people", (p) => p.position || ""),
    flat("role", "role_definitions", (r) => r.title || ""),
    flat("decision", "decisions", (d) => d.title || ""),
    flat("separation", "separations",
         (x) => `${(x.decisions || []).length} decisions kept apart`),
    flat("policy", "policies", (p) => p.effect || ""),
    flat("capability", "capabilities", (c) => c.action || ""),
    flat("data_class", "data_classes", (d) => d.scope || ""),
    flat("environment", "environments", (e) => postureOf(e)),
    flat("endpoint", "endpoints", (e) => e.trust || ""),
    flat("mission", "missions", (m) => `ends ${m.ends_on || "—"}`),
    flat("workflow", "workflows"),
    flat("trigger", "triggers", (t) => t.kind || ""),
    flat("channel", "channels", (c) => c.purpose || ""),
    flat("knowledge", "knowledge", (k) => k.kind || ""),
    flat("skill", "skills"),
    flat("plugin", "plugins"),
    flat("tool", "tools"),
    flat("guardrail", "guardrails", (g) => g.on_violation || ""),
    flat("output_contract", "output_contracts"),
  ].filter((group) => group.children.length);
}

function matchesFilter(node, needle) {
  if (!needle) return true;
  const own = `${node.id} ${node.label} ${node.note || ""}`.toLowerCase();
  if (own.includes(needle)) return true;
  return (node.children || []).some((c) => matchesFilter(c, needle));
}

function renderExplorer() {
  const host = $("#explorer-tree");
  if (!host) return;
  const needle = ($("#explorer-filter")?.value || "").trim().toLowerCase();
  const laidOut = layoutNodes();

  const row = (node, depth) => {
    if (!matchesFilter(node, needle)) return [];
    const onCanvas = !node.group && !!laidOut[node.id];
    const choose = node.group ? null : () => {
      if (onCanvas) return selectAndReveal(node.id);
      canvas.selected = { kind: node.kind, id: node.id };
      showSide("details");
      syncSelection();
      renderInspector();
    };
    /* A row is a control, so a keyboard reaches it and Enter chooses it. */
    const item = el("div", {
      "data-id": node.group ? null : node.id,
      role: node.group ? null : "button",
      tabindex: node.group ? null : "0",
      onkeydown: node.group ? null : (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(); }
      },
      class: `ex-row${node.group ? " group" : ""}`
        + `${canvas.selected?.id === node.id ? " selected" : ""}`
        + `${onCanvas ? "" : " off-canvas"}`,
      style: `padding-left:${8 + depth * 13}px`,
      /* A component not on the canvas is dimmed rather than hidden: it is in
         the model, and pretending otherwise is how a declaration gets lost. */
      title: node.group ? ""
        : `${node.id}${node.note ? ` — ${node.note}` : ""}`
          + (onCanvas ? "" : " — declared, not on the canvas"),
      onclick: choose,
    },
      el("span", { class: "ic", "aria-hidden": "true" }, kindSpec(node.kind).icon || "▫"),
      el("span", { class: "ex-label" }, node.label),
      /* The note is context, the label is the thing. In a column this
         narrow, showing both at every depth truncated the labels to three
         characters, so the note gives way once the tree gets deep. */
      node.note && depth < 2 ? el("span", { class: "ex-note" }, node.note) : null,
      node.group ? el("span", { class: "ex-count" },
                      String(node.children.length)) : null);
    return [item, ...(node.children || []).flatMap((c) => row(c, depth + 1))];
  };

  const rows = explorerModel().flatMap((group) => row(group, 0));
  host.replaceChildren(...(rows.length ? rows
    : [el("p", { class: "hint" },
          needle ? `Nothing in the model matches '${needle}'.`
                 : "Nothing declared yet.")]));
}

/* ------------------------------------------------------------- outline */
/*
   The whole diagram at a fifth of the size, with the viewport drawn on it.
   A canvas larger than the window has no other way of saying where you are,
   and scrolling to look for a node you cannot see is not navigation.
*/
function renderOutline() {
  const svg = $("#outline-svg");
  const surface = $("#canvas");
  if (!svg || !surface) return;
  const nodes = Object.values(layoutNodes());
  if (!nodes.length) {
    svg.replaceChildren();
    $("#outline-viewport").style.display = "none";
    return;
  }
  const maxX = Math.max(...nodes.map((n) => n.x + n.width), surface.clientWidth);
  const maxY = Math.max(...nodes.map((n) => n.y + (n.height || 80)),
                        surface.clientHeight);
  const box = $("#outline-surface").getBoundingClientRect();
  if (box.width < 4 || box.height < 4) {
    /* Called before the panel has been laid out, which happens on the first
       paint: a scale computed from a zero box draws every node as a dot. */
    requestAnimationFrame(renderOutline);
    return;
  }
  const scale = Math.min(box.width / maxX, box.height / maxY);
  canvas.outlineScale = scale;

  const ns = "http://www.w3.org/2000/svg";
  svg.setAttribute("width", String(box.width));
  svg.setAttribute("height", String(box.height));
  svg.replaceChildren(...nodes.map((node) => {
    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("x", String(node.x * scale));
    rect.setAttribute("y", String(node.y * scale));
    rect.setAttribute("width", String(Math.max(2, node.width * scale)));
    rect.setAttribute("height", String(Math.max(2, (node.height || 80) * scale)));
    rect.setAttribute("class",
      `o-node${canvas.selected?.id === node.id ? " selected" : ""}`);
    rect.setAttribute("data-kind", node.kind);
    rect.setAttribute("data-id", node.id);
    return rect;
  }));
  positionOutlineViewport();
}

/* The viewport box alone: scrolling moves it, and scrolling happens on every
   frame of a scroll, so it must not rebuild the boxes under it. */
function positionOutlineViewport() {
  const surface = $("#canvas");
  const scale = canvas.outlineScale;
  const view = $("#outline-viewport");
  if (!surface || !scale || !view || !Object.keys(layoutNodes()).length) return;
  view.style.display = "block";
  view.style.left = `${surface.scrollLeft * scale}px`;
  view.style.top = `${surface.scrollTop * scale}px`;
  view.style.width = `${surface.clientWidth * scale}px`;
  view.style.height = `${surface.clientHeight * scale}px`;
}

/* ------------------------------------------------------------ publishing */

function publishTarget() {
  return $("#publish-target")?.value || "local";
}

/* Requesting a deployment needs a design that compiles, the right to
   publish, and — first — a person who has reviewed what it grants
   (ADR-0108). */
function syncPublishRequest() {
  const reviewed = $("#publish-reviewed")?.checked;
  const button = $("#btn-publish-request");
  button.disabled = !(canvas.publishVerdictOk && reviewed);
  button.title = !canvas.publishVerdictOk
    ? "the design does not compile, or you may not publish it"
    : reviewed ? "" : "review what this design grants first (step 1)";
}

async function openPublish() {
  const reviewed = $("#publish-reviewed");
  if (reviewed) reviewed.checked = false;       // a new review for each publish
  canvas.publishVerdictOk = false;
  const bar = $("#publish-bar");
  if (!bar) return;
  if (!canvas.systemId) {
    return ui.alertDialog("Open an organisation first.", { title: "Nothing to publish" });
  }
  bar.hidden = false;
  bar.removeAttribute("data-ok");
  $("#publish-verdict").textContent = "checking";
  $("#publish-summary").textContent = "Validating and compiling…";
  $("#publish-findings").replaceChildren();
  $("#btn-publish-request").disabled = true;
  try {
    const verdict = await dapi(`/systems/${canvas.systemId}/preflight`, {
      method: "POST", body: JSON.stringify({ target: publishTarget() }),
    });
    renderPublishVerdict(verdict);
  } catch (err) {
    $("#publish-verdict").textContent = "error";
    $("#publish-summary").textContent = err.message;
  }
}

function renderPublishVerdict(verdict) {
  const bar = $("#publish-bar");
  bar.dataset.ok = String(!!verdict.ok);
  $("#publish-verdict").textContent = verdict.ok ? "would compile" : "refused";
  /* A refusal names the stage, because "it does not compile" and "the gate
     refuses it" are different problems for the person reading this. */
  $("#publish-summary").textContent = verdict.ok
    ? `${verdict.files.length} file(s) would be generated for `
      + `${verdict.target}${verdict.platform_policy
          ? ` under platform policy ${verdict.platform_policy}` : ""}.`
      + ` ${verdict.warnings.length} warning(s) were not blocking.`
    : `Refused at the ${verdict.stage} stage. Nothing was requested.`;
  const rows = [...verdict.refusals, ...verdict.warnings].map((f) =>
    el("div", { class: "publish-row", "data-severity": f.severity },
      el("code", {}, f.where || f.code),
      el("span", {}, f.message)));
  $("#publish-findings").replaceChildren(...rows);
  /* Publishing needs the permission as well as a clean verdict: an editor may
     change a design all day and may not switch it on. */
  canvas.publishVerdictOk = !!verdict.ok
    && canvas.permissions.includes("system.publish");
  syncPublishRequest();
}

async function requestDeployment() {
  const tenant = $("#publish-tenant").value.trim();
  if (!tenant) {
    /* Said at the field, which is where it is fixed. */
    const field = $("#publish-tenant");
    field.setAttribute("aria-invalid", "true");
    field.classList.add("invalid");
    field.addEventListener("input", () => {
      field.removeAttribute("aria-invalid");
      field.classList.remove("invalid");
    }, { once: true });
    field.focus();
    setStatus("Name the tenant. It is assigned by the fabric, so the "
      + "designer cannot choose one for you.");
    return;
  }
  try {
    const result = await dapi(`/systems/${canvas.systemId}/publish`, {
      method: "POST",
      body: JSON.stringify({ tenant_id: tenant, target: publishTarget() }),
    });
    $("#publish-verdict").textContent = "requested";
    $("#publish-summary").textContent =
      `${result.deployment.id} is ${result.deployment.state} for `
      + `${result.deployment.tenant_id}, from revision `
      + `${result.deployment.revision}. ${result.note}`;
    $("#btn-publish-request").disabled = true;
    setStatus("deployment requested");
  } catch (err) {
    /* The refusal is the deliverable: show the gate's findings rather than a
       status code. */
    const detail = err.detail;
    if (detail?.verdict) return renderPublishVerdict(detail.verdict);
    $("#publish-verdict").textContent = "refused";
    $("#publish-summary").textContent =
      detail?.reason || (typeof detail === "string" ? detail : err.message);
  }
}

/* dragging an existing node */
function startDrag(event, node, box) {
  if (lockOn(node.id)) return;
  event.preventDefault();
  const startX = event.clientX, startY = event.clientY;
  const originX = node.x, originY = node.y;
  const pointer = event.pointerId;
  const snap = 10;
  let frame = 0;
  /* One box and the lines that touch it. This redrew every edge and region
     on every pointer move; the regions still follow, once a frame. */
  function move(e) {
    if (pointer !== undefined && e.pointerId !== undefined && e.pointerId !== pointer) return;
    node.x = Math.max(0, Math.round((originX + e.clientX - startX) / snap) * snap);
    node.y = Math.max(0, Math.round((originY + e.clientY - startY) / snap) * snap);
    box.style.left = `${node.x}px`;
    box.style.top = `${node.y}px`;
    updateEdgesFor(node.id);
    if (!frame) frame = requestAnimationFrame(() => { frame = 0; renderRegions(); });
  }
  const before = containersHolding(node);
  function end() {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", end);
    window.removeEventListener("pointercancel", end);
    cancelAnimationFrame(frame);
    /* A press that did not move is a click, and a click is not an edit: this
       marked the design dirty and pushed an empty undo step on every one. */
    if (node.x === originX && node.y === originY) return;
    renderRegions();
    markDirty(`moved ${node.id}`);
    droppedAt(node, before);
  }
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", end);
  window.addEventListener("pointercancel", end);
}

/* ---------------------------------------------- containers and dropping
   Where a box is let go is a gesture (ADR-0103), and the model says what it
   means: onto a whole it is part of → a move; into an environment's box → a
   deployment; out of one it was in → an undeployment. Anywhere else it is
   only a new position. */
const CONTAINER = { width: 360, height: 220 };

function isContainer(node) {
  return node && node.kind === "environment" && diagram()?.kind !== "deployment";
}

function centre(node) {
  return { x: node.x + (node.width || 200) / 2,
           y: node.y + (node.height || 80) / 2 };
}

function inside(point, box) {
  return point.x >= box.x && point.x <= box.x + box.width
    && point.y >= box.y && point.y <= box.y + (box.height || CONTAINER.height);
}

/* The environment boxes a node's centre is in. */
function containersHolding(node) {
  const c = centre(node);
  return Object.values(layoutNodes())
    .filter((n) => isContainer(n) && n.id !== node.id && inside(c, n))
    .map((n) => n.id);
}

/* The rule for a part let go onto a whole: a composition from that whole. */
function composeRule(hostKind, kind) {
  return (canvas.palette?.links || []).find(
    (r) => r.source === hostKind && r.target === kind && r.shape === "part");
}

function wholeOf(kind, id) {
  if (kind === "agent") return teamOf(id)?.id || null;
  if (kind === "team") {
    let parent = null;
    walkTeams(spec()?.organization, (t) => {
      if ((t.teams || []).some((c) => c.id === id)) parent = t.id;
    });
    return parent;
  }
  if (kind === "subagent") {
    return allAgents().find(({ agent }) =>
      (agent.subagents || []).some((x) => x.id === id))?.agent.id || null;
  }
  return null;
}

/* The workers a box stands for when it is let go in an environment: itself,
   when it can be deployed (an agent, a sub-agent), or — for a team — every
   agent in it and in its sub-teams. A team is not deployed; its agents are
   (ADR-0082), and dropping the team is how a person says "all of them". */
function deployables(kind, id) {
  if ((canvas.palette?.links || []).some(
      (r) => r.source === kind && r.target === "environment")) {
    return [{ kind, id }];
  }
  if (kind === "team") {
    const team = allTeams().find((t) => t.id === id)
      || (spec()?.organization?.id === id ? spec().organization : null);
    const out = [];
    walkTeams(team, (t) => (t.members || []).forEach(
      (m) => out.push({ kind: "agent", id: m.id })));
    return out;
  }
  return [];
}

function deploymentRequests(kind, id, before, after) {
  const requests = [];
  const workers = deployables(kind, id);
  for (const env of after.filter((e) => !before.includes(e))) {
    for (const w of workers) {
      requests.push([`${w.id} deployed in ${env}`, {
        op: "link", source: { kind: w.kind, id: w.id },
        target: { kind: "environment", id: env }, relationship: "environments",
      }]);
    }
  }
  for (const env of before.filter((e) => !after.includes(e))) {
    for (const w of workers) {
      requests.push([`${w.id} no longer deployed in ${env}`, {
        op: "unlink", source: { kind: w.kind, id: w.id },
        target: { kind: "environment", id: env }, relationship: "environments",
      }]);
    }
  }
  if (kind === "team" && !workers.length && after.length > before.length) {
    requests.push([`${id} has no agents yet, so nothing was deployed`, null]);
  }
  return requests;
}

async function droppedAt(node, before) {
  const requests = deploymentRequests(node.kind, node.id, before,
                                      containersHolding(node));
  const c = centre(node);
  const host = Object.values(layoutNodes())
    .filter((n) => n.id !== node.id && !isContainer(n) && inside(c, n))
    .sort((a, b) => a.width * (a.height || 80) - b.width * (b.height || 80))[0];
  const rule = host && composeRule(host.kind, node.kind);
  if (rule && wholeOf(node.kind, node.id) !== host.id) {
    requests.push([`${node.id} moved into ${host.id}`, {
      op: "link", source: { kind: host.kind, id: host.id },
      target: { kind: node.kind, id: node.id }, relationship: rule.field,
    }]);
  }
  const said = [];
  for (const [text, request] of requests) {
    if (!request) { said.push(text); continue; }
    try {
      const answer = await modelOperation(request);
      said.push(text + effectsLine(answer));
    } catch (err) {
      said.push(`refused: ${err.message}`);
    }
  }
  if (said.length) {
    setStatus(said.join(" · "));
    markDirty(said[0]);
    renderCanvas();
    renderInspector();
  }
}

/* An environment box is resized by its corner; the size is the layout's and
   is kept like a position. */
function startResize(event, node, box) {
  event.preventDefault();
  event.stopPropagation();
  const startX = event.clientX, startY = event.clientY;
  const w0 = node.width || CONTAINER.width;
  const h0 = node.height || CONTAINER.height;
  function move(e) {
    node.width = Math.max(200, Math.round((w0 + e.clientX - startX) / 10) * 10);
    node.height = Math.max(120, Math.round((h0 + e.clientY - startY) / 10) * 10);
    box.style.width = `${node.width}px`;
    box.style.height = `${node.height}px`;
    box.style.minWidth = `${node.width}px`;
  }
  function end() {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", end);
    window.removeEventListener("pointercancel", end);
    markDirty(`resized ${node.id}`);
    renderCanvas();
  }
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", end);
  window.addEventListener("pointercancel", end);
}

/* dropping a new component from the palette */
function wireDropTarget() {
  const surface = $("#canvas");
  surface.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    surface.classList.add("drag-over");
  });
  surface.addEventListener("dragleave", () => surface.classList.remove("drag-over"));
  surface.addEventListener("drop", (e) => {
    e.preventDefault();
    surface.classList.remove("drag-over");
    const kind = e.dataTransfer.getData("text/kind");
    const step = e.dataTransfer.getData("text/step");
    if (!kind && !step) return;
    if (!canvas.record) { setStatus("Open or create a system first, then drop components onto the canvas."); return; }
    const rect = surface.getBoundingClientRect();
    const x = Math.round((e.clientX - rect.left + surface.scrollLeft) / 10) * 10;
    const y = Math.round((e.clientY - rect.top + surface.scrollTop) / 10) * 10;
    if (step) {
      if (diagram()?.kind !== "process") return setStatus("a step belongs on a process canvas");
      return placeStep(step, x, y);
    }
    if (diagram()?.kind === "process") {
      return setStatus("a process canvas holds steps; open an organisation diagram to add components");
    }
    if (diagram()?.kind === "deployment") {
      return setStatus("the Deployment diagram draws the binding and does not edit it");
    }
    /* A canvas places the stereotypes of the profiles its aspect draws
       (ADR-0112 M7); the palette offers nothing else, and a drag from
       elsewhere is refused the same way. */
    const profile = canvas.palette?.profiles?.[kind] || "Core";
    const aspect = ASPECTS[aspectOf()];
    if (aspectOf() === "data" && profile !== "Core" && !aspect.profiles.includes(profile)) {
      return setStatus(`the Data diagram draws the Data profile; ${an(kindSpec(kind).label.toLowerCase())} `
        + `is in the ${profile} profile`);
    }
    placeComponent(kind, x, y);
  });
}

/* --------------------------------------------------------- delete / rename */
/* Deleting is the model's: the element, its parts and every link to it go,
   and the status line lists each link destroyed — or nothing goes, and it
   names what still requires the element. A note is the canvas's own. */
/* No "are you sure?" first: the removal is one step of the undo history, and
   the toast afterwards says what went and offers it back (ADR-0117). A
   question asked before every delete is answered without being read; an undo
   is there when the mistake is noticed. */
async function deleteNode(kind, id) {
  if (!canvas.record) return;
  /* Where the keyboard goes next: the nearest box, so a run of deletes does
     not drop focus on <body> after the first. */
  const hadFocus = document.activeElement?.closest?.(".node")?.dataset.id === id;
  const next = hadFocus ? (nearestBox(id, 1, 0) || nearestBox(id, -1, 0)
    || nearestBox(id, 0, 1) || nearestBox(id, 0, -1))?.dataset.id : null;
  try {
    if (kind === "note" || !findComponent(kind, id)) {
      removeComponent(kind, id);
    } else {
      const answer = await modelOperation({ op: "delete", kind, id });
      delete layoutNodes()[id];
      setStatus(`removed ${id}${effectsLine(answer)}`);
    }
  } catch (err) {
    await ui.alertDialog(err.message, { title: `${id} was not removed` });
    return;
  }
  if (canvas.selected?.id === id) canvas.selected = null;
  markDirty(`removed ${id}`);
  renderCanvas();
  renderInspector();
  // By id: the neighbour's element may have been redrawn by the delete.
  if (next && nodeEls.get(next)) focusNode(nodeEls.get(next).el);
  offerUndo(`Removed ${id}.`);
}

/* A toast for a change that is easy to miss, with Undo. The button undoes
   this change only: if something else has been done since, the history's top
   is no longer it, and it says so rather than undoing the wrong thing. */
function offerUndo(message) {
  const entry = canvas.history?.past[canvas.history.past.length - 1];
  ui.announce(`${message} Undo with Control+Z.`);
  ui.toast(message, {
    action: "Undo",
    onAction: () => {
      const top = canvas.history?.past[canvas.history.past.length - 1];
      if (!entry || top !== entry) {
        setStatus("something else changed since; use ↶ to step back through it");
        return;
      }
      undo();
    },
  });
}

function duplicateNode(node) {
  const src = findComponent(node.kind, node.id);
  const id = nextId(node.kind);
  try {
    addComponent(node.kind, id, { x: node.x + 30, y: node.y + 30 });
  } catch (err) {
    setStatus(err.message);
    return;
  }
  /* copy simple string/number fields from the source component */
  const dest = findComponent(node.kind, id);
  if (src && dest) {
    for (const [k, v] of Object.entries(src)) {
      if (k === "id") continue;
      if (typeof v === "string" || typeof v === "number" || typeof v === "boolean")
        dest[k] = v;
    }
  }
  layoutNodes()[id] = {
    id, kind: node.kind,
    x: node.x + 30, y: node.y + 30,
    width: node.width, height: node.height,
    collapsed: false, note: node.note || "",
  };
  markDirty();
  renderCanvas();
  selectNode(layoutNodes()[id]);
}

function startInlineRename(node, component, titleEl) {
  const current = component.name || component.id || node.id;
  const input = document.createElement("input");
  input.className = "node-rename-input";
  input.value = current;
  input.style.width = `${Math.max(90, titleEl.offsetWidth)}px`;
  titleEl.replaceChildren(input);
  input.focus();
  input.select();
  function commit() {
    const val = input.value.trim() || current;
    if (component && "name" in component) component.name = val;
    else if (node.kind === "note") node.note = val;
    markDirty();
    renderCanvas();
  }
  input.addEventListener("blur", commit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); input.blur(); }
    if (e.key === "Escape") { input.value = current; input.blur(); }
    e.stopPropagation();   // don't let Escape/Delete bubble to the canvas handler
  });
}

/* ------------------------------------------------------ context menu */
let _ctxCleanup = null;
function hideContextMenu() {
  const m = document.getElementById("ctx-menu");
  if (m) m.hidden = true;
  if (_ctxCleanup) { document.removeEventListener("click", _ctxCleanup); _ctxCleanup = null; }
}

function showContextMenu(clientX, clientY, node, readOnly) {
  const menu = document.getElementById("ctx-menu");
  if (!menu) return;
  menu.replaceChildren(
    ctxItem("✏️ Rename", () => {
      /* find the rendered title el and trigger inline rename */
      const box = document.querySelector(`#canvas-nodes [data-id="${node.id}"] .n-title`);
      const component = findComponent(node.kind, node.id) || {};
      if (box) startInlineRename(node, component, box);
    }, readOnly),
    ctxItem("⧉ Duplicate", () => duplicateNode(node), readOnly),
    ctxItem("⤷ Open a diagram of this team",
            () => addDiagram(node.id),
            readOnly || node.kind !== "team"),
    el("hr", {}),
    ctxItem(
      legalTargetsFrom(node.kind).length
        ? `🔗 Link from here → ${legalTargetsFrom(node.kind).join(" / ")}`
        : "🔗 Nothing links from this",
      () => beginLink(node),
      readOnly || !legalTargetsFrom(node.kind).length),
    ctxItem("⛓ Unlink — move to the top", () => unlink(node),
            readOnly || node.kind === "subagent"),
    el("hr", {}),
    ctxItem("🗑 Delete", () => deleteNode(node.kind, node.id), readOnly),
  );
  /* position relative to viewport */
  menu.style.left = `${clientX}px`;
  menu.style.top  = `${clientY}px`;
  menu.hidden = false;
  /* Kept inside the window: opened near an edge, a menu whose items fall
     below the fold cannot be used at all. */
  const box = menu.getBoundingClientRect();
  const margin = 8;
  menu.style.left = `${Math.max(margin,
    Math.min(clientX, window.innerWidth - box.width - margin))}px`;
  menu.style.top = `${Math.max(margin,
    Math.min(clientY, window.innerHeight - box.height - margin))}px`;
  /* auto-close on next click anywhere */
  setTimeout(() => {
    _ctxCleanup = () => hideContextMenu();
    document.addEventListener("click", _ctxCleanup, { once: true });
  }, 0);
}

function ctxItem(label, fn, disabled = false) {
  const btn = el("button", { class: "ctx-item", disabled: disabled ? "" : null }, label);
  btn.addEventListener("click", (e) => { e.stopPropagation(); hideContextMenu(); fn(); });
  return btn;
}

/* ---------------------------------------------- the keyboard on a node
   The canvas was mouse-only (WCAG 2.1.1). Each node is focusable (a roving
   tabindex: one in the Tab order), and the keys below are what a mouse does:

     arrows          move focus to the nearest box that way
     Enter / Space   select it and show Properties (or finish a link)
     Delete          remove it — undoable, and the toast says so
     L               start a link; arrows and Enter pick the target, Esc stops
     Shift+arrows    move the box 10px
     F2              rename in place
     Shift+F10       its context menu

   Handled on the node, not the document, so a key pressed in a form field or
   a dialog never reaches it. */
function nearestBox(fromId, dx, dy) {
  const layout = diagram();
  const a = layout?.nodes[fromId];
  if (!a) return null;
  const from = centre(a);
  let best = null, score = Infinity;
  for (const { el: box } of nodeEls.values()) {
    const id = box.dataset.id;
    const b = layout.nodes[id];
    if (id === fromId || !b || !box.isConnected) continue;
    const to = centre(b);
    const vx = to.x - from.x, vy = to.y - from.y;
    const along = vx * dx + vy * dy;
    if (along <= 0) continue;                 // not in that direction
    const across = Math.abs(vx * dy - vy * dx);
    /* Straight ahead beats diagonal: sideways distance counts double, so a
       box directly below wins over a nearer one off to the side. */
    const s = along + 2 * across;
    if (s < score) { score = s; best = box; }
  }
  return best;
}

const ARROWS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };

function handleNodeKey(e, node, box, readOnly) {
  if (e.target !== box) return;               // the rename box, a chip, a link
  if (e.ctrlKey || e.metaKey || e.altKey) return;   // undo/redo go to the page
  const key = e.key;
  const arrow = ARROWS[key];
  const component = findComponent(node.kind, node.id) || {};
  if (arrow && e.shiftKey && !canvas.linking) {
    if (readOnly || lockOn(node.id)) return;
    node.x = Math.max(0, node.x + arrow[0] * 10);
    node.y = Math.max(0, node.y + arrow[1] * 10);
    box.style.left = `${node.x}px`;
    box.style.top = `${node.y}px`;
    updateEdgesFor(node.id);
    renderRegions();
    // One undo step for a run of nudges, as for a run of keystrokes.
    markDirty(`moved ${node.id}`, true);
    ui.announce(`${component.name || node.id} at ${node.x}, ${node.y}.`);
  } else if (arrow) {
    const next = nearestBox(node.id, arrow[0], arrow[1]);
    if (!next) { ui.announce("Nothing further that way."); }
    else {
      focusNode(next);
      if (canvas.linking) {
        const target = layoutNodes()[next.dataset.id];
        ui.announce(linkRule(canvas.linking.kind, target.kind)
          ? "Enter links here." : `${an(canvas.linking.kind, true)} does not link to ${an(target.kind)}.`);
      }
    }
  } else if (key === "Enter" || key === " ") {
    if (canvas.linking) completeLink(node);
    else {
      selectNode(node);
      ui.announce(`${component.name || node.id} selected. Properties shown.`);
    }
  } else if ((key === "Delete" || key === "Backspace") && !canvas.linking) {
    if (readOnly) return ui.announce("This design is read-only for you.");
    deleteNode(node.kind, node.id);
  } else if (key.toLowerCase() === "l" && !e.shiftKey) {
    if (readOnly) return ui.announce("This design is read-only for you.");
    if (canvas.linking) cancelLink(true);
    beginLink(node);
    nodeEls.get(node.id)?.el.focus({ preventScroll: true });
  } else if (key === "F2" && !readOnly) {
    const title = box.querySelector(".n-title");
    if (title) startInlineRename(node, component, title);
  } else if (key === "ContextMenu" || (key === "F10" && e.shiftKey)) {
    const r = box.getBoundingClientRect();
    selectNode(node);
    showContextMenu(r.left + 12, r.bottom - 4, node, readOnly);
    document.querySelector("#ctx-menu .ctx-item:not([disabled])")?.focus();
  } else if (key === "Escape") {
    if (canvas.linking) cancelLink();
    else if (canvas.selected) {
      canvas.selected = null;
      syncSelection();
      renderInspector();
      ui.announce("Selection cleared.");
    } else return;
  } else return;
  e.preventDefault();
  e.stopPropagation();
}

/* ---------------------------------------------- keyboard shortcuts */
/* Views whose edits are the open design's, so undo means the design's undo. */
const DESIGN_VIEWS = new Set(["canvas", "org", "designer", "components"]);

function handleCanvasKey(e) {
  /* A modal owns the keyboard; so does any view that is not the canvas. This
     listener is on the document, and Delete pressed in the catalog's search
     removed the selected canvas node from a view nobody could see. */
  if (window.ui?.modalOpen()) return;
  const view = document.querySelector(".view.active")?.id.replace("view-", "");
  const tag = document.activeElement?.tagName;
  /* Undo is the exception to "ignore keys while typing": the browser's own
     undo inside a text box is what you want there, and ours everywhere else.
     So it is checked before the guard and skipped inside a field. */
  if (!DESIGN_VIEWS.has(view)) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z"
      && tag !== "INPUT" && tag !== "TEXTAREA") {
    e.preventDefault();
    return e.shiftKey ? redo() : undo();
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y"
      && tag !== "INPUT" && tag !== "TEXTAREA") {
    e.preventDefault();
    return redo();
  }
  /* ignore when typing in an input/textarea/select */
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (view !== "canvas") return;
  if (e.key === "Escape" && canvas.linking) {
    e.preventDefault();
    return cancelLink();
  }

  if (e.key === "Escape") {
    hideContextMenu();
    if (!canvas.selected) return;
    canvas.selected = null;
    syncSelection();
    renderInspector();
    return;
  }

  /* Delete and the arrows belong to the focused node (handleNodeKey). Here
     they only cover a selection made from the Explorer or an issue, where
     focus is not on a box. */
  if (!canvas.selected || !canvas.record) return;
  if (document.activeElement && document.activeElement !== document.body) return;
  const { kind, id } = canvas.selected;
  if (!layoutNodes()[id]) return;
  if (e.key === "Delete") {
    e.preventDefault();
    deleteNode(kind, id);
  }
}

/* Every id declared anywhere in the open spec, plus every layout node.
   An id is the spec's only handle on a component — references are ids, and
   `findComponent` returns the first match — so two components sharing one
   means the second is invisible and un-editable. */
function declaredIds() {
  const found = new Set(Object.keys(layoutNodes()));
  const walk = (node) => {
    if (Array.isArray(node)) return node.forEach(walk);
    if (!node || typeof node !== "object") return;
    if (typeof node.id === "string") found.add(node.id);
    Object.values(node).forEach(walk);
  };
  walk(spec());
  return found;
}

/* Every name in use, by kind: two teams called "Finance" is a legible design
   mistake rather than a broken one, but it is still one — a reader cannot
   tell the boxes apart, and neither can a reviewer reading a diff. */
function namesInUse(kind, exceptId = null) {
  const out = new Set();
  const add = (c) => {
    if (c && c.id !== exceptId && typeof c.name === "string" && c.name) {
      out.add(c.name);
    }
  };
  if (kind === "team") allTeams().forEach(add);
  else if (kind === "agent") allAgents().forEach(({ agent }) => add(agent));
  else if (kind === "subagent") {
    for (const { agent } of allAgents()) (agent.subagents || []).forEach(add);
  } else if (NESTED[kind]) nestedList(spec(), kind).forEach(add);
  else if (COLLECTIONS[kind]) (spec()[COLLECTIONS[kind]] || []).forEach(add);
  return out;
}

/* A fresh id, and a fresh name to go with it. The id counter used to consult
   only the layout, so a component declared in the spec with no node — which
   is every component of an imported spec until it is laid out — could have
   its id handed to a second one. */
function nextId(kind) {
  const base = kind === "memory_namespace" ? "namespace" : kind;
  const taken = declaredIds();
  let n = 1;
  while (taken.has(`${base}_${n}`)) n += 1;
  return `${base}_${n}`;
}

function nextName(kind, id) {
  const taken = namesInUse(kind);
  if (!taken.has(id)) return id;
  let n = 2;
  while (taken.has(`${id}_${n}`)) n += 1;
  return `${id}_${n}`;
}

/* ------------------------------------------------------------- renaming */

/* Renaming an id is not editing a field. The layout is keyed by id and every
   reference in the spec is an id, so writing a new one into the form left the
   component with no node, no references and no way back — the canvas showed
   the old name on a box that pointed at nothing.

   Refuses a duplicate, because two components sharing an id is not something
   to repair afterwards. */
function renameComponent(kind, oldId, newId) {
  const id = String(newId || "").trim();
  if (!id) throw new Error("an id cannot be empty: it is how everything else refers to this");
  if (id === oldId) return;
  if (!/^[A-Za-z_][A-Za-z0-9_.-]*$/.test(id)) {
    throw new Error(`'${id}' is not a usable id: start with a letter, then `
      + "letters, digits, underscore, dot or hyphen");
  }
  if (declaredIds().has(id)) {
    throw new Error(`'${id}' is already taken by another component. An id is `
      + "how the spec refers to a component, so it has to be unique");
  }

  /* Ids are unique across the document, so a string equal to the old one is a
     reference to it wherever it appears. Rewriting them all is what keeps a
     rename from silently detaching a team from its leader. */
  const rewrite = (node) => {
    if (Array.isArray(node)) {
      node.forEach((value, i) => {
        if (value === oldId) node[i] = id; else rewrite(value);
      });
      return;
    }
    if (!node || typeof node !== "object") return;
    for (const [key, value] of Object.entries(node)) {
      if (value === oldId) node[key] = id; else rewrite(value);
    }
  };
  rewrite(spec());

  const nodes = layoutNodes();
  if (nodes[oldId]) {
    nodes[id] = { ...nodes[oldId], id };
    delete nodes[oldId];
  }
  if (canvas.selected?.id === oldId) canvas.selected = { kind, id };
  markDirty(`renamed ${oldId} to ${id}`);
}

/* The innermost node whose box contains a point, or null.

   Innermost, because a team's box encloses its agents' boxes and dropping on
   an agent means the agent. This is not the proximity guess that used to
   nest whatever you dropped *near* whatever was nearest — "inside a box" is
   a gesture somebody made on purpose. */
function nodeAt(x, y) {
  const nodes = Object.values(layoutNodes());
  const hits = nodes.filter((n) =>
    x >= n.x && x <= n.x + n.width
    && y >= n.y && y <= n.y + (n.height || 80));
  if (!hits.length) return null;
  return hits.reduce((best, n) =>
    (n.width * (n.height || 80)) < (best.width * (best.height || 80)) ? n : best);
}

/* Which rule a *drop* means, when several could apply.

   Dropping inside a box says containment and nothing else: it is a statement
   about where the thing sits, not about how two units relate. So an
   association — which needs a kind and a reason — is never what a drop meant,
   and neither is a flow. */
const DROP_RELATIONSHIPS = ["contains", "member", "holds", "uses"];

function dropRule(hostKind, kind) {
  const rules = linkRules(hostKind, kind);
  for (const relationship of DROP_RELATIONSHIPS) {
    const rule = rules.find((r) => r.relationship === relationship);
    if (rule) return rule;
  }
  return null;
}

/* What the model is told a new component starts as. Only what the palette
   would otherwise leave the author to type before anything works: a name,
   and the few seeds a kind needs to mean anything. */
function seedFor(kind, id) {
  const seed = { name: nextName(kind, id) };
  if (kind === "subagent") Object.assign(seed, { kind: "research" });
  if (kind === "memory_namespace") Object.assign(seed, { scope: "private" });
  if (kind === "policy") {
    /* `effect` is required, so a rule dropped without one was refused by the
       model and the palette's Policy rule could not be placed at all. A new
       rule matches everything until it is narrowed, so it starts as a deny:
       failing closed, and saying so, beats a fresh allow-all grant. */
    Object.assign(seed, { effect: "deny",
      description: "New rule: denies everything until its actions, resources "
        + "and subjects are narrowed." });
  }
  if (kind === "evaluation") {
    // No `applies_to` would apply it to every agent — a wider claim than a
    // drop on a canvas means (ADR-0102).
    Object.assign(seed, { given: "", expect: "", applies_to: [] });
  }
  if (!(kindSpec(kind).fields || []).some((f) => f.name === "name")) {
    delete seed.name;
  }
  return seed;
}

/* Dropping from the palette is `create` (ADR-0103): inside the whole it was
   dropped on when it is a part of one, and in the organisation otherwise.
   Only the canvas's own things — a note, and the first team becoming the
   organisation's root — are placed without asking the model. */
async function placeComponent(kind, x, y) {
  const id = nextId(kind);
  const host = nodeAt(x, y);
  const size = kind === "environment" ? CONTAINER : { width: 200, height: 80 };
  const s = spec();
  const firstTeam = kind === "team" && (!s.organization?.id
    || (!(s.organization.members || []).length
        && !(s.organization.teams || []).length
        && !layoutNodes()[s.organization.id]));
  const place = () => {
    layoutNodes()[id] = {
      id, kind, x, y, width: size.width, height: size.height,
      collapsed: false, note: "",
    };
  };

  if (kind === "note" || firstTeam) {
    try {
      const made = addComponent(kind, id, { x, y });
      if (made && typeof made.name === "string") made.name = nextName(kind, id);
    } catch (err) {
      setStatus(err.message);
      return;
    }
    place();
    markDirty();
    renderCanvas();
    selectNode(layoutNodes()[id]);
    return;
  }

  const whole = host && host.id !== id && composeRule(host.kind, kind)
    ? host : null;
  let owner = whole?.id || "";
  if (!owner && kind === "subagent") {
    // A sub-agent is always some agent's part; dropped on open ground it
    // goes to the first agent, and the status line says which.
    owner = allAgents()[0]?.agent.id || "";
    if (!owner) return setStatus("add an Agent before adding a sub-agent");
  }
  let said = "";
  try {
    const answer = await modelOperation(
      { op: "create", kind, id, ...(owner ? { owner } : {}),
        attrs: seedFor(kind, id) });
    said = `${id} created${owner ? ` in ${owner}` : ""}${effectsLine(answer)}`;
  } catch (err) {
    setStatus(err.message);
    return;
  }
  place();

  /* Dropped inside an environment's box: deployed there, as if it had been
     dragged in (ADR-0105). */
  const envs = containersHolding(layoutNodes()[id]).filter((e) => e !== id);
  for (const [text, request] of deploymentRequests(kind, id, [], envs)) {
    if (!request) { said += ` — ${text}`; continue; }
    try {
      const answer = await modelOperation(request);
      said += ` — ${text}${effectsLine(answer)}`;
    } catch (err) {
      said += ` — refused: ${err.message}`;
    }
  }

  /* Dropped onto something that references rather than owns it — a tool
     onto an agent — the drop also draws that link. Onto anything else it is
     placed on its own, and told why. */
  if (host && host.id !== id && !whole && !isContainer(host)) {
    const rule = dropRule(host.kind, kind);
    if (rule) {
      try {
        const answer = await applyLink(rule, { kind: host.kind, id: host.id },
                                       { kind, id });
        said = `${host.id} ${rule.label} ${id}${effectsLine(answer)}`;
      } catch (err) {
        said = err.message;
      }
    } else {
      said = `${an(host.kind, true)} does not hold ${an(kind)}, so `
        + `${id} was placed on its own`;
    }
  }
  setStatus(said);
  markDirty();
  renderCanvas();
  selectNode(layoutNodes()[id]);
}

/* ------------------------------------------------ fields that are places

   Some things the inspector shows are not values on the component at all:
   they are *where the component sits*. A sub-agent's parent is the agent
   whose `subagents` list holds it; whether an agent leads its team is the
   team's `leader`. Neither is a key on the object.

   Both used to be read and written as if they were. The palette gave a
   sub-agent a required `parent` field, so linking one to an agent on the
   canvas moved it into that agent's list — and the inspector, reading a
   `parent` key nothing had ever set, showed it blank. Choosing a parent there
   wrote a stray key that moved nothing, and the spec model drops unknown keys,
   so it vanished on the next load. Leadership had no control on an agent at
   all.

   So these read from the structure and write to it: changing the parent moves
   the sub-agent, and ticking "leads its team" sets the team's leader. The
   canvas and the inspector are then two views of one fact. */
function teamOf(agentId) {
  return allTeams().find((t) => (t.members || []).some((m) => m.id === agentId))
    || null;
}

const DERIVED_FIELDS = {
  "subagent.parent": {
    get: (component, id) =>
      allAgents().find(({ agent }) =>
        (agent.subagents || []).some((x) => x.id === id))?.agent.id || "",
    /* A move: the sub-agent becomes a part of the chosen agent (ADR-0103). */
    set: async (component, id, value) => {
      if (!value) throw new Error("a sub-agent belongs to an agent; pick one");
      const answer = await modelOperation({
        op: "link", source: { kind: "agent", id: value },
        target: { kind: "subagent", id }, relationship: "subagents",
      });
      return `${id} now belongs to ${value}${effectsLine(answer)}`;
    },
  },
  "agent.leads_team": {
    get: (component, id) => teamOf(id)?.leader === id,
    /* The team's `leader` {subsets members}: set by the model, and unset
       leaves the team without one — the gate reports it; the canvas does
       not pick a successor on the author's behalf. */
    set: async (component, id, value) => {
      const team = teamOf(id);
      if (!team) throw new Error(`${id} is not a member of any team`);
      const answer = value
        ? await modelOperation({ op: "set_leader", team: team.id, agent: id })
        : await modelOperation({ op: "update", kind: "team", id: team.id,
                                 attrs: { leader: "" } });
      return (value ? `${id} leads ${team.id}` : `${team.id} has no leader`)
        + effectsLine(answer);
    },
  },
};

/* A Properties edit is the model's `update` (ADR-0103). The box writes at
   once, so typing stays typing; when it pauses, the value is sent against
   the draft as it was before the edit began, so a value the model refuses
   is caught and put back — with the reason — rather than kept. */
const pendingEdits = {};

function confirmEdit(kind, id, field, base) {
  const key = `${kind}:${id}:${field}`;
  const entry = pendingEdits[key] || (pendingEdits[key] = { base });
  clearTimeout(entry.timer);
  entry.timer = setTimeout(async () => {
    delete pendingEdits[key];
    const component = findComponent(kind, id);
    if (!component) return;
    const request = { op: "update", kind, id,
                      attrs: { [field]: component[field] } };
    let refusal = null;
    try {
      const answer = await dapi("/operations", {
        method: "POST", body: JSON.stringify({ spec: entry.base, request }),
      });
      if (!answer.accepted) refusal = new ModelRefusal(answer).message;
    } catch (err) {
      refusal = err.message;
    }
    if (!refusal) return;
    const was = findIn(entry.base, kind, id);
    component[field] = was ? was[field] : undefined;
    setStatus(`${id}.${field} put back — ${refusal}`);
    renderCanvas();
    if (canvas.selected?.id === id) {
      renderInspector();
      inspectorError(field, `Put back: ${refusal}`);
    }
  }, 700);
}

/* A message under one field of Properties (ADR-0106). */
function inspectorError(fieldName, message) {
  const holder = formHost().querySelector(
    `[data-field-name="${CSS.escape(fieldName)}"]`);
  if (!holder) return;
  holder.querySelector(":scope > .field-error")?.remove();
  holder.querySelector("input, select, textarea")?.classList.add("invalid");
  holder.appendChild(el("small", { class: "field-error", role: "alert" },
    message));
}

/* The same component in another copy of the draft. */
function findIn(draft, kind, id) {
  const saved = canvas.record.spec;
  canvas.record.spec = draft;
  try {
    return findComponent(kind, id);
  } finally {
    canvas.record.spec = saved;
  }
}

/* ------------------------------------------------------------- inspector */
function selectNode(node) {
  canvas.selected = { kind: node.kind, id: node.id };
  showSide("details");
  syncSelection();
  renderInspector();
}

/* The same form serves Properties on the canvas and the Components editor
   (ADR-0107): `canvas.formHost` says where it is drawn. */
function formHost() {
  return canvas.formHost?.host || $("#inspector");
}

function renderInspector() {
  const host = formHost();
  const titleEl = canvas.formHost?.title || $("#inspector-title");
  if (!canvas.selected || !canvas.record) {
    titleEl.textContent = "Properties";
    host.className = "empty";
    host.replaceChildren(
      "Select a component on the canvas or in the explorer.");
    return;
  }
  const { kind, id } = canvas.selected;
  const component = findComponent(kind, id);
  const node = layoutNodes()[id];
  const definition = kindSpec(kind);
  titleEl.textContent = `${definition.label} · ${id}`;
  host.className = "";

  /* A box on the Deployment diagram is a binding record, which the designer
     shows and does not edit (ADR-0112 M7). A bound spec component keeps its
     own form above the record it is bound by. */
  const bound = aspectOf() === "deployment" ? deploymentModel().nodes.get(id) : null;
  if (BINDING_KINDS[kind] || bound) {
    const record = el("div", { class: "binding-record" },
      el("h3", {}, `${BINDING_KINDS[kind]?.stereo || "«deploy»"} binding · target ${bound?.target || "—"}`),
      el("p", { class: "hint" }, "From the binding the design was loaded with. "
        + "Read-only here: the binding is a document of its own (ADR-0110)."),
      el("pre", { class: "code" }, JSON.stringify(bound?.record ?? component, null, 2)));
    if (BINDING_KINDS[kind]) return host.replaceChildren(record);
    canvas.bindingRecord = record;
  } else {
    canvas.bindingRecord = null;
  }

  const readOnly = !canvas.permissions.includes("system.edit") || !!lockOn(id)
    || (!!ASPECTS[aspectOf()].readOnly && !!layoutNodes()[id]);
  const form = el("form", { class: "form", onsubmit: (e) => e.preventDefault() });
  for (const field of definition.fields) {
    const derivedField = DERIVED_FIELDS[`${kind}.${field.name}`];
    if (derivedField) {
      const value = derivedField.get(component, id);
      const derivedControl = fieldControl(field, value, readOnly, async (v) => {
        try {
          setStatus(await derivedField.set(component, id, v));
        } catch (err) {
          setStatus(err.message);
          renderInspector();          // put the real value back in the box
          inspectorError(field.name, err.message);
          return;
        }
        markDirty(`${id}.${field.name}`);
        renderCanvas();
        renderInspector();
      }, kind, component);
      derivedControl.setAttribute?.("data-field-name", field.name);
      form.appendChild(derivedControl);
      continue;
    }
    const value = kind === "note" ? node.note : (component || {})[field.name];
    // One field that throws used to take the whole inspector with it: the
    // form was replaced at the end, so a mid-loop failure left the *previous*
    // component's form on screen under this component's title. Showing the
    // wrong data under a correct heading is worse than showing a gap, so a
    // field that cannot render says so and the rest of the form still draws.
    let control;
    try {
      control = fieldControl(field, value, readOnly, (v) => {
      if (kind === "note") {
        node.note = v;
      } else if (field.name === "id" && kind !== "note") {
        /* An id is the spec's handle on this component, not a label: the
           layout is keyed by it and every reference is one. Renaming goes
           through the rename, which moves both and refuses a duplicate. */
        try {
          renameComponent(kind, id, v);
        } catch (err) {
          setStatus(err.message);
          renderInspector();          // put the old id back in the box
          return;
        }
        renderCanvas();
        renderInspector();
        return;
      } else if (component) {
        if (field.name === "name" && namesInUse(kind, id).has(v)) {
          /* Not fatal the way a duplicate id is — the design still compiles —
             but two boxes with one name is a picture nobody can read. */
          setStatus(`another ${definition.label.toLowerCase()} is already `
            + `called '${v}'`);
        }
        const base = pendingEdits[`${kind}:${id}:${field.name}`]?.base
          || structuredClone(spec());
        component[field.name] = v;
        confirmEdit(kind, id, field.name, base);
      }
      /* Named per field and per component: a run of typing in one box is one
         step, and moving to the next box starts another. */
      markDirty(`edited ${id}.${field.name}`, true);
      renderCanvas();
    }, kind, component);
    } catch (err) {
      console.error(`inspector: ${kind}.${field.name} did not render`, err);
      control = el("label", { class: "stacked" }, fieldTitle(field),
        el("small", { class: "hint warn" },
           `this control could not be drawn (${err.message}). The value is `
           + "unchanged; edit it in the spec until this is fixed."));
    }
    control.setAttribute?.("data-field-name", field.name);
    form.appendChild(control);
    /* A required field left empty says so under itself, the way every form
       does (ADR-0106), rather than only in the Issues tab. */
    const empty = value === undefined || value === null || value === ""
      || (Array.isArray(value) && !value.length);
    if (field.required && empty && kind !== "note" && !readOnly) {
      control.appendChild(el("small", { class: "field-error", role: "alert" },
        "Required."));
      control.querySelector("input, select, textarea")?.classList.add("invalid");
    }
  }
  const actions = el("div", { class: "actions" },
    el("button", {
      type: "button", disabled: readOnly ? "" : null,
      onclick: () => deleteNode(kind, id),
    }, "Remove"));
  form.appendChild(actions);
  host.replaceChildren(form,
    ...(canvas.bindingRecord ? [canvas.bindingRecord] : []),
    ...(kind === "agent" ? [resolvesTo(component), effectiveAuthority(id)] : []),
    el("h3", {}, "Raw"),
    el("pre", { class: "code" }, JSON.stringify(component ?? node, null, 2)));
}

/* "Resolves to" — the question no other view answers: given these bindings,
   what may this agent actually reach? It is resolved here from the open spec,
   because it is a design fact; nothing runtime is consulted. */
function effectivePermissions(agent) {
  const s = spec();
  if (!s || !agent) return [];
  const capabilities = Object.fromEntries((org(s).capabilities || []).map((c) => [c.id, c]));
  const out = [];
  const seen = new Set();
  const add = (capabilityId, via) => {
    const capability = capabilities[capabilityId];
    if (!capability) return;
    const key = `${capability.action}:${capability.resource_class}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ action: capability.action || "invoke",
      resource: capability.resource_class || capability.id, via });
  };
  (agent.capabilities || []).forEach((c) => add(c, "bound directly"));
  for (const assignment of agent.roles || []) {
    const roleId = typeof assignment === "string" ? assignment : assignment.role;
    const role = (org(s).role_definitions || []).find((r) => r.id === roleId);
    (role?.capabilities || []).forEach((c) => add(c, roleId));
  }
  return out;
}

/* What an agent may decide, resolved (ADR-0108): its own mandate merged with
   its team's and its line's, each decision marked as declared here or
   inherited. The same resolution the Authority review and the phase gate
   use, as of the last save. */
const authorityCache = {};

function effectiveAuthority(agentId) {
  const box = el("div", { class: "resolves effective-authority" },
    el("header", {}, el("span", { class: "eyebrow" }, "Effective authority")),
    el("p", { class: "hint" }, "resolving…"));
  const systemId = canvas.systemId;
  if (!systemId) return box;
  const key = `${systemId}@${canvas.record?.version}`;
  const load = authorityCache[key] ||= dapi(`/systems/${systemId}/authority`)
    .catch(() => null);
  load.then((data) => {
    const a = data?.agents?.[agentId];
    const body = [];
    if (!data) {
      body.push(el("p", { class: "hint" },
        "The design does not resolve yet; this appears once it compiles."));
    } else if (!a) {
      body.push(el("p", { class: "hint" }, "Save to see this agent resolved."));
    } else {
      const declared = new Set(a.declared || []);
      body.push(a.decisions.length
        ? el("ul", { class: "decisions" }, ...a.decisions.map((d) =>
            el("li", { class: declared.has(d) ? "own" : "inherited" },
              el("code", {}, d), " ",
              el("span", { class: "origin" },
                declared.has(d) ? "declared here" : "inherited"))))
        : el("p", { class: "hint" }, "Decides nothing."));
      body.push(el("p", { class: "from" }, `through ${a.line.join(" › ")}`));
    }
    body.push(el("button", { type: "button", class: "link-btn",
      onclick: () => window.showView?.("authority") },
      "Review the whole design's authority…"));
    box.replaceChildren(box.firstChild, ...body);
  });
  return box;
}

function resolvesTo(agent) {
  const permissions = effectivePermissions(agent);
  const shown = permissions.slice(0, 6);
  const rest = permissions.length - shown.length;
  return el("div", { class: "resolves" },
    el("header", {},
      el("span", { class: "eyebrow" }, "Resolves to"),
      el("span", { class: "badge" },
        `${permissions.length} permission${permissions.length === 1 ? "" : "s"}`)),
    ...shown.map((p) => el("div", { class: "perm" },
      el("span", { class: "act" }, p.action), el("span", {}, p.resource))),
    ...(rest > 0
      ? [el("div", { class: "perm" }, el("span", { class: "act" }, "…"),
          el("span", {}, `${rest} more`))]
      : []),
    ...(permissions.length ? [] : [el("div", { class: "from" },
      "Nothing yet: this agent binds no capability, directly or through a role.")]),
    el("div", { class: "from" },
      shown.length
        ? `via ${[...new Set(shown.map((p) => p.via))].join(", ")}`
        : ""));
}

/* -------------------------------------------------- reference field helpers
   fieldContext returns { mode:"ref"|"reflist", options:string[] } when a
   field on a given component kind should be rendered as a live-spec picker,
   or null to fall through to the generic field renderers.                  */
function fieldContext(componentKind, fieldName) {
  const s = spec();
  if (!s) return null;

  // The organisation owns its collections (ADR-0101); roles are its
  // `role_definitions`, a team's own `roles` being assignments.
  const ids = (col) => (org(s)[col === "roles" ? "role_definitions" : col] || [])
    .map((x) => x.id).filter(Boolean);
  const agentIds = () => allAgents().map((a) => a.agent.id);

  const REF_MAP = {
    agent: {
      roles:        { mode: "reflist", col: "roles" },
      capabilities: { mode: "reflist", col: "capabilities" },
      knowledge:    { mode: "reflist", col: "knowledge" },
      endpoints:    { mode: "reflist", col: "endpoints" },
      environments: { mode: "reflist", col: "environments" },
      workflows:    { mode: "reflist", col: "workflows" },
      /* A successor must be an agent that exists, so the control offers the
         ones that do rather than a free-text box that fails the gate later.
         Leaving it unset is the safe answer and the picker says so. */
      successor:    { mode: "ref",     fn: agentIds },
    },
    subagent: {
      capabilities: { mode: "reflist", col: "capabilities" },
      parent:       { mode: "ref",     fn: agentIds },
    },
    role: {
      capabilities: { mode: "reflist", col: "capabilities" },
    },
    evaluation: {
      applies_to: { mode: "reflist", fn: agentIds },
    },
    guardrail: {
      data_classes:     { mode: "reflist", col: "data_classes" },
      escalate_channel: { mode: "ref",     col: "channels" },
    },
    skill: {
      requires_capabilities: { mode: "reflist", col: "capabilities" },
    },
    plugin: {
      provides_skills:       { mode: "reflist", col: "skills" },
      provides_tools:        { mode: "reflist", col: "tools" },
      requires_capabilities: { mode: "reflist", col: "capabilities" },
    },
    tool: {
      // What a tool may wrap depends on what it says it wraps, so the picker
      // reads the node being edited rather than offering everything.
      wraps: {
        mode: "ref",
        fn: (nodeId) => {
          const tool = (org(s).tools || []).find((t) => t.id === nodeId);
          const kind = tool?.wraps_kind || "capability";
          if (kind === "subagent") {
            return allAgents().flatMap(({ agent }) =>
              (agent.subagents || []).map((x) => x.id));
          }
          return ids({ capability: "capabilities", workflow: "workflows",
                       endpoint: "endpoints" }[kind] || "capabilities");
        },
      },
    },
    team: {
      leader: {
        mode: "ref",
        fn: (nodeId) => {
          /* only agents that are already members of this team */
          const t = allTeams().find((t) => t.id === nodeId);
          return (t?.members || []).map((m) => m.id);
        },
      },
    },
    trigger: {
      agent: { mode: "ref", fn: agentIds },
    },
    capability: {
      data_classes: { mode: "reflist", col: "data_classes" },
    },
    knowledge: {
      data_classes: { mode: "reflist", col: "data_classes" },
    },
    memory_namespace: {
      data_classes: { mode: "reflist", col: "data_classes" },
    },
    endpoint: {
      send_data_classes: { mode: "reflist", col: "data_classes" },
    },
  };

  const entry = REF_MAP[componentKind]?.[fieldName];
  if (entry) {
    /* fn may accept the currently-selected node id for context (team.leader) */
    const options = entry.fn ? entry.fn(canvas.selected?.id) : ids(entry.col);
    const kindOfCol = Object.entries(COLLECTIONS)
      .find(([, col]) => col === entry.col)?.[0];
    return { mode: entry.mode, options,
             target: kindOfCol ? (kindSpec(kindOfCol)?.label || kindOfCol) : "" };
  }
  /* Everything else that is a reference comes from the model's link rules
     (ADR-0101): a field that holds a relationship gets a picker of what that
     relationship may point at, whatever the kind. A field that is not a
     relationship stays what it is. */
  const rules = (canvas.palette?.links || []).filter(
    (r) => r.source === componentKind && r.field === fieldName
      && ["ref", "refs", "ref_objects"].includes(r.shape));
  if (!rules.length) return null;
  const targets = [...new Set(rules.map((r) => r.target))];
  const options = [...new Set(targets.flatMap(
    (t) => componentsOf(t).map((x) => x.id).filter(Boolean)))];
  return { mode: rules[0].shape === "ref" ? "ref" : "reflist", options,
           target: targets.map((t) => kindSpec(t)?.label || t).join(" or ") };
}

/* Single-reference <select> — value is a string id */
function renderRef(field, value, readOnly, onChange, options) {
  const attrs = readOnly ? { disabled: "" } : {};
  // What "none" *means* differs per field, and for a successor it is the whole
  // point: unset is the safe answer, not a gap to be filled in.
  const blank = el("option", { value: "" }, field.blank || "— none —");
  const sel = el("select", attrs, blank,
    ...options.map((o) => el("option", { value: o }, o)));
  sel.value = value ?? "";
  // Named like every other control. The reference pickers return early, before
  // the shared naming at the end of `fieldControl`, so their controls were the
  // only ones a test or a screen reader could not address by field.
  sel.setAttribute("name", field.name);
  sel.addEventListener("change", () => onChange(sel.value || null));
  return sel;
}

/* Multi-reference pill picker.

   The value is usually string[] of ids, and sometimes is not: an agent's
   `environments` is a list of `{environment: id}` objects, because a sandbox
   reference can carry an override beside the id. This rendered the object
   itself as a pill's child, which is not a Node, so the control threw — and
   because the form was only swapped in at the end, the *whole* inspector kept
   the previously selected component's fields under this one's title.

   So: the shape is read and written back unchanged. An entry that carries more
   than an id keeps whatever else it carries, because dropping an override
   silently would be a worse bug than the one this fixes. */
const REF_KEYS = ["environment", "role", "data_class", "person", "agent",
                  "capability", "id"];

function refId(entry) {
  if (entry && typeof entry === "object") {
    const key = REF_KEYS.find((k) => typeof entry[k] === "string");
    return key ? entry[key] : "";
  }
  return entry ?? "";
}

/* A list of references, as chips plus a searchable dropdown (ADR-0106): type
   to filter what the design has of the right kind, pick one to add it, press
   × on a chip to remove it. Only what exists can be chosen — an id the model
   does not have would be refused anyway — and when nothing exists yet the
   control says what to add first rather than offering an empty box. */
function renderReflist(field, value, readOnly, onChange, options, targetLabel = "") {
  let selected = Array.isArray(value) ? [...value] : [];
  // How this field writes an entry back: as a bare id, or in the shape the
  // existing entries already use.
  const objectKey = (() => {
    for (const entry of selected) {
      if (entry && typeof entry === "object") {
        return REF_KEYS.find((k) => typeof entry[k] === "string") || null;
      }
    }
    return field.name === "environments" ? "environment" : null;
  })();
  const wrapId = (id) => (objectKey ? { [objectKey]: id } : id);
  const listId = `rl-${field.name}-${Math.random().toString(36).slice(2, 8)}`;

  const wrap = el("div", { class: "reflist-wrap", "data-field": field.name });

  function redraw() {
    const pills = selected.map((entry) => {
      const id = refId(entry);
      const pill = el("span", { class: "ref-pill", "data-id": id }, id);
      if (!readOnly) {
        const x = el("button", { type: "button", "aria-label": `remove ${id}` }, "×");
        x.addEventListener("click", () => {
          selected = selected.filter((v) => refId(v) !== id);
          onChange([...selected]);
          redraw();
        });
        pill.appendChild(x);
      }
      return pill;
    });

    const chosen = new Set(selected.map(refId));
    const remaining = options.filter((o) => !chosen.has(o));
    let picker = null;
    if (!readOnly && remaining.length) {
      const input = el("input", {
        class: "reflist-search", type: "search", list: listId,
        placeholder: `search to add${targetLabel ? " " + targetLabel : ""}…`,
        "aria-label": `add to ${field.name}`, autocomplete: "off",
      });
      const names = optionNames();
      const datalist = el("datalist", { id: listId },
        ...remaining.map((o) => el("option", { value: o },
          names[o] && names[o] !== o ? names[o] : "")));
      const add = () => {
        const v = input.value.trim();
        if (!v) return;
        if (!remaining.includes(v)) {
          input.setCustomValidity("not in the design");
          input.classList.add("invalid");
          input.title = `'${v}' is not in the design — choose one from the list`;
          return;
        }
        selected = [...selected, wrapId(v)];
        onChange([...selected]);
        redraw();
        wrap.querySelector(".reflist-search")?.focus();
      };
      input.addEventListener("input", () => {
        input.classList.remove("invalid");
        input.setCustomValidity("");
        // Picking from the list fires `input` with an exact match: add it.
        if (remaining.includes(input.value.trim())) add();
      });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); add(); }
      });
      picker = el("span", { class: "reflist-picker" }, input, datalist);
    } else if (!readOnly && !options.length) {
      picker = el("small", { class: "hint" },
        `nothing to choose yet${targetLabel ? ` — add ${targetLabel} to the design first` : ""}`);
    }
    wrap.replaceChildren(...pills, ...(picker ? [picker] : []));
  }
  redraw();
  return wrap;
}

/* Display names for ids, so a dropdown reads "cfo — Chief Financial
   Officer" rather than a bare id where a name exists. */
function optionNames() {
  const out = {};
  const s = spec();
  if (!s) return out;
  const add = (x) => { if (x?.id) out[x.id] = x.name || x.title || ""; };
  allTeams().forEach(add);
  allAgents().forEach(({ agent }) => {
    add(agent);
    (agent.subagents || []).forEach(add);
  });
  for (const value of Object.values(org(s))) {
    if (Array.isArray(value)) value.forEach((x) => typeof x === "object" && add(x));
  }
  return out;
}


/* ------------------------------------------------- process graphs (ADR-0096)

   A workflow is a process, and until now the designer could name one and not
   say what it does: the palette carried id, name and description, so the
   nodes and edges that *are* the workflow had to be written by hand in YAML.

   The editor below is three things stacked, and the order matters. A picture
   first, because the question a reader has about a process is its shape. Then
   the steps. Then the edges, which is where the mistakes live.

   It checks the same four rules the phase gate does, live, and says so in
   place: a step nothing reaches, an edge to a step that is not there, a
   non-branch step with two ways out, and an entry that is not a step. Learning
   a rule from the control while you draw is a different experience from
   learning it from a refusal afterwards, and these four are all rules people
   break by accident.

   A loop back to an earlier step is legal and drawn as one. A review loop that
   runs until it passes is a real process; what stops one that never converges
   is the step bound at run time, not a prohibition here. */

const NODE_KINDS = [
  ["tool", "tool", "calls a tool"],
  ["agent", "agent", "hands the step to an agent"],
  ["workflow", "workflow", "runs another workflow"],
  ["branch", "", "chooses what runs next"],
  ["transform", "expr", "reshapes the state"],
  ["human", "", "pauses for a person"],
  ["fork", "", "runs every way out at once"],
  ["join", "", "waits for every branch of a fork"],
];

/* ------------------------------------ two levels, one seam (ADR-0110)

   The process canvas draws the governed workflow: which steps happen, who
   owns each, where a person approves, what runs in parallel. What happens
   *inside* a step may belong to an engine; such a step calls a workflow
   whose body is external, and the canvas says which engine holds it and
   links out to it. It never draws the engine's own graph. */

const STEP_LABELS = {
  tool: "Tool step", agent: "Agent step", workflow: "Workflow step",
  branch: "Branch", transform: "Transform", human: "Human approval",
  fork: "Fork", join: "Join",
};

function stepWorkflow(step) {
  if (step?.kind !== "workflow" || !step.workflow) return null;
  return (org(spec())?.workflows || []).find((w) => w.id === step.workflow) || null;
}

function stepKindLabel(step) {
  const called = stepWorkflow(step);
  if (called?.body === "external") return "External step";
  return STEP_LABELS[step?.kind] || "Step";
}

/* Who does the step. Every step a person reads on the canvas answers that
   first, because it is what the step is governed by. */
function stepOwnerText(step) {
  const kind = step?.kind || "tool";
  if (kind === "fork") return "every way out runs";
  if (kind === "join") return "waits for every branch";
  if (kind === "branch") return `${(step.cases || []).length} case(s)`
    + (step.default ? ` · else ${step.default}` : "");
  if (kind === "transform") return "reshapes the state";
  if (kind === "human") {
    const who = step.person
      ? personLabel({ person: step.person })
      : step.role ? `role ${step.role}` : step.owner || "";
    return who ? `approver: ${who}` : "approver: — nobody named";
  }
  const owner = step.owner || (kind === "agent" ? step.agent : "");
  const what = kind === "tool" ? step.tool : kind === "workflow" ? step.workflow : "";
  return [owner ? `owner: ${owner}` : "owner: —", what ? `calls ${what}` : ""]
    .filter(Boolean).join(" · ");
}

/* Which engine runs each workflow comes from the binding the design was
   saved with. The spec never names an engine (ADR-0056), so without a saved
   binding the canvas can only say that the step is external. */
const engineCache = {};

function workflowEngines() {
  const systemId = canvas.systemId;
  if (!systemId) return Promise.resolve(null);
  const key = `${systemId}@${canvas.record?.version}`;
  return (engineCache[key] ||= dapi(`/systems/${systemId}/workflow-engines`)
    .catch(() => null));
}

function stepEngine(step) {
  const called = stepWorkflow(step);
  if (!called) return [];
  const external = called.body === "external";
  const box = el("div", { class: `n-engine${external ? " external" : ""}` },
    external ? "built in an engine" : "drawn here");
  workflowEngines().then((data) => {
    const bound = data?.workflows?.[called.id] || data?.workflows?.["*"];
    if (!bound || bound.engine === "native") {
      box.replaceChildren(external
        ? el("span", { class: "n-engine-missing" },
             data?.target ? `no engine bound for ${data.target}`
                          : "engine: save with a binding to see it")
        : "drawn here");
      return;
    }
    const parts = [el("span", {}, `engine: ${bound.engine}`)];
    if (bound.editor_url) {
      const link = el("a", { href: bound.editor_url, target: "_blank",
                             rel: "noopener", class: "n-open-in",
                             title: `the flow '${bound.flow || called.id}' in ${bound.engine}` },
                      `Open in ${bound.engine} ↗`);
      link.addEventListener("pointerdown", (e) => e.stopPropagation());
      link.addEventListener("click", (e) => e.stopPropagation());
      parts.push(link);
    }
    box.replaceChildren(...parts);
  });
  return [box];
}

/* A workflow's interface: the seam to an engine (ADR-0110). Six lists, each
   of which a step calling the workflow is checked against. */
const INTERFACE_FIELDS = [
  ["inputs", "inputs", "what the step is given"],
  ["outputs", "outputs", "what it hands back"],
  ["tools", "calls", "capabilities or tools the body calls; the step's owner must hold them"],
  ["endpoints", "endpoints", "external endpoints the body calls"],
  ["receives_data_classes", "receives", "data classes it is sent; the owner must hold them"],
  ["returns_data_classes", "returns", "data classes it hands back"],
];

function renderInterface(field, value, readOnly, onChange) {
  const current = value && typeof value === "object" ? { ...value } : {};
  const wrap = el("div", { class: "interface-wrap" });
  for (const [key, label, hint] of INTERFACE_FIELDS) {
    const input = el("input", { placeholder: "comma separated", title: hint,
                                ...(readOnly ? { disabled: "" } : {}) });
    input.value = (current[key] || []).join(", ");
    input.addEventListener("input", () => {
      const items = input.value.split(",").map((x) => x.trim()).filter(Boolean);
      if (items.length) current[key] = items; else delete current[key];
      onChange({ ...current });
    });
    wrap.appendChild(el("label", { class: "interface-row" },
      el("span", { class: "interface-key" }, label), input));
  }
  return wrap;
}

function graphIssues(graph) {
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  const ids = nodes.map((n) => n.id).filter(Boolean);
  const known = new Set(ids);
  const issues = [];
  const entry = graph.entry || ids[0];

  if (entry && !known.has(entry)) {
    issues.push({ id: entry, text: `starts at “${entry}”, which is not a step` });
  }
  const out = {};
  for (const e of edges) {
    if (e.to && e.to !== "END" && !known.has(e.to)) {
      issues.push({ id: e.from, text: `“${e.from}” leads to “${e.to}”, which is not a step` });
      continue;
    }
    (out[e.from] = out[e.from] || []).push(e.to);
  }
  // A branch carries its own ways out as cases, and they are ways out. The
  // gate counts them; this did not, so it drew reachable steps as unreachable
  // and taught the opposite of the rule it was there to teach.
  for (const n of nodes) {
    if (n.kind !== "branch") continue;
    const targets = [...(n.cases || []).map((c) => c.to),
                     ...(n.default ? [n.default] : [])];
    for (const t of targets) {
      if (t && t !== "END" && !known.has(t)) {
        issues.push({ id: n.id,
          text: `“${n.id}” branches to “${t}”, which is not a step` });
        continue;
      }
      (out[n.id] = out[n.id] || []).push(t);
    }
  }
  for (const n of nodes) {
    if (n.kind === "branch" || n.kind === "fork") continue;
    if ((out[n.id] || []).length > 1) {
      issues.push({ id: n.id,
        text: `“${n.id}” has ${out[n.id].length} ways out and is not a branch `
              + "or a fork, so nothing decides what they mean" });
    }
  }
  // A fork's branches meet at a join, and a join is where a fork's meet.
  const kindOf = Object.fromEntries(nodes.map((n) => [n.id, n.kind]));
  const joined = new Set();
  for (const n of nodes) {
    if (n.kind !== "fork") continue;
    const seen = new Set(), stack = [...(out[n.id] || [])];
    let found = false;
    while (stack.length) {
      const t = stack.pop();
      if (!t || t === "END" || seen.has(t)) continue;
      seen.add(t);
      if (kindOf[t] === "join") { found = true; joined.add(t); continue; }
      stack.push(...(out[t] || []));
    }
    if (!found) {
      issues.push({ id: n.id,
        text: `“${n.id}” forks and its branches never meet at a join` });
    }
  }
  for (const n of nodes) {
    if (n.kind === "join" && !joined.has(n.id)) {
      issues.push({ id: n.id, text: `no fork's branches lead to “${n.id}”` });
    }
    if (n.kind === "human" && !n.person && !n.role && !n.owner) {
      issues.push({ id: n.id,
        text: `“${n.id}” pauses for a person without naming who approves` });
    }
  }
  // Reachability, which is the one that finds a step that simply never runs.
  const seen = new Set(entry ? [entry] : []);
  const stack = entry ? [entry] : [];
  while (stack.length) {
    for (const t of out[stack.pop()] || []) {
      if (t && t !== "END" && !seen.has(t)) { seen.add(t); stack.push(t); }
    }
  }
  for (const id of ids) {
    if (!seen.has(id)) {
      issues.push({ id, text: `nothing reaches “${id}” — a step that cannot run is not a step` });
    }
  }
  return issues;
}

/* The picture. Steps in a column, edges as arrows beside them, and an edge
   that goes back up drawn as a loop and labelled one — because a cycle is the
   fact a reader most needs to see and the one a list of edges hides best. */
function graphPreview(graph) {
  const nodes = (graph.nodes || []).filter((n) => n.id);
  if (!nodes.length) {
    return el("p", { class: "muted small" },
              "No steps yet. Add one below and the shape appears here.");
  }
  const row = {}, H = 46, W = 300;
  nodes.forEach((n, i) => { row[n.id] = i; });
  const height = nodes.length * H + 18;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `-16 0 ${W + 16} ${height}`);
  svg.setAttribute("class", "graph-preview");
  const mk = (tag, attrs, text) => {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text !== undefined) n.textContent = text;
    svg.appendChild(n);
    return n;
  };
  const issueIds = new Set(graphIssues(graph).map((i) => i.id));
  const entry = graph.entry || nodes[0].id;

  const drawn = [...(graph.edges || [])];
  for (const n of nodes) {
    if (n.kind !== "branch") continue;
    for (const c of n.cases || []) drawn.push({ from: n.id, to: c.to });
    if (n.default) drawn.push({ from: n.id, to: n.default });
  }
  for (const e of drawn) {
    const a = row[e.from], b = e.to === "END" ? nodes.length : row[e.to];
    if (a === undefined || b === undefined) continue;
    const y1 = a * H + 30, y2 = b * H + 30;
    const back = b <= a;
    const x = back ? 28 : 168;
    mk("path", {
      d: back
        ? `M 30 ${y1} C ${x - 18} ${y1}, ${x - 18} ${y2}, 30 ${y2}`
        : `M 160 ${y1} L 160 ${y2}`,
      class: back ? "gp-edge gp-loop" : "gp-edge",
    });
    if (back) mk("text", { x: -14, y: (y1 + y2) / 2 + 3, class: "gp-loop-label" }, "↺ loop");
  }
  nodes.forEach((n, i) => {
    const y = i * H + 14;
    mk("rect", { x: 34, y, width: 124, height: 32, rx: 6,
                 class: `gp-node${issueIds.has(n.id) ? " gp-bad" : ""}`
                        + (n.id === entry ? " gp-entry" : "") });
    mk("text", { x: 44, y: y + 14, class: "gp-id" }, n.id);
    mk("text", { x: 44, y: y + 26, class: "gp-kind" },
       (n.kind || "—") + (n.person ? ` · ${n.person}` : "")
       + (n.owner ? ` · ${n.owner}` : ""));
  });
  return svg;
}

function renderGraph(field, value, readOnly, onChange) {
  let graph = value && typeof value === "object"
    ? JSON.parse(JSON.stringify(value)) : { entry: "", nodes: [], edges: [] };
  graph.nodes = graph.nodes || [];
  graph.edges = graph.edges || [];
  const wrap = el("div", { class: "graph-wrap" });

  const push = () => { onChange(graph); redraw(); };
  const ids = () => graph.nodes.map((n) => n.id).filter(Boolean);

  function stepRow(node, i) {
    const kind = el("select", readOnly ? { disabled: "" } : {},
      ...NODE_KINDS.map(([k, , hint]) => el("option", { value: k, title: hint }, k)));
    kind.value = node.kind || "tool";
    kind.addEventListener("change", () => { node.kind = kind.value; push(); });

    const id = el("input", { value: node.id || "", placeholder: "step id",
                             ...(readOnly ? { disabled: "" } : {}) });
    id.addEventListener("input", () => { node.id = id.value; onChange(graph); });

    const keyName = (NODE_KINDS.find(([k]) => k === (node.kind || "tool")) || [])[1];
    const target = keyName
      ? (() => {
          const t = el("input", { value: node[keyName] || "",
                                  placeholder: keyName,
                                  ...(readOnly ? { disabled: "" } : {}) });
          t.addEventListener("input", () => { node[keyName] = t.value; onChange(graph); });
          return t;
        })()
      : node.kind === "branch"
      ? el("span", { class: "muted small" },
           `${(node.cases || []).length} case(s)`
           + (node.default ? ` · else ${node.default}` : " · no default"))
      : node.kind === "human"
      ? (() => {
          const t = el("input", { value: node.person || node.role || "",
                                  placeholder: "approver (person id)",
                                  ...(readOnly ? { disabled: "" } : {}) });
          t.addEventListener("input", () => { node.person = t.value; onChange(graph); });
          return t;
        })()
      : el("span", { class: "muted small" },
           node.kind === "fork" ? "every way out runs"
           : node.kind === "join" ? "waits for every branch" : "");

    const del = el("button", { type: "button", class: "ghost",
                               ...(readOnly ? { disabled: "" } : {}) }, "×");
    del.addEventListener("click", () => {
      const gone = graph.nodes[i].id;
      graph.nodes.splice(i, 1);
      graph.edges = graph.edges.filter((e) => e.from !== gone && e.to !== gone);
      push();
    });
    return el("div", { class: "graph-row graph-step-row" }, id, kind, target, del);
  }

  function edgeRow(edge, i) {
    const pick = (key, extra) => {
      const sel = el("select", readOnly ? { disabled: "" } : {},
        el("option", { value: "" }, "—"),
        ...ids().map((v) => el("option", { value: v }, v)),
        ...(extra ? [el("option", { value: extra }, extra)] : []));
      sel.value = edge[key] || "";
      sel.addEventListener("change", () => { edge[key] = sel.value; push(); });
      return sel;
    };
    const del = el("button", { type: "button", class: "ghost",
                               ...(readOnly ? { disabled: "" } : {}) }, "×");
    del.addEventListener("click", () => { graph.edges.splice(i, 1); push(); });
    return el("div", { class: "graph-row graph-edge-row" },
      pick("from"), el("span", { class: "muted" }, "→"), pick("to", "END"), del);
  }

  function redraw() {
    const entry = el("select", readOnly ? { disabled: "" } : {},
      el("option", { value: "" }, "— first step —"),
      ...ids().map((v) => el("option", { value: v }, v)));
    entry.value = graph.entry || "";
    entry.addEventListener("change", () => { graph.entry = entry.value; push(); });

    const addStep = el("button", { type: "button", class: "ghost",
                                   ...(readOnly ? { disabled: "" } : {}) },
                       "+ step");
    addStep.addEventListener("click", () => {
      graph.nodes.push({ id: `step_${graph.nodes.length + 1}`, kind: "tool" });
      push();
    });
    const addEdge = el("button", { type: "button", class: "ghost",
                                   ...(readOnly ? { disabled: "" } : {}) },
                       "+ edge");
    addEdge.addEventListener("click", () => {
      graph.edges.push({ from: ids()[0] || "", to: "END" });
      push();
    });

    const issues = graphIssues(graph);
    const problems = issues.length
      ? el("ul", { class: "graph-issues" },
           ...issues.map((i) => el("li", {}, i.text)))
      : el("p", { class: "muted small" },
           graph.nodes.length ? "Every step runs, and every way out is decided."
                              : "");

    wrap.replaceChildren(
      graphPreview(graph),
      el("label", { class: "inline" }, "starts at", entry),
      el("div", { class: "graph-section" }, "Steps", addStep),
      ...graph.nodes.map(stepRow),
      el("div", { class: "graph-section" }, "What follows what", addEdge),
      ...graph.edges.map(edgeRow),
      problems,
    );
  }

  redraw();
  return wrap;
}

/* ------------------------------------------- authority pickers (ADR-0065/72)

   Two fields the org model gained that the inspector could not edit, and both
   carry a distinction a plain control would flatten.

   A mandate that is *absent* inherits its parent's; one that is present and
   empty decides nothing. Those are opposite meanings, and writing an empty
   list where the author meant "not set here" is how a unit written as advisory
   ends up holding every decision in the company — which is exactly what
   happened to Corporate Development in the worked finance example. So the
   control asks the question outright instead of inferring it from emptiness.

   An autonomy posture may only ever be tightened from what the capability
   declares, so the loosening options are not offered at all. A control that
   looks available and is then refused by the gate is the bug. */

const POSTURE_RANK = ["autonomous", "supervised", "human_decides", "advisory"];

function renderMandate(field, value, readOnly, onChange) {
  const options = (org(spec())?.decisions || []).map((d) => d.id).filter(Boolean);
  const wrap = el("div", { class: "mandate-wrap" });
  let current = value && typeof value === "object" ? { ...value } : null;

  function redraw() {
    const box = el("input", { type: "checkbox",
                              ...(readOnly ? { disabled: "" } : {}) });
    box.checked = current !== null;
    box.addEventListener("change", () => {
      current = box.checked ? { decisions: [] } : null;
      onChange(current);
      redraw();
    });
    const toggle = el("label", { class: "inline" }, box,
                      "declare a mandate here");

    const body = [];
    if (current) {
      body.push(renderReflist(
        { ...field, name: "decisions" }, current.decisions || [], readOnly,
        (v) => {
          current = { ...current, decisions: v };
          onChange(current);
          redraw();
        }, options));
      body.push(el("small", { class: "hint" },
        (current.decisions || []).length
          ? "Narrowed to these, and only where the line above already holds them."
          : "Declared and empty: this unit decides nothing."));
      if (!options.length) {
        body.push(el("small", { class: "hint" },
          "No decision classes declared yet — add them from the palette."));
      }
    } else {
      body.push(el("small", { class: "hint" },
        "Not declared: inherits its parent's mandate. Never everything."));
    }
    wrap.replaceChildren(toggle, ...body);
  }
  redraw();
  return wrap;
}

function renderAutonomy(field, value, readOnly, onChange, component) {
  const s = spec();
  const caps = new Map((org(s)?.capabilities || []).map((c) => [c.id, c]));
  const roles = new Map((org(s)?.role_definitions || []).map((r) => [r.id, r]));
  const held = new Set(component?.capabilities || []);
  for (const assignment of component?.roles || []) {
    const role = roles.get(
      typeof assignment === "string" ? assignment : assignment.role);
    (role?.capabilities || []).forEach((c) => held.add(c));
  }
  const rows = [...held].filter((c) => caps.has(c)).sort();
  if (!rows.length) {
    return el("p", { class: "hint" },
      "No capabilities yet. A posture is per activity, so there is nothing " +
      "to tighten until this agent holds one.");
  }
  const map = { ...(value || {}) };
  return el("div", { class: "autonomy-wrap" }, ...rows.map((id) => {
    const declared = caps.get(id).autonomy || "advisory";
    const floor = POSTURE_RANK.indexOf(declared);
    const tighter = POSTURE_RANK.slice(floor < 0 ? 0 : floor + 1);
    const select = el("select", readOnly ? { disabled: "" } : {},
      el("option", { value: "" }, `as declared — ${declared}`),
      ...tighter.map((p) => el("option", { value: p }, p)));
    select.value = map[id] || "";
    select.addEventListener("change", () => {
      if (select.value) map[id] = select.value;
      else delete map[id];
      onChange({ ...map });
    });
    return el("div", { class: "autonomy-row" },
      el("code", {}, id),
      select,
      tighter.length ? null
        : el("small", { class: "hint" }, "already the tightest"));
  }));
}

/* A field's name as a person reads it, and its required mark as every form
   shows it (ADR-0106): "model_policy" is "Model policy", and a required
   field carries the same red * the other forms do. */
function humanise(name) {
  const text = String(name || "").replace(/_/g, " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function fieldTitle(field) {
  const title = field.label || humanise(field.name);
  return field.required
    ? [title, el("span", { class: "req", title: "Required",
                           "aria-hidden": "true" }, "*")]
    : [title];
}

function fieldControl(field, value, readOnly, onChange, componentKind = null,
                      component = null) {
  const attrs = readOnly ? { disabled: "" } : {};
  let input;

  /* ---- smart reference pickers (live-spec aware) ---- */
  if (componentKind) {
    const ctx = fieldContext(componentKind, field.name);
    if (ctx) {
      input = ctx.mode === "ref"
        ? renderRef(field, value, readOnly, onChange, ctx.options)
        : renderReflist(field, value, readOnly, onChange, ctx.options,
                        ctx.target ? an(ctx.target.toLowerCase()) : "");
      const label = el("label", {}, fieldTitle(field), input);
      if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
      return label;
    }
  }

  /* ---- a process graph (ADR-0096) ---- */
  if (field.type === "graph") {
    const input = renderGraph(field, value, readOnly, onChange);
    const label = el("label", { class: "stacked" }, fieldTitle(field), input);
    if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
    return label;
  }

  /* ---- a workflow's interface (ADR-0110) ---- */
  if (field.type === "interface") {
    const input = renderInterface(field, value, readOnly, onChange);
    const label = el("label", { class: "stacked" }, fieldTitle(field), input);
    if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
    return label;
  }

  /* ---- policy conditions (ADR-0008) ---- */
  if (field.type === "conditions") {
    const input = renderConditions(field, value, readOnly, onChange);
    const label = el("label", { class: "stacked" }, fieldTitle(field), input);
    if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
    return label;
  }

  /* ---- authority (ADR-0065, ADR-0072) ---- */
  if (field.type === "decisions" || field.type === "decision_refs"
      || field.type === "autonomy") {
    const decisionIds = () =>
      (org(spec())?.decisions || []).map((d) => d.id).filter(Boolean);
    input = field.type === "decisions"
      // A mandate: absent inherits, present-and-empty decides nothing.
      ? renderMandate(field, value, readOnly, onChange)
      : field.type === "decision_refs"
      // A plain reference list, with no inherit-or-empty question to ask.
      ? renderReflist(field, value, readOnly, onChange, decisionIds())
      : renderAutonomy(field, value, readOnly, onChange, component);
    const label = el("label", { class: "stacked" },
      fieldTitle(field), input);
    if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
    return label;
  }

  /* ---- a nested object with its own fields ---- */
  if (field.type === "object") {
    // Absent and empty differ here the way they do for a mandate: an agent
    // with no model policy inherits the system's, and one with an empty
    // policy permits nothing at all.
    let current = value && typeof value === "object" ? { ...value } : null;
    const wrap = el("div", { class: "object-wrap" });

    function redraw() {
      const box = el("input", { type: "checkbox", ...attrs });
      box.checked = current !== null;
      box.addEventListener("change", () => {
        current = box.checked ? {} : null;
        onChange(current);
        redraw();
      });
      const toggle = el("label", { class: "inline" }, box,
                        `set ${humanise(field.name).toLowerCase()} on this agent`);
      const body = current
        ? (field.fields || []).map((sub) =>
            fieldControl(sub, current[sub.name], readOnly, (v) => {
              current = { ...current, [sub.name]: v };
              onChange(current);
            }))
        : [el("small", { class: "hint" },
              "Not set: inherits the system's.")];
      wrap.replaceChildren(toggle, ...body);
    }
    redraw();
    const label = el("label", { class: "stacked" },
      fieldTitle(field), wrap);
    if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
    return label;
  }

  /* ---- closed vocabularies and structured values ---- */
  if (field.type === "multi") {
    // A set from a fixed list. A text box over a closed vocabulary invites a
    // typo the validator then reports as an unknown value, which is a worse
    // way to learn the four boundaries are named.
    const chosen = new Set(Array.isArray(value) ? value : []);
    input = el("div", { class: "multi-wrap" },
      ...(field.options || []).map((option) => {
        const box = el("input", { type: "checkbox", ...attrs });
        box.checked = chosen.has(option);
        box.addEventListener("change", () => {
          if (box.checked) chosen.add(option); else chosen.delete(option);
          onChange((field.options || []).filter((o) => chosen.has(o)));
        });
        return el("label", { class: "inline" }, box, option);
      }));
    const label = el("label", { class: "stacked" },
      fieldTitle(field), input);
    if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
    return label;
  }

  if (field.type === "map") {
    // Key/value pairs, one per line. A `=` splits on the first occurrence
    // only, because a plugin hook's handler and a skill resource's contents
    // both legitimately contain one.
    input = el("textarea", { rows: "4", placeholder: "one per line: key = value",
                             ...attrs });
    input.value = Object.entries(value || {})
      .map(([k, v]) => `${k} = ${v}`).join("\n");
    input.addEventListener("input", () => {
      const out = {};
      for (const line of input.value.split("\n")) {
        const at = line.indexOf("=");
        if (at < 1) continue;
        const key = line.slice(0, at).trim();
        if (key) out[key] = line.slice(at + 1).trim();
      }
      onChange(out);
    });
    const label = el("label", { class: "stacked" },
      fieldTitle(field), input);
    if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
    return label;
  }

  if (field.type === "json") {
    // Invalid JSON keeps the text and does not reach the spec: discarding
    // what somebody typed mid-keystroke is how a schema gets silently
    // emptied. The field says it is not valid yet instead.
    input = el("textarea", { rows: "6", class: "json", ...attrs });
    input.value = value == null ? "" : JSON.stringify(value, null, 2);
    const note = el("small", { class: "hint" }, "");
    input.addEventListener("input", () => {
      const raw = input.value.trim();
      if (!raw) { note.textContent = ""; input.classList.remove("invalid");
                  return onChange(null); }
      try {
        const parsed = JSON.parse(raw);
        input.classList.remove("invalid");
        note.textContent = "";
        onChange(parsed);
      } catch (e) {
        input.classList.add("invalid");
        note.textContent = `not valid JSON yet — ${e.message}`;
      }
    });
    const label = el("label", { class: "stacked" },
      fieldTitle(field), input, note);
    if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
    return label;
  }

  /* ---- generic field renderers (unchanged) ---- */
  if (field.type === "text") {
    input = el("textarea", { rows: "3", ...attrs });
    input.value = value ?? "";
    input.addEventListener("input", () => onChange(input.value));
  } else if (field.type === "bool") {
    input = el("input", { type: "checkbox", ...attrs });
    input.checked = !!value;
    input.addEventListener("change", () => onChange(input.checked));
  } else if (field.type === "enum") {
    input = el("select", attrs,
      ...(field.options || []).map((o) => el("option", { value: o }, o)));
    input.value = value ?? (field.options || [])[0] ?? "";
    input.addEventListener("change", () => onChange(input.value));
  } else if (field.type === "list") {
    input = el("input", { placeholder: "comma separated", ...attrs });
    input.value = Array.isArray(value) ? value.join(", ") : (value ?? "");
    input.addEventListener("input", () =>
      onChange(input.value.split(",").map((x) => x.trim()).filter(Boolean)));
  } else if (field.type === "humans") {
    input = el("textarea", { rows: "3", placeholder:
      "name | contact | roles  (one per line)", ...attrs });
    input.value = (value || []).map((h) =>
      `${h.name} | ${h.contact} | ${(h.roles || []).join(",")}`).join("\n");
    input.addEventListener("input", () => onChange(
      input.value.split("\n").filter(Boolean).map((line) => {
        const [name, contact, roles] = line.split("|").map((x) => (x || "").trim());
        return { name, contact, roles: (roles || "owner").split(",").map((r) => r.trim()) };
      })));
  } else if (field.type === "number") {
    input = el("input", { type: "number", ...attrs });
    input.value = value ?? "";
    input.addEventListener("input", () =>
      onChange(input.value === "" ? null : Number(input.value)));
  } else {
    input = el("input", attrs);
    input.value = value ?? "";
    /* An id is renamed, not edited: the rename moves the layout and every
       reference, then redraws the form. Doing that per keystroke replaced the
       box under the cursor after each character, so an id is committed when
       the box is left or Enter is pressed. */
    input.addEventListener(field.name === "id" ? "change" : "input",
                           () => onChange(input.value));
  }
  if (input && input.tagName !== "DIV") input.setAttribute("name", field.name);
  // A yes/no reads as one line — the box, then what ticking it means.
  const label = field.type === "bool"
    ? el("label", { class: "check-row" }, input, fieldTitle(field))
    : el("label", {}, fieldTitle(field), input);
  if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
  return label;
}



/* ------------------------------------------------------- load and persist */
async function loadWorkspaces() {
  canvas.workspaces = await dapi("/workspaces");
  if (!canvas.workspaces.length) {
    canvas.workspaces = [await dapi("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name: "My workspace" }),
    })];
  }
  canvas.workspaceId = canvas.workspaceId || canvas.workspaces[0].id;
  fillSelect($("#ws-select"), canvas.workspaces.map((w) => [w.id, w.name]));
  $("#ws-select").value = canvas.workspaceId;
  await loadSystems();
}

async function loadSystems() {
  canvas.systems = await dapi(`/systems?workspace_id=${canvas.workspaceId}`);
  if (canvas.systems.length) {
    canvas.systemId = canvas.systems.some((s) => s.id === canvas.systemId)
      ? canvas.systemId : canvas.systems[0].id;
    renderOrgSelectors();
    await openSystem(canvas.systemId);
  } else {
    canvas.systemId = null;
    canvas.record = null;
    renderOrgSelectors();
    renderCanvas();
    renderInspector();
    announce("opened");
  }
}

/* Every organisation selector in the page — the context bar's and the org
   chart tab's — is filled from this one list and this one open id, so the two
   cannot drift apart: they are two views of `canvas.systemId`. */
function renderOrgSelectors() {
  const options = canvas.systems.length
    ? canvas.systems.map((s) => [s.id, `${s.name} (v${s.version})`])
    : [["", "— no organisations —"]];
  document.querySelectorAll("[data-org-select]").forEach((select) => {
    fillSelect(select, options);
    select.value = canvas.systemId || "";
  });
}

async function openSystem(systemId) {
  const payload = await dapi(`/systems/${systemId}`);
  canvas.systemId = systemId;
  canvas.record = payload.record;
  canvas.role = payload.role;
  canvas.permissions = payload.permissions || [];
  canvas.locks = payload.locks || [];
  canvas.dirty = false;
  canvas.selected = null;
  /* A fresh read of the design: the history of the last one describes a
     document this is not. */
  resetHistory();
  renderOrgSelectors();
  updateBadges();
  canvas.validation = payload.validation;
  renderCanvas();
  renderInspector();
  renderValidation(payload.validation);
  announce("opened");
}

function updateBadges() {
  $("#version-badge").textContent = canvas.record ? `v${canvas.record.version}` : "v—";
  const mine = canvas.locks.find((l) => l.holder === canvas.user);
  const theirs = canvas.locks.find((l) => l.holder !== canvas.user);
  const badge = $("#lock-badge");
  badge.textContent = mine ? "you hold the lock"
    : theirs ? `locked by ${theirs.holder_name || theirs.holder}` : "unlocked";
  badge.className = `badge ${mine ? "ok" : theirs ? "warn" : ""}`;
  $("#btn-lock").textContent = mine ? "Unlock" : "Lock";
  $("#btn-save").textContent = canvas.dirty ? "Save •" : "Save";
}

/* ---------------------------------------------------------------- undo */
/*
   Undo is deliberately *local*, bounded, and thrown away the moment somebody
   else's change is merged in. The workstream named the reason before this was
   built: locks mean one editor at a time on a node, not on a design, so an
   undo stack that crosses a merge would restore a state that was never true
   for anybody. Better to have no undo past that point and say so.

   Snapshots rather than inverse operations: the model is one JSON document
   and the edits are arbitrary, so a diff of the whole thing is both the
   simplest correct answer and the one that cannot go out of step with the
   operations it is meant to invert.
*/
const UNDO_LIMIT = 60;
const UNDO_COALESCE_MS = 700;

function undoState() {
  return JSON.stringify({ spec: canvas.record.spec,
                          layout: canvas.record.layout });
}

function resetHistory() {
  canvas.history = { past: [], future: [], last: null, at: 0, reason: "",
                     coalescing: false };
  canvas.rev = (canvas.rev || 0) + 1;
  if (canvas.record) canvas.history.last = undoState();
  updateUndoButtons();
}

/* Called *after* a mutation, with the state from before it — which is what
   `history.last` is holding at that moment.

   Coalescing is opt-in, and that is the whole design. Typing in a form marks
   the design dirty on every keystroke and one undo per character is not an
   undo, so a run of keystrokes in one field collapses. Everything else is its
   own step. The first version of this coalesced on time and a shared default
   reason instead, and a drop followed within 700ms by a rename became one
   step: undoing the rename also removed the component. A rule that merges two
   unrelated actions because they were close together is worse than no
   coalescing at all. */
function pushHistory(reason, coalesce = false) {
  const history = canvas.history;
  if (!history || history.last === null) return;
  const now = Date.now();
  const continuing = coalesce
    && history.coalescing
    && history.reason === reason
    && now - history.at < UNDO_COALESCE_MS
    && history.past.length;
  if (!continuing) {
    history.past.push({ state: history.last, reason });
    if (history.past.length > UNDO_LIMIT) history.past.shift();
  }
  history.future = [];
  history.last = undoState();
  history.at = now;
  history.reason = reason;
  history.coalescing = coalesce;
  updateUndoButtons();
}

function applyHistory(state) {
  const parsed = JSON.parse(state);
  canvas.record.spec = parsed.spec;
  canvas.record.layout = parsed.layout;
  canvas.selected = null;
  canvas.linking = null;
  canvas.dirty = true;
  canvas.history.last = state;
  canvas.rev = (canvas.rev || 0) + 1;
  updateBadges();
  renderCanvas();
  renderInspector();
  updateUndoButtons();
}

function undo() {
  const history = canvas.history;
  if (!history?.past.length) return setStatus("nothing to undo");
  const entry = history.past.pop();
  history.future.push({ state: history.last, reason: entry.reason });
  history.reason = "";                 // never coalesce across an undo
  history.coalescing = false;
  applyHistory(entry.state);
  setStatus(`undid: ${entry.reason}`);
}

function redo() {
  const history = canvas.history;
  if (!history?.future.length) return setStatus("nothing to redo");
  const entry = history.future.pop();
  history.past.push({ state: history.last, reason: entry.reason });
  history.reason = "";
  history.coalescing = false;
  applyHistory(entry.state);
  setStatus(`redid: ${entry.reason}`);
}

function updateUndoButtons() {
  const history = canvas.history || { past: [], future: [] };
  const undoBtn = $("#btn-undo");
  const redoBtn = $("#btn-redo");
  if (undoBtn) {
    undoBtn.disabled = !history.past.length;
    undoBtn.title = history.past.length
      ? `undo: ${history.past[history.past.length - 1].reason}`
      : "nothing to undo";
  }
  if (redoBtn) {
    redoBtn.disabled = !history.future.length;
    redoBtn.title = history.future.length
      ? `redo: ${history.future[history.future.length - 1].reason}`
      : "nothing to redo";
  }
}

function markDirty(reason = "edited", coalesce = false) {
  canvas.dirty = true;
  canvas.record.layout.updated_at = new Date().toISOString();
  const open = diagram();
  if (open) open.updated_at = canvas.record.layout.updated_at;
  pushHistory(reason, coalesce);
  canvas.rev = (canvas.rev || 0) + 1;
  schedulePanels();
  updateBadges();
  announce(reason);
}

/* The strip above the surface: counts, and the rules that actually fired.
   A validation message names its rule in the backend, so the strip repeats
   those names rather than inventing a summary. */
function renderValidationStrip(validation) {
  const strip = $("#validation-strip");
  if (!strip) return;
  if (!validation) return strip.replaceChildren();
  const errors = validation.errors || [];
  const warnings = validation.warnings || [];
  const rules = [...errors, ...warnings]
    .map((m) => String(m).match(/^([a-z0-9_]+)\b/)?.[1])
    .filter(Boolean);
  strip.replaceChildren(
    el("span", { class: `count ${errors.length ? "err" : "ok"}` },
      el("span", { class: `dot ${errors.length ? "err" : "ok"}` }),
      `${errors.length} error${errors.length === 1 ? "" : "s"}`),
    el("span", { class: `count ${warnings.length ? "warn" : "ok"}` },
      el("span", { class: `dot ${warnings.length ? "warn" : "ok"}` }),
      `${warnings.length} warning${warnings.length === 1 ? "" : "s"}`),
    el("span", { class: "rules" },
      [...new Set(rules)].slice(0, 4).join(" · ")
        || (validation.ok ? "nothing to answer" : "")));
}

/* Which of the left column's two tabs is showing: what the model contains,
   or what may be added to it. */
function showLeft(which) {
  canvas.left = which;
  for (const button of document.querySelectorAll("#left-tabs button")) {
    const on = button.dataset.left === which;
    button.classList.toggle("active", on);
    button.setAttribute("aria-selected", String(on));
  }
  $("#left-explorer").hidden = which !== "explorer";
  $("#left-palette").hidden = which !== "palette";
}

/* Which of the right panel's two tabs is showing. */
function showSide(which) {
  canvas.side = which;
  for (const button of document.querySelectorAll("#side-tabs button")) {
    const on = button.dataset.side === which;
    button.classList.toggle("active", on);
    button.setAttribute("aria-selected", String(on));
  }
  $("#side-details").hidden = which !== "details";
  $("#side-issues").hidden = which !== "issues";
}

/* A finding the backend could not attribute to a component still has to say
   where it is, so the raw path is the fallback — never nothing. */
function findingWhere(finding) {
  return finding.component || finding.where || "";
}

function renderValidation(validation) {
  const host = $("#validation");
  renderValidationStrip(validation);
  const badge = $("#issues-badge");
  if (!validation) {
    if (badge) badge.textContent = "";
    return host.replaceChildren();
  }
  const errors = validation.errors || [];
  const warnings = validation.warnings || [];
  if (badge) {
    badge.textContent = errors.length ? String(errors.length)
      : warnings.length ? String(warnings.length) : "";
    badge.className = `badge ${errors.length ? "err" : warnings.length ? "warn" : ""}`;
  }

  /* Structured findings if the backend sent them; the old strings otherwise,
     so an older response still renders rather than showing an empty panel. */
  const findings = validation.findings || [
    ...errors.map((m) => ({ severity: "error", message: String(m) })),
    ...warnings.map((m) => ({ severity: "warning", message: String(m) })),
  ];

  const row = (finding) => {
    const where = findingWhere(finding);
    const known = finding.component
      && layoutNodes()[finding.component];
    return el("li", { class: finding.severity === "error" ? "v-err" : "v-warn" },
      el("div", { class: "v-head" },
        where
          ? el(known ? "button" : "span", {
              class: known ? "v-where link" : "v-where",
              title: known ? "show this component on the canvas"
                : (finding.where || ""),
              ...(known ? { onclick: () => selectAndReveal(finding.component) } : {}),
            }, where)
          : null,
        finding.issue_id
          ? el("span", { class: "v-num" }, finding.issue_id) : null,
        finding.code
          ? el("button", {
              class: "v-code link",
              title: finding.title
                ? `${finding.title} — what this means and how to fix it`
                : "what this means and how to fix it",
              onclick: () => openIssueCode(finding),
            }, finding.code)
          : null),
      el("div", { class: "v-msg" }, finding.message));
  };

  host.replaceChildren(
    el("h3", {}, validation.ok ? "Nothing blocking" : "Not yet valid"),
    findings.length
      ? el("ul", { class: "findings" }, ...findings.map(row))
      : ui.emptyState({ title: "No errors and no warnings.",
          body: "Nothing stands between this design and publishing it.",
          action: "Publish…", onAction: () => openPublish() }));
}

/* An issue code, explained. The row says what is wrong here; the catalog says
   what the rule is, why it exists and how to satisfy it. */
const issueCodeCache = new Map();

async function openIssueCode(finding) {
  const dialog = $("#issue-dialog");
  if (!dialog) return;
  const code = finding.code;
  $("#issue-number").textContent = finding.issue_id || "";
  $("#issue-severity").textContent = finding.severity || "";
  $("#issue-severity").className = `issue-severity ${finding.severity === "error" ? "err" : "warn"}`;
  $("#issue-title").textContent = finding.title || code;
  $("#issue-body").replaceChildren(el("p", { class: "hint" }, "Loading…"));
  if (!dialog.open) dialog.showModal();

  let entry = issueCodeCache.get(code);
  if (!entry) {
    try {
      entry = await dapi(`/issue-codes/${encodeURIComponent(code)}`);
      issueCodeCache.set(code, entry);
    } catch (e) {
      entry = null;
    }
  }
  if (!entry) {
    $("#issue-body").replaceChildren(
      el("p", {}, finding.message || ""),
      el("p", { class: "hint" }, `No explanation is catalogued for ${code} yet.`));
    return;
  }
  $("#issue-number").textContent = entry.id;
  $("#issue-title").textContent = entry.title || code;

  const section = (heading, ...content) =>
    el("section", { class: "issue-sec" }, el("h4", {}, heading), ...content);
  const para = (text) => el("p", {}, text || "");
  const where = findingWhere(finding);
  $("#issue-body").replaceChildren(
    el("p", { class: "issue-summary" }, entry.summary || ""),
    el("div", { class: "issue-here" },
      el("span", { class: "issue-label" }, "In this design"),
      where ? el("code", {}, where) : null,
      el("p", {}, finding.message || "")),
    section("What triggers it", para(entry.explanation)),
    section("Why it matters", para(entry.why)),
    section("How to fix it",
      el("ol", {}, ...(entry.fix || []).map((step) => el("li", {}, step)))),
    entry.example
      ? section("Example", el("pre", { class: "issue-example" }, entry.example))
      : null,
    el("div", { class: "issue-foot" },
      el("code", {}, entry.code),
      el("span", {}, entry.section || ""),
      ...(entry.refs || []).map((r) => el("span", { class: "issue-ref" }, r))));
}

/* Guarded like the rest of this file's top level: the placement tests load
   canvas.js under node, where there is no document, and an unguarded listener
   here threw on load and failed CI. */
if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", () => {
  $("#btn-issue-close")?.addEventListener("click", () => $("#issue-dialog").close());
  /* A click on the backdrop lands on the dialog element itself. */
  $("#issue-dialog")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) e.currentTarget.close();
  });
});

/* Take the reader to the component a finding names: select it, and scroll it
   into view, which is the whole point of attributing a finding at all. */
function selectAndReveal(id) {
  const node = layoutNodes()[id];
  if (!node) return;
  canvas.selected = { kind: node.kind, id };
  canvas.focusId = id;
  showSide("details");
  syncSelection();
  renderInspector();
  const surface = $("#canvas");
  if (surface) {
    surface.scrollTo({
      left: Math.max(0, node.x - surface.clientWidth / 2),
      top: Math.max(0, node.y - surface.clientHeight / 2),
      behavior: "smooth",
    });
  }
}

async function saveSystem(resolutions = null) {
  if (!canvas.record) return;
  setStatus("saving…");
  try {
    const outcome = await dapi(`/systems/${canvas.systemId}`, {
      method: "PUT",
      body: JSON.stringify({
        spec: canvas.record.spec,
        layout: canvas.record.layout,
        name: canvas.record.name,
        base_version: canvas.record.version,
        strategy: "merge",
        resolutions: resolutions || {},
      }),
    });
    handleSaveOutcome(outcome);
  } catch (err) {
    setStatus(`save failed: ${err.message}`);
    if (err.status === 409) {
      const lock = err.detail && err.detail.lock;
      await ui.alertDialog(
        `Locked by ${lock ? lock.holder_name || lock.holder : "someone else"}. `
        + "Your changes are still here, unsaved.", { title: "Not saved" });
    }
  }
}

function handleSaveOutcome(outcome) {
  if (outcome.status === "conflict") {
    canvas.conflicts = outcome.conflicts;
    canvas.mergedSpec = outcome.merged_spec;
    canvas.resolutions = {};
    renderConflicts();
    setStatus(outcome.message);
    return;
  }
  $("#conflict-bar").hidden = true;
  canvas.conflicts = [];
  canvas.record = outcome.record;
  canvas.dirty = false;
  /* A merge brought in somebody else's work, so every state before it is a
     state that was never true for anybody. Undoing past that point would
     silently delete their change, which is worse than having no undo. */
  resetHistory();
  updateBadges();
  renderCanvas();
  renderInspector();
  setStatus(outcome.status === "merged"
    ? `merged with v${outcome.current_version}` : `saved as v${outcome.record.version}`);
  loadSystems();
}

/* ------------------------------------------------------------- conflicts */
function renderConflicts() {
  const bar = $("#conflict-bar");
  bar.hidden = false;
  $("#conflict-summary").textContent =
    ` — ${canvas.conflicts.length} place(s) where your edit and theirs disagree. ` +
    "Choose for each, then apply.";
  $("#conflict-list").replaceChildren(...canvas.conflicts.map((c) => {
    const row = el("div", { class: "conflict-row" },
      el("code", { class: "grow" }, c.path),
      el("span", {}, `yours: ${JSON.stringify(c.ours)}`),
      el("span", {}, `theirs: ${JSON.stringify(c.theirs)}`));
    const choose = (which) => {
      canvas.resolutions[c.path] = which;
      renderConflicts();
    };
    row.appendChild(el("span", { class: "choice" },
      el("button", {
        class: canvas.resolutions[c.path] === "ours" ? "on" : "",
        onclick: () => choose("ours"),
      }, "mine"),
      el("button", {
        class: canvas.resolutions[c.path] === "theirs" ? "on" : "",
        onclick: () => choose("theirs"),
      }, "theirs")));
    return row;
  }));
}

/* ----------------------------------------------------------------- wiring */
function wireCanvas() {
  $("#user-input").addEventListener("change", async (e) => {
    canvas.user = e.target.value.trim() || "anonymous";
    announce("identity");
    await loadWorkspaces();
  });
  $("#ws-select").addEventListener("change", async (e) => {
    canvas.workspaceId = e.target.value;
    announce("identity");
    await loadSystems();
  });
  /* Both selectors change the one open organisation; the creation affordance
     lives with the rest of organisation management in app.js. */
  document.querySelectorAll("[data-org-select]").forEach((select) => {
    select.addEventListener("change", async (e) => {
      if (e.target.value) await openSystem(e.target.value);
      else renderOrgSelectors();
    });
  });
  document.querySelectorAll("#side-tabs button").forEach((button) =>
    button.addEventListener("click", () => showSide(button.dataset.side)));
  document.querySelectorAll("#left-tabs button").forEach((button) =>
    button.addEventListener("click", () => showLeft(button.dataset.left)));
  ui.wireTabs($("#side-tabs"), { label: "Inspector",
    panel: (t) => `side-${t.dataset.side}` });
  ui.wireTabs($("#left-tabs"), { label: "Model",
    panel: (t) => `left-${t.dataset.left}` });
  $("#explorer-filter")?.addEventListener("input", () => { renderExplorer(); syncSelection(); });
  $("#explorer-filter")?.setAttribute("aria-label", "Filter the model");
  /* The outline follows the viewport, and clicking it moves the viewport.
     Both directions, or it is a picture rather than a control. */
  $("#canvas")?.addEventListener("scroll", positionOutlineViewport, { passive: true });
  $("#outline-surface")?.addEventListener("click", (e) => {
    const scale = canvas.outlineScale;
    const surface = $("#canvas");
    if (!scale || !surface) return;
    const box = e.currentTarget.getBoundingClientRect();
    surface.scrollTo({
      left: Math.max(0, (e.clientX - box.left) / scale - surface.clientWidth / 2),
      top: Math.max(0, (e.clientY - box.top) / scale - surface.clientHeight / 2),
      behavior: "smooth",
    });
  });
  /* Selecting a component is a request to read it, so the panel shows it. */
  $("#btn-undo")?.addEventListener("click", undo);
  $("#btn-redo")?.addEventListener("click", redo);
  $("#btn-save").addEventListener("click", () => saveSystem());
  $("#btn-lock").addEventListener("click", async () => {
    const mine = canvas.locks.find((l) => l.holder === canvas.user);
    try {
      if (mine) {
        await dapi(`/systems/${canvas.systemId}/lock?target=*`, { method: "DELETE" });
      } else {
        await dapi(`/systems/${canvas.systemId}/lock`, {
          method: "POST",
          body: JSON.stringify({ target: "*", scope: "system" }),
        });
      }
      await openSystem(canvas.systemId);
    } catch (err) {
      if (err.status === 409 && canvas.permissions.includes("lock.break")) {
        if (await ui.confirmDialog(`${err.message}\n\nTheir unsaved work is not lost, `
            + "but they can no longer save without merging.",
            { title: "Break the lock and take it?", confirmLabel: "Break and take",
              danger: true })) {
          await dapi(`/systems/${canvas.systemId}/lock/break`, {
            method: "POST", body: JSON.stringify({ target: "*" }),
          });
          /* Take it. The person clicked **Lock**: breaking alone left the
             design unlocked and them holding nothing, so they had to click
             again — and in the gap the holder could simply take it back. */
          try {
            await dapi(`/systems/${canvas.systemId}/lock`, {
              method: "POST",
              body: JSON.stringify({ target: "*", scope: "system" }),
            });
          } catch (takeErr) {
            /* Somebody got there first. Say so plainly rather than leaving
               the badge to imply it worked. */
            await ui.alertDialog(`The lock was broken, and ${takeErr.message}`,
              { title: "Lock not taken" });
          }
          await openSystem(canvas.systemId);
        }
      } else await ui.alertDialog(err.message, { title: "Lock refused" });
    }
  });
  /* The publish path (WS-032 M9).

     Two steps on purpose. The preflight answers "would this be refused" and
     changes nothing; the request creates a deployment in `requested` that the
     fabric picks up. The designer never deploys — the tenant is the fabric's
     to assign and the platform policy is the fabric's to apply. */
  $("#btn-publish").addEventListener("click", () => openPublish());
  $("#btn-publish-close").addEventListener("click", () => {
    $("#publish-bar").hidden = true;
  });
  $("#btn-publish-recheck").addEventListener("click", () => openPublish());
  $("#btn-publish-request").addEventListener("click", () => requestDeployment());
  $("#publish-reviewed")?.addEventListener("change", syncPublishRequest);
  $("#btn-publish-review")?.addEventListener("click",
    () => window.showView?.("authority"));

  $("#btn-revisions").addEventListener("click", async () => {
    const revisions = await dapi(`/systems/${canvas.systemId}/revisions`);
    if (!revisions.length) {
      return ui.alertDialog("This design has not been saved yet.", { title: "History" });
    }
    /* A list to choose from rather than a version number to type from
       memory of a list the prompt had just closed. */
    const answer = await ui.formDialog({
      title: "History", wide: true, submitLabel: "Restore",
      message: "Restoring makes a new version from the one you choose; "
        + "nothing is deleted.",
      fields: [{ name: "version", label: "Restore which version?", type: "select",
                 value: String(revisions[0].version),
                 options: revisions.map((r) => [String(r.version),
                   `v${r.version} — ${r.author} — ${r.message || "saved"}`]) }],
    });
    if (!answer) return;
    await dapi(`/systems/${canvas.systemId}/restore/${Number(answer.version)}`,
      { method: "POST" });
    await openSystem(canvas.systemId);
  });
  $("#btn-members").addEventListener("click", () => showView("workspace"));
  /* The ⚙ button opens settings.js's dialog: display preferences and the
     installation's settings. */
  $("#btn-resolve-mine").addEventListener("click", () => {
    canvas.conflicts.forEach((c) => { canvas.resolutions[c.path] = "ours"; });
    renderConflicts();
  });
  $("#btn-resolve-theirs").addEventListener("click", () => {
    canvas.conflicts.forEach((c) => { canvas.resolutions[c.path] = "theirs"; });
    renderConflicts();
  });
  $("#btn-resolve-apply").addEventListener("click", () => saveSystem(canvas.resolutions));
  $("#btn-conflict-dismiss").addEventListener("click", () => {
    $("#conflict-bar").hidden = true;
  });
  wireDropTarget();
  $("#canvas").addEventListener("scroll", rememberViewport, { passive: true });
  document.addEventListener("keydown", handleCanvasKey);
  /* clicking the empty canvas surface hides the context menu and deselects */
  $( "#canvas").addEventListener("click", (e) => {
    hideContextMenu();
    if (e.target.id === "canvas" || e.target.id === "canvas-nodes") {
      if (canvas.linking) return cancelLink();
      canvas.selected = null;
      syncSelection();
      renderInspector();
    }
  });
}

async function initCanvas() {
  await loadPalette();
  wireCanvas();
  await loadWorkspaces();
}
/* Guarded so node can load this file for the placement drift test; the
   browser path is unchanged. */
if (typeof window !== "undefined") {
window.initCanvas = initCanvas;

/* The one door onto the open design. Everything else in the bundle goes
   through this, so there is exactly one copy of the spec in the browser. */
window.designer = {
  state: canvas,
  dapi,
  dfile,
  spec,
  teams: allTeams,
  agents: allAgents,
  kindSpec,
  find: findComponent,
  add: addComponent,
  remove: removeComponent,
  renameComponent,
  undo,
  redo,
  handleSaveOutcome,
  markDirty,
  renderCanvas,
  offerUndo,
  renderExplorer,
  renderComponents,
  wireComponentsMenu,
  effectiveAuthority,
  diagram,
  addDiagram,
  openDiagram,
  removeDiagram,
  renderOutline,
  showLeft,
  declaredIds,
  namesInUse,
  save: saveSystem,
  reopen: () => (canvas.systemId ? openSystem(canvas.systemId) : null),
  reloadWorkspaces: loadWorkspaces,
  reloadSystems: loadSystems,
  open: openSystem,
  renderSelectors: renderOrgSelectors,
  lockedByOther: () => canvas.locks.find((l) => l.holder !== canvas.user) || null,
  canEdit: () => canvas.permissions.includes("system.edit"),
  classificationOf,
  effectivePermissions,
};
}

/* Node loads this file to check `derivedPlacements` against the Python
   resolver it mirrors. Browsers have no `module`. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = { derivedPlacements, regionBoxes, __canvas: canvas };
}


/* ------------------------------------------------------ components editor
   Every kind of component gets what the Agents screen gives agents
   (ADR-0107): a list of them, a form for one, and what refers to it. The form
   is Properties' own — the same pickers, required marks, errors under the
   field and model-checked edits — drawn full-size; creating and removing go
   through the model like every other gesture (ADR-0103). */
const PART_OWNERS = { agent: "team", team: "team", subagent: "agent" };

function componentKinds() {
  return paletteKinds().filter((k) => k.kind !== "note" && k.kind !== "step");
}

function renderComponents(kind) {
  canvas.componentsKind = kind = kind || canvas.componentsKind || "team";
  const spec_ = kindSpec(kind);
  $("#comp-kind-title").textContent = spec_.label + "s";
  const list = $("#comp-list");
  const items = canvas.record ? componentsOf(kind) : [];
  list.replaceChildren(...items.map((item) => {
    const row = el("button", {
      type: "button", class: "comp-row"
        + (canvas.selected?.id === item.id ? " active" : ""),
      "data-id": item.id,
    }, el("span", { class: "comp-name" }, item.name || item.id),
       el("code", { class: "comp-id" }, item.id));
    row.addEventListener("click", () => selectComponent(kind, item.id));
    return row;
  }));
  if (!items.length) {
    list.appendChild(el("p", { class: "hint" },
      canvas.record ? `No ${spec_.label.toLowerCase()}s yet.`
                    : "Open or create an organisation first."));
  }
  renderNewComponentForm(kind);
  if (canvas.selected && canvas.selected.kind === kind
      && items.some((x) => x.id === canvas.selected.id)) {
    selectComponent(kind, canvas.selected.id);
  } else {
    canvas.selected = null;
    canvas.formHost = { host: $("#comp-form"), title: $("#comp-title") };
    renderInspector();
    $("#comp-title").textContent = `Choose a ${spec_.label.toLowerCase()}`;
    $("#comp-used").replaceChildren();
    $("#comp-raw").textContent = "";
  }
}

function selectComponent(kind, id) {
  canvas.selected = { kind, id };
  canvas.formHost = { host: $("#comp-form"), title: $("#comp-title") };
  renderInspector();
  document.querySelectorAll("#comp-list .comp-row").forEach((r) =>
    r.classList.toggle("active", r.dataset.id === id));
  // What refers to it: every drawn relationship that ends here.
  const into = derivedEdges().filter((e) => e.target === id && e.rel);
  const out = derivedEdges().filter((e) => e.source === id && e.rel);
  const row = (e, other) => el("li", {},
    el("code", {}, other), ` — ${e.rel}`,
    el("small", { class: "hint" }, ` (${e.uml})`));
  $("#comp-used").replaceChildren(
    el("h4", {}, "Referred to by"),
    into.length ? el("ul", {}, ...into.map((e) => row(e, e.source)))
                : el("p", { class: "hint" }, "Nothing refers to it."),
    el("h4", {}, "Refers to"),
    out.length ? el("ul", {}, ...out.map((e) => row(e, e.target)))
               : el("p", { class: "hint" }, "It refers to nothing."));
  $("#comp-raw").textContent = JSON.stringify(findComponent(kind, id), null, 2);
}

function renderNewComponentForm(kind) {
  const spec_ = kindSpec(kind);
  const host = $("#comp-new");
  const ownerKind = PART_OWNERS[kind];
  const owners = ownerKind ? componentsOf(ownerKind).map((x) => x.id) : [];
  const form = el("form", { class: "form comp-new-form" },
    el("label", {}, "Id", el("input", { name: "id", required: "",
      pattern: "[A-Za-z][A-Za-z0-9_\\-]*",   // `-` escaped: patterns compile with the v flag
      title: "letters, digits, _ and -; starting with a letter",
      value: canvas.record ? nextId(kind) : "" })),
    ...(ownerKind ? [el("label", {}, `Belongs to (${kindSpec(ownerKind).label})`,
      el("select", { name: "owner", required: "" },
        el("option", { value: "" }, "— choose —"),
        ...owners.map((o) => el("option", { value: o }, o))))] : []),
    el("div", { class: "actions" },
      el("button", { type: "submit", class: "primary" },
         `New ${spec_.label.toLowerCase()}`)));
  host.replaceChildren(form);
  formKit.wire(form, async (values) => {
    if (!canvas.record) throw new Error("open or create an organisation first");
    const id = values.id.trim();
    const answer = await modelOperation({
      op: "create", kind, id, ...(values.owner ? { owner: values.owner } : {}),
      attrs: seedFor(kind, id),
    });
    markDirty(`created ${id}`);
    setStatus(`${id} created${effectsLine(answer)}`);
    renderComponents(kind);
    selectComponent(kind, id);
  }, (f) => {
    const id = f.elements.id.value.trim();
    return declaredIds().has(id)
      ? { id: `'${id}' is already taken; an id must be unique.` } : {};
  });
}

function wireComponentsMenu() {
  const button = $("#btn-components");
  const menu = $("#components-list");
  if (!button || !menu) return;
  const close = () => { menu.hidden = true; button.setAttribute("aria-expanded", "false"); };
  const build = () => {
    // Grouped the way the palette is, so the menu and the palette agree.
    const groups = {};
    const walk = (label, kinds) => {
      for (const k of kinds) {
        if (k.kind !== "note") (groups[label] ||= []).push(k);
        walk(label, k.children || []);
      }
    };
    for (const g of canvas.palette?.groups || []) walk(g.label, g.kinds || []);
    menu.replaceChildren(
      el("button", { type: "button", role: "menuitem", "data-kind": "agent-view",
                     onclick: () => { close(); window.showView?.("designer"); } },
         "Agents"),
      ...Object.entries(groups).flatMap(([group, kinds]) => [
        el("div", { class: "menu-group" }, group),
        ...kinds.filter((k) => k.kind !== "agent").map((k) =>
          el("button", { type: "button", role: "menuitem", "data-kind": k.kind,
                         onclick: () => {
                           close();
                           canvas.componentsKind = k.kind;
                           window.showView?.("components");
                         } },
             el("span", { class: "ic" }, k.icon || "▫"), ` ${k.label}`)),
      ]));
  };
  button.addEventListener("click", (e) => {
    e.stopPropagation();
    if (menu.hidden) { build(); menu.hidden = false; button.setAttribute("aria-expanded", "true"); }
    else close();
  });
  document.addEventListener("click", (e) => {
    if (!menu.contains(e.target)) close();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
}
