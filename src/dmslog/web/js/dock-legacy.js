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

  function emit(event, payload) {
    for (const fn of Array.from(listeners[event])) {
      try {
        fn(payload);
      } catch (err) {
        // A broken listener never breaks the dock.
      }
    }
  }

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
      emit("close", { id: entry.def.id });
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

  function focusPanel(entry) {
    const instance = entry.instance || {};
    let target = instance.focusTarget || null;
    if (!target && typeof entry.host.querySelector === "function") {
      target = entry.host.querySelector("h2, h3");
    }
    if (target && typeof target.focus === "function") {
      if (typeof target.setAttribute === "function" && !target.getAttribute("tabindex")) {
        target.setAttribute("tabindex", "-1");
      }
      target.focus();
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
      panels.set(def.id, { def, order, host: null, instance: null, mounting: null, announced: false, opener: null });
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
      const instance = await mountOnce(entry);
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
      const host = entry.host;
      const focusInside = Boolean(doc.activeElement && typeof host.contains === "function" && host.contains(doc.activeElement));
      try {
        if (entry.instance && typeof entry.instance.closed === "function") {
          entry.instance.closed();
        }
      } catch (err) {
        showError(err, entry);
      }
      host.hidden = true;
      sync(entry);
      if (focusInside) {
        const opener = entry.opener;
        const connected = opener && (typeof opener.isConnected === "boolean" ? opener.isConnected : true);
        if (opener && connected && typeof opener.focus === "function") {
          opener.focus();
        } else {
          onFocusOut();
        }
      }
      entry.opener = null;
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
