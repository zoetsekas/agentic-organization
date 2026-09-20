/* Fabric Command Centre — vanilla operator application over /api/fabric.
   A separate application from the designer (ADR-0051): it shares no state, no
   navigation and no code with web/app.js, so one cannot become the other. */
const API = "/api/fabric";
const FIXTURE_URL = "command-centre.sample.json";

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    n.appendChild(typeof kid === "object" ? kid : document.createTextNode(String(kid)));
  }
  return n;
};

const state = {
  live: true,          // false once a request has fallen back to the fixture
  fixture: null,
  whoami: null,
  tenants: [],
  deployments: [],
  selectedTenant: null,
  selectedDeployment: null,
};

/* ------------------------------------------------------------- transport */

class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

/* An operator needs the backend's own words on a refusal: the lifecycle table
   names the role and the states, which is more use than anything invented here. */
async function detailOf(res) {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") return body.detail;
    return JSON.stringify(body);
  } catch (err) {
    return `${res.status} ${res.statusText}`;
  }
}

async function apiGet(path) {
  if (!state.live) return fixtureGet(path);
  let res;
  try {
    res = await fetch(path, { headers: { Accept: "application/json" } });
  } catch (err) {
    return degradeToFixture(path, "the backend did not answer");
  }
  if (res.status >= 500 || res.status === 404 && !res.headers.get("content-type")) {
    return degradeToFixture(path, `the backend answered ${res.status}`);
  }
  if (!res.ok) throw new ApiError(res.status, await detailOf(res));
  return res.json();
}

async function apiWrite(method, path, body) {
  if (!state.live) {
    throw new ApiError(0, "fixture mode: operator actions need a live backend, " +
      "because an action that only happened in this page would be a lie about the fabric.");
  }
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  } catch (err) {
    throw new ApiError(0, "the backend did not answer; nothing was changed");
  }
  if (!res.ok) throw new ApiError(res.status, await detailOf(res));
  return res.json();
}

async function degradeToFixture(path, why) {
  state.live = false;
  showSource(why);
  return fixtureGet(path);
}

async function loadFixture() {
  if (!state.fixture) {
    const res = await fetch(FIXTURE_URL);
    state.fixture = await res.json();
  }
  return state.fixture;
}

function routeOf(key) {
  const fx = state.fixture.routes[key];
  return fx ? JSON.parse(JSON.stringify(fx.response)) : null;
}

/* Maps a live path onto the fixture's route keys, then filters client-side so
   the offline view still answers per-tenant and per-deployment questions. */
async function fixtureGet(path) {
  await loadFixture();
  const [bare, query = ""] = path.split("?");
  const params = new URLSearchParams(query);
  const tenant = params.get("tenant_id");
  const rest = bare.slice(API.length);
  const byTenant = (rows) => tenant ? rows.filter((r) => r.tenant_id === tenant) : rows;

  if (rest === "/whoami") return routeOf("GET /api/fabric/whoami");
  if (rest === "/tenants") return routeOf("GET /api/fabric/tenants");
  if (rest === "/services") return routeOf("GET /api/fabric/services");
  if (rest === "/services/boundary") return routeOf("GET /api/fabric/services/boundary");
  if (rest === "/operators") return routeOf("GET /api/fabric/operators");
  if (rest === "/health") return byTenant(routeOf("GET /api/fabric/health"));
  if (rest === "/drift") return byTenant(routeOf("GET /api/fabric/drift"));
  if (rest === "/quotas") return byTenant(routeOf("GET /api/fabric/quotas"));
  if (rest === "/deployments") return byTenant(routeOf("GET /api/fabric/deployments"));
  if (rest === "/audit") {
    let rows = routeOf("GET /api/fabric/audit");
    if (tenant) rows = rows.filter((r) => r.tenant_id === tenant);
    if (params.get("actor")) rows = rows.filter((r) => r.actor === params.get("actor"));
    if (params.get("cross_tenant_only") === "true") rows = rows.filter((r) => r.cross_tenant);
    return rows;
  }
  const dep = rest.match(/^\/deployments\/([^/]+)(\/history)?$/);
  if (dep) {
    const found = routeOf("GET /api/fabric/deployments").find((d) => d.id === dep[1]);
    if (!found) throw new ApiError(404, `no such deployment: ${dep[1]}`);
    if (dep[2]) return found.history;
    const health = routeOf("GET /api/fabric/health")
      .filter((h) => h.deployment_id === found.id);
    return { ...found, health: health[0] || null };
  }
  const ten = rest.match(/^\/tenants\/([^/]+)$/);
  if (ten) {
    const record = routeOf("GET /api/fabric/tenants").find((t) => t.id === ten[1]);
    if (!record) throw new ApiError(404, `no such tenant: ${ten[1]}`);
    const { deployment_count, states, ...bareTenant } = record;
    const quotas = routeOf("GET /api/fabric/quotas")
      .find((q) => q.tenant_id === ten[1]) || null;
    return {
      tenant: bareTenant,
      deployments: routeOf("GET /api/fabric/deployments").filter((d) => d.tenant_id === ten[1]),
      health: routeOf("GET /api/fabric/health").filter((h) => h.tenant_id === ten[1]),
      drift: routeOf("GET /api/fabric/drift").filter((d) => d.tenant_id === ten[1]),
      quotas,
    };
  }
  throw new ApiError(404, `no fixture for ${path}`);
}

function showSource(why) {
  const banner = $("#source-banner");
  banner.hidden = state.live;
  if (!state.live && why) {
    $("#source-detail").textContent =
      `${why}; this page is showing the packaged fixture. Nothing here is live, ` +
      `and operator actions are refused until the backend answers again.`;
  }
}

function showError(err) {
  const title = { 403: "Refused (403)", 409: "Illegal transition (409)",
    404: "Not found (404)", 400: "Rejected (400)", 401: "Not authenticated (401)" };
  $("#error-title").textContent = title[err.status] || "Refused";
  $("#error-detail").textContent = err.detail || err.message;
  $("#error-bar").hidden = false;
}
$("#btn-error-dismiss").addEventListener("click", () => ($("#error-bar").hidden = true));
$("#btn-retry").addEventListener("click", async () => {
  state.live = true;
  showSource();
  await boot();
});

const guard = (fn) => async (...args) => {
  try { await fn(...args); } catch (err) {
    if (err instanceof ApiError) showError(err); else throw err;
  }
};

/* --------------------------------------------------------------- helpers */

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString() : "—");
function fmtAge(seconds) {
  if (seconds === null || seconds === undefined) return "never observed";
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
const STATUS_TONE = { healthy: "ok", degraded: "warn", unhealthy: "err", unknown: "unk" };
const SEVERITY_TONE = { info: "ok", warning: "warn", critical: "err" };
const TENANT_TONE = { active: "ok", pending: "warn", suspended: "warn", retired: "muted" };

function badge(text, tone) {
  return el("span", { class: `badge ${tone || ""}` }, text);
}

/* The health card says which of the three things we are looking at: it is
   healthy, it is broken, or we cannot see it (ADR-0052). */
function healthCard(h) {
  const unseen = h.confidence !== "fresh";
  const card = el("div", { class: `hcard tone-${STATUS_TONE[h.status] || "unk"}${unseen ? " unseen" : ""}` },
    el("div", { class: "hhead" },
      el("strong", {}, h.status === "unknown" ? "Not observed — status unknown" : `Status ${h.status}`),
      badge(`confidence ${h.confidence}`, unseen ? "unk" : "ok")),
    el("div", { class: "hbody" },
      el("div", {}, `believed ${h.believed_state} @ ${h.believed_revision || "—"}`),
      el("div", {}, `observed ${h.observed_state || "—"} @ ${h.observed_revision || "—"}`),
      el("div", { class: "muted" },
        `observed ${fmtAge(h.observation_age_seconds)} · horizon ${Math.round(h.staleness_horizon_seconds || 0)}s · checked ${fmtTime(h.checked_at)}`)),
    unseen ? el("p", { class: "unseen-note" },
      h.confidence === "unobserved"
        ? "The target has never been observed. This is not evidence of health."
        : "The last observation is past the staleness horizon, so it is not evidence of health.") : null,
    (h.signals || []).length
      ? el("div", { class: "list" }, (h.signals || []).map(driftRow))
      : null);
  return card;
}

function driftRow(d) {
  return el("div", {},
    badge(d.severity, SEVERITY_TONE[d.severity]),
    el("span", { class: "mono" }, d.kind),
    el("span", { class: "grow" }, d.detail),
    el("span", { class: "muted" }, `${d.believed} → ${d.observed}`),
    el("a", { href: `#/deployments/${d.deployment_id}` }, d.deployment_id));
}

/* Three quota outcomes, kept apart: within, served-but-breached, refused. */
function quotaVerdict(limit, used) {
  const soft = limit.soft_limit;
  const hard = limit.hard_ceiling;
  const unbounded = hard === undefined || hard === null || hard < 0;
  if (used > soft && !unbounded && used > hard) {
    return { key: "refused", tone: "err", label: "over the hard ceiling — refused",
      note: `usage ${used} is above the hard ceiling of ${hard}: further requests are refused.` };
  }
  if (used > soft) {
    return { key: "degraded", tone: "warn", label: "over soft limit — served, breach recorded",
      note: `usage ${used} is above the soft limit of ${soft}` +
        (unbounded ? " and there is no hard ceiling: nothing is refused, every excess is recorded."
                   : ` and below the hard ceiling of ${hard}: the request is served and the breach recorded.`) };
  }
  return { key: "within", tone: "ok", label: "within the soft limit",
    note: `usage ${used} of ${soft}${unbounded ? "" : ` (ceiling ${hard})`}.` };
}

function quotaPanel(q) {
  if (!q) return el("p", { class: "empty" }, "No quota record.");
  const kinds = Object.keys(q.quotas || {});
  return el("div", { class: "qpanel" },
    el("div", { class: "row-head" },
      el("strong", {}, q.tenant_id),
      q.recorded ? badge("entitlement record", "ok")
                 : badge("no entitlement record yet", "warn")),
    kinds.length
      ? el("div", { class: "cards" }, kinds.map((kind) => {
          const limit = q.quotas[kind];
          const used = (q.usage || {})[kind] || 0;
          const verdict = quotaVerdict(limit, used);
          return el("div", { class: `card quota tone-${verdict.tone}` },
            el("h3", {}, kind.replace(/_/g, " ")),
            badge(verdict.label, verdict.tone),
            el("p", {}, verdict.note),
            el("div", { class: "meter" },
              el("span", { class: `fill ${verdict.key}`,
                style: `width:${Math.min(100, limit.soft_limit ? (used / limit.soft_limit) * 100 : 0)}%` })),
            el("div", { class: "muted" },
              `soft ${limit.soft_limit}${limit.hard_ceiling < 0 ? " · no hard ceiling" : ` · hard ${limit.hard_ceiling}`}${limit.unit ? ` · ${limit.unit}` : ""}`));
        }))
      : el("p", { class: "empty" }, "No quota kinds set for this tenant."),
    el("h4", {}, "Entitlements"),
    el("div", { class: "chips" },
      (q.tenant_entitlements || []).length
        ? q.tenant_entitlements.map((e) => badge(e))
        : el("span", { class: "empty" }, "none")),
    el("h4", {}, "Catalog allow-list"),
    el("div", { class: "chips" },
      (q.catalog_entries || []).length
        ? q.catalog_entries.map((e) => badge(e))
        : el("span", { class: "empty" }, "empty — entitled to nothing")),
    el("h4", {}, "Breaches"),
    (q.breaches || []).length
      ? el("div", { class: "list" }, q.breaches.map((b) =>
          el("div", {},
            badge(b.decision, b.decision === "refuse" ? "err" : "warn"),
            el("span", { class: "mono" }, b.kind),
            el("span", { class: "grow" }, b.reason),
            el("span", { class: "muted" }, fmtTime(b.at)))))
      : el("p", { class: "empty" }, "none recorded"));
}

/* ------------------------------------------------------------- navigation */

$("#tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (btn) location.hash = `#/${btn.dataset.view}`;
});
window.addEventListener("hashchange", () => route());

function showView(name) {
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.id === `view-${name}`));
}

const route = guard(async () => {
  const parts = (location.hash.replace(/^#\/?/, "") || "tenants").split("/");
  const [view, id] = parts;
  if (view === "tenants") {
    showView("tenants");
    await loadTenants();
    if (id) await openTenant(id);
  } else if (view === "deployments") {
    showView("deployments");
    await loadDeployments();
    if (id) await openDeployment(id);
  } else if (view === "health") {
    showView("health");
    await loadHealth();
  } else if (view === "quotas") {
    showView("quotas");
    await loadQuotas();
  } else if (view === "services") {
    showView("services");
    await loadServices();
  } else if (view === "audit") {
    showView("audit");
    await loadAudit();
  } else if (view === "operators") {
    showView("operators");
    await loadOperators();
  } else {
    location.hash = "#/tenants";
  }
});

/* ------------------------------------------------------------------ views */

async function loadWhoami() {
  const who = await apiGet(`${API}/whoami`);
  state.whoami = who;
  const node = $("#whoami");
  node.replaceChildren(
    el("span", {}, `${who.display_name || who.user_id} · `),
    who.is_operator
      ? badge((who.operator_roles || []).join(", "), "ok")
      : badge("not an operator", "warn"));
  node.title = (who.permissions || []).join("\n");
}

async function loadTenants() {
  state.tenants = await apiGet(`${API}/tenants`);
  renderTenants();
}

function renderTenants() {
  const filter = ($("#tenant-filter").value || "").toLowerCase();
  const rows = state.tenants.filter((t) =>
    !filter || `${t.id} ${t.name}`.toLowerCase().includes(filter));
  $("#tenant-list").replaceChildren(...rows.map((t) =>
    el("div", { class: state.selectedTenant === t.id ? "sel" : "" },
      el("a", { class: "grow", href: `#/tenants/${t.id}` },
        el("strong", {}, t.name), el("span", { class: "muted" }, ` ${t.id}`)),
      badge(t.status, TENANT_TONE[t.status] || ""),
      badge(`${t.deployment_count} deployments`),
      el("span", { class: "muted mono" }, (t.states || []).join(" · ")),
      el("span", { class: "muted" }, `domain ${t.isolation_domain?.id || "—"}`))));
  if (!rows.length) $("#tenant-list").replaceChildren(el("p", { class: "empty" }, "No tenants."));
}
$("#tenant-filter").addEventListener("input", renderTenants);

async function openTenant(tenantId) {
  const detail = await apiGet(`${API}/tenants/${encodeURIComponent(tenantId)}`);
  state.selectedTenant = tenantId;
  renderTenants();
  const audit = await recentAudit(tenantId);
  const t = detail.tenant;
  const dom = t.isolation_domain || {};
  $("#tenant-detail").replaceChildren(
    el("div", { class: "row-head" }, el("h3", {}, t.name),
      badge(t.status, TENANT_TONE[t.status] || "")),
    el("p", { class: "muted" }, `${t.id} · namespace ${t.namespace_prefix} · created ${fmtTime(t.created_at)}`),
    el("h4", {}, "Isolation domain"),
    el("div", { class: "kv" },
      ...["id", "network", "identity_realm", "secret_scope", "data_scope", "cloud_boundary"]
        .map((k) => el("div", {}, el("span", { class: "muted" }, k.replace(/_/g, " ")),
          el("span", { class: "mono" }, dom[k] || "—")))),
    el("h4", {}, "Entitlements"),
    el("div", { class: "chips" }, (t.entitlements || []).length
      ? t.entitlements.map((e) => badge(e))
      : el("span", { class: "empty" }, "none")),
    el("h4", {}, "Deployments"),
    el("div", { class: "list" }, detail.deployments.map((d) =>
      el("div", {},
        el("a", { class: "grow", href: `#/deployments/${d.id}` }, d.name),
        badge(d.state, d.state === "quarantined" ? "err" : d.state === "running" ? "ok" : ""),
        el("span", { class: "muted mono" }, `${d.revision} · ${d.target}`)))),
    el("h4", {}, "Health"),
    detail.health.length
      ? el("div", { class: "hstack" }, detail.health.map(healthCard))
      : el("p", { class: "empty" }, "no checks"),
    el("h4", {}, "Drift"),
    detail.drift.length
      ? el("div", { class: "list" }, detail.drift.map(driftRow))
      : el("p", { class: "empty" }, "no drift signals"),
    el("h4", {}, "Quotas"),
    quotaPanel(detail.quotas),
    requotaForm(t.id, detail.quotas),
    el("h4", {}, "Recent audit"),
    audit);
}

/* Re-quota is the one write this application makes outside the deployment
   lifecycle. It moves limits, never anything about what the tenant *is*. */
function requotaForm(tenantId, quotas) {
  const kinds = ["deployments", "agents", "concurrent_sessions",
    "monthly_spend_usd", "tokens_per_day"];
  const kind = el("select", {}, kinds.map((k) => el("option", { value: k }, k)));
  const soft = el("input", { type: "number", min: "0", value: "0" });
  const hard = el("input", { type: "number", value: "-1",
    title: "-1 means unbounded: breaches are recorded, nothing is refused" });
  const reason = el("input", { placeholder: "why you are re-quotaing" });
  const submit = el("button", { class: "primary", onclick: guard(async (e) => {
    e.preventDefault();
    const body = { quotas: { [kind.value]: {
      soft_limit: Number(soft.value), hard_ceiling: Number(hard.value),
      unit: (quotas?.quotas?.[kind.value] || {}).unit || "" } },
      reason: reason.value };
    await apiWrite("PUT", `${API}/tenants/${encodeURIComponent(tenantId)}/quotas`, body);
    await openTenant(tenantId);
  }) }, "Apply new limits");
  return el("form", { class: "requota" },
    el("h4", {}, "Re-quota"),
    el("div", { class: "row" },
      el("label", {}, "kind", kind),
      el("label", {}, "soft limit", soft),
      el("label", {}, "hard ceiling (-1 unbounded)", hard),
      el("label", {}, "reason", reason)),
    el("div", { class: "actions" }, submit));
}

/* Audit is admin-only; an operator without it should see why, not an empty box. */
async function recentAudit(tenantId) {
  try {
    const rows = await apiGet(
      `${API}/audit?tenant_id=${encodeURIComponent(tenantId)}&limit=10`);
    return rows.length ? auditTable(rows) : el("p", { class: "empty" }, "no audit rows");
  } catch (err) {
    if (err instanceof ApiError && (err.status === 403 || err.status === 401)) {
      return el("p", { class: "empty" }, `audit not visible: ${err.detail}`);
    }
    throw err;
  }
}

async function loadDeployments() {
  state.deployments = await apiGet(`${API}/deployments`);
  $("#deployment-list").replaceChildren(...state.deployments.map((d) =>
    el("div", { class: state.selectedDeployment === d.id ? "sel" : "" },
      el("a", { class: "grow", href: `#/deployments/${d.id}` },
        el("strong", {}, d.name), el("span", { class: "muted" }, ` ${d.tenant_id}`)),
      badge(d.state, d.state === "quarantined" ? "err" : d.state === "running" ? "ok" : ""),
      el("span", { class: "muted mono" }, `${d.revision} · ${d.target}`))));
}

/* The action names the backend offers for each target state. A move whose
   target is not in allowed_transitions is simply not drawn. */
const ACTION_FOR_TARGET = {
  generated: { action: "generate", label: "Generate" },
  deployed: { action: "deploy", label: "Deploy" },
  stopped: { action: "stop", label: "Stop" },
  quarantined: { action: "quarantine", label: "Quarantine" },
  running: { action: "redeploy", label: "Redeploy" },
};

async function openDeployment(deploymentId) {
  const d = await apiGet(`${API}/deployments/${encodeURIComponent(deploymentId)}`);
  const history = await apiGet(
    `${API}/deployments/${encodeURIComponent(deploymentId)}/history`);
  state.selectedDeployment = d.id;
  if (state.deployments.length) await loadDeployments();
  const roles = (state.whoami && state.whoami.operator_roles) || [];
  const transitions = d.allowed_transitions || {};
  const buttons = Object.keys(transitions).sort().map((target) => {
    const spec = ACTION_FOR_TARGET[target];
    if (!spec) return null;                       // a target this UI has no action for
    const accepted = transitions[target] || [];
    const mine = roles.some((r) => accepted.includes(r));
    const why = mine
      ? `${spec.label.toLowerCase()} → ${target}`
      : `the lifecycle table accepts ${accepted.join(", ")} for → ${target}`;
    return el("button", {
      class: mine ? "primary" : "",
      title: why,
      onclick: guard(() => promptAction(d, spec, target, accepted)),
    }, spec.label);
  }).filter(Boolean);

  $("#deployment-detail").replaceChildren(
    el("div", { class: "row-head" }, el("h3", {}, d.name),
      badge(d.state, d.state === "quarantined" ? "err" : d.state === "running" ? "ok" : "")),
    el("p", { class: "muted" },
      `${d.id} · tenant ${d.tenant_id} · system ${d.system_id} · revision ${d.revision} · target ${d.target}`),
    el("p", { class: "muted" }, `created ${fmtTime(d.created_at)} · updated ${fmtTime(d.updated_at)}`),
    el("h4", {}, "Moves the backend allows from here"),
    buttons.length
      ? el("div", { class: "actions" }, buttons)
      : el("p", { class: "empty" }, "The lifecycle table offers no move out of this state."),
    el("p", { class: "hint" },
      "Buttons come from this deployment's allowed_transitions; a move the backend does not allow is not offered."),
    el("h4", {}, "Health"),
    d.health ? healthCard(d.health) : el("p", { class: "empty" }, "no check has been taken"),
    el("h4", {}, "History"),
    history.length
      ? el("div", { class: "list" }, history.slice().reverse().map((h) =>
          el("div", {},
            el("span", { class: "mono" }, `${h.source} → ${h.target}`),
            el("span", { class: "grow" }, h.reason || ""),
            badge(h.role || "—"),
            el("span", { class: "muted" }, `${h.actor} · ${fmtTime(h.at)}`))))
      : el("p", { class: "empty" }, "no transitions yet"));
}

function promptAction(deployment, spec, target, accepted) {
  const dialog = $("#action-dialog");
  $("#action-title").textContent = `${spec.label} ${deployment.name}`;
  $("#action-hint").textContent =
    `${deployment.state} → ${target}. The lifecycle table accepts: ${accepted.join(", ")}. ` +
    "The reason is recorded in the audit log and in this deployment's history.";
  $("#action-reason").value = "";
  dialog.returnValue = "cancel";
  dialog.showModal();
  dialog.onclose = guard(async () => {
    if (dialog.returnValue !== "confirm") return;
    await apiWrite("POST",
      `${API}/deployments/${encodeURIComponent(deployment.id)}/actions/${encodeURIComponent(spec.action)}`,
      { reason: $("#action-reason").value });
    await openDeployment(deployment.id);
  });
}

async function loadHealth() {
  const [health, drift] = await Promise.all([
    apiGet(`${API}/health`), apiGet(`${API}/drift`),
  ]);
  const counts = { healthy: 0, degraded: 0, unhealthy: 0, unknown: 0 };
  health.forEach((h) => (counts[h.status] = (counts[h.status] || 0) + 1));
  $("#health-list").replaceChildren(
    el("div", { class: "statrow" }, Object.keys(counts).map((k) =>
      el("div", { class: `stat tone-${STATUS_TONE[k]}` },
        el("div", { class: "v" }, counts[k]),
        el("div", { class: "k" }, k === "unknown" ? "unknown (not seen)" : k)))),
    el("div", { class: "hstack" }, health.map((h) =>
      el("div", {},
        el("div", { class: "muted" },
          el("a", { href: `#/deployments/${h.deployment_id}` }, h.deployment_id),
          ` · ${h.tenant_id}`),
        healthCard(h)))));
  $("#drift-list").replaceChildren(...(drift.length
    ? drift.map(driftRow)
    : [el("p", { class: "empty" }, "no drift signals")]));
}

async function loadQuotas() {
  const quotas = await apiGet(`${API}/quotas`);
  $("#quota-list").replaceChildren(...quotas.map(quotaPanel));
}

async function loadServices() {
  const [services, boundary] = await Promise.all([
    apiGet(`${API}/services`), apiGet(`${API}/services/boundary`),
  ]);
  $("#service-list").replaceChildren(...services.map((s) =>
    el("div", { class: "card" },
      el("h3", {}, s.name),
      el("div", { class: "meta" }, badge(s.kind), badge(s.shared ? "shared" : "per tenant",
        s.shared ? "warn" : "ok")),
      el("p", {}, s.summary),
      el("p", { class: "muted" }, `If it fails: ${s.failure_impact}`))));
  $("#boundary-list").replaceChildren(table(
    ["service", "kind", "what", "direction", "why", "visible_to_other_tenants"],
    boundary.map((r) => [r.service, r.kind, r.what, r.direction, r.why,
      r.visible_to_other_tenants])));
}

function table(headers, rows) {
  return el("table", { class: "grid" },
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h.replace(/_/g, " "))))),
    el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c) =>
      el("td", {}, typeof c === "object" && c !== null ? c : String(c ?? "")))))));
}

function auditTable(rows) {
  return table(["when", "actor", "action", "outcome", "scope", "route"],
    rows.map((r) => [
      fmtTime(r.timestamp),
      `${r.actor_name || r.actor}`,
      r.action,
      badge(r.outcome, r.outcome === "success" ? "ok"
        : r.outcome === "denied" ? "err" : "warn"),
      r.cross_tenant ? badge(`cross-tenant ×${r.detail?.tenant_count ?? "?"}`, "warn")
        : (r.tenant_id || r.deployment_id || "—"),
      r.route,
    ]));
}

const loadAudit = guard(async () => {
  const params = new URLSearchParams({ limit: "200" });
  if ($("#audit-tenant").value) params.set("tenant_id", $("#audit-tenant").value);
  if ($("#audit-actor").value) params.set("actor", $("#audit-actor").value);
  if ($("#audit-cross").checked) params.set("cross_tenant_only", "true");
  try {
    const rows = await apiGet(`${API}/audit?${params}`);
    $("#audit-list").replaceChildren(rows.length
      ? auditTable(rows) : el("p", { class: "empty" }, "no rows"));
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      $("#audit-list").replaceChildren(el("p", { class: "empty" }, err.detail));
      return;
    }
    throw err;
  }
});
$("#btn-audit-reload").addEventListener("click", () => loadAudit());

const loadOperators = guard(async () => {
  try {
    const rows = await apiGet(`${API}/operators`);
    $("#operator-list").replaceChildren(table(
      ["user", "roles", "granted by", "reason", "granted at"],
      rows.map((r) => [r.user_id, (r.roles || []).join(", "), r.granted_by,
        r.reason, fmtTime(r.granted_at)])));
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      $("#operator-list").replaceChildren(el("p", { class: "empty" }, err.detail));
      return;
    }
    throw err;
  }
});

/* ------------------------------------------------------------------- boot */

const boot = guard(async () => {
  await loadWhoami();
  showSource();
  await route();
});

boot();
