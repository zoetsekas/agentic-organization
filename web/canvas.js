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
  decision: "decisions",
  separation: "separations",
  person: "people",
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
  delete canvas.record.layout.nodes[id];
  canvas.record.layout.edges = canvas.record.layout.edges.filter(
    (e) => e.source !== id && e.target !== id);
}

/* --------------------------------------------------------------- palette */
async function loadPalette() {
  canvas.palette = await dapi("/palette");
  const root = $("#palette-groups");
  root.replaceChildren(
    ...canvas.palette.groups.map((group) => el("div", { class: "group" },
      el("h4", {}, group.label),
      ...group.kinds.map((kind) =>
        el("div", {
          class: "drag-item", draggable: "true",
          ondragstart: (e) => {
            e.dataTransfer.setData("text/kind", kind.kind);
            e.dataTransfer.effectAllowed = "copy";
          },
        }, el("span", { class: "ic" }, kind.icon || "▫"), kind.label)))),
    el("p", { class: "note" },
      "Dropping near a team joins it. Edges are derived from the spec, "
      + "never drawn by hand."));
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
  renderRegions();
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
    line.setAttribute("stroke-width", "1.5");
    /* An edge says which kind of relation it is by how it is drawn: a solid
       reporting line, a dashed mission peer that expires with the mission, a
       dotted amber egress that leaves the boundary. */
    const style = EDGE_STYLES[edge.kind] || EDGE_STYLES.member_of;
    line.setAttribute("stroke", `var(${style.stroke})`);
    if (style.dash) line.setAttribute("stroke-dasharray", style.dash);
    parts.push(line);
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
  const layout = canvas.record?.layout;
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
      const box = document.querySelector(`#canvas-nodes [data-id="${node.id}"] .n-title`);
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

function renderValidation(validation) {
  const host = $("#validation");
  renderValidationStrip(validation);
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
  markDirty,
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
