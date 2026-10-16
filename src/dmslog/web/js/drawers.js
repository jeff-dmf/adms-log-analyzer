// The sources rail and the drawers (UI-17): below 1500 px the sources panel is a 44 px rail that opens as a drawer over
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
  };
  const store = storage === undefined ? storageOf(win) : storage;
  const state = new DrawerState();
  let prefs = loadPrefs(store);
  let counts = { selected: 0, total: NaN };
  let openers = { side: null, detail: null };
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
    return { layout, closed };
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
    const opener = openers[which];
    const target = [opener, buttonOf(which), el.viewport].find((node) => node && node.isConnected !== false && node.hidden !== true);
    if (target && typeof target.focus === "function") {
      target.focus();
    }
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

  function toggle(which, opener = null) {
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
  on(el.gridPanel, "pointerdown", () => close(null)); // a click on the grid dismisses the drawer; the focus stays where the click put it
  on(win, "resize", () => {
    // A drawer whose panel goes back into the flow closes; if the focus was inside it, it goes to the opener.
    const was = state.open;
    const panel = was ? panelOf(was) : null;
    const hadFocus = Boolean(panel && panel.contains(doc.activeElement));
    paint();
    if (was !== null && state.open === null && hadFocus) {
      restoreFocus(was);
    }
  });
  paint();

  return {
    open,
    close,
    toggle,
    paint,
    openWhich: () => state.open,
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
