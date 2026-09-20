/* Agentic Designer — vanilla SPA over the platform API. */
const API = "/api";
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null) continue;
    n.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
  }
  return n;
};

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.status === 204 ? null : res.json();
}

const state = { components: null, agents: [], units: [], selected: null,
  canvasReady: false };

/* ---------------------------------------------------------------- tabs */
$("#tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  showView(btn.dataset.view);
});

function showView(name) {
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.id === `view-${name}`));
  location.hash = `#/${name}`;
  const loaders = {
    org: loadOrg, catalog: loadCatalog, sessions: loadSessions, ops: loadOps,
    platform: loadPlatformCatalog,
    canvas: () => {
      if (window.initCanvas && !state.canvasReady) {
        state.canvasReady = true;
        window.initCanvas();
      }
    },
  };
  (loaders[name] || (() => {}))();
}

window.addEventListener("hashchange", () => {
  const route = location.hash.match(/^#\/(\w+)/)?.[1];
  const session = location.hash.match(/^#\/sessions\/(\S+)/)?.[1];
  if (session) { showView("sessions"); loadTrace(session); }
  else if (route) { showView(route); }
});

/* ----------------------------------------------------------- org chart */
async function loadOrg() {
  const [tree, units, agents] = await Promise.all([
    api("/org/tree"), api("/org/units"), api("/agents"),
  ]);
  state.units = units;
  state.agents = agents;
  const root = $("#orgtree");
  root.replaceChildren(renderTree(tree));
  populateSelectors();
}

function renderTree(nodes) {
  const ul = el("ul");
  for (const n of nodes) {
    const node = el("div", { class: "node", onclick: () => selectAgent(n.id, node) },
      el("span", { class: "nm" }, n.name),
      el("span", { class: "ti" }, n.title || n.kind),
      el("span", { class: "badge" }, n.human || "unassigned"));
    const li = el("li", {}, node);
    if (n.children?.length) li.appendChild(renderTree(n.children));
    ul.appendChild(li);
  }
  return ul;
}

async function selectAgent(agentId, node) {
  document.querySelectorAll(".node.sel").forEach((n) => n.classList.remove("sel"));
  node?.classList.add("sel");
  state.selected = agentId;
  const [agent, harness, sessions] = await Promise.all([
    api(`/agents/${agentId}`), api(`/agents/${agentId}/harness`),
    api(`/sessions?agent_id=${agentId}&limit=5`),
  ]);
  $("#agentdetail").replaceChildren(
    el("div", { class: "statrow" },
      stat("Kind", agent.kind), stat("Tools", harness.tools.length),
      stat("Sessions", sessions.length)),
    el("h3", {}, "Human counterpart"),
    el("p", { class: "hint" }, agent.human
      ? `${agent.human.display_name} · ${agent.human.role_title} · ${agent.human.email}`
      : "none assigned"),
    el("h3", {}, "Composed system prompt"),
    el("pre", { class: "code" }, harness.system_prompt),
    el("h3", {}, "Toolset"),
    el("div", { class: "meta" }, harness.tools.map((t) =>
      el("span", { class: `badge${t.requires_approval ? " warn" : ""}` }, t.name))),
    el("h3", {}, "Sandbox"),
    el("pre", { class: "code" }, JSON.stringify(harness.sandbox, null, 2) || "none"),
    el("h3", {}, "Recent sessions"),
    el("div", { class: "list" }, sessions.map((s) =>
      el("div", {}, el("span", { class: "grow" }, s.title || s.id),
        el("span", { class: "badge" }, s.state),
        el("a", { href: s.url, target: "_blank" }, "open")))),
    el("div", { class: "actions" },
      el("button", { onclick: () => runAgent(agentId) }, "Run a task")),
  );
}

async function runAgent(agentId) {
  const prompt = window.prompt("Task for this agent:");
  if (!prompt) return;
  setStatus("running…");
  const r = await api(`/agents/${agentId}/run`, {
    method: "POST", body: JSON.stringify({ prompt, created_by: "designer-ui" }),
  });
  setStatus(`session ${r.state}`);
  alert(`${r.output}\n\nSession: ${r.session_url}`);
}

/* ------------------------------------------------------------ designer */
async function loadComponents() {
  const c = await api("/components");
  state.components = c;
  $("#palette").replaceChildren(
    ...paletteGroup("Runtimes", c.runtimes.map((r) => [r.name, r.id])),
    ...paletteGroup("Sandbox templates",
      c.sandbox_templates.map((t) => [t.name, `${t.cpu} cpu · ${t.memory} · ${t.network}`])),
    ...paletteGroup("Workflows", c.workflows.map((w) => [w.name, w.description])),
    ...paletteGroup("Plugins", c.plugins.map((p) => [p.name, p.description])),
  );
  fillSelect($("[name=kind]"),
    ["executive", "manager", "individual", "subagent", "service"].map((k) => [k, k]));
  fillSelect($("[name=runtime]"), c.runtimes.map((r) => [r.id, r.name]));
  fillSelect($("[name=sandbox]"),
    [["", "none"], ...c.sandbox_templates.map((t) => [t.id, t.name])]);
  checkboxes($("#pick-planes"), c.data_planes.map((p) => [p.id, `${p.name} — ${p.detail}`]));
  checkboxes($("#pick-skills"), c.skills.map((s) => [s.id, s.name]));
  checkboxes($("#pick-workflows"), c.workflows.map((w) => [w.id, w.name]));
  checkboxes($("#pick-channels"), c.channels.map((ch) => [ch.id, ch.name]));
  $("#infra").replaceChildren(...Object.entries(c.infrastructure).map(([group, items]) =>
    el("div", { class: "group" }, el("h4", {}, group),
      el("ul", {}, items.map((i) => el("li", {},
        el("strong", {}, i.name), ` — ${i.use}`))))));
}

function paletteGroup(title, items) {
  return [el("h4", {}, title),
    ...items.map(([name, detail]) =>
      el("div", { class: "item" }, name, el("small", {}, detail || "")))];
}

function fillSelect(select, pairs) {
  select.replaceChildren(...pairs.map(([v, label]) => el("option", { value: v }, label)));
}

function checkboxes(container, pairs) {
  container.replaceChildren(...pairs.map(([v, label]) =>
    el("label", {}, el("input", { type: "checkbox", value: v }), label)));
}

function populateSelectors() {
  fillSelect($("[name=org_unit_id]"),
    [["", "— none —"], ...state.units.map((u) => [u.id, `${u.name} (${u.kind})`])]);
  fillSelect($("[name=manager_agent_id]"),
    [["", "— top level —"], ...state.agents.map((a) => [a.id, a.name])]);
}

const picked = (id) =>
  [...document.querySelectorAll(`${id} input:checked`)].map((i) => i.value);
const csv = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);

function buildAgentPayload() {
  const f = Object.fromEntries(new FormData($("#agentform")).entries());
  const planes = picked("#pick-planes");
  const groups = csv(f.groups);
  const dataGrants = planes.map((v) => ({
    visibility: v, groups: v === "protected" ? groups : [],
    can_read: true, can_write: true,
  }));
  const relational = f.db_connection ? [{
    connection_name: f.db_connection, engine: f.db_engine,
    dsn_secret_ref: f.db_secret, tables: csv(f.db_tables),
    allowed_statements: csv(f.db_statements).length ? csv(f.db_statements) : ["select"],
    masked_columns: csv(f.db_masked),
  }] : [];
  return {
    name: f.name, title: f.title, kind: f.kind, description: f.description,
    org_unit_id: f.org_unit_id || null,
    manager_agent_id: f.manager_agent_id || null,
    groups,
    human: f.human_email ? {
      user_id: f.human_email, display_name: f.human_name || f.human_email,
      email: f.human_email, role_title: f.human_role,
      approval_required_for: csv(f.approval_required_for),
    } : null,
    harness: {
      runtime: f.runtime, system_prompt: f.system_prompt,
      model: { model: f.model },
      data_grants: dataGrants, relational_grants: relational,
      max_turns: Number(f.max_turns), max_subagent_depth: Number(f.max_subagent_depth),
      token_budget: Number(f.token_budget),
    },
    skill_ids: picked("#pick-skills"),
    workflow_ids: picked("#pick-workflows"),
    channels: picked("#pick-channels").length ? picked("#pick-channels")
      : ["direct_tool_call", "internal_bus"],
    sandbox: f.sandbox ? { template_id: f.sandbox } : null,
  };
}

$("#btn-preview").addEventListener("click", () => {
  $("#preview").textContent = JSON.stringify(buildAgentPayload(), null, 2);
});

$("#agentform").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const created = await api("/agents", {
      method: "POST", body: JSON.stringify(buildAgentPayload()),
    });
    const harness = await api(`/agents/${created.id}/harness`);
    $("#preview").textContent =
      `created ${created.id}\n\n${harness.system_prompt}\n\ntools:\n` +
      harness.tools.map((t) => `  - ${t.name}`).join("\n");
    setStatus(`created ${created.name}`);
    await loadOrg();
  } catch (err) {
    $("#preview").textContent = `error: ${err.message}`;
  }
});

$("#btn-dryrun").addEventListener("click", async () => {
  const payload = buildAgentPayload();
  payload.harness.runtime = "echo";
  try {
    const created = await api("/agents", { method: "POST", body: JSON.stringify(payload) });
    const run = await api(`/agents/${created.id}/run`, {
      method: "POST",
      body: JSON.stringify({ prompt: "Dry run: confirm wiring.", created_by: "designer-ui" }),
    });
    $("#preview").textContent =
      `${run.output}\n\nstate: ${run.state}\nsession: ${run.session_url}`;
  } catch (err) {
    $("#preview").textContent = `error: ${err.message}`;
  }
});

/* ----------------------------------------------------------- catalog */
async function loadCatalog() {
  const q = $("#cat-q").value, kind = $("#cat-kind").value, sort = $("#cat-sort").value;
  const groups = csv($("#cat-groups").value).map((g) => `&groups=${encodeURIComponent(g)}`).join("");
  const [entries, stats] = await Promise.all([
    api(`/catalog?q=${encodeURIComponent(q)}&kind=${kind}&sort=${sort}${groups}`),
    api("/catalog/stats"),
  ]);
  $("#catstats").replaceChildren(
    stat("Listings", stats.total), stat("Installs", stats.installs),
    ...Object.entries(stats.by_kind).map(([k, v]) => stat(k, v)));
  $("#catalog").replaceChildren(...entries.map(catalogCard));
}

function catalogCard(e) {
  return el("div", { class: "card" },
    el("h3", {}, e.name),
    el("div", { class: "meta" },
      el("span", { class: "badge" }, e.kind),
      el("span", { class: "badge" }, `v${e.version}`),
      el("span", { class: "badge" }, e.visibility),
      ...(e.tags || []).map((t) => el("span", { class: "badge" }, t))),
    el("p", {}, e.summary || "No summary."),
    el("p", { class: "hint" },
      `${e.installs} installs · ${e.rating ? e.rating.toFixed(1) : "—"}★ · ${e.owner || "unowned"}`),
    el("div", { class: "actions" },
      el("button", { onclick: () => installEntry(e.id) }, "Install"),
      el("button", { onclick: () => rateEntry(e.id) }, "Rate"),
      el("a", { href: e.url, target: "_blank" }, "Open")));
}

async function installEntry(entryId) {
  const agentId = window.prompt("Install into which agent id?", state.selected || "");
  if (!agentId) return;
  try {
    const r = await api(`/catalog/${entryId}/install`, {
      method: "POST", body: JSON.stringify({ agent_id: agentId }),
    });
    setStatus(`installed ${r.installed}`);
    loadCatalog();
  } catch (err) { alert(err.message); }
}

async function rateEntry(entryId) {
  const stars = Number(window.prompt("Rating 1-5:", "5"));
  if (!stars) return;
  await api(`/catalog/${entryId}/rate?stars=${stars}`, { method: "POST" });
  loadCatalog();
}

["#cat-q", "#cat-kind", "#cat-sort", "#cat-groups"].forEach((sel) =>
  $(sel).addEventListener("input", debounce(loadCatalog, 250)));

function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/* ------------------------------------------------- platform catalog */
async function loadPlatformCatalog() {
  const kinds = await api("/catalogs/kinds");
  const select = $("#pc-kind");
  if (select.options.length <= 1) {
    fillSelect(select, [["", "All kinds"],
      ...kinds.filter((k) => k.count).map((k) => [k.id, `${k.label} (${k.count})`])]);
  }
  const q = $("#pc-q").value;
  const kind = select.value;
  const status = $("#pc-status").value;
  const [entries, stats] = await Promise.all([
    api(`/catalogs?q=${encodeURIComponent(q)}&kind=${kind}&status=${status}`),
    api("/catalogs/stats"),
  ]);
  $("#pc-stats").replaceChildren(
    stat("Entries", stats.total),
    ...Object.entries(stats.by_status).map(([k, v]) => stat(k, v)),
    stat("Unreviewed", stats.unreviewed.length));
  $("#pc-entries").replaceChildren(...entries.map(platformCard));
}

function platformCard(entry) {
  const a = entry.attributes || {};
  const detail = [];
  if (entry.kind === "model") {
    if (a.context_tokens) detail.push(`${(a.context_tokens / 1000).toFixed(0)}k context`);
    if (a.cost_per_million_input != null)
      detail.push(`$${a.cost_per_million_input}/$${a.cost_per_million_output} per M`);
    if (a.regions?.length) detail.push(a.regions.join(", "));
    if (a.trains_on_data) detail.push("trains on data");
  } else if (entry.kind === "mcp_server") {
    detail.push(a.transport || "");
    if (a.tools?.length) detail.push(`${a.tools.length} tools`);
    if (a.read_only) detail.push("read-only");
  } else if (entry.kind === "environment_template") {
    detail.push(a.tier || "", `network: ${a.network || "none"}`);
  } else if (entry.kind === "permission_set") {
    detail.push(`${(a.permissions || []).length} permissions`, `risk: ${a.risk}`);
  }
  const statusClass = { approved: "ok", restricted: "warn", retired: "err",
    rejected: "err", deprecated: "warn" }[entry.status] || "";
  return el("div", { class: "card" },
    el("h3", {}, entry.name),
    el("div", { class: "meta" },
      el("span", { class: "badge" }, entry.kind),
      el("span", { class: `badge ${statusClass}` }, entry.status),
      el("span", { class: "badge" }, `v${entry.version}`),
      ...(entry.entitlement?.groups || []).map((g) =>
        el("span", { class: "badge warn" }, `group: ${g}`))),
    el("p", {}, entry.summary || "No summary."),
    el("p", { class: "hint" }, detail.filter(Boolean).join(" · ") || "—"),
    el("p", { class: "hint" },
      `${entry.owner || "unowned"}${entry.installs ? ` · ${entry.installs} uses` : ""}`),
    el("div", { class: "actions" },
      el("button", { onclick: () => reviewEntry(entry.id, "approved") }, "Approve"),
      el("button", { onclick: () => reviewEntry(entry.id, "restricted") }, "Restrict"),
      el("button", { onclick: () => reviewEntry(entry.id, "retired") }, "Retire")));
}

async function reviewEntry(entryId, status) {
  await api(`/catalogs/${entryId}/review?status=${status}`, { method: "POST" });
  setStatus(`marked ${status}`);
  loadPlatformCatalog();
}

["#pc-q", "#pc-kind", "#pc-status"].forEach((sel) =>
  $(sel).addEventListener("input", debounce(loadPlatformCatalog, 250)));

/* ---------------------------------------------------------- sessions */
async function loadSessions() {
  const sessions = await api("/sessions?limit=100");
  $("#sessions").replaceChildren(...sessions.map((s) =>
    el("div", {},
      el("span", { class: "grow", onclick: () => loadTrace(s.id), style: "cursor:pointer" },
        s.title || s.id),
      el("span", { class: "badge" }, s.state),
      el("span", { class: "badge" }, `${s.turn_count} turns`),
      el("a", { href: s.url, target: "_blank" }, s.id))));
}

async function loadTrace(sessionId) {
  const trace = await api(`/sessions/${sessionId}/trace`);
  $("#trace").replaceChildren(renderTrace(trace));
}

function renderTrace(t) {
  return el("div", { class: "trace-node" },
    el("div", {}, el("strong", {}, t.session.title || t.session.id),
      " ", el("span", { class: "badge" }, t.session.state),
      " ", el("a", { href: t.url, target: "_blank" }, "url")),
    ...t.events.map((e) => el("div", { class: "t" },
      `${e.ts.slice(11, 19)}  ${e.type}  ${JSON.stringify(e.payload).slice(0, 160)}`)),
    ...t.children.map(renderTrace));
}

/* --------------------------------------------------------------- ops */
async function loadOps() {
  const [m, alerts] = await Promise.all([api("/ops/metrics"), api("/ops/alerts")]);
  $("#metrics").replaceChildren(
    stat("Agents", m.agents), stat("Sessions", m.sessions.total),
    stat("Running", m.sessions.running), stat("Waiting human", m.sessions.waiting_human),
    stat("Failed", m.sessions.failed), stat("Tokens", m.tokens),
    stat("Cost (USD)", m.cost_usd.toFixed(2)));
  $("#alerts").replaceChildren(...(alerts.length ? alerts.map((a) =>
    el("div", {}, el("span", { class: `badge ${a.severity === "info" ? "ok" : "err"}` }, a.severity),
      el("span", { class: "grow" }, `${a.title} — ${a.detail}`),
      el("button", {
        onclick: async () => { await api(`/ops/alerts/${a.id}/ack`, { method: "POST" }); loadOps(); },
      }, "Ack"))) : [el("div", { class: "empty" }, "No open alerts.")]));
  $("#peragent").replaceChildren(...Object.entries(m.per_agent).map(([id, v]) =>
    el("div", {}, el("span", { class: "grow" }, id),
      el("span", { class: "badge" }, `${v.sessions} sessions`),
      el("span", { class: "badge" }, `${v.tokens} tokens`))));
}

/* ------------------------------------------------------------ helpers */
function stat(k, v) {
  return el("div", { class: "stat" }, el("div", { class: "v" }, String(v)),
    el("div", { class: "k" }, k));
}
function setStatus(text) { $("#status").textContent = text; }

/* -------------------------------------------------------------- boot */
(async function boot() {
  try {
    await loadComponents();
    await loadOrg();
    const route = location.hash.match(/^#\/(\w+)/)?.[1];
    const session = location.hash.match(/^#\/sessions\/(\S+)/)?.[1];
    if (session) { showView("sessions"); loadTrace(session); }
    else showView(route || "org");
    setStatus("ready");
  } catch (err) {
    setStatus(`error: ${err.message}`);
  }
})();
