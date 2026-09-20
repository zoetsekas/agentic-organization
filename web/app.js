/* Agentic Designer — vanilla SPA over the platform API.

   Two kinds of view live here, and the difference is the point:

   - *Design* views (org chart, agent editor, workspace) read and write the
     System Spec open in the designer — the same `record.spec` the canvas
     mutates, owned by canvas.js and reached through `window.designer`. There
     is one document in the browser, so editing an agent here and moving its
     box on the canvas are the same edit (ADR-0004, WS-009 M3).
   - *Runtime* views (sessions, operations) read the running system through
     `/sessions` and `/ops`. Those are observations, not design, and unifying
     them onto the spec would make them lie. The marketplace and the platform
     catalog are platform facts, not per-design ones, and stay where they are.
*/
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

/* Runtime-view state only. The design state lives in one place — the open
   record in canvas.js — and is reached through `window.designer`. */
const state = { whoami: null, selected: null, agentId: null };

const design = () => window.designer;
const openSpec = () => (design() ? design().spec() : null);
/* One saved design holds exactly one organisation, so the empty state is about
   the organisation rather than about a "system"; the wire keeps the older word
   ("No system open" was this copy, and /api/designer/systems is the route). */
const NO_SYSTEM = "No organisation open. Choose or create one above.";

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
    org: renderOrg, designer: renderAgentView, workspace: loadWorkspaceView,
    catalog: loadCatalog, sessions: loadSessions, ops: loadOps,
    platform: loadPlatformCatalog,
  };
  (loaders[name] || (() => {}))();
}

function activeView() {
  return document.querySelector(".view.active")?.id.replace("view-", "");
}

window.addEventListener("hashchange", () => {
  const route = location.hash.match(/^#\/(\w+)/)?.[1];
  const session = location.hash.match(/^#\/sessions\/(\S+)/)?.[1];
  if (session) { showView("sessions"); loadTrace(session); }
  else if (route) { showView(route); }
});

/* The open design changed under us — because the canvas moved something, the
   user switched system, or a save came back. Re-render what is showing. */
document.addEventListener("designer:changed", async (e) => {
  const reason = e.detail?.reason;
  if (reason === "identity") await loadWhoami();
  renderIdentity();
  renderOrg();
  if (reason === "opened") { state.agentId = null; renderAgentView(); }
  else renderAgentSide();
  if (activeView() === "workspace") loadWorkspaceView();
});

/* ------------------------------------------------------------- identity */
async function loadWhoami() {
  try {
    state.whoami = await design().dapi("/whoami");
  } catch (err) {
    state.whoami = null;
    setStatus(`identity: ${err.message}`);
  }
}

/* The membership that decides what this viewer may do here. A refusal is only
   understandable if the role behind it is on screen. */
function membership() {
  const workspaceId = design()?.state.workspaceId;
  return (state.whoami?.workspaces || []).find((m) => m.workspace_id === workspaceId)
    || null;
}

function renderIdentity() {
  const who = state.whoami;
  $("#whoami").textContent = who
    ? `${who.display_name || who.user_id}${who.email ? ` · ${who.email}` : ""}`
    : "not identified";
  const m = membership();
  const badge = $("#role-badge");
  badge.textContent = m ? `${m.role} in ${m.workspace}` : "no role in this workspace";
  badge.className = `badge ${m ? "ok" : "warn"}`;
}

const may = (permission) => (membership()?.permissions || []).includes(permission);

/* ------------------------------------- the organisation being designed */
/* One design = one organisation (`SystemSpec.organization`). The selector here
   and the one in the context bar are filled by canvas.js from the same open
   id, so switching in either switches both. Everything destructive is gated on
   the permission the API reports, and every refusal is shown in the API's own
   words. */

function orgMetadata() {
  const s = openSpec();
  return s ? (s.metadata = s.metadata || {}) : null;
}

function renderOrgToolbar() {
  const d = design();
  const record = d?.state.record;
  const canEdit = !!d && d.canEdit();
  const blocked = d ? d.lockedByOther() : null;
  $("#org-version").textContent = record ? `v${record.version}` : "v—";
  const lock = $("#org-lock");
  lock.textContent = blocked
    ? `locked by ${blocked.holder_name || blocked.holder}` : "";
  lock.className = `badge ${blocked ? "warn" : ""}`;
  // A viewer, or someone else's lock, is not offered a control that would be
  // refused: the gate is the permission the API reported when it opened.
  $("#btn-org-new").disabled = !d;
  $("#btn-org-edit").disabled = !record || !canEdit || !!blocked;
  $("#btn-org-duplicate").disabled = !record || !canEdit;
  $("#btn-org-delete").disabled = !record || !canEdit || !!blocked;
}

function labelsToText(labels) {
  return Object.entries(labels || {}).map(([k, v]) => `${k}=${v}`).join("\n");
}

function labelsFromText(text) {
  const out = {};
  for (const line of (text || "").split("\n")) {
    const [key, ...rest] = line.split("=");
    if (key.trim()) out[key.trim()] = rest.join("=").trim();
  }
  return out;
}

function showOrgForm(mode) {
  const form = $("#orgform");
  form.dataset.mode = mode;
  $("#orgform-error").textContent = "";
  $("#org-error").textContent = "";
  const record = design()?.state.record;
  const metadata = mode === "edit" ? (orgMetadata() || {}) : {};
  $("#orgform-title").textContent = mode === "edit"
    ? `Edit ${record?.name || "this organisation"}` : "New organisation";
  $("#btn-orgform-save").textContent = mode === "edit" ? "Save changes" : "Create";
  form.elements.name.value = mode === "edit"
    ? (record?.name || metadata.name || "") : "";
  form.elements.description.value = mode === "edit"
    ? (record?.description || metadata.description || "") : "";
  form.elements.owner.value = metadata.owner || "";
  form.elements.environment.value = metadata.environment || "development";
  form.elements.labels.value = labelsToText(metadata.labels);
  form.hidden = false;
  form.elements.name.focus();
}

function hideOrgForm() {
  $("#orgform").hidden = true;
}

function orgFormValues() {
  const form = $("#orgform");
  return {
    name: form.elements.name.value.trim(),
    description: form.elements.description.value.trim(),
    owner: form.elements.owner.value.trim(),
    environment: form.elements.environment.value,
    labels: labelsFromText(form.elements.labels.value),
  };
}

/* Create: the API makes the record and its starter spec; the rest of the
   metadata the user gave is written straight after, through the same PUT that
   every other edit uses. */
async function createOrganisation(values) {
  const d = design();
  const created = await d.dapi("/systems", {
    method: "POST",
    body: JSON.stringify({
      workspace_id: d.state.workspaceId,
      name: values.name,
      description: values.description,
    }),
  });
  d.state.systemId = created.id;
  await d.reloadSystems();
  applyOrgMetadata(values);
  await d.save();
}

function applyOrgMetadata(values) {
  const metadata = orgMetadata();
  if (!metadata) return;
  metadata.name = values.name;
  metadata.description = values.description;
  metadata.owner = values.owner;
  metadata.environment = values.environment;
  metadata.labels = values.labels;
  const record = design().state.record;
  record.name = values.name;
  record.description = values.description;
  // The organisation node carries the name people read on the chart.
  if (record.spec.organization && !record.spec.organization.name)
    record.spec.organization.name = values.name;
  design().markDirty("organisation");
}

/* Duplicate: there is no server-side copy route, and none is needed — the open
   record's spec and layout are everything a new one needs. */
async function duplicateOrganisation() {
  const d = design();
  const systemId = d.state.systemId;
  const source = await d.dapi(`/systems/${systemId}`);
  const record = source.record;
  const copyName = `${record.name} (copy)`;
  const spec = JSON.parse(JSON.stringify(record.spec));
  spec.metadata = spec.metadata || {};
  spec.metadata.name = copyName;
  const created = await d.dapi("/systems", {
    method: "POST",
    body: JSON.stringify({
      workspace_id: record.workspace_id,
      name: copyName,
      description: record.description,
      spec,
    }),
  });
  // Create takes no layout, so the copy's boxes are placed by the save that
  // follows it; otherwise the duplicate would open on an empty canvas.
  await d.dapi(`/systems/${created.id}`, {
    method: "PUT",
    body: JSON.stringify({
      layout: JSON.parse(JSON.stringify(record.layout)),
      base_version: created.version,
      strategy: "merge",
      message: `duplicated from ${record.name}`,
    }),
  });
  d.state.systemId = created.id;
  await d.reloadSystems();
}

async function deleteOrganisation() {
  const d = design();
  const record = d.state.record;
  if (!record) return;
  if (!window.confirm(
    `Delete the organisation "${record.name}"?\n\n`
    + "Its spec, layout and revision history go with it. This cannot be undone."))
    return;
  try {
    await d.dapi(`/systems/${d.state.systemId}`, { method: "DELETE" });
    d.state.systemId = null;
    await d.reloadSystems();
    $("#org-error").textContent = "";
  } catch (err) {
    // Whatever refused — a lock, a role — says so better than we could.
    $("#org-error").textContent = err.message;
  }
}

function wireOrgView() {
  $("#btn-org-new").addEventListener("click", () => showOrgForm("create"));
  // The context bar's create button is the same affordance, not a second one.
  $("#btn-new-system").addEventListener("click", () => {
    showView("org");
    showOrgForm("create");
  });
  $("#btn-org-edit").addEventListener("click", () => showOrgForm("edit"));
  $("#btn-org-duplicate").addEventListener("click", async () => {
    $("#org-error").textContent = "";
    try {
      await duplicateOrganisation();
    } catch (err) {
      $("#org-error").textContent = err.message;
    }
  });
  $("#btn-org-delete").addEventListener("click", deleteOrganisation);
  $("#btn-orgform-cancel").addEventListener("click", hideOrgForm);
  $("#orgform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const values = orgFormValues();
    if (!values.name) return;
    try {
      if (e.target.dataset.mode === "edit") {
        applyOrgMetadata(values);
        await design().save();
      } else {
        await createOrganisation(values);
      }
      hideOrgForm();
    } catch (err) {
      $("#orgform-error").textContent = err.message;
    }
  });
}

/* --------------------------------------------- org chart, from the spec */
function renderOrg() {
  const root = $("#orgtree");
  const s = openSpec();
  renderOrgToolbar();
  if (!s) {
    root.replaceChildren(el("div", { class: "empty" }, NO_SYSTEM,
      el("div", { class: "actions" },
        el("button", { class: "primary", onclick: () => showOrgForm("create") },
          "Create an organisation"))));
    showDetail(el("div", {}, NO_SYSTEM), true);
    return;
  }
  const selected = state.selected
    && design().find(state.selected.kind, state.selected.id);
  if (!selected) state.selected = null;
  root.replaceChildren(renderTeam(s.organization));
  showDetail(selected
    ? (state.selected.kind === "team"
      ? teamDetail(selected)
      : agentDetail(selected, state.selected.id))
    : el("div", {}, "Select a team or an agent."), !selected);
}

function renderTeam(team) {
  if (!team) return el("ul");
  const node = el("div", {
    class: `node${state.selected?.id === team.id ? " sel" : ""}`,
    onclick: () => selectOrgNode("team", team.id),
  },
    el("span", { class: "nm" }, team.name || team.id),
    el("span", { class: "ti" }, "team"),
    el("span", { class: "badge" }, `leader: ${team.leader || "none"}`));
  const children = el("ul", {},
    ...(team.members || []).map((agent) => el("li", {}, agentNode(agent))),
    ...(team.teams || []).map((child) => el("li", {}, renderTeam(child))));
  return el("ul", {}, el("li", {}, node, children));
}

function agentNode(agent) {
  const owner = (agent.humans || []).find((h) => (h.roles || []).includes("owner"));
  return el("div", {
    class: `node${state.selected?.id === agent.id ? " sel" : ""}`,
    onclick: () => selectOrgNode("agent", agent.id),
  },
    el("span", { class: "nm" }, agent.name || agent.id),
    el("span", { class: "ti" }, roleIds(agent.roles).join(", ") || "agent"),
    el("span", { class: "badge" }, owner ? owner.name : "unassigned"));
}

/* A role may be written as an id or as an assignment object; both are valid. */
const roleIds = (roles) =>
  (roles || []).map((r) => (typeof r === "string" ? r : r.role)).filter(Boolean);

function showDetail(content, empty = false) {
  const host = $("#agentdetail");
  host.className = empty ? "empty" : "";
  host.replaceChildren(content);
}

function selectOrgNode(kind, id) {
  state.selected = { kind, id };
  renderOrg();
}

function teamDetail(team) {
  return el("div", {},
    el("div", { class: "statrow" },
      stat("Members", (team.members || []).length),
      stat("Sub-teams", (team.teams || []).length),
      stat("Roles", roleIds(team.roles).length)),
    el("h3", {}, "Leader"),
    el("p", { class: "hint" }, team.leader || "none named"),
    el("h3", {}, "Mandate"),
    el("ul", {}, (team.mandate || []).map((m) => el("li", {}, m))),
    el("h3", {}, "Shared instructions"),
    el("ul", {}, (team.shared_instructions || []).map((i) => el("li", {}, i))),
    el("h3", {}, "Groups"),
    el("div", { class: "meta" },
      (team.groups || []).map((g) => el("span", { class: "badge" }, g))));
}

function agentDetail(agent, id) {
  const team = design().agents().find((a) => a.agent.id === id)?.team;
  return el("div", {},
    el("div", { class: "statrow" },
      stat("Capabilities", (agent.capabilities || []).length),
      stat("Humans", (agent.humans || []).length),
      stat("Sub-agents", (agent.subagents || []).length)),
    el("h3", {}, "Team"),
    el("p", { class: "hint" },
      `${team ? team.name || team.id : "—"}${team && team.leader === id ? " · leads it" : ""}`),
    el("h3", {}, "Description"),
    el("p", { class: "hint" }, agent.description || "none"),
    el("h3", {}, "Roles"),
    el("div", { class: "meta" },
      roleIds(agent.roles).map((r) => el("span", { class: "badge" }, r))),
    el("h3", {}, "Capabilities"),
    el("div", { class: "meta" }, (agent.capabilities || []).map((c) =>
      el("span", { class: "badge" }, c))),
    el("h3", {}, "Human counterparts"),
    el("div", { class: "list" }, (agent.humans || []).map((h) =>
      el("div", {},
        el("span", { class: "grow" }, `${h.name} · ${h.contact}`),
        ...(h.roles || []).map((r) => el("span", { class: "badge" }, r)),
        ...(h.approves || []).map((a) =>
          el("span", { class: "badge warn" }, `approves ${a}`))))),
    el("h3", {}, "Sub-agents"),
    el("div", { class: "list" }, (agent.subagents || []).map((sub) =>
      el("div", {}, el("span", { class: "grow" }, sub.name || sub.id),
        el("span", { class: "badge" }, sub.kind || "custom")))),
    el("h3", {}, "Environment"),
    el("p", { class: "hint" }, agent.environment || "inherited"),
    el("div", { class: "actions" },
      el("button", { onclick: () => editAgent(id) }, "Edit this agent")));
}

/* -------------------------------------------- agent editor, on the spec */
function editAgent(id) {
  state.agentId = id;
  showView("designer");
}

function currentAgent() {
  return state.agentId ? design().find("agent", state.agentId) : null;
}

function renderAgentView() {
  renderAgentSide();
  const form = $("#agentform");
  const agent = currentAgent();
  const s = openSpec();
  $("#agentform-empty").hidden = !!agent;
  $("#agentform-empty").textContent = s
    ? "Select an agent, or add one."
    : NO_SYSTEM;
  form.hidden = !agent;
  $("#btn-new-agent").disabled = !s || !design().canEdit();
  if (!agent) { $("#preview").textContent = s ? "Select an agent." : NO_SYSTEM; return; }

  const { team } = design().agents().find((a) => a.agent.id === state.agentId) || {};
  form.elements.id.value = agent.id;
  form.elements.name.value = agent.name || "";
  form.elements.description.value = agent.description || "";
  fillSelect(form.elements.team_id,
    design().teams().map((t) => [t.id, t.name || t.id]));
  form.elements.team_id.value = team ? team.id : "";
  form.elements.leader.checked = !!team && team.leader === agent.id;
  form.elements.shared_service.checked = !!agent.shared_service;
  form.elements.max_delegation_depth.value = agent.max_delegation_depth ?? 3;
  form.elements.humans.value = (agent.humans || []).map((h) =>
    [h.name, h.contact, (h.roles || []).join(","), (h.approves || []).join(",")]
      .join(" | ")).join("\n");

  /* Every binding is chosen from what this spec declares: an agent cannot
     reach a capability the document does not define. */
  bind("#pick-roles", s.roles, roleIds(agent.roles));
  bind("#pick-capabilities", s.capabilities, agent.capabilities);
  bind("#pick-knowledge", s.knowledge, agent.knowledge);
  bind("#pick-workflows", s.workflows, agent.workflows);
  bind("#pick-endpoints", s.endpoints, agent.endpoints);
  bind("#pick-skills", s.skills, agent.skills);
  bind("#pick-plugins", s.plugins, agent.plugins);
  checkboxes($("#pick-channels"), channelClasses().map((c) => [c, c]));
  check("#pick-channels", agent.channels || []);
  fillSelect(form.elements.environment,
    [["", "— inherited —"], ...(s.environments || []).map((e2) => [e2.id, e2.id])]);
  form.elements.environment.value = agent.environment || "";
  fillSelect(form.elements.artifact_store,
    [["", "— none —"], ...(s.artifact_stores || []).map((a) => [a.id, a.id])]);
  form.elements.artifact_store.value = agent.artifact_store || "";
  fillSelect(form.elements.output_contract,
    [["", "— none —"], ...(s.output_contracts || []).map((o) => [o.id, o.id])]);
  form.elements.output_contract.value = agent.output_contract || "";

  const readOnly = !design().canEdit();
  [...form.elements].forEach((input) => {
    if (input.name !== "id") input.disabled = readOnly;
  });
  form.elements.id.readOnly = true;
  renderAgentPreview();
}

/* The channel vocabulary comes from the palette, which the backend derives
   from the spec model — never a list kept here. */
function channelClasses() {
  const field = (design().kindSpec("channel").fields || [])
    .find((f) => f.name === "channel_class");
  return field?.options || [];
}

function bind(selector, collection, chosen) {
  checkboxes($(selector), (collection || []).map((item) =>
    [item.id, item.title || item.name || item.id]));
  check(selector, chosen || []);
}

function check(selector, values) {
  document.querySelectorAll(`${selector} input`).forEach((input) => {
    input.checked = values.includes(input.value);
  });
}

function renderAgentSide() {
  const s = openSpec();
  $("#agent-list").replaceChildren(...(s
    ? design().agents().map(({ agent, team }) =>
      el("div", { class: agent.id === state.agentId ? "sel" : "" },
        el("span", { class: "grow", style: "cursor:pointer",
          onclick: () => { state.agentId = agent.id; renderAgentView(); } },
        agent.name || agent.id),
        el("span", { class: "badge" }, team.name || team.id)))
    : [el("div", { class: "empty" }, NO_SYSTEM)]));
  $("#spec-inventory").replaceChildren(...(s
    ? [["roles", s.roles], ["capabilities", s.capabilities],
      ["data classes", s.data_classes], ["environments", s.environments],
      ["knowledge", s.knowledge], ["workflows", s.workflows],
      ["channels", s.channels], ["triggers", s.triggers],
      ["endpoints", s.endpoints], ["skills", s.skills], ["plugins", s.plugins],
      ["memory namespaces", s.memory?.namespaces]].map(([label, items]) =>
      el("div", {}, el("span", { class: "grow" }, label),
        el("span", { class: "badge" }, String((items || []).length))))
    : []));
  renderValidationInto($("#agent-validation"), design()?.state.validation);
}

function renderValidationInto(host, validation) {
  if (!host) return;
  if (!validation) return host.replaceChildren();
  host.replaceChildren(
    el("h4", {}, validation.ok ? "Valid" : "Not yet valid"),
    el("ul", {},
      ...validation.errors.map((e) => el("li", { class: "v-err" }, e)),
      ...validation.warnings.slice(0, 5).map((w) => el("li", { class: "v-warn" }, w))));
}

function renderAgentPreview() {
  const agent = currentAgent();
  $("#preview").textContent = agent
    ? JSON.stringify(agent, null, 2)
    : "Select an agent.";
}

const picked = (id) =>
  [...document.querySelectorAll(`${id} input:checked`)].map((i) => i.value);
const csv = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);

function parseHumans(text) {
  return (text || "").split("\n").filter((line) => line.trim()).map((line) => {
    const [name, contact, roles, approves] = line.split("|").map((x) => (x || "").trim());
    return {
      name, contact: contact || "",
      roles: csv(roles).length ? csv(roles) : ["owner"],
      approves: csv(approves),
    };
  });
}

/* One handler for the whole form: the spec is the model, the form is a view of
   it, and writing every field back on each keystroke keeps them identical. */
function applyAgentForm() {
  const agent = currentAgent();
  if (!agent || !design().canEdit()) return;
  const form = $("#agentform");
  agent.name = form.elements.name.value;
  agent.description = form.elements.description.value;
  agent.shared_service = form.elements.shared_service.checked;
  agent.max_delegation_depth = Number(form.elements.max_delegation_depth.value) || 3;
  agent.humans = parseHumans(form.elements.humans.value);
  agent.roles = picked("#pick-roles");
  agent.capabilities = picked("#pick-capabilities");
  agent.knowledge = picked("#pick-knowledge");
  agent.workflows = picked("#pick-workflows");
  agent.endpoints = picked("#pick-endpoints");
  agent.skills = picked("#pick-skills");
  agent.plugins = picked("#pick-plugins");
  agent.channels = picked("#pick-channels");
  agent.environment = form.elements.environment.value || null;
  agent.artifact_store = form.elements.artifact_store.value || null;
  agent.output_contract = form.elements.output_contract.value || null;
  moveAgent(agent, form.elements.team_id.value, form.elements.leader.checked);
  design().markDirty();
  renderAgentPreview();
  renderAgentSide();
  renderOrg();
}

/* Reporting structure is a design fact: changing the team here is the same
   edit as dragging the box onto another team on the canvas. */
function moveAgent(agent, teamId, leads) {
  const entry = design().agents().find((a) => a.agent.id === agent.id);
  const from = entry?.team;
  const to = design().teams().find((t) => t.id === teamId);
  if (!to) return;
  if (from && from !== to) {
    from.members = (from.members || []).filter((m) => m.id !== agent.id);
    if (from.leader === agent.id) from.leader = (from.members[0] || {}).id || "";
    to.members = [...(to.members || []), agent];
  }
  if (leads) to.leader = agent.id;
  else if (to.leader === agent.id) to.leader = (to.members.find((m) =>
    m.id !== agent.id) || {}).id || "";
}

function wireAgentEditor() {
  const form = $("#agentform");
  form.addEventListener("submit", (e) => e.preventDefault());
  form.addEventListener("input", applyAgentForm);
  form.addEventListener("change", applyAgentForm);
  $("#btn-new-agent").addEventListener("click", () => {
    const id = window.prompt("Id for the new agent:");
    if (!id) return;
    try {
      design().add("agent", id);
      design().markDirty();
      state.agentId = id;
      renderAgentView();
      renderOrg();
    } catch (err) { setStatus(err.message); }
  });
  $("#btn-agent-save").addEventListener("click", () => design().save());
  $("#btn-agent-remove").addEventListener("click", () => {
    const agent = currentAgent();
    if (!agent || !window.confirm(`Remove ${agent.id} from this organisation?`)) return;
    design().remove("agent", agent.id);
    design().markDirty();
    state.agentId = null;
    renderAgentView();
    renderOrg();
  });
}

/* ------------------------------ workspace: membership and the audit log */
async function loadWorkspaceView() {
  renderMembers();
  await loadAudit();
}

function renderMembers() {
  const d = design();
  const workspace = d.state.workspaces.find((w) => w.id === d.state.workspaceId);
  const host = $("#members");
  const canManage = may("workspace.members");
  const role = membership()?.role;
  $("#members-hint").textContent = canManage
    ? "Add someone, or change the role they hold here."
    : `Your role (${role || "none"}) does not manage membership; this list is`
      + " read-only.";
  $("#memberform").hidden = !canManage;
  $("#members-error").textContent = "";
  host.replaceChildren(...((workspace?.members || []).map((m) =>
    el("div", {},
      el("span", { class: "grow" }, `${m.display_name || m.user_id} (${m.user_id})`),
      el("span", { class: "badge" }, m.role),
      ...(canManage ? [el("button", { onclick: () => removeMember(m.user_id) },
        "Remove")] : []))) || []));
}

async function removeMember(userId) {
  const d = design();
  try {
    await d.dapi(`/workspaces/${d.state.workspaceId}/members/${userId}`,
      { method: "DELETE" });
    await d.reloadWorkspaces();
    loadWorkspaceView();
  } catch (err) {
    // The API's own words: it knows which role refused and why, and we do not.
    $("#members-error").textContent = err.message;
  }
}

function wireWorkspaceView() {
  $("#memberform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = design();
    const f = Object.fromEntries(new FormData(e.target).entries());
    try {
      await d.dapi(`/workspaces/${d.state.workspaceId}/members`, {
        method: "POST",
        body: JSON.stringify({
          user_id: f.user_id, display_name: f.display_name || f.user_id,
          email: f.email, role: f.role,
        }),
      });
      e.target.reset();
      await d.reloadWorkspaces();
      loadWorkspaceView();
    } catch (err) {
      $("#members-error").textContent = err.message;
    }
  });
  $("#btn-audit-reload").addEventListener("click", loadAudit);
  ["#audit-system", "#audit-actor", "#audit-action"].forEach((sel) =>
    $(sel).addEventListener("input", debounce(loadAudit, 250)));
}

async function loadAudit() {
  const d = design();
  const systems = d.state.systems || [];
  const select = $("#audit-system");
  const chosen = select.value;
  fillSelect(select, [["", "All organisations"], ...systems.map((s) => [s.id, s.name])]);
  select.value = chosen;
  const query = new URLSearchParams({ limit: "100" });
  if (chosen) query.set("system_id", chosen);
  if ($("#audit-actor").value) query.set("actor", $("#audit-actor").value);
  if ($("#audit-action").value) query.set("action", $("#audit-action").value);
  try {
    const events = await d.dapi(`/audit?${query}`);
    $("#audit-error").textContent = "";
    $("#audit").replaceChildren(...(events.length
      ? events.map(auditRow)
      : [el("div", { class: "empty" }, "Nothing recorded for this filter.")]));
  } catch (err) {
    $("#audit").replaceChildren();
    $("#audit-error").textContent = err.message;
  }
}

/* A refusal reads differently from a change, because it is the entry a
   reviewer is looking for. */
function auditRow(event) {
  const denied = event.outcome === "denied";
  return el("div", { class: denied ? "denied" : "" },
    el("span", { class: `badge ${denied ? "err" : "ok"}` }, event.outcome),
    el("span", { class: "grow" },
      `${event.timestamp.slice(0, 19).replace("T", " ")} · ${event.actor_name || event.actor} · ${event.action}`),
    event.system_id ? el("span", { class: "badge" }, event.system_id) : null,
    event.permission ? el("span", { class: "badge warn" }, event.permission) : null,
    event.reason ? el("span", { class: "hint" }, event.reason) : null);
}

function fillSelect(select, pairs) {
  select.replaceChildren(...pairs.map(([v, label]) => el("option", { value: v }, label)));
}

function checkboxes(container, pairs) {
  container.replaceChildren(...pairs.map(([v, label]) =>
    el("label", {}, el("input", { type: "checkbox", value: v }), label)));
}

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
  // A runtime agent id: the marketplace installs into the running system, not
  // into the spec being edited.
  const agentId = window.prompt("Install into which runtime agent id?");
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
let pcKinds = [];

async function loadPlatformCatalog() {
  const kinds = await api("/catalogs/kinds");
  pcKinds = kinds;
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
  const approvedish = entry.status === "approved" || entry.status === "restricted";
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
      el("button", { onclick: () => openEntryForm(entry.id) }, "Edit"),
      approvedish
        ? el("button", { onclick: () => sendBackEntry(entry.id) }, "Send back")
        : null,
      el("button", { onclick: () => retireEntry(entry.id) }, "Retire"),
      entry.status === "proposed" && !entry.reviewed_at
        ? el("button", { onclick: () => deleteEntry(entry.id, entry.name) },
            "Delete draft")
        : null));
}

async function reviewEntry(entryId, status) {
  await api(`/catalogs/${entryId}/review?status=${status}`, { method: "POST" });
  setStatus(`marked ${status}`);
  loadPlatformCatalog();
}

async function retireEntry(entryId) {
  /* Retirement is refused while designs reference the entry; the service says
     who, and forcing it is a second, explicit answer. */
  try {
    await api(`/catalogs/${entryId}/retire`, { method: "POST" });
  } catch (e) {
    if (!confirm(`${e.message}\n\nRetire anyway and break them?`)) return;
    await api(`/catalogs/${entryId}/retire?force=true`, { method: "POST" });
  }
  setStatus("retired");
  loadPlatformCatalog();
}

async function sendBackEntry(entryId) {
  const note = prompt(
    "Send back for review. The entry stops being selectable immediately, and "
    + "any design bound to it fails at the next compile. Why?", "");
  if (note === null) return;
  await api(`/catalogs/${entryId}/send_back`, {
    method: "POST", body: JSON.stringify({ note }),
  });
  setStatus("sent back for review — now unselectable");
  loadPlatformCatalog();
}

async function deleteEntry(entryId, name) {
  if (!confirm(`Delete the draft '${name}'? Only a never-approved, unused `
    + "entry can be deleted; anything else retires.")) return;
  try {
    await api(`/catalogs/${entryId}`, { method: "DELETE" });
    setStatus("draft deleted");
  } catch (e) {
    setStatus(e.message);
  }
  loadPlatformCatalog();
}

/* -------------------------------------------- one generated entry form

   The form is built from the kind's declared attributes (`/catalogs/kinds`
   carries them), so all twelve kinds share one form and a field added to an
   attribute model appears here without this file changing. */

const PC_EDITORIAL = [
  ["name", "Name"], ["summary", "Summary"], ["description", "Description"],
  ["owner", "Owner"], ["tags", "Tags (comma separated)"],
  ["documentation_url", "Documentation URL"],
];

function attributeInput(field, value) {
  if (field.type === "choice") {
    const sel = el("select", { id: `pc-a-${field.name}` });
    fillSelect(sel, field.choices.map((c) => [c, c]));
    sel.value = value ?? field.choices[0];
    return sel;
  }
  if (field.type === "boolean") {
    const box = el("input", { id: `pc-a-${field.name}`, type: "checkbox" });
    box.checked = !!value;
    return box;
  }
  const type = (field.type === "integer" || field.type === "number")
    ? "number" : "text";
  const text = field.type === "list" ? (value || []).join(", ")
    : field.type === "objects" ? JSON.stringify(value ?? [])
    : (value ?? "");
  return el("input", { id: `pc-a-${field.name}`, type, value: text });
}

function readAttributes(fields) {
  const out = {};
  for (const field of fields) {
    const node = $(`#pc-a-${field.name}`);
    if (!node) continue;
    if (field.type === "boolean") out[field.name] = node.checked;
    else if (field.type === "list") {
      out[field.name] = node.value.split(",").map((v) => v.trim()).filter(Boolean);
    } else if (field.type === "objects") {
      try { out[field.name] = JSON.parse(node.value || "[]"); }
      catch { out[field.name] = []; }
    } else if (field.type === "integer" || field.type === "number") {
      out[field.name] = node.value === "" ? null : Number(node.value);
    } else out[field.name] = node.value;
  }
  return out;
}

async function openEntryForm(entryId = null, preset = null) {
  const box = $("#pc-form");
  const detail = entryId ? await api(`/catalogs/${entryId}`) : null;
  const entry = detail ? detail.entry : (preset || { kind: pcKinds[0]?.id,
    version: "1.0.0", tags: [], attributes: {} });
  const locked = detail ? detail.substantively_locked : false;
  const kindSelect = el("select", { id: "pc-f-kind" });
  fillSelect(kindSelect, pcKinds.map((k) => [k.id, k.label]));
  kindSelect.value = entry.kind;
  if (entryId) kindSelect.disabled = true;

  const fieldsFor = (kindId) =>
    (pcKinds.find((k) => k.id === kindId) || {}).attributes || [];

  const attrBox = el("div", { class: "form" });
  const drawAttributes = () => {
    const fields = fieldsFor(kindSelect.value);
    attrBox.replaceChildren(...fields.map((f) =>
      el("label", {}, f.label,
        Object.assign(attributeInput(f, entry.attributes?.[f.name]),
          locked ? { disabled: true } : {}))));
  };
  kindSelect.addEventListener("change", drawAttributes);

  const banner = locked
    ? el("div", { class: "validation" },
        el("p", { class: "v-err" }, detail.amend_refusal),
        el("div", { class: "actions" },
          el("button", { onclick: () => openEntryForm(null, {
            ...entry, id: undefined, version: "", status: "proposed",
          }) }, "Publish the next version as a new entry"),
          el("button", { onclick: () => sendBackEntry(entry.id) },
            "Send this one back for review")))
    : el("p", { class: "hint" }, entryId
        ? "This entry is not approved, so its kind, version and attributes may still be edited."
        : "It will be saved as proposed: recorded, and not selectable by any design until approved.");

  const editorial = PC_EDITORIAL.map(([key, label]) =>
    el("label", {}, label,
      el("input", { id: `pc-f-${key}`,
        value: key === "tags" ? (entry.tags || []).join(", ") : (entry[key] || "") })));

  box.hidden = false;
  box.replaceChildren(
    el("h3", {}, entryId ? `Edit ${entry.name}` : "New catalog entry"),
    banner,
    el("div", { class: "form" },
      el("label", {}, "Kind", kindSelect),
      el("label", {}, "Version",
        el("input", { id: "pc-f-version", value: entry.version || "1.0.0",
          ...(locked ? { disabled: true } : {}) })),
      ...editorial),
    el("h4", {}, "Attributes"),
    attrBox,
    el("div", { class: "actions" },
      el("button", { onclick: () => saveEntryForm(entryId, locked, kindSelect.value) },
        "Save"),
      el("button", { onclick: () => { box.hidden = true; } }, "Cancel")));
  drawAttributes();
}

async function saveEntryForm(entryId, locked, kindId) {
  const fields = (pcKinds.find((k) => k.id === kindId) || {}).attributes || [];
  const editorial = Object.fromEntries(PC_EDITORIAL.map(([key]) =>
    [key, key === "tags"
      ? $(`#pc-f-${key}`).value.split(",").map((v) => v.trim()).filter(Boolean)
      : $(`#pc-f-${key}`).value]));
  try {
    if (!entryId) {
      await api("/catalogs", {
        method: "POST",
        body: JSON.stringify({ ...editorial, kind: kindId,
          version: $("#pc-f-version").value || "1.0.0",
          attributes: readAttributes(fields) }),
      });
      setStatus("published as proposed — approve it before a design can pick it");
    } else {
      await api(`/catalogs/${entryId}`, {
        method: "PATCH", body: JSON.stringify(editorial),
      });
      if (!locked) {
        await api(`/catalogs/${entryId}/amend`, {
          method: "POST",
          body: JSON.stringify({ version: $("#pc-f-version").value,
            attributes: readAttributes(fields) }),
        });
      }
      setStatus("saved");
    }
    $("#pc-form").hidden = true;
    loadPlatformCatalog();
  } catch (e) {
    setStatus(e.message);
  }
}

$("#pc-add").addEventListener("click", () => openEntryForm(null));

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
/* The designer opens with a design context, not with a runtime read: the
   canvas is initialised for every view, because the org chart and the agent
   editor read the record it holds. */
window.addEventListener("DOMContentLoaded", async () => {
  try {
    wireAgentEditor();
    wireOrgView();
    wireWorkspaceView();
    await window.initCanvas();
    await loadWhoami();
    renderIdentity();
    const route = location.hash.match(/^#\/(\w+)/)?.[1];
    const session = location.hash.match(/^#\/sessions\/(\S+)/)?.[1];
    if (session) { showView("sessions"); loadTrace(session); }
    else showView(route || "org");
    setStatus("ready");
  } catch (err) {
    setStatus(`error: ${err.message}`);
  }
});
