// The sources rail and the drawers (UI-17): below 1600 px the sources panel is a 44 px rail that opens as a drawer over
// the grid; below 1280 px the detail is a drawer too (opened with Enter in the grid or the "Detail" button of the filter
// strip). The layout itself is CSS (css/responsive.css, media queries) so nothing waits for this module; this file owns
// the open state, the focus, Escape, aria-expanded, the user's own collapse choices and the count on the rail.
// The rules are pure and live in drawers-model.js. Text comes from strings.js and is written with textContent only.
import { DETAIL_DRAWER_QUERY, DrawerState, SIDE_RAIL_QUERY, effectiveLayout, layoutModeFor, loadPrefs, railCount, savePrefs } from "./drawers-model.js";
import { S, format } from "./strings.js";

function storageOf(win) {
  try {
    return win.localStorage || null;
  } catch {
    return null; // blocked site data: the page works the same, it just does not remember
  }
}

// deps: {doc, win, storage?}. Every element comes from index.html; a missing one is skipped, never fatal.
export function createDrawers({ doc = document, win = window, storage } = {}) {
  const byId = (id) => (typeof doc.getElementById === "function" ? doc.getElementById(id) : null);
  const el = {
    panels: byId("panels"),
    sources: byId("sources-panel"),
    detail: byId("detail-panel"),
    rail: byId("side-rail"),
    count: byId("side-rail-count"),
    sideCollapse: byId("side-collapse"),
    detailToggle: byId("detail-toggle"),
    detailCollapse: byId("detail-collapse"),
    viewport: byId("grid-viewport"),
    gridPanel: byId("grid-panel"),
    search: byId("search-input"), // the last place for the focus when the whole grid area is not displayed
  };
  const store = storage === undefined ? storageOf(win) : storage;
  const state = new DrawerState();
  let prefs = loadPrefs(store);
  let counts = { selected: 0, total: NaN };
  let openers = { side: null, detail: null };
  let focusEl = null; // the element of the drawers (a panel, a control inside it, the rail, the Detail button) that holds the focus
  let snapshot = "";
  const listeners = new Set();
  const cleanups = [];

  const on = (target, type, handler) => {
    if (target && typeof target.addEventListener === "function") {
      target.addEventListener(type, handler);
      cleanups.push(() => target.removeEventListener && target.removeEventListener(type, handler));
    }
  };

  const modeNow = () => {
    if (typeof win.matchMedia === "function") {
      try {
        if (!win.matchMedia(SIDE_RAIL_QUERY).matches) {
          return "wide";
        }
        return win.matchMedia(DETAIL_DRAWER_QUERY).matches ? "narrow" : "rail";
      } catch {
        // fall through to the width
      }
    }
    return layoutModeFor(Number(win.innerWidth));
  };
  const layoutNow = () => effectiveLayout(modeNow(), prefs);
  const panelOf = (which) => (which === "side" ? el.sources : el.detail);
  const buttonOf = (which) => (which === "side" ? el.rail : el.detailToggle);

  // Effective visibility: the node and every ancestor are not [hidden], and the CSS (media queries, the open class)
  // leaves it displayed. `hidden` alone is not enough: the panels are hidden by CSS, not by the attribute.
  const visible = (node) => {
    if (!node || node.isConnected === false) {
      return false;
    }
    for (let n = node; n; n = n.parentElement || n.parent || null) {
      if (n.hidden === true) {
        return false;
      }
    }
    if (typeof node.checkVisibility === "function") {
      try {
        return node.checkVisibility({ visibilityProperty: true }) !== false;
      } catch {
        return true;
      }
    }
    return true;
  };
  // The guarded nodes: whatever can lose the focus by being hidden when the layout changes, and where it goes then.
  const guards = () => [
    { id: "sources", node: el.sources, fallback: [el.rail] },
    { id: "detail", node: el.detail, fallback: [el.detailToggle] },
    { id: "rail", node: el.rail, fallback: [el.sources] },
    { id: "detailToggle", node: el.detailToggle, fallback: [el.detail] },
  ];
  const guardOf = (target) => {
    for (const g of guards()) {
      if (g.node && target && typeof g.node.contains === "function" && g.node.contains(target)) {
        return g;
      }
    }
    return null;
  };
  // First visible control that can take the focus; the grid panel is always there.
  const focusFirstVisible = (candidates) => {
    const target = [...candidates, el.viewport, el.gridPanel, el.search].find((node) => node && visible(node) && typeof node.focus === "function");
    if (target) {
      target.focus();
    }
  };
  // The focus was inside a guarded node that is no longer displayed (a resize, a collapse): move it somewhere visible.
  function settleFocus() {
    if (focusEl === null || visible(focusEl)) {
      return;
    }
    const guard = guardOf(focusEl);
    focusEl = null;
    // A control inside a panel that is still displayed (the collapse and keep-open buttons) goes to the panel itself.
    focusFirstVisible(guard ? [guard.node, ...guard.fallback] : []);
  }

  const setHidden = (node, hidden) => {
    if (node) {
      node.hidden = hidden;
    }
  };
  const setText = (node, text) => {
    if (node && node.textContent !== text) {
      node.textContent = text;
    }
  };
  const toggleClass = (node, name, on_) => {
    if (node) {
      node.classList.toggle(name, on_);
    }
  };

  function paintCount() {
    const text = railCount(counts.selected, counts.total);
    setText(el.count, text);
    if (el.rail) {
      const [selected, total] = text === "" ? [0, 0] : text.split("/");
      el.rail.title = text === "" ? S.railTitle : format(S.railCountTitle, { selected, total });
    }
  }

  // Writes the whole state to the page. Safe to call at any time.
  function paint() {
    const layout = layoutNow();
    const closed = state.sync(layout);
    toggleClass(el.panels, "is-side-collapsed", prefs.sideCollapsed);
    toggleClass(el.panels, "is-detail-collapsed", prefs.detailCollapsed);
    toggleClass(el.panels, "is-side-open", state.open === "side");
    toggleClass(el.panels, "is-detail-open", state.open === "detail");
    setHidden(el.rail, !layout.sideRail);
    if (el.rail) {
      el.rail.setAttribute("aria-expanded", state.open === "side" ? "true" : "false");
    }
    setHidden(el.detailToggle, !layout.detailDrawer);
    if (el.detailToggle) {
      el.detailToggle.setAttribute("aria-expanded", state.open === "detail" ? "true" : "false");
    }
    // The collapse button: "collapse" while the panel is in the flow, "keep open" while it is an overlay by choice.
    paintCollapse(el.sideCollapse, layout.canCollapseSide && (!layout.sideRail || prefs.sideCollapsed), layout.sideRail, S.sideCollapse, S.sidePin);
    paintCollapse(el.detailCollapse, layout.canCollapseDetail && (!layout.detailDrawer || prefs.detailCollapsed), layout.detailDrawer, S.detailCollapse, S.detailPin);
    paintCount();
    settleFocus();
    emitIfChanged(layout);
    return { layout, closed };
  }

  function emitIfChanged(layout) {
    const next = `${state.open}|${layout.sideRail}|${layout.detailDrawer}`;
    if (next === snapshot) {
      return;
    }
    const first = snapshot === "";
    snapshot = next;
    if (first) {
      return;
    }
    for (const fn of [...listeners]) {
      try {
        fn({ open: state.open, sideRail: layout.sideRail, detailDrawer: layout.detailDrawer });
      } catch {
        // a listener's failure never breaks the layout
      }
    }
  }

  function paintCollapse(button, visible, asDrawer, collapseText, pinText) {
    if (!button) {
      return;
    }
    button.hidden = !visible;
    const text = asDrawer ? pinText : collapseText;
    button.setAttribute("aria-label", text);
    button.title = text;
  }

  function restoreFocus(which) {
    focusFirstVisible([openers[which], buttonOf(which)]);
  }

  function open(which, opener = null) {
    const layout = layoutNow();
    const wasOpen = state.open;
    if (!state.show(which, layout)) {
      return false;
    }
    if (wasOpen !== which) {
      openers[which] = opener || (doc.activeElement && doc.activeElement !== doc.body ? doc.activeElement : null);
    }
    paint();
    const panel = panelOf(which);
    if (panel && typeof panel.focus === "function") {
      panel.focus();
    }
    return true;
  }

  // restore: put the focus back on the opener (Escape, the button); false when the user clicked elsewhere.
  function close(which = null, { restore = false } = {}) {
    const current = state.open;
    if (current === null || (which !== null && which !== current)) {
      return false;
    }
    const panel = panelOf(current);
    const hadFocus = Boolean(panel && panel.contains(doc.activeElement));
    state.hide(current);
    paint();
    if (restore || hadFocus) {
      restoreFocus(current);
    }
    return true;
  }

  // Is the panel on screen: in the flow, or an open drawer.
  function isOpen(which) {
    const layout = layoutNow();
    if (which === "side") {
      return !layout.sideRail || state.open === "side";
    }
    if (which === "detail") {
      return !layout.detailDrawer || state.open === "detail";
    }
    return false;
  }

  function toggle(which, opener = null) {
    const layout = layoutNow();
    const inFlow = which === "side" ? !layout.sideRail : which === "detail" ? !layout.detailDrawer : false;
    if (inFlow) {
      collapse(which); // a panel in the flow: the toggle collapses it (a choice that is remembered)
      return isOpen(which);
    }
    if (state.open === which) {
      close(which, { restore: true });
      return false;
    }
    return open(which, opener);
  }

  function collapse(which) {
    const key = which === "side" ? "sideCollapsed" : "detailCollapsed";
    const layout = layoutNow();
    const asDrawer = which === "side" ? layout.sideRail : layout.detailDrawer;
    if (!(which === "side" ? layout.canCollapseSide : layout.canCollapseDetail)) {
      return;
    }
    const was = state.open;
    prefs = { ...prefs, [key]: !asDrawer }; // in the flow: collapse; a drawer by choice: keep it open (back in the flow)
    savePrefs(store, prefs);
    if (was === which) {
      state.hide(which);
    }
    paint();
    // The button that was pressed is now inside a hidden or moved panel: the focus goes where the panel went.
    const target = prefs[key] ? buttonOf(which) : panelOf(which);
    if (target && typeof target.focus === "function") {
      target.focus();
    }
  }

  // ---- wiring -------------------------------------------------------------------------------------------------
  if (el.sources && el.sources.setAttribute) {
    el.sources.setAttribute("tabindex", "-1");
  }
  if (el.detail && el.detail.setAttribute) {
    el.detail.setAttribute("tabindex", "-1");
  }
  on(el.rail, "click", () => toggle("side", el.rail));
  on(el.detailToggle, "click", () => toggle("detail", el.detailToggle));
  on(el.sideCollapse, "click", () => collapse("side"));
  on(el.detailCollapse, "click", () => collapse("detail"));
  on(el.panels, "keydown", (event) => {
    if (event.key === "Escape" && state.open !== null && !event.defaultPrevented) {
      event.preventDefault();
      close(null, { restore: true });
    }
  });
  on(el.viewport, "keydown", (event) => {
    // Enter shows the selected record; where the detail is a drawer it also opens the drawer.
    if (event.key === "Enter" && !event.ctrlKey && !event.altKey && !event.metaKey && layoutNow().detailDrawer) {
      open("detail", el.viewport);
    }
  });
  // A click on the grid dismisses the drawer; the focus stays where the click put it. The Detail button and the rail are
  // the controls of the drawers themselves: their own click toggles, so a pointerdown there must not close it first.
  on(el.gridPanel, "pointerdown", (event) => {
    const target = event && event.target;
    const own = [el.rail, el.detailToggle].some((node) => node && target && typeof node.contains === "function" && node.contains(target));
    if (!own) {
      close(null);
    }
  });
  // Where the focus is, so a layout change that hides it can move it (the browser reports nothing when CSS hides a node).
  const onFocusIn = (event) => {
    focusEl = guardOf(event && event.target) ? event.target : null;
  };
  on(doc, "focusin", onFocusIn);
  on(doc, "pointerdown", (event) => {
    if (guardOf(event && event.target) === null) {
      focusEl = null; // a click elsewhere took the focus away from the drawers
    }
  });
  on(win, "resize", () => paint()); // paint() closes a drawer whose panel went back to the flow and rescues the focus
  paint();

  return {
    open,
    close,
    toggle,
    paint,
    openWhich: () => state.open,
    isOpen,
    // fn({open, sideRail, detailDrawer}) after the open drawer or the layout changed; returns the function that unsubscribes.
    on(type, fn) {
      if (type !== "change" || typeof fn !== "function") {
        return () => {};
      }
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    layout: layoutNow,
    prefs: () => ({ ...prefs }),
    // The rail shows "selected/total" next to its name. Anything that is not a count is shown as the name alone.
    setSources(selected, total) {
      counts = { selected, total };
      paintCount();
    },
    destroy() {
      cleanups.splice(0).forEach((fn) => fn());
    },
  };
}
