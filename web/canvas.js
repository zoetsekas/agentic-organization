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
    const message = typeof detail === "string"
      ? detail
      : (detail && typeof detail.error === "string"
          ? detail.error
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
  role: "roles",
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
    container: (s) => (s.memory = s.memory || {}),
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
    return alert("A design has at least one diagram. Rename this one, or add "
      + "another before removing it.");
  }
  const name = layout.diagrams[id].name;
  if (!window.confirm(
      `Remove the diagram '${name}'?\n\nOnly the picture goes: every `
      + "component on it stays declared in the model, and the Explorer still "
      + "lists them.")) return;
  delete layout.diagrams[id];
  if (layout.active === id) layout.active = Object.keys(layout.diagrams)[0];
  markDirty(`removed the diagram '${name}'`);
  renderDiagramBar();
  renderCanvas();
  renderInspector();
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
  bar.replaceChildren(
    ...diagramList().map((d) => {
      const tab = el("button", {
        class: `dia-tab${d.id === layout.active ? " active" : ""}`,
        title: d.root ? `a diagram of ${d.root}` : "the whole organisation",
      }, d.root ? el("span", { class: "ic" }, "\u21b3") : null, d.name);
      tab.addEventListener("click", () => openDiagram(d.id));
      tab.addEventListener("dblclick", () => {
        if (readOnly) return;
        const name = window.prompt("Name this diagram", d.name);
        if (!name) return;
        d.name = name;
        markDirty("renamed a diagram");
        renderDiagramBar();
      });
      tab.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        if (!readOnly) removeDiagram(d.id);
      });
      return tab;
    }),
    el("button", {
      class: "dia-add", title: "a new, empty diagram of this design",
      disabled: readOnly ? "" : null,
      onclick: () => addDiagram(""),
    }, "+"));
}

/* The nodes of the diagram currently open, always an object. */
function layoutNodes() {
  return diagram()?.nodes || {};
}

function findComponent(kind, id) {
  const s = spec();
  if (!s) return null;
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
  return collection ? (s[collection] || []).find((x) => x.id === id) || null : null;
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
    const team = { id, name: id, leader: "", mandate: [], members: [], teams: [] };
    const empty = !s.organization?.id
      || (!(s.organization.members || []).length
          && !(s.organization.teams || []).length
          && !layoutNodes()[s.organization.id]);
    if (empty) {
      // The first team dropped *is* the organization, rather than a child of an
      // invisible root nobody asked for.
      s.organization = team;
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
  s[collection] = s[collection] || [];
  const item = { id };
  s[collection].push(item);
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
    s[COLLECTIONS[kind]] = (s[COLLECTIONS[kind]] || []).filter((x) => x.id !== id);
  }
  delete layoutNodes()[id];
}

/* --------------------------------------------------------------- palette */
async function loadPalette() {
  canvas.palette = await dapi("/palette");
  const root = $("#palette-groups");

  /* A nested entry is a component the parent *contains* in the spec: a Tool
     under an Agent is `agent.tools`. The indent is the model, not styling,
     so it is rendered from the tree the server sends rather than guessed. */
  const item = (kind, depth) => el("div", {
    class: "drag-item", draggable: "true",
    style: depth ? `margin-left:${depth * 14}px` : null,
    title: kind.help || kind.label,
    ondragstart: (e) => {
      e.dataTransfer.setData("text/kind", kind.kind);
      e.dataTransfer.effectAllowed = "copy";
    },
  }, el("span", { class: "ic" }, kind.icon || "▫"), kind.label);

  const branch = (kind, depth) => [
    item(kind, depth),
    ...(kind.children || []).flatMap((c) => branch(c, depth + 1)),
  ];

  root.replaceChildren(
    ...canvas.palette.groups.map((group) => el("div", { class: "group" },
      el("h4", { title: group.help || "" }, group.label),
      ...group.kinds.flatMap((kind) => branch(kind, 0)))),
    el("p", { class: "note" },
      "An indented component is one the component above it contains. "
      + "Drop to place, then draw the links the model allows."));
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
  const byId = Object.fromEntries((s.data_classes || []).map((d) => [d.id, d]));
  const touched = new Set();
  for (const capabilityId of agent.capabilities || []) {
    const capability = (s.capabilities || []).find((c) => c.id === capabilityId);
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

function kindSpec(kind) {
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

function renderCanvas() {
  const nodes = $("#canvas-nodes");
  const layout = diagram() || { nodes: {} };
  /* A component one agent holds lives inside that agent's box (below), so
     drawing it a second time as its own node would say two different things
     about one fact. Held by two or more, it is shared, it keeps its node, and
     the edges are the point. */
  const inlined = inlinedComponents();
  nodes.replaceChildren(
    ...Object.values(layout.nodes)
      .filter((node) => !inlined.has(node.id))
      .map((node) => renderNode(node)));
  $("#canvas-empty").hidden = Object.keys(layout.nodes).length > 0;
  renderRegions();
  renderEdges();
  restoreViewport();
  renderExplorer();
  renderOutline();
  renderDiagramBar();
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
        renderCanvas();
        renderInspector();
      });
      chips.push(chip);
    }
  }
  return chips.length
    ? [el("div", { class: "n-held-row" }, ...chips)]
    : [];
}

function renderNode(node) {
  const component = findComponent(node.kind, node.id) || {};
  const linking = canvas.linking;
  const blocked = lockOn(node.id);
  const readOnly = !canvas.permissions.includes("system.edit") || !!blocked;

  /* × delete button — top-right corner */
  const delBtn = el("button", {
    class: "node-delete-btn", title: "Delete", tabindex: "-1",
    disabled: readOnly ? "" : null,
  }, "×");
  delBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    deleteNode(node.kind, node.id);
  });

  /* inline-editable title */
  const titleEl = el("div", { class: "n-title" }, component.name || component.id || node.id);
  if (!readOnly) {
    titleEl.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      startInlineRename(node, component, titleEl);
    });
  }

  const box = el("div", {
    class: `node${canvas.selected?.id === node.id ? " selected" : ""}`
      + `${blocked ? " locked" : ""}`
      + (linking
          ? (linking.id === node.id ? " link-source"
             : linkRule(linking.kind, node.kind) ? " link-target" : " link-no")
          : ""),
    "data-kind": node.kind, "data-id": node.id,
    "data-shape": shapeOf(node.kind),
    "data-classification": node.kind === "agent"
      ? classificationOf(component) : null,
    style: `left:${node.x}px; top:${node.y}px; min-width:${node.width}px`,
    title: blocked ? `locked by ${blocked.holder_name || blocked.holder}` : "",
  },
    delBtn,
    node.kind === "environment"
      ? el("span", { class: "n-net" }, postureOf(component))
      : el("span", { class: "n-icon" }, kindSpec(node.kind).icon || "▫"),
    el("div", { class: "n-kind" }, kindSpec(node.kind).label),
    titleEl,
    el("div", { class: "n-sub" }, nodeSubtitle(node.kind, component, node)),
    ...(node.kind === "agent" ? heldChips(component, readOnly) : []));
  box.addEventListener("mousedown", (e) => {
    if (e.target === delBtn) return;
    /* While a link is being drawn, a press is aiming at a target rather than
       picking the node up. */
    if (canvas.linking) return;
    startDrag(e, node, box);
  });
  box.addEventListener("click", (e) => {
    if (e.target === delBtn) return;
    if (canvas.linking) return completeLink(node);
    selectNode(node);
  });
  box.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    selectNode(node);
    showContextMenu(e.clientX, e.clientY, node, readOnly);
  });
  return box;
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
    const person = (spec()?.people || []).find((p) => p.id === human.person);
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
function linkRules(sourceKind, targetKind) {
  return (canvas.palette?.links || []).filter(
    (r) => r.source === sourceKind && r.target === targetKind);
}

function linkRule(sourceKind, targetKind) {
  return linkRules(sourceKind, targetKind)[0] || null;
}

/* When several apply, the reader says which. The prompt states what each one
   does, because "contains" and "associates" have very different consequences
   and neither is obvious from a line on a canvas. */
function chooseRule(rules, from, target) {
  if (rules.length === 1) return rules[0];
  const numbered = rules.map((r, i) => `${i + 1}. ${r.label} — ${r.help}`);
  const answer = window.prompt(
    `How is ${target.id} related to ${from.id}?\n\n${numbered.join("\n\n")}\n\n`
    + "Enter a number.", "1");
  if (answer === null) return null;
  const at = Number(answer.trim()) - 1;
  if (!Number.isInteger(at) || at < 0 || at >= rules.length) {
    throw new Error(`'${answer}' is not one of 1..${rules.length}`);
  }
  return rules[at];
}

function legalTargetsFrom(sourceKind) {
  return (canvas.palette?.links || [])
    .filter((r) => r.source === sourceKind).map((r) => r.target);
}

function beginLink(node) {
  canvas.linking = { id: node.id, kind: node.kind };
  const targets = legalTargetsFrom(node.kind);
  setStatus(targets.length
    ? `linking from ${node.id} — click ${targets.map((t) => an(t)).join(" or ")},`
      + " or press Escape"
    : `nothing links from ${an(node.kind)}`);
  renderCanvas();
}

function cancelLink(quiet = false) {
  canvas.linking = null;
  if (!quiet) setStatus("link cancelled");
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

function completeLink(target) {
  const from = canvas.linking;
  if (!from) return;
  if (from.id === target.id) return cancelLink();
  const rules = linkRules(from.kind, target.kind);
  let rule = rules[0] || null;
  if (rule) {
    try {
      rule = chooseRule(rules, from, target);
    } catch (err) {
      alert(err.message);
      return cancelLink(true);
    }
    if (!rule) return cancelLink();
  }
  if (!rule) {
    /* The refusal names the model, not the UI: two agents are not connected
       by a line, they are connected by a declared flow or by belonging to the
       same organisation. */
    const legal = legalTargetsFrom(from.kind);
    alert(`${an(from.kind, true)} does not link to ${an(target.kind)}.\n\n`
      + (legal.length
          ? `From ${an(from.kind)} you can link to: ${legal.join(", ")}.`
          : `Nothing links from ${an(from.kind)}.`));
    return cancelLink(true);
  }
  try {
    applyLink(rule, from, target);
    markDirty(`linked ${from.id} → ${target.id}`);
    setStatus(`${from.id} ${rule.label} ${target.id}`);
  } catch (err) {
    alert(err.message);
  }
  canvas.linking = null;
  renderCanvas();
  renderInspector();
}

function applyLink(rule, from, target) {
  const s = spec();
  if (rule.relationship === "flow") {
    const kinds = rule.kinds || [];
    const kind = window.prompt(
      `How may ${from.id} reach ${target.id}?\n\n`
      + `One of: ${kinds.join(", ")}\n\n`
      + "A flow is directional: 'consult' lets the source ask without being "
      + "able to instruct, and the reverse does not follow.",
      kinds[0] || "consult");
    if (!kind) throw new Error("a flow needs a kind; nothing was linked");
    if (!kinds.includes(kind)) {
      throw new Error(`'${kind}' is not one of ${kinds.join(", ")}`);
    }
    const flows = (s.interaction_flows = s.interaction_flows || []);
    if (flows.some((f) => f.source === from.id && f.target === target.id
                          && f.kind === kind)) {
      throw new Error("that flow is already declared");
    }
    flows.push({ source: from.id, target: target.id, kind });
    return;
  }
  if (rule.relationship === "association") {
    /* Not a move. Containment has one parent; an association is a declared
       fact about two units that both stay where they are. */
    const kinds = rule.kinds || [];
    const kind = window.prompt(
      `How is ${from.id} related to ${target.id}?\n\n`
      + `One of: ${kinds.join(", ")}\n\n`
      + "'oversees' is checked: an overseer that sits inside what it oversees "
      + "is refused. 'partners_with' is declared inert and grants nothing.",
      kinds[0] || "oversees");
    if (!kind) throw new Error("an association needs a kind; nothing was linked");
    if (!kinds.includes(kind)) {
      throw new Error(`'${kind}' is not one of ${kinds.join(", ")}`);
    }
    const reason = window.prompt(
      "Why does this relationship exist?\n\nAn association nobody can "
      + "explain is decoration, and the validator says so.", "") || "";
    const links = (s.unit_links = s.unit_links || []);
    if (links.some((l) => l.source === from.id && l.target === target.id
                          && l.kind === kind)) {
      throw new Error("that association is already declared");
    }
    links.push({ source: from.id, target: target.id, kind, reason });
    return;
  }
  if (rule.relationship === "fires") {
    const trigger = findComponent("trigger", from.id);
    if (!trigger) throw new Error("that trigger is no longer in the spec");
    trigger.agent = target.id;
    return;
  }
  if (rule.relationship === "holds") {
    /* A reference, not a move: a skill, a plugin or a tool may be held by
       several agents at once, and linking it to a second does not take it
       from the first. The spec stores the id on each holder. */
    const field = rule.writes.split(".")[1];
    const agent = findComponent("agent", from.id);
    if (!agent) throw new Error("that agent is no longer in the spec");
    const held = (agent[field] = agent[field] || []);
    if (held.includes(target.id)) {
      throw new Error(`${from.id} already holds ${target.id}`);
    }
    held.push(target.id);
    return;
  }
  /* The structural links move the component: a team or an agent belongs in
     exactly one place, so linking it somewhere is detaching it from where it
     was. Re-parenting, not duplication. */
  const moved = detach(target.kind, target.id);
  if (!moved) throw new Error(`${target.id} is not in the organisation`);
  if (rule.relationship === "contains") {
    const parent = findComponent("team", from.id);
    (parent.teams = parent.teams || []).push(moved);
  } else if (rule.relationship === "member") {
    const parent = findComponent("team", from.id);
    (parent.members = parent.members || []).push(moved);
    if (!parent.leader) parent.leader = moved.id;
  } else if (rule.relationship === "uses") {
    const parent = findComponent("agent", from.id);
    (parent.subagents = parent.subagents || []).push(moved);
  }
}

/* Unlinking is defined for what a link created. A structural link put the
   component somewhere, so undoing it returns it to the root; a flow is a
   declaration, so undoing it removes the declaration. */
function unlink(node) {
  const s = spec();
  if (node.kind === "team" || node.kind === "agent") {
    if (node.id === s.organization?.id) {
      return alert("The organisation itself has nowhere to be unlinked to.");
    }
    const moved = detach(node.kind, node.id);
    if (!moved) return;
    if (node.kind === "team") {
      (s.organization.teams = s.organization.teams || []).push(moved);
    } else {
      (s.organization.members = s.organization.members || []).push(moved);
      if (!s.organization.leader) s.organization.leader = moved.id;
    }
    markDirty(`unlinked ${node.id}`);
    setStatus(`${node.id} moved to ${s.organization.id}`);
  } else if (node.kind === "subagent") {
    alert("A sub-agent belongs to the agent that calls it; delete it instead.");
    return;
  }
  if (HELD_KINDS[node.kind]) {
    /* Held by any number of agents, so unlinking releases it from all of
       them; it stays declared at the top level. */
    const field = HELD_KINDS[node.kind];
    let released = 0;
    for (const { agent } of allAgents()) {
      const held = agent[field] || [];
      const at = held.indexOf(node.id);
      if (at >= 0) { held.splice(at, 1); released += 1; }
    }
    if (!released) return alert(`Nothing holds ${node.id}.`);
    markDirty(`released ${node.id}`);
    setStatus(`${node.id} released from ${released} agent`
      + (released === 1 ? "" : "s"));
  }
  const links = s.unit_links || [];
  const keptLinks = links.filter(
    (l) => l.source !== node.id && l.target !== node.id);
  if (keptLinks.length !== links.length) {
    s.unit_links = keptLinks;
    markDirty(`removed associations on ${node.id}`);
  }
  const flows = s.interaction_flows || [];
  const kept = flows.filter((f) => f.source !== node.id && f.target !== node.id);
  if (kept.length !== flows.length) {
    s.interaction_flows = kept;
    markDirty(`removed flows on ${node.id}`);
  }
  renderCanvas();
  renderInspector();
}

function nodeSubtitle(kind, component, node) {
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
  const edges = derivedEdges();
  const ns = "http://www.w3.org/2000/svg";
  const parts = [];
  for (const edge of edges) {
    const a = layout.nodes[edge.source], b = layout.nodes[edge.target];
    if (!a || !b) continue;
    const line = document.createElementNS(ns, "path");
    const x1 = a.x + a.width / 2, y1 = a.y + 45;
    const x2 = b.x + b.width / 2, y2 = b.y + 45;
    const mid = (y1 + y2) / 2;
    line.setAttribute("d", `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke-width", "1.5");
    /* An edge says which kind of relation it is by how it is drawn: a solid
       reporting line, a dashed mission peer that expires with the mission, a
       dotted amber egress that leaves the boundary. */
    const style = edge.association
      ? (EDGE_STYLES[`link:${edge.kind}`] || EDGE_STYLES["link:partners_with"])
      : (EDGE_STYLES[edge.kind] || EDGE_STYLES.member_of);
    line.setAttribute("stroke", `var(${style.stroke})`);
    if (style.dash) line.setAttribute("stroke-dasharray", style.dash);
    parts.push(line);
    if (edge.association) {
      /* Containment is a line; an association is a line plus what it means.
         Without the word, the two would be told apart only by a dash
         pattern, and a reader should not have to consult a legend to know
         whether a team is inside another or supervising it. */
      const text = document.createElementNS(ns, "text");
      text.setAttribute("x", String((x1 + x2) / 2));
      text.setAttribute("y", String(mid - 4));
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("class", "edge-label");
      text.setAttribute("fill", `var(${style.stroke})`);
      text.textContent = edge.kind.replace(/_/g, " ");
      parts.push(text);
    }
  }
  svg.replaceChildren(...parts);
}

const EDGE_STYLES = {
  member_of: { stroke: "--edge-report" },
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
      const environment = agent.environment?.environment;
      if (!environment) continue;     // no environment class, so no place
      const id = `${unit}--${environment}`;
      if (!members.has(id)) members.set(id, { id, unit, environment, agents: [] });
      members.get(id).agents.push(agent.id);
    }
    for (const child of team.teams || []) walk(child, unit);
  };
  walk(root, root.id);
  /* The posture comes off the environment class, because that is where it is
     declared. A region's border says it, so a reader sees which places can
     reach out at all without opening anything. */
  const postures = new Map(
    (spec()?.environments || []).map((e) => [e.id, e.network || "none"]));
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
  walkTeams(spec()?.organization, (team) => {
    (team.members || []).forEach((m) =>
      out.push({ source: team.id, target: m.id, kind: "member_of" }));
    (team.teams || []).forEach((child) =>
      out.push({ source: team.id, target: child.id, kind: "member_of" }));
    (team.members || []).forEach((m) =>
      (m.subagents || []).forEach((sub) =>
        out.push({ source: m.id, target: sub.id, kind: "uses" })));
  });
  /* A held component drawn twice would say two different things. One holder
     means the thing lives inside that agent's box, so no edge is drawn; two
     or more means it is shared, and sharing is exactly what an edge is for. */
  for (const [kind, field] of Object.entries(HELD_KINDS)) {
    for (const [id, holders] of Object.entries(holdersByComponent(field))) {
      if (holders.length < 2) continue;
      holders.forEach((agentId) =>
        out.push({ source: agentId, target: id, kind: `holds:${kind}` }));
    }
  }
  (spec()?.interaction_flows || []).forEach((f) =>
    out.push({ source: f.source, target: f.target, kind: f.kind }));
  /* Association, drawn as an association: dashed, labelled with its kind, and
     never mistakable for the containment line above (ADR-0081). */
  (spec()?.unit_links || []).forEach((l) =>
    out.push({ source: l.source, target: l.target, kind: l.kind,
               association: true }));
  (spec()?.triggers || []).forEach((t) =>
    out.push({ source: t.id, target: t.agent, kind: "triggers" }));
  return out;
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
    children: (s[collection] || []).map((item) => ({
      id: item.id, kind, label: item.name || item.id,
      note: note ? note(item) : "", children: [],
    })),
  });

  return [
    { id: "group:organisation", kind: "group", label: "Organisation",
      group: true,
      children: s.organization?.id ? [unit(s.organization)] : [] },
    flat("person", "people", (p) => p.position || ""),
    flat("role", "roles", (r) => r.title || ""),
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
    const item = el("div", {
      class: `ex-row${node.group ? " group" : ""}`
        + `${canvas.selected?.id === node.id ? " selected" : ""}`
        + `${onCanvas ? "" : " off-canvas"}`,
      style: `padding-left:${8 + depth * 13}px`,
      /* A component not on the canvas is dimmed rather than hidden: it is in
         the model, and pretending otherwise is how a declaration gets lost. */
      title: node.group ? ""
        : `${node.id}${node.note ? ` — ${node.note}` : ""}`
          + (onCanvas ? "" : " — declared, not on the canvas"),
      onclick: node.group ? null : () => {
        if (onCanvas) return selectAndReveal(node.id);
        canvas.selected = { kind: node.kind, id: node.id };
        showSide("details");
        renderExplorer();
        renderInspector();
      },
    },
      el("span", { class: "ic" }, kindSpec(node.kind).icon || "▫"),
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
    return rect;
  }));

  const view = $("#outline-viewport");
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

async function openPublish() {
  const bar = $("#publish-bar");
  if (!bar) return;
  if (!canvas.systemId) return alert("Open an organisation first.");
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
  $("#btn-publish-request").disabled =
    !verdict.ok || !canvas.permissions.includes("system.publish");
}

async function requestDeployment() {
  const tenant = $("#publish-tenant").value.trim();
  if (!tenant) {
    return alert("Name the tenant. It is assigned by the fabric, so the "
      + "designer cannot choose one for you.");
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
  const surface = $("#canvas");
  const startX = event.clientX, startY = event.clientY;
  const originX = node.x, originY = node.y;
  const snap = 10;
  function move(e) {
    node.x = Math.max(0, Math.round((originX + e.clientX - startX) / snap) * snap);
    node.y = Math.max(0, Math.round((originY + e.clientY - startY) / snap) * snap);
    box.style.left = `${node.x}px`;
    box.style.top = `${node.y}px`;
    renderEdges();
    renderRegions();
  }
  function end() {
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", end);
    markDirty();
  }
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", end);
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
    if (!kind) return;
    if (!canvas.record) { setStatus("Open or create a system first, then drop components onto the canvas."); return; }
    const rect = surface.getBoundingClientRect();
    const x = Math.round((e.clientX - rect.left + surface.scrollLeft) / 10) * 10;
    const y = Math.round((e.clientY - rect.top + surface.scrollTop) / 10) * 10;
    placeComponent(kind, x, y);
  });
}

/* --------------------------------------------------------- delete / rename */
function deleteNode(kind, id) {
  if (!canvas.record) return;
  if (!window.confirm(`Remove "${id}"?`)) return;
  removeComponent(kind, id);
  if (canvas.selected?.id === id) canvas.selected = null;
  markDirty();
  renderCanvas();
  renderInspector();
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

/* ---------------------------------------------- keyboard shortcuts */
function handleCanvasKey(e) {
  const tag = document.activeElement?.tagName;
  /* Undo is the exception to "ignore keys while typing": the browser's own
     undo inside a text box is what you want there, and ours everywhere else.
     So it is checked before the guard and skipped inside a field. */
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
  if (e.key === "Escape" && canvas.linking) {
    e.preventDefault();
    return cancelLink();
  }

  if (e.key === "Escape") {
    hideContextMenu();
    canvas.selected = null;
    renderCanvas();
    renderInspector();
    return;
  }

  if (!canvas.selected || !canvas.record) return;
  const { kind, id } = canvas.selected;
  const node = layoutNodes()[id];
  if (!node) return;

  if (e.key === "Delete" || e.key === "Backspace") {
    e.preventDefault();
    deleteNode(kind, id);
    return;
  }

  const snap = 10;
  const dirs = { ArrowLeft: [-snap, 0], ArrowRight: [snap, 0], ArrowUp: [0, -snap], ArrowDown: [0, snap] };
  if (dirs[e.key]) {
    e.preventDefault();
    const [dx, dy] = dirs[e.key];
    node.x = Math.max(0, node.x + dx);
    node.y = Math.max(0, node.y + dy);
    markDirty();
    renderCanvas();
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

function placeComponent(kind, x, y) {
  const id = nextId(kind);
  const host = nodeAt(x, y);
  try {
    const made = addComponent(kind, id, { x, y });
    if (made && typeof made.name === "string") made.name = nextName(kind, id);
  } catch (err) {
    setStatus(err.message);
    return;
  }
  layoutNodes()[id] = {
    id, kind, x, y, width: 200, height: 80, collapsed: false, note: "",
  };

  /* Dropped inside something that can hold it → linked, there and then.
     Dropped inside something that cannot → still placed, and told why, rather
     than silently landing on top of a box it has no relationship with. */
  if (host && host.id !== id) {
    const rule = dropRule(host.kind, kind);
    if (rule) {
      try {
        applyLink(rule, { kind: host.kind, id: host.id }, { kind, id });
        setStatus(`${host.id} ${rule.label} ${id}`);
      } catch (err) {
        setStatus(err.message);
      }
    } else {
      setStatus(`${an(host.kind, true)} does not hold ${an(kind)}, so `
        + `${id} was placed on its own`);
    }
  }
  markDirty();
  renderCanvas();
  selectNode(layoutNodes()[id]);
}

/* ------------------------------------------------------------- inspector */
function selectNode(node) {
  canvas.selected = { kind: node.kind, id: node.id };
  showSide("details");
  renderCanvas();
  renderInspector();
}

function renderInspector() {
  const host = $("#inspector");
  if (!canvas.selected || !canvas.record) {
    $("#inspector-title").textContent = "Properties";
    host.className = "empty";
    host.replaceChildren(
      "Select a component on the canvas or in the explorer.");
    return;
  }
  const { kind, id } = canvas.selected;
  const component = findComponent(kind, id);
  const node = layoutNodes()[id];
  const definition = kindSpec(kind);
  $("#inspector-title").textContent = `${definition.label} · ${id}`;
  host.className = "";

  const readOnly = !canvas.permissions.includes("system.edit") || !!lockOn(id);
  const form = el("form", { class: "form", onsubmit: (e) => e.preventDefault() });
  for (const field of definition.fields) {
    const value = kind === "note" ? node.note : (component || {})[field.name];
    form.appendChild(fieldControl(field, value, readOnly, (v) => {
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
        component[field.name] = v;
      }
      /* Named per field and per component: a run of typing in one box is one
         step, and moving to the next box starts another. */
      markDirty(`edited ${id}.${field.name}`, true);
      renderCanvas();
    }, kind, component));
  }
  const actions = el("div", { class: "actions" },
    el("button", {
      type: "button", disabled: readOnly ? "" : null,
      onclick: () => {
        if (!window.confirm(`Remove ${id}?`)) return;
        removeComponent(kind, id);
        canvas.selected = null;
        markDirty();
        renderCanvas();
        renderInspector();
      },
    }, "Remove"));
  form.appendChild(actions);
  host.replaceChildren(form,
    ...(kind === "agent" ? [resolvesTo(component)] : []),
    el("h3", {}, "Raw"),
    el("pre", { class: "code" }, JSON.stringify(component ?? node, null, 2)));
}

/* "Resolves to" — the question no other view answers: given these bindings,
   what may this agent actually reach? It is resolved here from the open spec,
   because it is a design fact; nothing runtime is consulted. */
function effectivePermissions(agent) {
  const s = spec();
  if (!s || !agent) return [];
  const capabilities = Object.fromEntries((s.capabilities || []).map((c) => [c.id, c]));
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
    const role = (s.roles || []).find((r) => r.id === roleId);
    (role?.capabilities || []).forEach((c) => add(c, roleId));
  }
  return out;
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

  const ids = (col) => (s[col] || []).map((x) => x.id).filter(Boolean);
  const agentIds = () => allAgents().map((a) => a.agent.id);

  const REF_MAP = {
    agent: {
      roles:        { mode: "reflist", col: "roles" },
      capabilities: { mode: "reflist", col: "capabilities" },
      knowledge:    { mode: "reflist", col: "knowledge" },
      endpoints:    { mode: "reflist", col: "endpoints" },
      environment:  { mode: "ref",     col: "environments" },
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
          const tool = (s.tools || []).find((t) => t.id === nodeId);
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
  if (!entry) return null;

  /* resolve options */
  let options;
  if (entry.fn) {
    /* fn may accept the currently-selected node id for context (team.leader) */
    options = entry.fn(canvas.selected?.id);
  } else {
    options = ids(entry.col);
  }
  /* only activate picker when there are options; otherwise fall through */
  if (!options.length) return null;
  return { mode: entry.mode, options };
}

/* Single-reference <select> — value is a string id */
function renderRef(field, value, readOnly, onChange, options) {
  const attrs = readOnly ? { disabled: "" } : {};
  const blank = el("option", { value: "" }, "— none —");
  const sel = el("select", attrs, blank,
    ...options.map((o) => el("option", { value: o }, o)));
  sel.value = value ?? "";
  sel.addEventListener("change", () => onChange(sel.value || null));
  return sel;
}

/* Multi-reference pill picker — value is string[] of ids */
function renderReflist(field, value, readOnly, onChange, options) {
  let selected = Array.isArray(value) ? [...value] : [];

  const wrap = el("div", { class: "reflist-wrap" });

  function redraw() {
    const pills = selected.map((id) => {
      const pill = el("span", { class: "ref-pill" }, id);
      if (!readOnly) {
        const x = el("button", { type: "button", "aria-label": `remove ${id}` }, "×");
        x.addEventListener("click", () => {
          selected = selected.filter((v) => v !== id);
          onChange([...selected]);
          redraw();
        });
        pill.appendChild(x);
      }
      return pill;
    });

    const remaining = options.filter((o) => !selected.includes(o));
    const adder = remaining.length && !readOnly
      ? (() => {
          const sel = el("select", {},
            el("option", { value: "" }, "+ add…"),
            ...remaining.map((o) => el("option", { value: o }, o)));
          sel.addEventListener("change", () => {
            if (!sel.value) return;
            if (!selected.includes(sel.value)) {
              selected = [...selected, sel.value];
              onChange([...selected]);
              redraw();
            }
          });
          return sel;
        })()
      : null;

    /* free-text fallback for IDs not in the spec yet */
    const freeText = !readOnly
      ? (() => {
          const inp = el("input", {
            class: "reflist-free", placeholder: "type id + Enter",
            title: "Add an id not yet in the spec",
          });
          inp.addEventListener("keydown", (e) => {
            if (e.key !== "Enter") return;
            e.preventDefault();
            const v = inp.value.trim();
            if (v && !selected.includes(v)) {
              selected = [...selected, v];
              onChange([...selected]);
              inp.value = "";
              redraw();
            }
          });
          return inp;
        })()
      : null;

    wrap.replaceChildren(...pills, ...(adder ? [adder] : []), ...(freeText ? [freeText] : []));
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
  const options = (spec()?.decisions || []).map((d) => d.id).filter(Boolean);
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
  const caps = new Map((s?.capabilities || []).map((c) => [c.id, c]));
  const roles = new Map((s?.roles || []).map((r) => [r.id, r]));
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
        : renderReflist(field, value, readOnly, onChange, ctx.options);
      const label = el("label", {}, `${field.name}${field.required ? " *" : ""}`, input);
      if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
      return label;
    }
  }

  /* ---- policy conditions (ADR-0008) ---- */
  if (field.type === "conditions") {
    const input = renderConditions(field, value, readOnly, onChange);
    const label = el("label", { class: "stacked" }, field.name, input);
    if (field.help) label.appendChild(el("small", { class: "hint" }, field.help));
    return label;
  }

  /* ---- authority (ADR-0065, ADR-0072) ---- */
  if (field.type === "decisions" || field.type === "decision_refs"
      || field.type === "autonomy") {
    const decisionIds = () =>
      (spec()?.decisions || []).map((d) => d.id).filter(Boolean);
    input = field.type === "decisions"
      // A mandate: absent inherits, present-and-empty decides nothing.
      ? renderMandate(field, value, readOnly, onChange)
      : field.type === "decision_refs"
      // A plain reference list, with no inherit-or-empty question to ask.
      ? renderReflist(field, value, readOnly, onChange, decisionIds())
      : renderAutonomy(field, value, readOnly, onChange, component);
    const label = el("label", { class: "stacked" },
      `${field.name}${field.required ? " *" : ""}`, input);
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
                        `set ${field.name} on this agent`);
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
      `${field.name}${field.required ? " *" : ""}`, wrap);
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
      `${field.name}${field.required ? " *" : ""}`, input);
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
      `${field.name}${field.required ? " *" : ""}`, input);
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
      `${field.name}${field.required ? " *" : ""}`, input, note);
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
    input.addEventListener("input", () => onChange(input.value));
  }
  if (input && input.tagName !== "DIV") input.setAttribute("name", field.name);
  const label = el("label", {},
    `${field.name}${field.required ? " *" : ""}`, input);
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
        finding.code ? el("span", { class: "v-code" }, finding.code) : null),
      el("div", { class: "v-msg" }, finding.message));
  };

  host.replaceChildren(
    el("h3", {}, validation.ok ? "Nothing blocking" : "Not yet valid"),
    findings.length
      ? el("ul", { class: "findings" }, ...findings.map(row))
      : el("p", { class: "hint" }, "No errors and no warnings."));
}

/* Take the reader to the component a finding names: select it, and scroll it
   into view, which is the whole point of attributing a finding at all. */
function selectAndReveal(id) {
  const node = layoutNodes()[id];
  if (!node) return;
  canvas.selected = { kind: node.kind, id };
  showSide("details");
  renderCanvas();
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
      alert(`Locked by ${lock ? lock.holder_name || lock.holder : "someone else"}.`);
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
  $("#explorer-filter")?.addEventListener("input", renderExplorer);
  /* The outline follows the viewport, and clicking it moves the viewport.
     Both directions, or it is a picture rather than a control. */
  $("#canvas")?.addEventListener("scroll", renderOutline);
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
        if (window.confirm(`${err.message}\n\nBreak the lock and take it?`)) {
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
            alert(`The lock was broken, and ${takeErr.message}`);
          }
          await openSystem(canvas.systemId);
        }
      } else alert(err.message);
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

  $("#btn-revisions").addEventListener("click", async () => {
    const revisions = await dapi(`/systems/${canvas.systemId}/revisions`);
    const choice = window.prompt(
      "History:\n" + revisions.map((r) =>
        `v${r.version} — ${r.author} — ${r.message || "saved"}`).join("\n") +
      "\n\nRestore which version? (blank to cancel)");
    if (!choice) return;
    await dapi(`/systems/${canvas.systemId}/restore/${Number(choice)}`,
      { method: "POST" });
    await openSystem(canvas.systemId);
  });
  $("#btn-members").addEventListener("click", () => showView("workspace"));
  $("#btn-designer-settings").addEventListener("click", async () => {
    const settings = await dapi("/settings");
    const entry = window.prompt(
      "Designer settings:\n" + JSON.stringify(settings, null, 2) +
      "\n\nChange as key=value (e.g. lock_ttl_seconds=300)");
    if (!entry) return;
    const [key, value] = entry.split("=");
    try {
      await dapi("/settings", {
        method: "PUT",
        body: JSON.stringify({ [key.trim()]: isNaN(Number(value)) ? value.trim()
          : Number(value) }),
      });
      setStatus("settings updated");
    } catch (err) { alert(err.message); }
  });
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
      renderCanvas();
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
  renderExplorer,
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
