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
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : res.statusText;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
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
};

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
  if (kind === "memory_namespace")
    return (s.memory?.namespaces || []).find((n) => n.id === id) || null;
  if (kind === "note") return canvas.record.layout.nodes[id] || null;
  const collection = COLLECTIONS[kind];
  return collection ? (s[collection] || []).find((x) => x.id === id) || null : null;
}

function nearestNode(kinds, x, y) {
  /* The parent a person means is the one they dropped it near. */
  const layout = canvas.record.layout;
  let best = null, bestDistance = Infinity;
  for (const node of Object.values(layout.nodes)) {
    if (!kinds.includes(node.kind)) continue;
    const distance = Math.hypot(node.x - x, node.y - y);
    if (distance < bestDistance) { best = node; bestDistance = distance; }
  }
  return best;
}

function addComponent(kind, id, at = null) {
  const s = spec();
  if (kind === "team") {
    const team = { id, name: id, leader: "", mandate: [], members: [], teams: [] };
    const empty = !s.organization?.id
      || (!(s.organization.members || []).length
          && !(s.organization.teams || []).length
          && !canvas.record.layout.nodes[s.organization.id]);
    if (empty) {
      // The first team dropped *is* the organization, rather than a child of an
      // invisible root nobody asked for.
      s.organization = team;
    } else {
      const host = at ? nearestNode(["team"], at.x, at.y) : null;
      const parent = host ? findComponent("team", host.id) : s.organization;
      (parent.teams = parent.teams || []).push(team);
    }
    return team;
  }
  if (kind === "agent") {
    const host = at ? nearestNode(["team"], at.x, at.y) : null;
    const team = host ? findComponent("team", host.id) : allTeams()[0];
    if (!team) throw new Error("drop a Team onto the canvas first");
    const agent = { id, name: id, roles: [], humans: [] };
    (team.members = team.members || []).push(agent);
    if (!team.leader) team.leader = id;
    return agent;
  }
  if (kind === "subagent") {
    const host = at ? nearestNode(["agent"], at.x, at.y) : null;
    const parent = host ? findComponent("agent", host.id) : allAgents()[0]?.agent;
    if (!parent) throw new Error("add an Agent before adding a sub-agent");
    const sub = { id, name: id, kind: "research", purpose: "", returns: "" };
    (parent.subagents = parent.subagents || []).push(sub);
    return sub;
  }
  if (kind === "memory_namespace") {
    s.memory = s.memory || { namespaces: [] };
    s.memory.namespaces = s.memory.namespaces || [];
    const ns = { id, scope: "private", data_classes: [] };
    s.memory.namespaces.push(ns);
    return ns;
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
  } else if (kind === "memory_namespace") {
    s.memory.namespaces = (s.memory.namespaces || []).filter((n) => n.id !== id);
  } else if (COLLECTIONS[kind]) {
    s[COLLECTIONS[kind]] = (s[COLLECTIONS[kind]] || []).filter((x) => x.id !== id);
  }
  delete canvas.record.layout.nodes[id];
  canvas.record.layout.edges = canvas.record.layout.edges.filter(
    (e) => e.source !== id && e.target !== id);
}

/* --------------------------------------------------------------- palette */
async function loadPalette() {
  canvas.palette = await dapi("/palette");
  const root = $("#palette-groups");
  root.replaceChildren(
    ...canvas.palette.groups.flatMap((group) => [
      el("h4", {}, group.label),
      ...group.kinds.map((kind) =>
        el("div", {
          class: "drag-item", draggable: "true",
          ondragstart: (e) => {
            e.dataTransfer.setData("text/kind", kind.kind);
            e.dataTransfer.effectAllowed = "copy";
          },
        }, el("span", { class: "ic" }, kind.icon || "▫"), kind.label)),
    ]));
}

function kindSpec(kind) {
  for (const group of canvas.palette?.groups || [])
    for (const k of group.kinds) if (k.kind === kind) return k;
  return { kind, label: kind, fields: [] };
}

/* ---------------------------------------------------------------- canvas */
function renderCanvas() {
  const nodes = $("#canvas-nodes");
  const layout = canvas.record?.layout || { nodes: {}, edges: [] };
  nodes.replaceChildren(
    ...Object.values(layout.nodes).map((node) => renderNode(node)));
  $("#canvas-empty").hidden = Object.keys(layout.nodes).length > 0;
  renderEdges();
}

function lockOn(id) {
  return canvas.locks.find(
    (l) => (l.scope === "system" || l.target === id) && l.holder !== canvas.user);
}

function renderNode(node) {
  const component = findComponent(node.kind, node.id) || {};
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
    class: `node${canvas.selected?.id === node.id ? " selected" : ""}${blocked ? " locked" : ""}`,
    "data-kind": node.kind, "data-id": node.id,
    style: `left:${node.x}px; top:${node.y}px; min-width:${node.width}px`,
    title: blocked ? `locked by ${blocked.holder_name || blocked.holder}` : "",
  },
    delBtn,
    el("span", { class: "n-icon" }, kindSpec(node.kind).icon || "▫"),
    el("div", { class: "n-kind" }, kindSpec(node.kind).label),
    titleEl,
    el("div", { class: "n-sub" }, nodeSubtitle(node.kind, component, node)));
  box.addEventListener("mousedown", (e) => {
    if (e.target === delBtn) return;
    startDrag(e, node, box);
  });
  box.addEventListener("click", (e) => {
    if (e.target === delBtn) return;
    selectNode(node);
  });
  box.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    selectNode(node);
    showContextMenu(e.clientX, e.clientY, node, readOnly);
  });
  return box;
}


function nodeSubtitle(kind, component, node) {
  if (kind === "team") return `leader: ${component.leader || "—"}`;
  if (kind === "agent") {
    const owner = (component.humans || []).find((h) => (h.roles || []).includes("owner"));
    return `${(component.humans || []).length} human(s)${owner ? ` · ${owner.name}` : ""}`;
  }
  if (kind === "subagent") return `${component.kind || "custom"} · tool`;
  if (kind === "note") return node.note || "note";
  if (kind === "capability") return component.action || "";
  if (kind === "trigger") return component.kind || "";
  return component.description ? String(component.description).slice(0, 40) : "";
}

function renderEdges() {
  const svg = $("#canvas-edges");
  const layout = canvas.record?.layout;
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
    line.setAttribute("stroke", "currentColor");
    line.setAttribute("stroke-width", "1.5");
    line.setAttribute("opacity", edge.kind === "member_of" ? "0.35" : "0.6");
    if (edge.kind !== "member_of") line.setAttribute("stroke-dasharray", "4 3");
    parts.push(line);
  }
  svg.replaceChildren(...parts);
  svg.style.color = "var(--muted)";
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
  (spec()?.interaction_flows || []).forEach((f) =>
    out.push({ source: f.source, target: f.target, kind: f.kind }));
  (spec()?.triggers || []).forEach((t) =>
    out.push({ source: t.id, target: t.agent, kind: "triggers" }));
  return out;
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
  canvas.record.layout.nodes[id] = {
    id, kind: node.kind,
    x: node.x + 30, y: node.y + 30,
    width: node.width, height: node.height,
    collapsed: false, note: node.note || "",
  };
  markDirty();
  renderCanvas();
  selectNode(canvas.record.layout.nodes[id]);
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
      const box = document.querySelector(`[data-id="${node.id}"] .n-title`);
      const component = findComponent(node.kind, node.id) || {};
      if (box) startInlineRename(node, component, box);
    }, readOnly),
    ctxItem("⧉ Duplicate", () => duplicateNode(node), readOnly),
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
  /* ignore when typing in an input/textarea/select */
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

  if (e.key === "Escape") {
    hideContextMenu();
    canvas.selected = null;
    renderCanvas();
    renderInspector();
    return;
  }

  if (!canvas.selected || !canvas.record) return;
  const { kind, id } = canvas.selected;
  const node = canvas.record.layout.nodes[id];
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

function nextId(kind) {
  const base = kind === "memory_namespace" ? "namespace" : kind;
  let n = 1;
  while (canvas.record.layout.nodes[`${base}_${n}`]) n += 1;
  return `${base}_${n}`;
}

function placeComponent(kind, x, y) {
  const id = nextId(kind);
  try {
    addComponent(kind, id, { x, y });
  } catch (err) {
    setStatus(err.message);
    return;
  }
  canvas.record.layout.nodes[id] = {
    id, kind, x, y, width: 200, height: 80, collapsed: false, note: "",
  };
  markDirty();
  renderCanvas();
  selectNode(canvas.record.layout.nodes[id]);
}

/* ------------------------------------------------------------- inspector */
function selectNode(node) {
  canvas.selected = { kind: node.kind, id: node.id };
  renderCanvas();
  renderInspector();
}

function renderInspector() {
  const host = $("#inspector");
  if (!canvas.selected || !canvas.record) {
    $("#inspector-title").textContent = "Inspector";
    host.className = "empty";
    host.replaceChildren("Select a component on the canvas.");
    return;
  }
  const { kind, id } = canvas.selected;
  const component = findComponent(kind, id);
  const node = canvas.record.layout.nodes[id];
  const definition = kindSpec(kind);
  $("#inspector-title").textContent = `${definition.label} · ${id}`;
  host.className = "";

  const readOnly = !canvas.permissions.includes("system.edit") || !!lockOn(id);
  const form = el("form", { class: "form", onsubmit: (e) => e.preventDefault() });
  for (const field of definition.fields) {
    const value = kind === "note" ? node.note : (component || {})[field.name];
    form.appendChild(fieldControl(field, value, readOnly, (v) => {
      if (kind === "note") node.note = v;
      else if (component) component[field.name] = v;
      markDirty();
      renderCanvas();
    }, kind));
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
    el("h3", {}, "Raw"),
    el("pre", { class: "code" }, JSON.stringify(component ?? node, null, 2)));
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

function fieldControl(field, value, readOnly, onChange, componentKind = null) {
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

function markDirty(reason = "edited") {
  canvas.dirty = true;
  canvas.record.layout.updated_at = new Date().toISOString();
  updateBadges();
  announce(reason);
}

function renderValidation(validation) {
  const host = $("#validation");
  if (!validation) return host.replaceChildren();
  host.replaceChildren(
    el("h3", {}, validation.ok ? "Valid" : "Not yet valid"),
    el("ul", {},
      ...validation.errors.map((e) => el("li", { class: "v-err" }, e)),
      ...validation.warnings.slice(0, 5).map((w) => el("li", { class: "v-warn" }, w))));
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
        if (window.confirm(`${err.message}\n\nBreak the lock?`)) {
          await dapi(`/systems/${canvas.systemId}/lock/break`, {
            method: "POST", body: JSON.stringify({ target: "*" }),
          });
          await openSystem(canvas.systemId);
        }
      } else alert(err.message);
    }
  });
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
  document.addEventListener("keydown", handleCanvasKey);
  /* clicking the empty canvas surface hides the context menu and deselects */
  $( "#canvas").addEventListener("click", (e) => {
    hideContextMenu();
    if (e.target.id === "canvas" || e.target.id === "canvas-nodes") {
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
  markDirty,
  save: saveSystem,
  reopen: () => (canvas.systemId ? openSystem(canvas.systemId) : null),
  reloadWorkspaces: loadWorkspaces,
  reloadSystems: loadSystems,
  open: openSystem,
  renderSelectors: renderOrgSelectors,
  lockedByOther: () => canvas.locks.find((l) => l.holder !== canvas.user) || null,
  canEdit: () => canvas.permissions.includes("system.edit"),
};
