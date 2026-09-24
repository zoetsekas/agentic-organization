/* Settings: this person's display preferences, and the installation's own.

   Preferences are a viewer's conveniences — theme, density, text size, what
   the canvas draws — so they live in this browser and apply at once, with no
   save and no round trip. The installation settings are the server's
   (ADR-0031) and change for everyone, so they are a form with a Save, and the
   server decides who may change them.

   Loaded in <head>, before the body exists, so the stored theme is on <html>
   before the first paint and nobody sees the dark theme flash on the way to
   the light one. */
(function () {
  "use strict";

  const KEY = "orgagents.designer.prefs";

  /* Every preference, its choices and its default. The dialog is built from
     this table, and so is the attribute each one sets on <html>. */
  const PREFS = [
    { group: "Appearance", key: "theme", attr: "data-theme-choice",
      label: "Theme", help: "System follows your operating system's setting.",
      control: "segmented", default: "system",
      options: [["light", "☀ Light"], ["system", "◐ System"], ["dark", "☾ Dark"]] },
    { group: "Appearance", key: "density", attr: "data-density",
      label: "Density", help: "How much space panels and forms leave around things.",
      control: "segmented", default: "comfortable",
      options: [["compact", "Compact"], ["comfortable", "Comfortable"], ["spacious", "Spacious"]] },
    { group: "Appearance", key: "text", attr: "data-text",
      label: "Text size", help: "Scales every label, field and heading.",
      control: "segmented", default: "normal",
      options: [["normal", "Normal"], ["large", "Large"], ["larger", "Larger"]] },
    { group: "Appearance", key: "motion", attr: "data-motion",
      label: "Motion", help: "Reduce turns off animated transitions and smooth scrolling.",
      control: "segmented", default: "system",
      options: [["system", "System"], ["full", "Full"], ["reduce", "Reduce"]] },
    { group: "Canvas", key: "canvasGrid", attr: "data-canvas-grid",
      label: "Grid", help: "The background grid on the canvas.",
      control: "toggle", default: "on" },
    { group: "Canvas", key: "canvasLegend", attr: "data-canvas-legend",
      label: "Legend", help: "The strip above the canvas saying what each kind of box means.",
      control: "toggle", default: "on" },
    { group: "Canvas", key: "canvasRegions", attr: "data-canvas-regions",
      label: "Environment regions",
      help: "The hatched areas behind agents showing where they run.",
      control: "toggle", default: "on" },
    { group: "Canvas", key: "canvasEdgeLabels", attr: "data-canvas-edge-labels",
      label: "Relationship labels", help: "The kind written on each association line.",
      control: "toggle", default: "on" },
  ];

  const defaults = Object.fromEntries(PREFS.map((p) => [p.key, p.default]));

  /* Browser storage can be absent or refuse (a private window, blocked site
     data): the designer still works, it just forgets on reload. */
  function load() {
    try {
      return { ...defaults, ...JSON.parse(localStorage.getItem(KEY) || "{}") };
    } catch (e) {
      return { ...defaults };
    }
  }
  function store(prefs) {
    try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (e) { /* kept for this page only */ }
  }

  const darkQuery = window.matchMedia?.("(prefers-color-scheme: dark)");
  const motionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  let prefs = load();

  function apply() {
    const root = document.documentElement;
    for (const p of PREFS) root.setAttribute(p.attr, prefs[p.key]);
    /* `system` is resolved here rather than in CSS, so theme.css holds one
       light palette instead of the same one twice. */
    const theme = prefs.theme === "system"
      ? (darkQuery && !darkQuery.matches ? "light" : "dark")
      : prefs.theme;
    root.setAttribute("data-theme", theme);
    const motion = prefs.motion === "system"
      ? (motionQuery?.matches ? "reduce" : "full")
      : prefs.motion;
    root.setAttribute("data-motion", motion);
  }

  function set(key, value) {
    prefs = { ...prefs, [key]: value };
    store(prefs);
    apply();
  }

  apply();
  darkQuery?.addEventListener?.("change", () => { if (prefs.theme === "system") apply(); });
  motionQuery?.addEventListener?.("change", () => { if (prefs.motion === "system") apply(); });

  /* ------------------------------------------------------------ the dialog */
  const h = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  };

  function prefControl(p) {
    if (p.control === "segmented") {
      return h("div", { class: "segmented", role: "radiogroup", "aria-label": p.label },
        ...p.options.map(([value, text]) => h("label", {},
          h("input", {
            type: "radio", name: `pref-${p.key}`, value,
            checked: prefs[p.key] === value,
            onchange: () => set(p.key, value),
          }), text)));
    }
    return h("input", {
      type: "checkbox", role: "switch", "aria-label": p.label,
      checked: prefs[p.key] === "on",
      onchange: (e) => set(p.key, e.target.checked ? "on" : "off"),
    });
  }

  function prefsPanel() {
    const groups = [...new Set(PREFS.map((p) => p.group))];
    return groups.map((group) => h("section", { class: "settings-group" },
      h("h4", {}, group),
      ...PREFS.filter((p) => p.group === group).map((p) => h("div", { class: "settings-row" },
        h("div", { class: "what" }, h("strong", {}, p.label), h("small", {}, p.help)),
        prefControl(p)))));
  }

  /* The installation's settings, drawn from whatever the server returns so a
     new server setting appears here without a UI change. */
  const HUMAN = (key) => key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

  async function installationPanel(body, foot) {
    body.replaceChildren(h("p", { class: "hint" }, "Loading…"));
    let current;
    try {
      current = await window.dapi("/settings");
    } catch (err) {
      body.replaceChildren(h("p", { class: "hint warn" }, err.message));
      return;
    }
    const inputs = {};
    const row = (key, value) => {
      let input;
      if (typeof value === "boolean") {
        input = h("input", { type: "checkbox", role: "switch", checked: value });
      } else if (typeof value === "number") {
        input = h("input", { type: "number", value: String(value) });
      } else if (Array.isArray(value)) {
        input = h("input", { type: "text", value: value.join(", "),
                             placeholder: "comma separated" });
      } else if (value !== null && typeof value === "object") {
        return null;          // nested settings stay in the API; none today
      } else {
        input = h("input", { type: "text", value: value ?? "" });
      }
      inputs[key] = { input, value };
      return h("div", { class: "settings-row" },
        h("div", { class: "what" }, h("strong", {}, HUMAN(key)), h("small", {}, key)),
        input);
    };
    body.replaceChildren(h("section", { class: "settings-group" },
      h("h4", {}, "This installation — changes apply to everyone"),
      ...Object.entries(current).map(([k, v]) => row(k, v)).filter(Boolean)));

    const status = h("span", { class: "hint" }, "Only an administrator may change these.");
    const save = h("button", { type: "button", class: "primary", onclick: async () => {
      const changes = {};
      for (const [key, { input, value }] of Object.entries(inputs)) {
        let next;
        if (typeof value === "boolean") next = input.checked;
        else if (typeof value === "number") next = Number(input.value);
        else if (Array.isArray(value)) {
          next = input.value.split(",").map((x) => x.trim()).filter(Boolean);
        } else next = input.value;
        if (JSON.stringify(next) !== JSON.stringify(value)) changes[key] = next;
      }
      if (!Object.keys(changes).length) { status.textContent = "Nothing changed."; return; }
      try {
        await window.dapi("/settings", { method: "PUT", body: JSON.stringify(changes) });
        status.textContent = `Saved: ${Object.keys(changes).join(", ")}.`;
        installationPanel(body, foot);
      } catch (err) {
        status.textContent = err.message;
      }
    } }, "Save installation settings");
    foot.replaceChildren(status, save);
  }

  function openSettings(tab = "prefs") {
    const dialog = document.getElementById("settings-dialog");
    if (!dialog) return;
    const body = dialog.querySelector(".settings-body");
    const foot = dialog.querySelector(".settings-foot");
    dialog.querySelectorAll(".settings-tabs button").forEach((b) =>
      b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    if (tab === "prefs") {
      body.replaceChildren(...prefsPanel());
      foot.replaceChildren(
        h("span", { class: "hint" }, "Saved in this browser and applied at once."),
        h("button", { type: "button", onclick: () => {
          prefs = { ...defaults }; store(prefs); apply(); openSettings("prefs");
        } }, "Reset to defaults"),
        h("button", { type: "button", class: "primary",
                      onclick: () => dialog.close() }, "Done"));
    } else {
      installationPanel(body, foot);
    }
    if (!dialog.open) dialog.showModal();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const dialog = document.getElementById("settings-dialog");
    dialog?.querySelectorAll(".settings-tabs button").forEach((b) =>
      b.addEventListener("click", () => openSettings(b.dataset.tab)));
    dialog?.querySelector(".settings-close")?.addEventListener("click", () => dialog.close());
    dialog?.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
    document.getElementById("btn-designer-settings")
      ?.addEventListener("click", () => openSettings("prefs"));
  });

  window.designerSettings = { open: openSettings, get: () => ({ ...prefs }), set };
})();
