// Provisional dock (UI-12): the final dock API (docs/notas/ui-contratos.md, section 2) over the hosts that sit
// above the grid today. UI-15 replaces this file with js/dock.js (tabs); only the import path and the
// createDock options in main.js change, bind(ctx) and app-actions.js stay as they are.
//
// Two steps: createDock(...) exists before the AppContext (which holds it), bind(ctx) hands the context over.
// A panel host is created on the first open, in registration order, and is shown and hidden with `hidden`.
// A module that hides its own host (Escape inside it) is noticed through the hidden attribute, so isOpen()
// and the "open"/"close" events stay coherent.
import { S } from "./strings.js";

const REASONS = ["case", "view", "indexed", "derived"];

export function createDock({ root, before = null, storage = null, onFocusOut = () => {}, doc = root.ownerDocument }) {
  void storage; // the height of the bottom dock is remembered by UI-15, not here
  const panels = new Map(); // id -> entry
  const listeners = { open: new Set(), close: new Set(), activate: new Set(), resize: new Set() };
  let ctx = null;
  let order = 0;
  let current = null; // id of the panel opened last that is still open
  let generation = 0; // grows with every case change: an open() that started before it must not finish after it

  function emit(event, payload) {
    for (const fn of Array.from(listeners[event])) {
      try {
        fn(payload);
      } catch (err) {
        // A broken listener never breaks the dock.
      }
    }
  }

  // Focus that moves outside a panel means it no longer has the focus to give back when it closes.
  doc.addEventListener("focusin", (event) => {
    for (const entry of panels.values()) {
      if (entry.host && entry.focusWithin && !(typeof entry.host.contains === "function" && entry.host.contains(event.target))) {
        entry.focusWithin = false;
      }
    }
  });

  function requireBound(name) {
    if (ctx === null) {
      throw new Error("dock." + name + "() before bind(ctx)");
    }
  }

  function entryOf(id) {
    const entry = panels.get(id);
    if (!entry) {
      throw new Error("unknown panel: " + id);
    }
    return entry;
  }

  const isOpen = (id) => {
    const entry = panels.get(id);
    return Boolean(entry && entry.host && !entry.host.hidden);
  };

  // Emits open/close when the visible state differs from the last one announced.
  function sync(entry) {
    const open = Boolean(entry.host && !entry.host.hidden);
    if (open === entry.announced) {
      return;
    }
    entry.announced = open;
    if (open) {
      current = entry.def.id;
      emit("open", { id: entry.def.id });
      emit("activate", { id: entry.def.id });
    } else {
      if (current === entry.def.id) {
        current = null;
        for (const other of panels.values()) {
          if (other.announced) {
            current = other.def.id;
          }
        }
      }
      restoreFocus(entry);
      emit("close", { id: entry.def.id });
    }
  }

  // Whatever closes a panel (menu, its own Close button, Escape, a case change) gives the focus back to the
  // element that opened it, or to the grid when that is gone. Only when the focus was inside the panel.
  function restoreFocus(entry) {
    const host = entry.host;
    const active = doc.activeElement;
    const lost = !active || active === doc.body || (typeof host.contains === "function" && host.contains(active));
    const had = entry.focusWithin && lost;
    entry.focusWithin = false;
    const opener = entry.opener;
    entry.opener = null;
    if (!had) {
      return;
    }
    const connected = opener && (typeof opener.isConnected === "boolean" ? opener.isConnected : true);
    if (opener && connected && typeof opener.focus === "function") {
      opener.focus();
    } else {
      onFocusOut();
    }
  }

  function ensureHost(entry) {
    if (entry.host) {
      return entry.host;
    }
    const host = doc.createElement("section");
    host.className = "panel host-panel";
    host.id = entry.def.id + "-host";
    host.hidden = true;
    // Registration order is the visual order: before the next registered panel that already has a host.
    let ref = before;
    let best = Infinity;
    for (const other of panels.values()) {
      if (other.host && other.order > entry.order && other.order < best) {
        best = other.order;
        ref = other.host;
      }
    }
    root.insertBefore(host, ref);
    entry.host = host;
    host.addEventListener("focusin", () => {
      entry.focusWithin = true;
    });
    const View = doc.defaultView && doc.defaultView.MutationObserver;
    if (typeof View === "function") {
      new View(() => sync(entry)).observe(host, { attributes: true, attributeFilter: ["hidden"] });
    }
    return host;
  }

  function showError(err, entry) {
    const message = err && err.message ? err.message : S.errorGeneric;
    if (ctx && ctx.status) {
      ctx.status.message(message, { kind: "error" });
    }
    void entry;
  }

  async function mountOnce(entry) {
    if (entry.instance) {
      return entry.instance;
    }
    if (!entry.mounting) {
      const host = ensureHost(entry);
      host.textContent = "";
      entry.mounting = Promise.resolve()
        .then(() => entry.def.mount(host, ctx))
        .then(
          (instance) => {
            entry.instance = instance || {};
            return entry.instance;
          },
          (err) => {
            showError(err, entry);
            const failed = doc.createElement("div");
            failed.className = "ui-error";
            failed.textContent = S.actionUnavailable;
            host.textContent = "";
            host.appendChild(failed);
            return null;
          },
        )
        .finally(() => {
          entry.mounting = null;
        });
    }
    return entry.mounting;
  }

  // Where the focus goes when a panel opens: the panel's own focusTarget (PanelDef.focusTarget(host, instance), or
  // instance.focusTarget), else its title, else the first control, else the host itself.
  function focusPanel(entry) {
    const instance = entry.instance || {};
    const host = entry.host;
    let target = typeof entry.def.focusTarget === "function" ? entry.def.focusTarget(host, instance) : null;
    target = target || instance.focusTarget || null;
    if (!target && typeof host.querySelector === "function") {
      target = host.querySelector("h2, h3") || host.querySelector("input, select, textarea, button, a");
    }
    target = target || host;
    if (typeof target.setAttribute === "function" && !target.getAttribute("tabindex") && !/^(INPUT|SELECT|TEXTAREA|BUTTON|A)$/.test(String(target.tagName || "").toUpperCase())) {
      target.setAttribute("tabindex", "-1");
    }
    if (typeof target.focus === "function") {
      target.focus();
      entry.focusWithin = true;
    }
  }

  const dock = {
    bind(context) {
      if (ctx !== null) {
        throw new Error("dock.bind() called twice");
      }
      ctx = context;
    },

    register(def) {
      if (!def || typeof def.id !== "string" || def.id === "" || typeof def.mount !== "function") {
        throw new TypeError("panel needs an id and mount()");
      }
      if (panels.has(def.id)) {
        throw new Error("duplicate panel id: " + def.id);
      }
      if (typeof S[def.titleKey] !== "string" || S[def.titleKey] === "") {
        throw new TypeError("titleKey is not in strings.js: " + def.titleKey + " (" + def.id + ")");
      }
      for (const reason of def.listens || []) {
        if (!REASONS.includes(reason)) {
          throw new TypeError("unknown listens reason: " + reason + " (" + def.id + ")");
        }
      }
      order += 1;
      panels.set(def.id, {
        def, order, host: null, instance: null, mounting: null, announced: false, opener: null, focusWithin: false,
      });
    },

    async open(id, args, { focus = true } = {}) {
      requireBound("open");
      const entry = entryOf(id);
      if (entry.def.needsCase !== false && ctx.caseId() === null) {
        ctx.status.message(S.needCase, { kind: "error" });
        return false;
      }
      const wasOpen = isOpen(id);
      if (!wasOpen) {
        entry.opener = doc.activeElement || null;
      }
      const started = generation;
      const instance = await mountOnce(entry);
      if (started !== generation) {
        return false; // the case changed while the panel was mounting: this opening belonged to the old case
      }
      entry.host.hidden = false;
      sync(entry);
      if (instance === null) {
        return false;
      }
      try {
        if (args !== undefined && typeof instance.show === "function") {
          await instance.show(args);
        } else if (!wasOpen && typeof instance.refresh === "function") {
          await instance.refresh();
        }
      } catch (err) {
        showError(err, entry);
      }
      if (focus) {
        focusPanel(entry);
      }
      return true;
    },

    close(id) {
      requireBound("close");
      const entry = entryOf(id);
      if (!isOpen(id)) {
        return;
      }
      try {
        if (entry.instance && typeof entry.instance.closed === "function") {
          entry.instance.closed();
        }
      } catch (err) {
        showError(err, entry);
      }
      entry.host.hidden = true;
      sync(entry); // restores the focus
    },

    closeAll() {
      for (const id of panels.keys()) {
        dock.close(id);
      }
    },

    maximize() {}, // the bottom dock of UI-15 maximizes; the legacy hosts have no such state

    isOpen,
    active: () => current,

    // First phase of a case switch: every mounted panel forgets the old case, open, closed or idle.
    caseChanged() {
      requireBound("caseChanged");
      generation += 1;
      for (const [id, entry] of panels) {
        if (!entry.instance) {
          continue;
        }
        if (entry.def.closeOnCase && isOpen(id)) {
          dock.close(id);
        }
        try {
          if (typeof entry.instance.reset === "function") {
            entry.instance.reset();
          }
        } catch (err) {
          showError(err, entry);
        }
      }
    },

    // Visual update only: the open panels that listen to `reason` refresh. A closed panel refreshes when reopened.
    notify(reason) {
      requireBound("notify");
      for (const [id, entry] of panels) {
        if (!entry.instance || !(entry.def.listens || []).includes(reason) || !isOpen(id)) {
          continue;
        }
        try {
          const result = typeof entry.instance.update === "function"
            ? entry.instance.update(reason)
            : entry.instance.refresh && entry.instance.refresh();
          if (result && typeof result.catch === "function") {
            result.catch((err) => showError(err, entry));
          }
        } catch (err) {
          showError(err, entry);
        }
      }
    },

    on(event, fn) {
      if (!listeners[event]) {
        throw new TypeError("unknown dock event: " + event);
      }
      listeners[event].add(fn);
      return () => listeners[event].delete(fn);
    },
  };
  return dock;
}
