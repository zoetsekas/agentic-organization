/* Shared interface pieces: one dialog, one toast, one tab pattern, one empty
   state and one live region (ADR-0117).

   Each of these existed several times over, slightly differently, or not at
   all. The browser's own alert/confirm/prompt were the dialogs: they cannot
   be styled, cannot validate a field, cannot say which field is wrong, block
   the whole page (including the lock heartbeat this app does not yet send),
   and a screen reader announces them as bare text with no labelled control.
   Tabs were four hand-written sets, only some of which set aria-selected and
   none of which answered an arrow key. So the patterns live here, once, and
   app.js, canvas.js and settings.js call them.

   Loaded before app.js and canvas.js and self-contained: it cannot lean on
   their `$` and `el`, because they do not exist yet when this runs. */
(function () {
  "use strict";

  const h = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  };

  let uid = 0;
  const nextId = (stem) => `${stem}-${++uid}`;

  /* ------------------------------------------------------------ live region
     A change a sighted person sees happen on the canvas — a selection, a
     link begun, a node moved — is otherwise silent to a screen reader. One
     polite region, cleared and refilled, so the same sentence twice is still
     read twice. */
  function announce(text) {
    const live = document.getElementById("sr-live");
    if (!live) return;
    live.textContent = "";
    // A new text node in a later task is what makes a repeat announce.
    setTimeout(() => { live.textContent = text; }, 30);
  }

  /* ----------------------------------------------------------------- modal */
  const openDialogs = [];

  /* True while any modal is up — this helper's, or one of the page's own
     <dialog>s. Global shortcuts check it: Delete pressed in a dialog must
     not remove the node behind it. */
  function modalOpen() {
    return openDialogs.length > 0 || !!document.querySelector("dialog[open]");
  }

  const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), "
    + "select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

  /* showModal() makes the page behind inert, but Tab from the last control
     still walks out into the browser's own chrome and back in at the top of
     the page's order in some browsers. The trap keeps it inside. */
  function trapFocus(dialog, e) {
    if (e.key !== "Tab") return;
    const items = [...dialog.querySelectorAll(FOCUSABLE)]
      .filter((n) => n.offsetParent !== null || n === document.activeElement);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }

  /* The one dialog. Resolves with `values` (an object keyed by field name)
     on submit, or null on cancel/Escape — never throws, so a caller writes
     `if (!answer) return`.

     fields: [{ name, label, type: text|textarea|number|integer|select|radio,
                value, required, min, max, options: [[value, label, help?]],
                help, placeholder, validate(value, all) -> message|"" }]

     Validation is inline and per field (ADR-0106's form rule): the reason is
     written under the field that is wrong, the field is aria-invalid and
     described by it, and focus goes to the first one wrong. A dialog that
     closed and then said "not a number" in an alert is what this replaces. */
  function formDialog({ title, message = "", fields = [], submitLabel = "OK",
                        cancelLabel = "Cancel", danger = false, wide = false }) {
    return new Promise((resolve) => {
      const opener = document.activeElement;
      const titleId = nextId("dlg-title");
      const descId = nextId("dlg-desc");
      const controls = {};
      const errors = {};

      const rows = fields.map((f) => {
        const id = nextId(`dlg-${f.name}`);
        const errId = `${id}-err`;
        const helpId = f.help ? `${id}-help` : null;
        const described = [helpId, errId].filter(Boolean).join(" ");
        let control;
        if (f.type === "select") {
          control = h("select", { id, name: f.name, "aria-describedby": described },
            ...(f.options || []).map(([v, text]) =>
              h("option", { value: v, selected: String(f.value ?? "") === String(v) }, text ?? v)));
        } else if (f.type === "radio") {
          /* A choice whose options need a sentence each — which relationship
             a line is — reads better as a list than as a dropdown. */
          control = h("div", { class: "dlg-radios", role: "radiogroup", id,
                               "aria-labelledby": `${id}-label`,
                               "aria-describedby": described },
            ...(f.options || []).map(([v, text, help], i) => h("label", { class: "dlg-radio" },
              h("input", { type: "radio", name: f.name, value: v,
                           checked: f.value !== undefined ? String(f.value) === String(v) : i === 0 }),
              h("span", {}, h("strong", {}, text ?? v), help ? h("small", {}, help) : null))));
        } else if (f.type === "textarea") {
          control = h("textarea", { id, name: f.name, rows: f.rows || 3,
                                    placeholder: f.placeholder, "aria-describedby": described });
          control.value = f.value ?? "";
        } else {
          const numeric = f.type === "number" || f.type === "integer";
          control = h("input", {
            id, name: f.name, type: numeric ? "number" : (f.type || "text"),
            step: f.type === "integer" ? "1" : f.step, min: f.min, max: f.max,
            inputmode: f.type === "integer" ? "numeric" : null,
            placeholder: f.placeholder, autocomplete: "off",
            "aria-describedby": described,
          });
          control.value = f.value ?? "";
        }
        if (f.required) control.setAttribute("aria-required", "true");
        controls[f.name] = { field: f, control };
        const error = h("div", { class: "field-error", id: errId, role: "alert" });
        errors[f.name] = error;
        const clear = () => {
          error.textContent = "";
          control.removeAttribute("aria-invalid");
        };
        control.addEventListener("input", clear);
        control.addEventListener("change", clear);
        return h("div", { class: "dlg-field" },
          h(f.type === "radio" ? "div" : "label",
            { class: "dlg-label", id: `${id}-label`, for: f.type === "radio" ? null : id },
            f.label, f.required ? h("span", { class: "req", "aria-hidden": "true" }, " *") : null),
          control,
          f.help ? h("small", { class: "hint", id: helpId }, f.help) : null,
          error);
      });

      const read = (name) => {
        const { field, control } = controls[name];
        if (field.type === "radio") {
          return control.querySelector("input:checked")?.value ?? "";
        }
        return control.value;
      };

      const problem = (f, raw, all) => {
        const text = typeof raw === "string" ? raw.trim() : raw;
        if (f.required && (text === "" || text == null)) return `${f.label} is required.`;
        if (text === "" && !f.required) return "";
        if (f.type === "integer" || f.type === "number") {
          const n = Number(text);
          if (!Number.isFinite(n)) return `${f.label} must be a number.`;
          if (f.type === "integer" && !Number.isInteger(n)) {
            return `${f.label} must be a whole number.`;
          }
          if (f.min !== undefined && n < f.min || f.max !== undefined && n > f.max) {
            return `${f.label} must be from ${f.min} to ${f.max}.`;
          }
        }
        return f.validate ? (f.validate(text, all) || "") : "";
      };

      const form = h("form", { class: "dlg-form", method: "dialog", novalidate: true });
      const submit = h("button", { type: "submit", class: danger ? "danger primary" : "primary" },
        submitLabel);
      // No Cancel on a statement: "Close" beside "OK" offered two ways to say one thing.
      const cancel = cancelLabel
        ? h("button", { type: "button", value: "cancel" }, cancelLabel) : null;
      const formError = h("div", { class: "field-error", role: "alert" });

      form.append(...[
        h("h3", { id: titleId }, title),
        message ? h("div", { class: "dlg-message", id: descId },
          ...String(message).split("\n\n").map((p) => h("p", {}, p))) : null,
        ...rows,
        formError,
        h("div", { class: "dlg-actions" }, cancel, submit)].filter(Boolean));

      const dialog = h("dialog", {
        class: `dialog ui-dialog${wide ? " wide" : ""}`,
        "aria-labelledby": titleId,
        "aria-describedby": message ? descId : null,
        role: danger ? "alertdialog" : null,
      }, form);

      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        openDialogs.splice(openDialogs.indexOf(dialog), 1);
        if (dialog.open) dialog.close();
        dialog.remove();
        /* Back where the person was: a dialog that drops focus on <body>
           strands a keyboard user at the top of the page. */
        if (opener && document.contains(opener)) opener.focus?.({ preventScroll: true });
        resolve(value);
      };

      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const all = Object.fromEntries(Object.keys(controls).map((n) => [n, read(n)]));
        let firstBad = null;
        for (const [name, { field, control }] of Object.entries(controls)) {
          const why = problem(field, all[name], all);
          errors[name].textContent = why;
          if (why) {
            control.setAttribute("aria-invalid", "true");
            firstBad = firstBad || control;
          } else control.removeAttribute("aria-invalid");
        }
        if (firstBad) {
          (firstBad.querySelector?.("input") || firstBad).focus();
          return;
        }
        for (const [name, { field }] of Object.entries(controls)) {
          if (field.type === "integer" || field.type === "number") {
            all[name] = all[name] === "" ? null : Number(all[name]);
          } else if (typeof all[name] === "string") all[name] = all[name].trim();
        }
        finish(all);
      });
      cancel?.addEventListener("click", () => finish(null));
      dialog.addEventListener("cancel", (e) => { e.preventDefault(); finish(null); });
      dialog.addEventListener("keydown", (e) => {
        e.stopPropagation();          // the canvas's shortcuts stay behind the modal
        if (e.key === "Escape") { e.preventDefault(); finish(null); }
        trapFocus(dialog, e);
      });

      document.body.append(dialog);
      openDialogs.push(dialog);
      dialog.showModal();
      /* The first field, or — for a plain confirmation — the safe choice:
         Enter on a destructive question must not be the destructive answer. */
      const first = rows.length
        ? (Object.values(controls)[0].control.querySelector?.("input:checked, input")
           || Object.values(controls)[0].control)
        : (danger && cancel ? cancel : submit);
      first.focus();
      if (first.select && first.type !== "radio") first.select();
    });
  }

  /* Yes or no. Resolves true or false. */
  async function confirmDialog(message, { title = "Are you sure?", confirmLabel = "OK",
                                          cancelLabel = "Cancel", danger = false } = {}) {
    const answer = await formDialog({ title, message, submitLabel: confirmLabel,
                                      cancelLabel, danger });
    return answer !== null;
  }

  /* A statement with one button. Resolves when it is dismissed. */
  async function alertDialog(message, { title = "Cannot do that" } = {}) {
    await formDialog({ title, message, submitLabel: "OK", cancelLabel: null });
  }

  /* One value. Resolves the trimmed string, or null. */
  async function promptDialog(title, { label = "Value", value = "", message = "",
                                       required = true, type = "text", validate,
                                       min, max, submitLabel = "OK", help } = {}) {
    const answer = await formDialog({
      title, message, submitLabel,
      fields: [{ name: "value", label, value, required, type, validate, min, max, help }],
    });
    return answer ? answer.value : null;
  }

  /* ----------------------------------------------------------------- toast
     A change that is easy to miss — a node gone, a link removed — says so
     where it will be seen, and offers the way back. The undo itself is the
     caller's (the canvas has a history; the catalog does not), so the toast
     only carries the button. */
  function toast(message, { action = null, onAction = null, timeout = 8000,
                            tone = "" } = {}) {
    let host = document.getElementById("toasts");
    if (!host) {
      host = h("div", { id: "toasts", class: "toasts", role: "region",
                        "aria-label": "Notifications" });
      document.body.append(host);
    }
    const close = h("button", { type: "button", class: "icon-btn toast-close",
                                "aria-label": "Dismiss" },
      h("span", { "aria-hidden": "true" }, "×"));
    const item = h("div", { class: `toast${tone ? ` ${tone}` : ""}`, role: "status" },
      h("span", { class: "grow" }, message),
      action ? h("button", { type: "button", class: "toast-action" }, action) : null,
      close);
    let timer = null;
    const dismiss = () => { clearTimeout(timer); item.remove(); };
    const arm = () => { clearTimeout(timer); if (timeout) timer = setTimeout(dismiss, timeout); };
    close.addEventListener("click", dismiss);
    if (action) {
      item.querySelector(".toast-action").addEventListener("click", () => {
        dismiss();
        onAction?.();
      });
    }
    // Hovering or focusing it holds it: nobody should race a timer to undo.
    item.addEventListener("pointerenter", () => clearTimeout(timer));
    item.addEventListener("pointerleave", arm);
    item.addEventListener("focusin", () => clearTimeout(timer));
    item.addEventListener("focusout", arm);
    host.append(item);
    while (host.children.length > 3) host.firstElementChild.remove();
    arm();
    return { dismiss };
  }

  /* ------------------------------------------------------------------ tabs
     The WAI-ARIA tabs pattern, once: role=tablist/tab/tabpanel, aria-selected,
     aria-controls, one tab in the Tab order, arrows/Home/End to move.

     The tab sets already had click handlers that do the switching (showSide,
     showLeft, openSettings, openDiagram), so an arrow key activates a tab by
     clicking it — the one behaviour, reached two ways — and the ARIA state is
     re-read from the DOM whenever it changes rather than tracked twice.

     options.tabs: selector for the tabs inside the list (default the
     children with role=tab or a button). options.panel(tab) -> the panel
     element or its id, for aria-controls. */
  function wireTabs(list, { tabs = null, panel = null, label = null,
                            orientation = "horizontal" } = {}) {
    if (!list || list.dataset.tabsWired) return;
    list.dataset.tabsWired = "1";
    list.setAttribute("role", "tablist");
    if (label) list.setAttribute("aria-label", label);
    if (orientation === "vertical") list.setAttribute("aria-orientation", "vertical");
    const all = () => [...list.querySelectorAll(tabs || ":scope > button, :scope > [role=tab]")];
    const isOn = (t) => t.getAttribute("aria-selected") === "true"
      || t.classList.contains("active");

    let syncing = false;
    const sync = () => {
      if (syncing) return;
      syncing = true;
      const items = all();
      const current = items.find(isOn) || items[0];
      for (const t of items) {
        if (!t.id) t.id = nextId("tab");
        t.setAttribute("role", "tab");
        const on = t === current;
        if (t.getAttribute("aria-selected") !== String(on)) {
          t.setAttribute("aria-selected", String(on));
        }
        t.tabIndex = on ? 0 : -1;
        const target = panel?.(t);
        const node = typeof target === "string" ? document.getElementById(target) : target;
        if (node) {
          if (!node.id) node.id = nextId("tabpanel");
          t.setAttribute("aria-controls", node.id);
          node.setAttribute("role", "tabpanel");
          node.setAttribute("aria-labelledby", t.id);
        }
      }
      // Attribute writes above re-trigger the observer; let them settle.
      queueMicrotask(() => { syncing = false; });
    };

    list.addEventListener("keydown", (e) => {
      const items = all();
      const at = items.indexOf(document.activeElement);
      if (at < 0) return;
      const back = orientation === "vertical" ? "ArrowUp" : "ArrowLeft";
      const fwd = orientation === "vertical" ? "ArrowDown" : "ArrowRight";
      let to = null;
      if (e.key === fwd) to = (at + 1) % items.length;
      else if (e.key === back) to = (at - 1 + items.length) % items.length;
      else if (e.key === "Home") to = 0;
      else if (e.key === "End") to = items.length - 1;
      if (to === null) return;
      e.preventDefault();
      e.stopPropagation();
      items[to].focus();
      items[to].click();
    });
    new MutationObserver(sync).observe(list, {
      subtree: true, childList: true, attributes: true,
      attributeFilter: ["class", "aria-selected"],
    });
    sync();
  }

  /* ----------------------------------------------------------- empty state
     "Nothing here" said once, the same way everywhere, and always with what
     to do next — an empty panel with no next step reads as a broken one. */
  function emptyState({ title, body = "", action = null, onAction = null,
                        href = null, tone = "" }) {
    return h("div", { class: `empty-state${tone ? ` ${tone}` : ""}` },
      h("strong", { class: "empty-title" }, title),
      body ? h("p", {}, body) : null,
      action && (onAction || href)
        ? (href
            ? h("a", { class: "button", href }, action)
            : h("button", { type: "button", onclick: onAction }, action))
        : null);
  }

  window.ui = {
    announce, modalOpen, formDialog, confirmDialog, alertDialog, promptDialog,
    toast, wireTabs, emptyState,
  };
})();
