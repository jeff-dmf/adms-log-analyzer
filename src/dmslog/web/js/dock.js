// The bottom dock (UI-15): analysis panels open as tabs under the grid instead of stacking above it.
// API and two-step creation are the contract of docs/notas/ui-contratos.md, section 2.2 (the same API the
// provisional dock of UI-12 had): createDock(deps) exists before the AppContext, bind(ctx) hands the context over.
// The rules (limits, heights, keys, tab order) are pure and live in dock-model.js; this file is the DOM.
//
// A panel host is created on the first open and kept for the session (closing a tab does not destroy the panel's
// state). `host.hidden` means "closed": a module that hides its own host (Escape inside it, its Close button) is
// noticed through the attribute, so isOpen() and the "open"/"close" events stay coherent. Which open tab shows is
// the class `is-active` on the host. Content is only ever written with textContent.
import { ROW_HEIGHT } from "./grid-model.js";
import { S, format } from "./strings.js";

// The rules (dock-model.js) load on the first open(): nothing of the dock's layout runs before a tab exists, and
// the opening of the viewer pays for one module less (UI-14, docs/notas/ui-contratos.md, section 2.3).
const model = {};
let modelLoading = null;
function loadModel() {
  if (model.limits) {
    return Promise.resolve(model);
  }
  if (modelLoading === null) {
    modelLoading = import("./dock-model.js").then(
      (loaded) => Object.assign(model, loaded),
      (err) => {
        modelLoading = null; // a later open() tries again
        throw err;
      },
    );
  }
  return modelLoading;
}

const REASONS = ["case", "view", "indexed", "derived"];
const DIVIDER_KEYS = ["ArrowUp", "ArrowDown", "Home", "End"];

// What the dock needs to know about the page. Replaceable in tests (the fake DOM has no layout).
//   available(): height shared by the grid (header + rows) and the dock, or null when the grid is hidden
//   gridHead(): the grid header plus the viewport chrome; content(): the natural height of the dock
export function domMeasure(root, doc) {
  const byId = (id) => (typeof doc.getElementById === "function" ? doc.getElementById(id) : null);
  let others = null; // height of everything in the grid area that is neither the grid nor the dock
  let headHeight = 0; // the grid header, remembered while the maximized dock hides it
  const container = () => (root.parentElement ? root.parentElement.clientHeight : 0);
  return {
    available() {
      const viewport = byId("grid-viewport");
      const header = byId("grid-header");
      if (header && header.offsetHeight > 0) {
        headHeight = header.offsetHeight;
      }
      if (viewport && header && viewport.offsetHeight > 0) {
        const shared = viewport.offsetHeight + header.offsetHeight + (root.hidden ? 0 : root.offsetHeight);
        if (container() > 0) {
          others = container() - shared;
        }
        return shared;
      }
      // The grid is hidden (dock maximized): the space is whatever the container has now, minus the same others.
      return others !== null ? Math.max(0, container() - others) : null;
    },
    gridHead() {
      const header = byId("grid-header");
      if (header && header.offsetHeight > 0) {
        headHeight = header.offsetHeight;
      }
      return headHeight + model.GRID_CHROME_PX;
    },
    content() {
      root.style.height = "";
      return root.offsetHeight;
    },
    container,
  };
}

export function createDock({
  root, storage = null, onFocusOut = () => {}, doc = root.ownerDocument, measure = null,
  windowHeight = () => (doc.defaultView && doc.defaultView.innerHeight) || 900, rowHeight = ROW_HEIGHT,
  before = null, // ignored: kept so the options of the provisional dock still work
}) {
  void before;
  const view = measure || domMeasure(root, doc);
  const panels = new Map(); // id -> entry
  const listeners = { open: new Set(), close: new Set(), activate: new Set(), resize: new Set() };
  let ctx = null;
  let order = 0;
  let tabs = []; // ids of the open tabs, in the order they were opened
  let active = null;
  let generation = 0; // grows with every case change: an open() that started before it must not finish after it
  let maximized = false;
  let lastAvailable = null;
  let sessionFraction = null; // the height of this opening of the dock (not remembered when no one resized it)
  let storedFraction = null; // the remembered height: only the divider sets it
  let lastHeight = 0;
  let dockOpener = null;

  let storageRead = false;
  function readStored() {
    if (storageRead) {
      return;
    }
    storageRead = true;
    try {
      storedFraction = storage ? model.parseStored(storage.getItem(model.STORAGE_KEY)) : null;
    } catch (err) {
      storedFraction = null; // storage blocked: the height lives for the session only
    }
  }

  // ---- structure ----------------------------------------------------------------------------------------
  const el = (tag, className) => {
    const node = doc.createElement(tag);
    if (className) {
      node.className = className;
    }
    return node;
  };
  root.hidden = true;
  const divider = el("div", "dock-divider");
  divider.setAttribute("role", "separator");
  divider.setAttribute("aria-orientation", "horizontal");
  divider.setAttribute("aria-label", S.dockResize);
  divider.setAttribute("tabindex", "0");
  const bar = el("div", "dock-bar");
  const tablist = el("div", "dock-tabs");
  tablist.setAttribute("role", "tablist");
  tablist.setAttribute("aria-label", S.dockLabel);
  const actions = el("div", "dock-actions");
  const maxButton = el("button", "ui-button is-ghost dock-max");
  maxButton.setAttribute("type", "button");
  const closeAllButton = el("button", "ui-button is-ghost dock-close-all");
  closeAllButton.setAttribute("type", "button");
  closeAllButton.textContent = S.dockCloseAll;
  actions.append(maxButton, closeAllButton);
  bar.append(tablist, actions);
  const body = el("div", "dock-body");
  root.append(divider, bar, body);

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
  const titleOf = (entry) => S[entry.def.titleKey];

  // ---- layout -------------------------------------------------------------------------------------------
  function bounds() {
    const measured = view.available();
    // 0 is a real measure (no room at all); only null, undefined and NaN mean "could not measure".
    if (typeof measured === "number" && Number.isFinite(measured) && measured >= 0) {
      lastAvailable = measured;
    }
    const fallback = Number(view.container());
    const available = lastAvailable !== null ? lastAvailable : (Number.isFinite(fallback) && fallback > 0 ? fallback : 0);
    return { available, limits: model.limits({ available, rowHeight, gridHead: view.gridHead() }) };
  }

  function paintDivider(height, available, bound) {
    const low = model.valueNow(bound.min, available);
    const high = model.valueNow(bound.max, available);
    const now = maximized ? high : Math.min(Math.max(model.valueNow(height, available), low), high); // min <= now <= max
    divider.setAttribute("aria-valuenow", String(now));
    divider.setAttribute("aria-valuemin", String(low));
    divider.setAttribute("aria-valuemax", String(high));
  }

  function paintMaximize() {
    maxButton.textContent = maximized ? S.dockRestore : S.dockMaximize;
    maxButton.setAttribute("aria-pressed", maximized ? "true" : "false");
  }

  // Puts the dock where it belongs: hidden without tabs, maximized, or at its height inside the limits.
  // `fresh`: the dock just opened with its first tab, so its height may follow the content.
  function layout({ fresh = false } = {}) {
    if (tabs.length === 0) {
      root.hidden = true;
      root.classList.remove("is-maximized");
      root.style.height = "";
      return;
    }
    root.hidden = false;
    if (fresh && model.shouldStartMaximized(windowHeight())) {
      maximized = true;
    }
    const { available, limits } = bounds();
    if (limits.maximizedOnly) {
      maximized = true; // no room for the grid minimum plus a 160 px dock: the dock takes the whole space
    }
    root.classList.toggle("is-maximized", maximized);
    if (maximized) {
      root.style.height = "";
    } else {
      let fraction = sessionFraction !== null ? sessionFraction : storedFraction; // this opening's height first, the remembered one seeds it
      let height;
      if (fraction === null) {
        height = model.initialHeight({ storedFraction: null, contentHeight: view.content(), available, limits });
      } else {
        height = model.initialHeight({ storedFraction: fraction, contentHeight: 0, available, limits });
      }
      fraction = model.fractionOf(height, available);
      sessionFraction = fraction;
      lastHeight = height;
      root.style.height = height + "px";
    }
    paintDivider(lastHeight, available, limits);
    paintMaximize();
    maxButton.disabled = limits.maximizedOnly;
    emit("resize", { height: maximized ? null : lastHeight, fraction: sessionFraction, maximized });
  }

  function remember(fraction) {
    storedFraction = fraction;
    sessionFraction = fraction;
    try {
      if (storage) {
        storage.setItem(model.STORAGE_KEY, model.formatStored(fraction));
      }
    } catch (err) {
      // No storage: the height is kept for this session.
    }
  }

  // The divider moved the dock to `height` px (clamped). `persist` is false while dragging.
  function setHeight(height, { persist }) {
    const { available, limits } = bounds();
    if (limits.maximizedOnly) {
      return;
    }
    const next = Math.round(Math.min(Math.max(height, limits.min), limits.max));
    maximized = false;
    sessionFraction = model.fractionOf(next, available);
    if (persist) {
      remember(sessionFraction);
    }
    layout();
  }

  function adjust(compute, { persist }) {
    if (tabs.length === 0) {
      return;
    }
    const { available, limits } = bounds();
    if (maximized && !limits.maximizedOnly) {
      lastHeight = limits.max; // leaving the maximized state: the largest normal height is the starting point
    }
    setHeight(compute(lastHeight, { available, limits }), { persist });
  }

  divider.addEventListener("keydown", (event) => {
    if (!DIVIDER_KEYS.includes(event.key) || tabs.length === 0) {
      return;
    }
    if (maximized && (event.key === "ArrowUp" || event.key === "End")) {
      return; // already as large as it gets
    }
    event.preventDefault();
    adjust((height, b) => model.keyStep(height, event.key, b), { persist: true });
  });
  divider.addEventListener("dblclick", () => {
    const { available, limits } = bounds();
    const target = model.doubleClickTarget({ maximized, available, limits });
    if (target.maximized) {
      dock.maximize(true);
    } else {
      setHeight(target.height, { persist: true });
    }
  });
  divider.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) {
      return;
    }
    event.preventDefault();
    if (maximized) {
      adjust((height) => height, { persist: false }); // grab the largest normal height
    }
    const startHeight = lastHeight;
    const startY = event.clientY;
    root.classList.add("is-dragging");
    if (typeof divider.setPointerCapture === "function" && event.pointerId !== undefined) {
      try {
        divider.setPointerCapture(event.pointerId);
      } catch (err) {
        // The pointer is gone already; the document listeners below still end the drag.
      }
    }
    const move = (moveEvent) => {
      adjust((height, b) => model.dragHeight({ startHeight, startY, y: moveEvent.clientY, limits: b.limits }), { persist: false });
    };
    const up = () => {
      doc.removeEventListener("pointermove", move);
      doc.removeEventListener("pointerup", up);
      doc.removeEventListener("pointercancel", up);
      root.classList.remove("is-dragging");
      remember(sessionFraction === null ? model.fractionOf(lastHeight, bounds().available) : sessionFraction);
    };
    doc.addEventListener("pointermove", move);
    doc.addEventListener("pointerup", up);
    doc.addEventListener("pointercancel", up);
  });
  maxButton.addEventListener("click", () => dock.maximize());
  closeAllButton.addEventListener("click", () => dock.closeAll());

  // The window or the grid area changed size: the same fraction applies to the new space.
  const ResizeObserverClass = (doc.defaultView && doc.defaultView.ResizeObserver) || globalThis.ResizeObserver;
  if (typeof ResizeObserverClass === "function" && root.parentElement) {
    new ResizeObserverClass(() => {
      if (tabs.length > 0) {
        layout();
      }
    }).observe(root.parentElement);
  }

  // ---- focus --------------------------------------------------------------------------------------------
  // Focus that moves outside a panel means it no longer has the focus to give back when it closes.
  doc.addEventListener("focusin", (event) => {
    for (const entry of panels.values()) {
      if (entry.host && entry.focusWithin && !(typeof entry.host.contains === "function" && entry.host.contains(event.target))) {
        entry.focusWithin = false;
      }
    }
  });

  // Whatever closes a panel (its tab, Close all, its own Close button, Escape, a case change) gives the focus back
  // to the element that opened it, or to the grid when that is gone. Only when the focus was inside the panel
  // (or on its tab, which disappears with it).
  function restoreFocus(entry, inTab) {
    const host = entry.host;
    const current = doc.activeElement;
    const lost = !current || current === doc.body || inTab || root.contains(current) || (typeof host.contains === "function" && host.contains(current));
    const had = (entry.focusWithin || inTab) && lost;
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

  // ---- tabs ---------------------------------------------------------------------------------------------
  function buildTab(entry) {
    const id = entry.def.id;
    const cell = el("div", "dock-tab");
    cell.setAttribute("role", "presentation");
    const tab = el("button", "dock-tab-button");
    tab.setAttribute("type", "button");
    tab.setAttribute("role", "tab");
    tab.id = "dock-tab-" + id;
    tab.setAttribute("aria-controls", entry.host.id);
    tab.textContent = titleOf(entry);
    tab.addEventListener("click", () => {
      activate(id);
    });
    tab.addEventListener("keydown", (event) => {
      if (event.key === "Delete") {
        event.preventDefault();
        dock.close(id);
        return;
      }
      const target = model.nextTab(tabs, id, event.key);
      if (target !== null) {
        event.preventDefault();
        activate(target);
        const next = panels.get(target);
        if (next && next.tabButton && typeof next.tabButton.focus === "function") {
          next.tabButton.focus();
        }
      }
    });
    const close = el("button", "dock-tab-close");
    close.setAttribute("type", "button");
    close.setAttribute("tabindex", "-1");
    close.setAttribute("aria-label", format(S.dockCloseTab, { title: titleOf(entry) }));
    close.addEventListener("click", () => dock.close(id));
    cell.append(tab, close);
    entry.tab = cell;
    entry.tabButton = tab;
    return cell;
  }

  function paintTabs() {
    for (const [id, entry] of panels) {
      if (!entry.tabButton) {
        continue;
      }
      const on = id === active;
      entry.tabButton.setAttribute("aria-selected", on ? "true" : "false");
      entry.tabButton.setAttribute("tabindex", on ? "0" : "-1");
      entry.tab.classList.toggle("is-active", on);
      entry.host.classList.toggle("is-active", on);
    }
  }

  // Makes an open tab the active one. A panel that listened to a reason while its tab was behind refreshes once.
  function activate(id) {
    const entry = panels.get(id);
    if (!entry || !isOpen(id)) {
      return;
    }
    const changed = active !== id;
    active = id;
    paintTabs();
    if (changed) {
      emit("activate", { id });
    }
    if (entry.stale) {
      entry.stale = false;
      refreshEntry(entry);
    }
  }

  function refreshEntry(entry) {
    const instance = entry.instance;
    if (!instance || typeof instance.refresh !== "function") {
      return;
    }
    try {
      const result = instance.refresh();
      if (result && typeof result.catch === "function") {
        result.catch((err) => showError(err));
      }
    } catch (err) {
      showError(err);
    }
  }

  // Emits open/close when the visible state differs from the last one announced; keeps the tab list in step.
  function sync(entry) {
    const open = Boolean(entry.host && !entry.host.hidden);
    if (open === entry.announced) {
      return;
    }
    entry.announced = open;
    const id = entry.def.id;
    if (open) {
      const first = tabs.length === 0;
      tabs.push(id);
      tablist.appendChild(entry.tab || buildTab(entry));
      active = id;
      paintTabs();
      layout({ fresh: first });
      emit("open", { id });
      emit("activate", { id });
    } else {
      const result = model.tabsAfterClose(tabs, active, id);
      tabs = result.tabs;
      const previous = active;
      active = result.active;
      entry.stale = false;
      // Read before the tab leaves the page. When the last tab goes the whole dock disappears with whatever had the
      // focus in it (Close all, Maximize), so the focus must be given back too.
      const inTab = Boolean(entry.tab && entry.tab.contains(doc.activeElement))
        || (result.tabs.length === 0 && root.contains(doc.activeElement));
      if (entry.tab) {
        entry.tab.remove();
      }
      entry.host.classList.remove("is-active");
      restoreFocus(entry, inTab);
      if (tabs.length === 0) {
        maximized = false;
        sessionFraction = null; // the next opening takes its height from the content again
      }
      paintTabs();
      layout();
      emit("close", { id });
      if (active !== null && active !== previous) {
        const next = panels.get(active);
        emit("activate", { id: active });
        if (next && next.stale) {
          next.stale = false;
          refreshEntry(next);
        }
      }
    }
  }

  // Escape closes the tab the focus is in and gives the focus back, unless a menu, popover or field of the panel
  // already handled the key.
  root.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented) {
      return;
    }
    let found = null;
    for (const [id, entry] of panels) {
      if (isOpen(id) && entry.host && typeof entry.host.contains === "function" && entry.host.contains(event.target)) {
        found = id;
      }
    }
    if (found === null && active !== null && tablist.contains(event.target)) {
      found = active;
    }
    if (found !== null) {
      event.preventDefault();
      dock.close(found);
    }
  });

  // ---- panels -------------------------------------------------------------------------------------------
  function showError(err) {
    const message = err && err.message ? err.message : S.errorGeneric;
    if (ctx && ctx.status) {
      ctx.status.message(message, { kind: "error" });
    }
  }

  function ensureHost(entry) {
    if (entry.host) {
      return entry.host;
    }
    const host = el("div", "dock-panel panel");
    host.id = entry.def.id + "-host";
    host.setAttribute("role", "tabpanel");
    host.setAttribute("aria-labelledby", "dock-tab-" + entry.def.id);
    host.hidden = true;
    body.appendChild(host);
    entry.host = host;
    host.addEventListener("focusin", () => {
      entry.focusWithin = true;
    });
    const Observer = doc.defaultView && doc.defaultView.MutationObserver;
    if (typeof Observer === "function") {
      new Observer(() => sync(entry)).observe(host, { attributes: true, attributeFilter: ["hidden"] });
    }
    return host;
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
          () => {
            // A rejected import() or a failing mount: say which panel, leave the cause out of the user's way.
            const text = format(S.panelLoadFailed, { panel: titleOf(entry) });
            if (ctx && ctx.status) {
              ctx.status.message(text, { kind: "error" });
            }
            const failed = el("div", "ui-error");
            failed.textContent = text;
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
        tab: null, tabButton: null, stale: false,
      });
    },

    async open(id, args, { focus = true } = {}) {
      requireBound("open");
      const entry = entryOf(id);
      if (entry.def.needsCase !== false && ctx.caseId() === null) {
        ctx.status.message(S.needCase, { kind: "error" });
        return false;
      }
      const started = generation;
      try {
        await loadModel();
      } catch (err) {
        ctx.status.message(format(S.panelLoadFailed, { panel: titleOf(entry) }), { kind: "error" });
        return false;
      }
      readStored();
      const wasOpen = isOpen(id);
      if (!wasOpen) {
        // The element outside the dock that had the focus when the dock opened; a panel opened from inside the
        // dock (focus in another tab) gives the focus back to that same element.
        const active = doc.activeElement || null;
        if (active && active !== doc.body && !root.contains(active)) {
          dockOpener = active;
        } else if (tabs.length === 0) {
          dockOpener = null;
        }
        entry.opener = dockOpener;
      }
      const instance = await mountOnce(entry);
      if (started !== generation) {
        return false; // the case changed while the panel was mounting: this opening belonged to the old case
      }
      entry.host.hidden = false;
      sync(entry);
      activate(id);
      if (instance === null) {
        return false;
      }
      entry.stale = false;
      try {
        if (args !== undefined && typeof instance.show === "function") {
          await instance.show(args);
        } else if (!wasOpen && typeof instance.refresh === "function") {
          await instance.refresh();
        }
      } catch (err) {
        showError(err);
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
        showError(err);
      }
      entry.host.hidden = true;
      sync(entry); // removes the tab and restores the focus
    },

    closeAll() {
      for (const id of Array.from(tabs)) {
        dock.close(id);
      }
    },

    // Maximized: the dock takes the whole space of the grid area (the grid is hidden until Restore).
    maximize(on) {
      if (tabs.length === 0) {
        return;
      }
      const next = on === undefined ? !maximized : Boolean(on);
      if (next === maximized) {
        return;
      }
      if (!next && bounds().limits.maximizedOnly) {
        return; // no room to restore: the grid minimum plus the dock minimum does not fit
      }
      maximized = next;
      layout();
    },

    isOpen,
    active: () => active,

    // First phase of a case switch: every mounted panel forgets the old case, open, closed or idle.
    caseChanged() {
      requireBound("caseChanged");
      generation += 1;
      for (const [id, entry] of panels) {
        if (entry.def.closeOnCase && isOpen(id)) {
          dock.close(id); // also a tab that only shows a load error: closeOnCase does not depend on the mount
        }
        if (!entry.instance) {
          continue;
        }
        entry.stale = false;
        try {
          if (typeof entry.instance.reset === "function") {
            entry.instance.reset();
          }
        } catch (err) {
          showError(err);
        }
      }
    },

    // Visual update only: the active tab that listens to `reason` updates; an open tab behind it is marked stale and
    // refreshes when it is shown; a closed panel refreshes when reopened.
    notify(reason) {
      requireBound("notify");
      for (const [id, entry] of panels) {
        if (!entry.instance || !(entry.def.listens || []).includes(reason) || !isOpen(id)) {
          continue;
        }
        if (id !== active) {
          entry.stale = true;
          continue;
        }
        try {
          const result = typeof entry.instance.update === "function"
            ? entry.instance.update(reason)
            : entry.instance.refresh && entry.instance.refresh();
          if (result && typeof result.catch === "function") {
            result.catch((err) => showError(err));
          }
        } catch (err) {
          showError(err);
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
  paintMaximize();
  return dock;
}
