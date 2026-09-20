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
  const box = el("div", {
    class: `node${canvas.selected?.id === node.id ? " selected" : ""}${blocked ? " locked" : ""}`,
    "data-kind": node.kind, "data-id": node.id,
    style: `left:${node.x}px; top:${node.y}px; min-width:${node.width}px`,
    title: blocked ? `locked by ${blocked.holder_name || blocked.holder}` : "",
  },
    el("span", { class: "n-icon" }, kindSpec(node.kind).icon || "▫"),
    el("div", { class: "n-kind" }, kindSpec(node.kind).label),
    el("div", { class: "n-title" }, component.name || component.id || node.id),
    el("div", { class: "n-sub" }, nodeSubtitle(node.kind, component, node)));
  box.addEventListener("mousedown", (e) => startDrag(e, node, box));
  box.addEventListener("click", () => selectNode(node));
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
    surface.removeEventListener("mousemove", move);
    surface.removeEventListener("mouseup", end);
    markDirty();
  }
  surface.addEventListener("mousemove", move);
  surface.addEventListener("mouseup", end);
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
    if (!kind || !canvas.record) return;
    const rect = surface.getBoundingClientRect();
    const x = Math.round((e.clientX - rect.left + surface.scrollLeft) / 10) * 10;
    const y = Math.round((e.clientY - rect.top + surface.scrollTop) / 10) * 10;
    placeComponent(kind, x, y);
  });
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
    }));
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

function fieldControl(field, value, readOnly, onChange) {
  const attrs = readOnly ? { disabled: "" } : {};
  let input;
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
  fillSelect($("#sys-select"),
    canvas.systems.length
      ? canvas.systems.map((s) => [s.id, `${s.name} (v${s.version})`])
      : [["", "— no systems —"]]);
  if (canvas.systems.length) {
    canvas.systemId = canvas.systems.some((s) => s.id === canvas.systemId)
      ? canvas.systemId : canvas.systems[0].id;
    $("#sys-select").value = canvas.systemId;
    await openSystem(canvas.systemId);
  } else {
    canvas.record = null;
    renderCanvas();
    renderInspector();
  }
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
  $("#role-badge").textContent = canvas.role || "no access";
  updateBadges();
  renderCanvas();
  renderInspector();
  renderValidation(payload.validation);
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

function markDirty() {
  canvas.dirty = true;
  canvas.record.layout.updated_at = new Date().toISOString();
  updateBadges();
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
    await loadWorkspaces();
  });
  $("#ws-select").addEventListener("change", async (e) => {
    canvas.workspaceId = e.target.value;
    await loadSystems();
  });
  $("#sys-select").addEventListener("change", async (e) => {
    if (e.target.value) await openSystem(e.target.value);
  });
  $("#btn-new-system").addEventListener("click", async () => {
    const name = window.prompt("Name for the new agentic system:");
    if (!name) return;
    try {
      const created = await dapi("/systems", {
        method: "POST",
        body: JSON.stringify({ workspace_id: canvas.workspaceId, name }),
      });
      canvas.systemId = created.id;
      await loadSystems();
    } catch (err) { alert(err.message); }
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
  $("#btn-members").addEventListener("click", async () => {
    const workspace = canvas.workspaces.find((w) => w.id === canvas.workspaceId);
    const listing = workspace.members.map((m) => `${m.user_id} — ${m.role}`).join("\n");
    const entry = window.prompt(
      `People in ${workspace.name}:\n${listing}\n\n` +
      "Add or change: user_id role  (e.g. 'bob editor')");
    if (!entry) return;
    const [user_id, role = "viewer"] = entry.trim().split(/\s+/);
    try {
      await dapi(`/workspaces/${canvas.workspaceId}/members`, {
        method: "POST",
        body: JSON.stringify({ user_id, display_name: user_id, role }),
      });
      await loadWorkspaces();
    } catch (err) { alert(err.message); }
  });
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
}

async function initCanvas() {
  await loadPalette();
  wireCanvas();
  await loadWorkspaces();
}
