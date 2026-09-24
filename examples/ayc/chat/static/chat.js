// AYC chat. One page, no framework: pick an agent, talk to it, see what it
// did to the mock systems and what stopped it.
"use strict";

// Starting arguments for each backing-system tool, against the seed data, so
// a person can click, read, and send. The agent decides nothing from these:
// with the stub model, the call you send is the call it makes.
const EXAMPLES = {
  shopify__order_query: { status: "paid" },
  shopify__product_publishing: { sku: "AYC-TB-820" },
  shopify__price_update: { sku: "AYC-CH-001", price: 499 },
  shopify__refund_processing: { order_id: "SO-50232", amount: 120, reason: "cracked footrest" },
  shopify__promotion_run: { code: "SALONWEEK", percent_off: 12, skus: ["AYC-TR-605"] },
  fishbowl__stock_check: { sku: "AYC-CH-001" },
  fishbowl__purchase_ordering: { supplier_id: "SUP-MAL", sku: "AYC-CH-001", quantity: 12, unit_cost: 212 },
  fishbowl__goods_receiving: { po_number: "PO-1040" },
  fishbowl__inventory_adjustment: { sku: "AYC-TR-605", counted: 61, reason: "cycle count, aisle E" },
  fishbowl__order_fulfillment: { order_id: "SO-50233" },
  accounting__invoice_payment: { invoice_id: "SINV-7790", amount: 980 },
  accounting__receivable_management: { customer_id: "C-1003", action: "receipt", amount: 5780 },
  cms__content_publishing: { slug: "guides/choosing-a-backwash", title: "Choosing a backwash unit" },
  deploy_pipeline__software_deploy: { service: "freight-quoter", version: "1.9.0", change_ticket: "CHG-2231" },
};
const SYSTEMS = ["shopify", "fishbowl", "accounting", "cms", "deploy_pipeline"];

const $ = (id) => document.getElementById(id);
const state = { agents: [], current: null, detail: {}, logs: {}, status: {}, me: null, people: [] };

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || `${r.status}`);
  return data;
}

// -- roster -------------------------------------------------------------------

function renderRoster() {
  const box = $("roster");
  box.replaceChildren();
  const byTeam = new Map();
  for (const a of state.agents) {
    const team = a.team_path.slice(1).join(" / ") || a.team;
    if (!byTeam.has(team)) byTeam.set(team, []);
    byTeam.get(team).push(a);
  }
  for (const [team, agents] of byTeam) {
    box.append(el("div", { class: "team" }, team || "AYC"));
    for (const a of agents) {
      const up = state.status[a.id];
      box.append(el("button", {
        class: "agent", type: "button", "aria-current": String(state.current === a.id),
        onclick: () => select(a.id),
      },
        el("span", { class: `dot ${up === true ? "up" : up === false ? "down" : ""}`,
                     title: up ? "worker answering" : "worker not answering" }),
        el("span", { class: "aname" }, a.name),
        el("span", { class: "aid" }, a.id)));
    }
  }
}

async function refreshStatus() {
  try {
    state.status = await api("/api/status");
    renderRoster();
  } catch (_) { /* the roster still works without it */ }
}

// -- identity -----------------------------------------------------------------

function renderIdentity() {
  const a = state.agents.find((x) => x.id === state.current);
  const d = state.detail[state.current];
  const box = $("identity");
  if (!a) return;
  const tools = (d && d.tools || []).filter((t) => SYSTEMS.some((s) => t.startsWith(s + "__")));
  box.replaceChildren(
    el("h1", {}, a.name),
    el("p", { class: "sub" }, a.description),
    el("dl", { class: "facts" },
      el("div", {}, el("dt", {}, "agent"), el("dd", { class: "mono" }, a.id)),
      el("div", {}, el("dt", {}, "team"), el("dd", {}, a.team_path.join(" / "))),
      el("div", {}, el("dt", {}, "reports to"), el("dd", { class: "mono" }, a.reports_to || "—")),
      el("div", {}, el("dt", {}, "mandate"),
        el("dd", {}, a.mandate.length ? a.mandate.map((m) => el("span", { class: "chip" }, m)) : "—")),
      el("div", {}, el("dt", {}, "systems it may reach"),
        el("dd", {}, tools.length ? tools.map((t) => el("span", { class: "chip tool" }, t)) : "none")),
      el("div", {}, el("dt", {}, "approvers"),
        el("dd", {}, d ? (d.approvers.map((p) => `${p.name} (${p.person})`).join(", ") || "—") : "…")),
      el("div", {}, el("dt", {}, "runtime · model"),
        el("dd", { class: "mono" }, d ? `${d.runtime} · ${d.model}` : "…"))));
  const quick = $("quick");
  quick.replaceChildren(...tools.map((t) => el("button", {
    class: "btn small", type: "button", title: "Insert this call",
    onclick: () => insertCall(t, EXAMPLES[t] || {}),
  }, t)));
}

function insertCall(tool, args) {
  const box = $("message");
  const line = `call ${tool} ${JSON.stringify(args)}`;
  box.value = box.value.trim() ? `${box.value.trim()}\n${line}` : line;
  box.focus();
}

async function select(id) {
  state.current = id;
  state.logs[id] = state.logs[id] || [];
  renderRoster();
  renderIdentity();
  renderLog();
  $("composer").hidden = false;
  if (!state.detail[id]) {
    try {
      state.detail[id] = await api(`/api/agents/${id}`);
    } catch (e) {
      push(id, { kind: "error", text: `Could not reach ${id}'s worker: ${e.message}` });
    }
    if (state.current === id) renderIdentity();
  }
}

// -- conversation -------------------------------------------------------------

function push(id, entry) {
  (state.logs[id] = state.logs[id] || []).push(entry);
  if (state.current === id) renderLog();
}

function callState(c) {
  const r = c.result;
  if (c.ok) return "ok";
  if (r && typeof r === "object" && r.requires_approval) return "waiting";
  return "refused";
}

function reason(c) {
  const r = c.result;
  if (c.ok) return "";
  if (r && typeof r === "object") return r.error || JSON.stringify(r);
  return String(r || "");
}

function renderCall(agentId, c) {
  const s = callState(c);
  const label = { ok: "done", waiting: "needs approval", refused: "refused" }[s];
  const node = el("details", { class: `call ${s}` },
    el("summary", {},
      el("span", { class: "tname" }, c.tool),
      el("span", { class: "state" }, label),
      s !== "ok" ? el("span", { class: "why" }, reason(c)) : null),
    el("pre", {}, `arguments ${JSON.stringify(c.arguments, null, 2)}\n\nresult ${JSON.stringify(c.result, null, 2)}`));
  if (s === "waiting") {
    // You approve as who you signed in as, and nobody else (ADR-0114). The
    // chat signs your release; the worker decides whether the design names you.
    const d = state.detail[agentId];
    const people = (d && d.approvers) || [];
    const names = people.map((p) => `${p.name} (${p.person})`).join(", ") || "nobody";
    const me = state.me;
    node.append(el("div", { class: "approve" },
      el("span", {}, me ? `Approvers: ${names}.` : `Approvers: ${names}. Sign in to approve.`),
      me ? el("button", {
        class: "btn small", type: "button",
        onclick: async (ev) => {
          ev.target.disabled = true;
          try {
            await api("/api/approve", { agent: agentId, tool: c.tool, arguments: c.arguments });
            push(agentId, { kind: "system", text: `${me.name} (${me.id}) approved one ${c.tool} call with exactly these arguments. Sending it again.` });
            await send(agentId, `call ${c.tool} ${JSON.stringify(c.arguments)}`);
          } catch (e) {
            push(agentId, { kind: "error", text: `Approval refused: ${e.message}` });
          }
        },
      }, `Approve as ${me.name}`) : null));
  }
  return node;
}

function renderLog() {
  const box = $("log");
  const id = state.current;
  const a = state.agents.find((x) => x.id === id);
  box.replaceChildren(...(state.logs[id] || []).map((m) => {
    if (m.kind === "me") return el("div", { class: "msg me" }, el("div", { class: "who" }, m.who), el("div", { class: "body" }, m.text));
    if (m.kind === "system") return el("div", { class: "msg system" }, el("div", { class: "body" }, m.text));
    if (m.kind === "error") return el("div", { class: "msg error" }, el("div", { class: "body" }, m.text));
    return el("div", { class: "msg" },
      el("div", { class: "who" }, `${a ? a.name : id} · ${m.state}${m.session ? " · " + m.session : ""}`),
      el("div", { class: "body" }, m.text || "(no reply)"),
      ...(m.calls || []).map((c) => renderCall(id, c)));
  }));
  box.scrollTop = box.scrollHeight;
}

async function send(id, text) {
  const button = $("send");
  button.disabled = true;
  try {
    const reply = await api("/api/chat", { agent: id, message: text });
    push(id, { kind: "agent", text: reply.error ? `${reply.output || ""}\n${reply.error}`.trim() : reply.output,
               calls: reply.tool_calls, state: reply.state, session: reply.session_id });
  } catch (e) {
    push(id, { kind: "error", text: e.message });
  } finally {
    button.disabled = false;
  }
}

$("composer").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("message").value.trim();
  if (!text || !state.current) return;
  if (!state.me) { push(state.current, { kind: "error", text: "Sign in first (top right)." }); return; }
  push(state.current, { kind: "me", who: state.me.name, text });
  $("message").value = "";
  await send(state.current, text);
});
$("message").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) $("composer").requestSubmit();
});

// -- sign-in (ADR-0114) -------------------------------------------------------

function renderMe() {
  $("signin").hidden = !!state.me;
  $("whoami").hidden = !state.me;
  if (state.me) $("me-name").textContent = `${state.me.name} (${state.me.id})`;
  if (state.current) renderLog();
}

$("signin").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/login", { person: $("person").value, passcode: $("passcode").value });
    $("passcode").value = "";
    state.me = (await api("/api/me")).person;
  } catch (e) {
    alert(`Sign-in refused: ${e.message}`);
  }
  renderMe();
});
$("signout").addEventListener("click", async () => {
  await api("/api/logout", {}).catch(() => {});
  state.me = null;
  renderMe();
});

(async function start() {
  state.people = (await api("/api/people")).people;
  $("person").replaceChildren(...state.people.map((p) =>
    el("option", { value: p.id }, `${p.name} — ${p.position || p.id}`)));
  state.me = (await api("/api/me")).person;
  renderMe();
  const data = await api("/api/agents");
  state.agents = data.agents;
  $("systems").replaceChildren(...(data.systems.length ? data.systems : ["(not published)"]).map((s) => {
    const [name, url] = s.split("=");
    return url ? el("a", { href: url, target: "_blank", rel: "noopener" }, `${name} → state`) : el("span", { class: "team" }, s);
  }));
  renderRoster();
  refreshStatus();
  setInterval(refreshStatus, 15000);
})();
